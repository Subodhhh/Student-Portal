"""Branch guard of the load-test environment: proves WHICH database a load-test env file points at before anything uses it.

    lt.sh guard identify <target>                 # read-only: print the IDs the connection reports (never the URL)
    lt.sh guard pin <target> --confirm <id>       # record them in config/targets.json once the user confirmed them
    lt.sh guard check <target|neon|local> ...     # exit 0 only if every target is exactly the pinned load-test database

Neon targets: neon.branch_id must equal the pinned branch, must not be a development branch (deny list), and the endpoint and
project must match. Local targets: the server must NOT be Neon (neon.branch_id unset), the database name must match and the
database's identity (cluster system identifier + database OID) must equal the pinned value, so a recreated or different database
is refused. DATABASE_URL and MIGRATION_DATABASE_URL (when set) must both resolve to the same database. The two targets of a pair
must be different databases. Every session is read-only. Connection strings and passwords are never printed."""
import argparse
import ipaddress
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TARGETS = ROOT / "tests/load/config/targets.json"
URL_KEYS = ("DATABASE_URL", "MIGRATION_DATABASE_URL")
_SECRET = re.compile(r"(?i)(postgres(?:ql)?(?:\+\w+)?://)[^\s'\"]+|(password\s*=\s*)\S+")


class GuardError(Exception):
    pass


def redact(text: str) -> str:
    """Remove anything that could be a connection string or password from a message."""
    return _SECRET.sub(lambda m: (m.group(1) or m.group(2)) + "***", str(text))


