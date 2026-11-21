"""Builds the application schema from the repository's own migration chains, exactly as scripts/verify-*-on-postgres.sh do: the `students`
table no chain reproduces, then every Student chain, then (Institute database only) the Institute chains. Each chain runs in the
virtualenv that matches its service. Refuses a database that already has tables. Fixture mode: python -m bulk.schema <scratch dir>."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VENVS = Path.home() / ".cache/student-service-test-venvs"
PG = "/usr/lib/postgresql/16/bin"
PORT = 55439
CHAINS = ("student-enrollment-service", "student-test-service", "student-attempt-service", "student-result-service", "student-notification-service")
INSTITUTE = ("identity", "organization", "academics", "enrollment", "paper", "exam", "operations")
STUDENT_TABLE = """CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE TYPE student_status AS ENUM ('ACTIVE');
CREATE TABLE students (student_id varchar(20) PRIMARY KEY DEFAULT gen_random_uuid(), first_name varchar(255) NOT NULL, last_name varchar(255) NOT NULL,
 email varchar(255) NOT NULL UNIQUE, phone varchar(20), status student_status NOT NULL, created_ip_address inet, created_latitude numeric, created_longitude numeric,
 updated_ip_address inet, updated_latitude numeric, updated_longitude numeric, deleted_at timestamp, deleted_ip_address inet, deleted_latitude numeric,
 deleted_longitude numeric, created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
 password_hash varchar(255), date_of_birth date);"""
INSTITUTE_TABLE = """create table students (student_id varchar(20) primary key, first_name varchar(255) not null default '', last_name varchar(255) not null default '',
 email varchar(255) unique not null default gen_random_uuid()::text, phone varchar(20), status varchar(20) not null default 'ACTIVE', deleted_at timestamp);"""
ENV = dict(os.environ, REDIS_URL="redis://127.0.0.1:1/0", CORS_ORIGINS="http://x", SESSION_COOKIE_SECURE="false", RESEND_API_KEY="", EMAIL_FROM="",
           FRONTEND_RESET_URL="http://x/r")


def _run(cmd: list, cwd: Path, env: dict) -> None:
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"{cwd.name}: migration failed (exit {r.returncode}): {r.stderr[-600:]}")


def _sql(url: str, statements: str) -> None:
    import psycopg
    with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://"), autocommit=True) as c:
        if c.execute("select count(*) from information_schema.tables where table_schema = 'public'").fetchone()[0]:
            raise SystemExit("the target database already has tables: nothing was changed")
        c.execute(statements)


def _student_chain(service: str, url: str, stamp: str | None = None) -> None:
    py, cwd = str(VENVS / service / "bin/python"), ROOT / "student-backend" / service
    env = dict(ENV, DATABASE_URL=url, PYTHONPATH=".")
    if stamp:
        _run([py, "-m", "alembic", "stamp", stamp], cwd, env)
    _run([py, "-m", "alembic", "upgrade", "head"], cwd, env)


def apply(student_url: str, institute_url: str) -> None:
    _sql(student_url, STUDENT_TABLE)
    _student_chain("student-authentication-service", student_url, stamp="9ffccd3f23f5")
    for service in CHAINS:
        _student_chain(service, student_url)
    _sql(institute_url, INSTITUTE_TABLE)
    for service in CHAINS:
        _student_chain(service, institute_url)
    for name in INSTITUTE:
        env = dict(ENV, DATABASE_URL=institute_url, PYTHONPATH=f".:{ROOT}/institute-backend/institute-common")
        _run([str(ROOT / ".venv/bin/python"), "-m", "alembic", "upgrade", "head"], ROOT / f"institute-backend/institute-{name}-service", env)


def fixture(scratch: Path) -> None:
    """A throwaway server: loopback TCP only, data under `scratch`. Empty schemas are kept as templates for the tests."""
    import psycopg
    data = scratch / "pgfix"
    if not (data / "PG_VERSION").exists():
        subprocess.run([f"{PG}/initdb", "-D", str(data), "-U", "lt", "--auth=trust", "-E", "UTF8", "--locale=C.UTF-8"], check=True, capture_output=True)
    if subprocess.run([f"{PG}/pg_ctl", "-D", str(data), "status"], capture_output=True).returncode:
        opts = f"-p {PORT} -c listen_addresses=127.0.0.1 -c unix_socket_directories='' -c fsync=off -c shared_buffers=128MB -c timezone=UTC"
        subprocess.run([f"{PG}/pg_ctl", "-D", str(data), "-w", "-l", str(data / "server.log"), "-o", opts, "start"], check=True, capture_output=True)
    with psycopg.connect(f"host=127.0.0.1 port={PORT} user=lt dbname=postgres", autocommit=True) as c:
        for name in ("fix_student", "fix_institute", "tpl_student", "tpl_institute"):
            c.execute(f"drop database if exists {name}")
            c.execute(f"create database {name} template template0")
        url = f"postgresql+psycopg://lt@127.0.0.1:{PORT}/"
        apply(url + "tpl_student", url + "tpl_institute")
        for name in ("student", "institute"):
            c.execute(f"drop database fix_{name}")
            c.execute(f"create database fix_{name} template tpl_{name}")


if __name__ == "__main__":
    fixture(Path(sys.argv[1]))
