#!/usr/bin/env python3
"""Creates the REAL load-test env files from the *.env.example templates. Run it yourself (never by an assistant):

    python3 tests/load/config/init_env.py neon        # compose.env, monitoring.env, neon/student.env, neon/institute.env
    python3 tests/load/config/init_env.py local       # local/postgres.env, local/student.env, local/institute.env (Stage 2)

* Service keys and local passwords are generated randomly (secrets.token_urlsafe); the Neon connection strings are typed or pasted
  at a HIDDEN prompt (getpass): nothing is echoed, printed, logged or kept in shell history.
* A Neon URL is refused unless it is a pooled us-east-1 Neon host and NOT one of the development endpoints; the student and the
  institute URL must use different endpoints. The branch guard verifies the branch itself afterwards (`lt.sh guard identify`).
* Files are written 0600 (directories 0700); an existing file is never overwritten (pass --force to replace one deliberately).
* Only file names are printed. To read a generated value later (e.g. the Grafana password), open the file yourself."""
import argparse
import os
import re
import secrets
import sys
from getpass import getpass
from pathlib import Path
from urllib.parse import urlparse

CONFIG = Path(__file__).resolve().parent
DEV_ENDPOINTS = ("ep-dark-heart-b8aehym2", "ep-snowy-unit-b875nn0h")
KEY_LINE = re.compile(r"^([A-Z0-9_]+)=(.*)$")


class Refused(SystemExit):
    pass


def token() -> str:
    return secrets.token_urlsafe(32)


def check_neon_url(url: str) -> str:
    """Validates a pasted Neon URL WITHOUT ever including it in an error message; returns it in SQLAlchemy psycopg form."""
    url = url.strip().strip('"').strip("'")
    u = urlparse(url)
    host = u.hostname or ""
    if u.scheme not in ("postgres", "postgresql", "postgresql+psycopg") or not u.username or not u.password or not u.path.strip("/"):
        raise Refused("refused: not a complete postgresql:// connection string (user, password, host, database)")
    if not host.endswith(".neon.tech") or "-pooler" not in host.split(".")[0]:
        raise Refused("refused: not a POOLED Neon host (Neon console -> Connect -> 'Connection pooling' on)")
    if any(host.startswith(ep) for ep in DEV_ENDPOINTS):
        raise Refused("refused: this is a DEVELOPMENT endpoint; use the load_test_* branch's own connection string")
    if "sslmode=require" not in (u.query or ""):
        raise Refused("refused: the URL must contain sslmode=require")
    return re.sub(r"^postgres(?:ql)?(?:\+psycopg)?://", "postgresql+psycopg://", url)


def endpoint_of(url: str) -> str:
    return (urlparse(url).hostname or "").split(".")[0].removesuffix("-pooler")


def fill(template: Path, values: dict[str, str]) -> str:
    """The template with the given KEY=value lines replaced (comments and every other line kept)."""
    out = []
    for line in template.read_text(encoding="utf-8").splitlines():
        m = KEY_LINE.match(line)
        out.append(f"{m.group(1)}={values[m.group(1)]}" if m and m.group(1) in values else line)
    return "\n".join(out) + "\n"


def keys_of(template: Path) -> list[str]:
    return [m.group(1) for line in template.read_text(encoding="utf-8").splitlines() if (m := KEY_LINE.match(line))]


def write_private(path: Path, text: str, force: bool) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    if path.exists() and not force:
        print(f"kept existing {path.relative_to(CONFIG.parents[2])} (use --force to replace it)")
        return False
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, 0o600)
    print(f"wrote {path.relative_to(CONFIG.parents[2])} (0600)")
    return True


def init_common(force: bool) -> None:
    t = CONFIG / "compose.env.example"
    write_private(CONFIG / "compose.env", fill(t, {k: token() for k in keys_of(t)}), force)
    write_private(CONFIG / "monitoring.env", fill(CONFIG / "monitoring.env.example", {"GF_SECURITY_ADMIN_PASSWORD": token()}), force)


def init_neon(force: bool, ask=getpass) -> None:
    init_common(force)
    urls = {}
    for part, branch in (("student", "load_test_student"), ("institute", "load_test_institute")):
        target = CONFIG / "neon" / f"{part}.env"
        if target.exists() and not force:
            print(f"kept existing {target.relative_to(CONFIG.parents[2])} (use --force to replace it)")
            continue
        urls[part] = check_neon_url(ask(f"Pooled connection string of {branch} (hidden input): "))
    if len(urls) == 2 and endpoint_of(urls["student"]) == endpoint_of(urls["institute"]):
        raise Refused("refused: both URLs use the same endpoint; each load-test branch has its own")
    for part, url in urls.items():
        write_private(CONFIG / "neon" / f"{part}.env", fill(CONFIG / "neon" / f"{part}.env.example", {"DATABASE_URL": url}), force=True)


def init_local(force: bool) -> None:
    init_common(force)
    pw = {"LT_INSTITUTE_PASSWORD": token(), "LT_STUDENT_PASSWORD": token(), "POSTGRES_PASSWORD": token(), "DATA_SOURCE_PASS": token()}
    if not write_private(CONFIG / "local" / "postgres.env", fill(CONFIG / "local" / "postgres.env.example", pw), force):
        print("local/postgres.env already exists: student/institute files left alone so their passwords stay in step")
        return
    for part, key, role in (("student", "LT_STUDENT_PASSWORD", "lt_student"), ("institute", "LT_INSTITUTE_PASSWORD", "lt_institute")):
        url = f"postgresql+psycopg://{role}:{pw[key]}@lt-postgres:5432/{role}"
        write_private(CONFIG / "local" / f"{part}.env", fill(CONFIG / "local" / f"{part}.env.example", {"DATABASE_URL": url}), force=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("target", choices=["neon", "local"])
    ap.add_argument("--force", action="store_true", help="replace existing files")
    args = ap.parse_args(argv)
    try:
        (init_neon if args.target == "neon" else init_local)(args.force)
    except Refused as e:
        print(e.code if isinstance(e.code, str) else "refused", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
