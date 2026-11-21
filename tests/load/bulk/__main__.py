"""LT_TARGET=local tests/load/lt.sh stage2 <prepare|schema|plan|load|validate|fingerprint|bundle|drop> [--pair local|stage2|lantest] [--fraction 0.01]
Writes only to the guarded local load-test databases. The full dataset needs --approved-full. Computer 2 runs this same module from the
bundle with LT_PG_CONNECT_HOST set (docs/stage2-dataset.md)."""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg

LOAD_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOAD_ROOT / "guard"))
import guard  # noqa: E402

from . import load as loader, remote, schema, validate  # noqa: E402
from .tables import PLAN  # noqa: E402


def guarded_connections(pair: str = "local") -> dict:
    cfg = guard.load_config()
    if guard.cmd_check(cfg, [pair]) != 0:
        raise SystemExit("branch guard refused the targets: nothing was touched")
    out = {}
    for db in ("student", "institute"):
        target = cfg["targets"][f"{pair}-{db}"]
        assert target["kind"] == "local"
        out[db] = psycopg.connect(guard.libpq_url(guard.database_urls(target)[0][1], guard.connect_host(target)), connect_timeout=15,
                                  keepalives=1, keepalives_idle=60, keepalives_interval=15, keepalives_count=6)      # one side idles for hours over the LAN
    return out


def password_hash() -> str | None:
    env = guard.config_dir() / "local/synthetic.env"
    return os.environ.get("LT_SYNTH_PASSWORD_HASH") or (guard.read_env(env).get("LT_SYNTH_PASSWORD_HASH") if env.is_file() else None)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("schema", "plan", "load", "validate", "prepare", "drop", "bundle", "fingerprint"))
    ap.add_argument("--pair", default="local", choices=("local", "stage2", "lantest"), help="target databases (config/targets.json)")
    ap.add_argument("--fraction", type=float, default=0.01)
    ap.add_argument("--anchor", help="UTC date the dataset is built around (default: the state file's, else today)")
    ap.add_argument("--approved-full", action="store_true", help="required above 5 %% of Stage 2")
    args = ap.parse_args(argv)
    if args.command == "prepare":
        return remote.prepare(args.pair)
    if args.command == "drop":
        return remote.drop(args.pair)
    if args.command == "bundle":
        return remote.bundle()
    if args.command == "schema":
        guarded_connections(args.pair)      # refuses anything but the pinned local databases
        cfg = guard.load_config()
        urls = [guard.database_urls(cfg["targets"][f"{args.pair}-{db}"])[0][1] for db in ("student", "institute")]
        host = cfg["targets"][f"{args.pair}-student"]["connect_host"]
        schema.apply(*[re.sub(r"@[^/@?]+(/|\?|$)", lambda x: f"@{host}{x.group(1)}", u, count=1) for u in urls])
        return 0
    if args.fraction > 0.05 and not args.approved_full:
        raise SystemExit("more than the pilot needs --approved-full: nothing was touched")
    label = "" if args.pair == "local" else f"{args.pair}-"
    state_file = LOAD_ROOT / "results" / "stage2" / f"state-{label}{args.fraction:g}.json"
    if args.anchor:
        anchor = datetime.fromisoformat(args.anchor).replace(tzinfo=timezone.utc)
    elif state_file.exists():
        anchor = datetime.fromisoformat(json.loads(state_file.read_text())["identity"]["anchor"])
    else:
        anchor = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    world = loader.build_world(args.fraction, anchor)
    if args.command == "fingerprint":
        print(f"{loader.fingerprint(world)} (python {sys.version.split()[0]}, fraction {args.fraction:g}, anchor {anchor.date()})")
        return 0
    if args.command == "plan":
        for db, table, _c, make in PLAN:
            print(f"{db}.{table}: {sum(1 for _ in make(world))}")
        return 0
    conns = guarded_connections(args.pair)
    world.password_hash = password_hash()
    if args.command == "load":
        loader.load(conns, world, state_file)
        return 0
    failures = validate.run(conns, world, state_file)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
