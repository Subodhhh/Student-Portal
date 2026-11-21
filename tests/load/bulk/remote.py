"""Computer 1 side of a remote load: the separate databases a large dataset goes into (the pilot's databases stay as they are), and the
bundle Computer 2 runs from. Names are fixed here, so these commands cannot reach lt_student, lt_institute or anything else."""
import os
import subprocess
import sys
import zipfile
from pathlib import Path

LOAD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOAD / "guard"))
import guard  # noqa: E402

from . import schema  # noqa: E402

DATABASES = {"stage2": "lt_stage2", "lantest": "lt_lantest"}
ROLE = {"student": "lt_student", "institute": "lt_institute"}
OUT = LOAD / "results" / "bundle"
CODE = ["bulk/__init__.py", "bulk/__main__.py", "bulk/load.py", "bulk/remote.py", "bulk/schema.py", "bulk/tables.py", "bulk/validate.py", "bulk/world.py",
        "generator/__init__.py", "generator/plan.py", "generator/questions.py", "guard/guard.py", "config/targets.json", "requirements-remote.txt"]


def psql(database: str, sql: str) -> str:
    r = subprocess.run(["docker", "exec", "-i", "lt-postgres", "sh", "-c", f'psql -U "$POSTGRES_USER" -d {database} -v ON_ERROR_STOP=1 -At'],
                       input=sql, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"lt-postgres refused: {guard.redact(r.stderr)[-300:]}")
    return r.stdout.strip()


def _names(pair: str) -> dict:
    if pair not in DATABASES:
        raise SystemExit(f"--pair must be one of {', '.join(DATABASES)}: lt_student and lt_institute are never prepared or dropped")
    return {part: f"{DATABASES[pair]}_{part}" for part in ROLE}


def prepare(pair: str) -> int:
    """Creates the two databases and their env files, applies the migrations, and pins both identities. A database that exists is refused."""
    names = _names(pair)
    cfg = guard.load_config()
    urls = {}
    for part, name in names.items():
        if psql("postgres", f"select 1 from pg_database where datname = '{name}'"):
            raise SystemExit(f"{name} already exists: nothing was changed")
        base = guard.read_env(guard.config_dir() / f"local/{part}.env")["DATABASE_URL"]
        urls[part] = base.rsplit("/", 1)[0] + f"/{name}"
    for part, name in names.items():
        psql("postgres", f"create database {name} owner {ROLE[part]}; revoke all on database {name} from public;")
        path = guard.config_dir() / pair / f"{part}.env"
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(f"DATABASE_URL={urls[part]}\n")
        print(f"created {name}")
    host = cfg["targets"][f"{pair}-student"]["connect_host"]
    schema.apply(*[guard.re.sub(r"@[^/@?]+/", f"@{host}/", urls[p], count=1) for p in ("student", "institute")])
    print("migrations applied")
    for part in names:
        name = f"{pair}-{part}"
        seen, _problems = guard.observe_target(cfg["targets"][name])
        if guard.cmd_pin(cfg, name, seen["identity"]):
            return 1
    return guard.cmd_check(guard.load_config(), [pair])


def drop(pair: str) -> int:
    """Removes the disposable test databases, their env files and pins. Only `lantest`."""
    if pair != "lantest":
        raise SystemExit("only the disposable lantest pair can be dropped")
    cfg = guard.load_config()
    for part, name in _names(pair).items():
        psql("postgres", f"drop database if exists {name} with (force);")
        (guard.config_dir() / pair / f"{part}.env").unlink(missing_ok=True)
        cfg["targets"][f"{pair}-{part}"]["identity"] = None
        print(f"dropped {name}")
    guard.TARGETS.write_text(guard.json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return 0


def bundle() -> int:
    """code.zip: everything Computer 2 runs, no secrets. secrets.zip: the connection URLs and the password HASH; move it by hand, delete it after."""
    OUT.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT / "lt-stage2-code.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for rel in CODE:
            z.write(LOAD / rel, f"tests/load/{rel}")
    config = guard.config_dir()
    secrets = OUT / "lt-stage2-secrets.zip"
    os.close(os.open(secrets, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600))
    with zipfile.ZipFile(secrets, "w", zipfile.ZIP_DEFLATED) as z:
        for pair in DATABASES:
            for part in ROLE:
                env = config / pair / f"{part}.env"
                if env.is_file():
                    z.write(env, f"tests/load/config/{pair}/{part}.env")
        pw = guard.read_env(config / "local/synthetic.env")["LT_SYNTH_PASSWORD_HASH"]
        z.writestr("tests/load/config/local/synthetic.env", f"LT_SYNTH_PASSWORD_HASH={pw}\n")     # the hash only, never the password
    print(f"wrote {OUT / 'lt-stage2-code.zip'} and {secrets}")
    return 0
