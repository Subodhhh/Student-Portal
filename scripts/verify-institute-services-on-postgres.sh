#!/usr/bin/env bash
# Verifies the Institute SERVICES' migration chains on a REAL but DISPOSABLE PostgreSQL (never a configured database):
#   1. legacy monolith chain (m10_alembic_version)               -> schema A
#   2. the seven service chains on an empty database             -> schema B   (A and B must be identical apart from the NEW tables)
#   3. the seven service chains on top of A ("adopt")            -> schema C   (existing tables untouched; C must equal B)
#   4. `alembic check` for every service (models vs schema), then the cross-service integration suite on B.
#
#   PGBASE=postgresql+psycopg://postgres@127.0.0.1:5432 scripts/verify-institute-services-on-postgres.sh
# It creates (dropping first!) only databases named inst_verify_legacy / inst_verify_services on a LOCAL server.
set -euo pipefail
: "${PGBASE:?set PGBASE to postgresql+psycopg://user@127.0.0.1:port (local server only)}"
python3 - "$PGBASE" <<'PY'
import re, sys
m = re.match(r"^postgresql(\+\w+)?://[^@/]*@(?P<host>[^:/]+)(:\d+)?$", sys.argv[1])
if not m or m["host"] not in {"127.0.0.1", "localhost", "::1"}:
    sys.exit("PGBASE must look like postgresql+psycopg://user@127.0.0.1:port (local servers only)")
PY
ROOT="$(git rev-parse --show-toplevel)"
PSQL_BASE="${PGBASE/postgresql+psycopg/postgresql}"
SERVICES=(identity organization academics enrollment paper exam operations)
NEW_TABLES='^(inst_.*_audit_spool|exam_notification_outbox|composite_.*|.*_alembic_version)$'

prep() {   # $1 = database name: baseline students table + the real Student chains
  psql "$PSQL_BASE/postgres" -q -c "drop database if exists $1" -c "create database $1 encoding 'UTF8' template template0"
  local url="$PGBASE/$1"
  psql "$PSQL_BASE/$1" -q -c "create table students (student_id varchar(20) primary key, first_name varchar(255) not null default '', last_name varchar(255) not null default '', email varchar(255) unique not null default gen_random_uuid()::text, phone varchar(20), status varchar(20) not null default 'ACTIVE', deleted_at timestamp)"
  for svc in student-enrollment-service student-test-service student-attempt-service student-result-service student-notification-service; do
    (cd "$ROOT/student-backend/$svc" && PYTHONPATH=. DATABASE_URL="$url" REDIS_URL=redis://x CORS_ORIGINS=http://x alembic upgrade head >/dev/null)
  done
}
services_up() {   # $1 = database name
  for s in "${SERVICES[@]}"; do
    (cd "$ROOT/institute-backend/institute-$s-service" && PYTHONPATH=".:$ROOT/institute-backend/institute-common" DATABASE_URL="$PGBASE/$1" CORS_ORIGINS=http://x alembic upgrade head >/dev/null)
    echo "  applied $s chain on $1"
  done
}
inventory() { python3 "$ROOT/scripts/pg_schema_inventory.py" "$PGBASE/$1" --skip "$2"; }

echo "== A: legacy chain"
prep inst_verify_legacy
(cd "$ROOT/institute-backend/legacy-monolith" && DATABASE_URL="$PGBASE/inst_verify_legacy" REDIS_URL=redis://x CORS_ORIGINS=http://x alembic upgrade head >/dev/null)
inventory inst_verify_legacy '^(.*_alembic_version)$' > /tmp/inv_A.txt

echo "== B: service chains on an empty database"
prep inst_verify_services
services_up inst_verify_services
inventory inst_verify_services "$NEW_TABLES" > /tmp/inv_B.txt
# Every legacy object must still exist, except the documented intentional changes (scripts/legacy_schema_evolutions.txt);
# objects ADDED by later revisions are expected (they are additive) and are listed for information.
ALLOW="$(grep -v '^#' "$ROOT/scripts/legacy_schema_evolutions.txt" | grep -v '^$')"
missing=$(comm -23 <(sort /tmp/inv_A.txt) <(sort /tmp/inv_B.txt) | grep -v -F -f <(printf '%s\n' "$ALLOW") || true)
if [ -n "$missing" ]; then echo "FAIL: service chains lost or changed legacy objects not listed in scripts/legacy_schema_evolutions.txt:" >&2; echo "$missing" >&2; exit 1; fi
echo "  every legacy object present except $(printf '%s\n' "$ALLOW" | wc -l) documented evolutions; $(comm -13 <(sort /tmp/inv_A.txt) <(sort /tmp/inv_B.txt) | wc -l) objects added by later revisions"

echo "== C: service chains adopt the legacy schema"
services_up inst_verify_legacy
inventory inst_verify_legacy '^(.*_alembic_version)$' > /tmp/inv_C.txt
inventory inst_verify_services '^(.*_alembic_version)$' > /tmp/inv_B_full.txt
if ! diff -u /tmp/inv_B_full.txt /tmp/inv_C.txt; then echo "FAIL: adoption changed the schema" >&2; exit 1; fi
echo "  adopted schema identical to the fresh one; pre-existing objects untouched"

echo "== alembic check per service"
for s in "${SERVICES[@]}"; do
  (cd "$ROOT/institute-backend/institute-$s-service" && PYTHONPATH=".:$ROOT/institute-backend/institute-common" DATABASE_URL="$PGBASE/inst_verify_services" CORS_ORIGINS=http://x alembic check)
done

echo "== integration suite on PostgreSQL"
cd "$ROOT/institute-backend/institute-integration-tests"
TEST_DATABASE_URL="$PGBASE/inst_verify_services" python3 -m pytest -q
