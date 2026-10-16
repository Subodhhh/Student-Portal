#!/usr/bin/env bash
# Verifies the Institute backend against a REAL but DISPOSABLE PostgreSQL:
#   baseline students table -> the real Student-service Alembic chains -> the Institute chain (last, as in production)
#   -> `alembic check` (ORM vs schema) -> the full Institute test-suite on PostgreSQL (triggers, FKs, INET, TIMESTAMPTZ).
#
# Safety: it refuses anything that is not a local, empty database whose name says it is disposable.
# It never reads a service's .env and never touches the configured DATABASE_URL.
#
#   createdb inst_verify          # a throwaway local database, UTF-8
#   PGURL=postgresql+psycopg://postgres@127.0.0.1:5432/inst_verify scripts/verify-institute-on-postgres.sh
set -euo pipefail

: "${PGURL:?set PGURL to a SQLAlchemy URL of a disposable local database}"
python3 - "$PGURL" <<'PY'
import re, sys
m = re.match(r"^postgresql(\+\w+)?://[^@/]*@?(?P<host>[^:/]+)(:\d+)?/(?P<db>[\w-]+)$", sys.argv[1])
if not m:
    sys.exit("PGURL must look like postgresql+psycopg://user@host:port/dbname")
if m["host"] not in {"127.0.0.1", "localhost", "::1"}:
    sys.exit(f"refusing non-local host {m['host']!r}")
if not re.search(r"(verify|test|scratch|tmp|disposable)", m["db"]):
    sys.exit(f"refusing database {m['db']!r}: its name must contain verify/test/scratch/tmp/disposable")
PY

ROOT="$(git rev-parse --show-toplevel)"
PSQL_URL="${PGURL/postgresql+psycopg/postgresql}"
if [ "$(psql "$PSQL_URL" -Atc "select count(*) from information_schema.tables where table_schema='public'")" != "0" ]; then
  echo "refusing: the database is not empty" >&2; exit 1
fi

# Baseline: students.student_id as VARCHAR(20), which is what every Student model and the enrollments foreign key require.
# (The committed auth migrations create it as UUID and cannot be replayed on an empty database: see docs/institute-portal/OPERATIONS.md.)
psql "$PSQL_URL" -q -c "create table students (student_id varchar(20) primary key, first_name varchar(255) not null default '', last_name varchar(255) not null default '', email varchar(255) unique not null default gen_random_uuid()::text, phone varchar(20), status varchar(20) not null default 'ACTIVE', deleted_at timestamp)"

for svc in student-enrollment-service student-test-service student-attempt-service student-result-service student-notification-service; do
  (cd "$ROOT/student-backend/$svc" && PYTHONPATH=. DATABASE_URL="$PGURL" REDIS_URL=redis://x CORS_ORIGINS=http://x alembic upgrade head >/dev/null)
  echo "applied $svc chain"
done

cd "$ROOT/institute-backend/legacy-monolith"
export DATABASE_URL="$PGURL" REDIS_URL=redis://x CORS_ORIGINS=http://x
alembic upgrade head >/dev/null && echo "applied institute chain"
alembic check
TEST_DATABASE_URL="$PGURL" python3 -m pytest -q -k "not migrations"

# Re-evaluation uses PostgreSQL-only SQL: prove it scores exactly like the production evaluator on the real tables.
cd "$ROOT/student-backend/student-result-service"
DATABASE_URL="$PGURL" TEST_DATABASE_URL="$PGURL" python3 -m pytest -q tests/test_corrections_pg.py