def read_env(path: Path) -> dict[str, str]:
    """KEY=VALUE lines parsed in Python (never `source`d: the URLs contain unquoted '&')."""
    if not path.is_file():
        raise GuardError(f"env file missing: {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
    out = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def libpq_url(url: str, connect_host: str | None = None) -> str:
    """SQLAlchemy URL -> libpq URL; for local targets the in-network host (lt-postgres:5432) is swapped for the host-side port."""
    url = re.sub(r"^postgres(?:ql)?\+\w+://", "postgresql://", url)
    if connect_host:
        url = re.sub(r"@[^/@?]+(/|\?|$)", lambda m: f"@{connect_host}{m.group(1)}", url, count=1)
    return url


def connect_host(target: dict) -> str | None:
    """Where a local target is reached from this machine. LT_PG_CONNECT_HOST lets the load generator (Computer 2) use Computer 1's LAN address;
    it never widens what is accepted: the server must still prove the pinned identity, and only private addresses are allowed."""
    host = os.environ.get("LT_PG_CONNECT_HOST")
    if not host or target["kind"] != "local":
        return target.get("connect_host")
    name, _, port = host.rpartition(":")
    try:
        private = ipaddress.ip_address(name).is_private and port.isdigit()
    except ValueError:
        private = False
    if not private:
        raise GuardError("LT_PG_CONNECT_HOST must be a private IPv4 address with a port")
    return host


def config_dir() -> Path:
    """The SAME directory Compose reads the env files from (lt.sh exports LT_CONFIG_DIR), so the guard verifies exactly those files."""
    d = Path(os.environ.get("LT_CONFIG_DIR") or "tests/load/config")
    return d if d.is_absolute() else ROOT / d


def database_urls(target: dict) -> list[tuple[str, str]]:
    env = read_env(config_dir() / target["env_file"])
    urls = [(k, env[k]) for k in URL_KEYS if env.get(k)]
    if not urls or urls[0][0] != "DATABASE_URL":
        raise GuardError(f"{target['env_file']}: DATABASE_URL is not set")
    return urls


def observe(url: str) -> dict:
    """What the server itself reports, in a read-only session. Imported lazily so the pure checks need no driver."""
    import psycopg
    try:
        with psycopg.connect(url, connect_timeout=15, options="-c default_transaction_read_only=on") as conn:
            row = conn.execute(
                "select current_setting('neon.branch_id', true), current_setting('neon.endpoint_id', true),"
                " current_setting('neon.project_id', true), current_database(), current_setting('transaction_read_only'),"
                " (select oid::text from pg_database where datname = current_database())").fetchone()
            try:
                sysid = str(conn.execute("select system_identifier from pg_control_system()").fetchone()[0])
            except psycopg.Error:
                conn.rollback()
                sysid = None
    except psycopg.Error as e:
        raise GuardError(f"cannot connect: {type(e).__name__}: {redact(e)}") from None
    branch, endpoint, project, database, read_only, db_oid = row
    return {"branch_id": branch or None, "endpoint_id": endpoint or None, "project_id": project or None, "database": database,
            "read_only": read_only == "on", "identity": f"{sysid or 'nosysid'}:{db_oid}"}


def evaluate(name: str, target: dict, seen: dict, deny: dict) -> list[str]:
    """Every reason this observation is NOT the pinned load-test database (empty list = verified). Pure: unit-tested."""
    problems = []
    if not seen.get("read_only"):
        problems.append("session is not read-only")
    if seen.get("branch_id") in deny.get("branch_ids", []) or seen.get("endpoint_id") in deny.get("endpoint_ids", []):
        problems.append(f"DEVELOPMENT database ({seen.get('branch_id') or seen.get('endpoint_id')}): refused")
    if target["kind"] == "neon":
        if not seen.get("branch_id"):
            problems.append("not a Neon database (neon.branch_id is not set)")
        for key in ("branch_id", "endpoint_id"):
            if not target.get(key):
                problems.append(f"{key} not pinned yet: run `lt.sh guard identify {name}` and pin after confirming")
            elif seen.get(key) != target[key]:
                problems.append(f"{key} is {seen.get(key)}, expected {target[key]}")
        if target.get("project_id") and seen.get("project_id") != target["project_id"]:
            problems.append(f"project is {seen.get('project_id')}, expected {target['project_id']}")
        if target.get("database") and seen.get("database") != target["database"]:
            problems.append(f"database is {seen.get('database')}, expected {target['database']}")
    elif target["kind"] == "local":
        if seen.get("branch_id") or seen.get("endpoint_id"):
            problems.append(f"local target answered from Neon ({seen.get('branch_id')}): refused")
        if seen.get("database") != target["database"]:
            problems.append(f"database is {seen.get('database')}, expected {target['database']}")
        if not target.get("identity"):
            problems.append(f"identity not pinned yet: run `lt.sh guard identify {name}` and pin after confirming")
        elif seen.get("identity") != target["identity"]:
            problems.append(f"identity is {seen.get('identity')}, expected {target['identity']} (database recreated or replaced?)")
    else:
        problems.append(f"unknown kind {target['kind']!r}")
    return problems


def same_database(a: dict, b: dict) -> bool:
    if a.get("branch_id") or b.get("branch_id"):
        return a.get("branch_id") == b.get("branch_id") and a.get("database") == b.get("database")
    return a.get("identity") == b.get("identity")


def load_config() -> dict:
    return json.loads(TARGETS.read_text(encoding="utf-8"))


def expand(cfg: dict, names: list[str]) -> list[str]:
    out = []
    for n in names:
        out += cfg["pairs"].get(n, [n])
    unknown = [n for n in out if n not in cfg["targets"]]
    if unknown:
        raise GuardError(f"unknown target(s): {', '.join(unknown)}")
    return list(dict.fromkeys(out))


def observe_target(target: dict) -> tuple[dict, list[str]]:
    """Observation of DATABASE_URL, plus a problem if MIGRATION_DATABASE_URL points elsewhere."""
    urls = database_urls(target)
    first = observe(libpq_url(urls[0][1], connect_host(target)))
    problems = []
    for key, url in urls[1:]:
        other = observe(libpq_url(url, connect_host(target)))
        if not same_database(first, other):
            problems.append(f"{key} points at a different database than DATABASE_URL")
    return first, problems


def shown(seen: dict) -> str:
    return " ".join(f"{k}={seen.get(k)}" for k in ("project_id", "branch_id", "endpoint_id", "database", "identity"))


def cmd_check(cfg: dict, names: list[str]) -> int:
    seen_by = {}
    failed = False
    for name in expand(cfg, names):
        try:
            seen, problems = observe_target(cfg["targets"][name])
        except GuardError as e:
            print(f"REFUSED {name}: {e}")
            failed = True
            continue
        problems += evaluate(name, cfg["targets"][name], seen, cfg["deny"])
        seen_by[name] = seen
        print(("OK      " if not problems else "REFUSED ") + f"{name}: {shown(seen)}")
        for p in problems:
            print(f"        - {p}")
        failed |= bool(problems)
    names_seen = list(seen_by)
    for i, a in enumerate(names_seen):
        for b in names_seen[i + 1:]:
            if same_database(seen_by[a], seen_by[b]):
                print(f"REFUSED {a} and {b} are the SAME database")
                failed = True
    return 1 if failed else 0


def cmd_identify(cfg: dict, name: str) -> int:
    target = cfg["targets"][expand(cfg, [name])[0]]
    seen, problems = observe_target(target)
    print(f"{name}: {shown(seen)}")
    denied = [p for p in evaluate(name, target, seen, cfg["deny"]) if "DEVELOPMENT" in p or "refused" in p]
    for p in problems + denied:
        print(f"  WARNING: {p}")
    key = "branch_id" if target["kind"] == "neon" else "identity"
    print(f"Confirm this is {target.get('branch_name') or target.get('database')}, then: lt.sh guard pin {name} --confirm {seen.get(key)}")
    return 1 if denied or problems else 0


def cmd_pin(cfg: dict, name: str, confirm: str) -> int:
    target = cfg["targets"][expand(cfg, [name])[0]]
    seen, problems = observe_target(target)
    key = "branch_id" if target["kind"] == "neon" else "identity"
    if seen.get(key) != confirm:
        print(f"REFUSED: the connection reports {key}={seen.get(key)}, not the confirmed {confirm}")
        return 1
    blocking = problems + [p for p in evaluate(name, {**target, key: confirm, "endpoint_id": seen.get("endpoint_id")}, seen, cfg["deny"])]
    if blocking:
        print("REFUSED: " + "; ".join(blocking))
        return 1
    if target["kind"] == "neon":
        target.update(branch_id=seen["branch_id"], endpoint_id=seen["endpoint_id"], database=seen["database"])
    else:
        target["identity"] = seen["identity"]
    TARGETS.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(f"pinned {name}: {shown(seen)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").add_argument("targets", nargs="+")
    sub.add_parser("identify").add_argument("target")
    pin = sub.add_parser("pin")
    pin.add_argument("target")
    pin.add_argument("--confirm", required=True)
    args = ap.parse_args(argv)
    try:
        cfg = load_config()
        if args.cmd == "check":
            return cmd_check(cfg, args.targets)
        if args.cmd == "identify":
            return cmd_identify(cfg, args.target)
        return cmd_pin(cfg, args.target, args.confirm)
    except GuardError as e:
        print(f"REFUSED: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
