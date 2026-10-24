#!/usr/bin/env bash
# Paper -> student test pipeline, end to end, on DISPOSABLE infrastructure only (never the shared databases or the live stack):
#   throwaway PostgreSQL 18 + Redis containers on non-live ports, the Student chains + this revision's Institute chains,
#   the six real Student services as processes (uvicorn), and pipeline_disposable.py driving the Institute services in-process
#   with REAL HTTP clients to them. Everything is removed on exit.
#
#   tests/e2e/pipeline_disposable.sh
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"; PY="$ROOT/.venv/bin/python"; export PATH="$ROOT/.venv/bin:$PATH"
PGPORT="${PIPELINE_PGPORT:-55433}"; RPORT="${PIPELINE_RPORT:-56380}"; DB=pipeline_verify; KEY="pipeline-$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')"
PG="postgresql://postgres@127.0.0.1:$PGPORT"; SA="postgresql+psycopg://postgres@127.0.0.1:$PGPORT"
WORK="$(mktemp -d)"; PIDS=()
cleanup() {
  for p in "${PIDS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null || true; done
  docker rm -f pipeline-pg pipeline-redis >/dev/null 2>&1 || true
  [ "${KEEP_LOGS:-}" ] && echo "logs kept in $WORK" || rm -rf "$WORK"
}
trap cleanup EXIT

docker run -d --rm --name pipeline-pg -p 127.0.0.1:$PGPORT:5432 -e POSTGRES_HOST_AUTH_METHOD=trust --tmpfs /var/lib/postgresql postgres:18 >/dev/null
docker run -d --rm --name pipeline-redis -p 127.0.0.1:$RPORT:6379 redis:7-alpine >/dev/null
for _ in $(seq 1 40); do pg_isready -h 127.0.0.1 -p $PGPORT -q && break; sleep 1; done

psql "$PG/postgres" -q -c "create database $DB encoding 'UTF8' template template0"
psql "$PG/$DB" -q -c "create table students (student_id varchar(20) primary key, first_name varchar(255) not null default '', last_name varchar(255) not null default '', email varchar(255) unique not null default gen_random_uuid()::text, phone varchar(20), status varchar(20) not null default 'ACTIVE', deleted_at timestamp)"
for svc in student-enrollment-service student-test-service student-attempt-service student-result-service student-notification-service; do
  (cd "$ROOT/student-backend/$svc" && PYTHONPATH=. DATABASE_URL="$SA/$DB" REDIS_URL=redis://x CORS_ORIGINS=http://x alembic upgrade head >/dev/null)
done
# the authentication service's own columns (password hash etc.) and reset-token table, as its models define them
(cd "$ROOT/student-backend/student-authentication-service" && PYTHONPATH=. DATABASE_URL="$SA/$DB" REDIS_URL=redis://127.0.0.1:$RPORT CORS_ORIGINS=http://x \
  SESSION_COOKIE_SECURE=false FRONTEND_RESET_URL=http://x/reset-password "$PY" - <<'EOF'
from sqlalchemy import inspect, text
from app.database import Base, engine
import app.models  # noqa: F401
cols = {c["name"] for c in inspect(engine).get_columns("students")}
with engine.begin() as c:   # every column the authentication model has and the baseline table lacks (nullable / defaulted)
    for col in Base.metadata.tables["students"].columns:
        if col.name not in cols:
            default = " default now()" if col.name in ("created_at", "updated_at") else ""
            c.execute(text(f'alter table students add column "{col.name}" {col.type.compile(engine.dialect)}{default}'))
Base.metadata.create_all(engine, tables=[t for n, t in Base.metadata.tables.items() if n != "students"], checkfirst=True)
with engine.begin() as c:   # the Student ID counter, as the authentication migration seeds it (a fresh database: nothing issued yet)
    c.execute(text("insert into student_id_allocator (name, last_value) select 'STU', 0 where not exists (select 1 from student_id_allocator)"))
EOF
)
for s in identity organization academics enrollment paper exam operations; do
  (cd "$ROOT/institute-backend/institute-$s-service" && PYTHONPATH=".:$ROOT/institute-backend/institute-common" DATABASE_URL="$SA/$DB" CORS_ORIGINS=http://x alembic upgrade head >/dev/null)
done
echo "disposable database ready (Student chains + Institute chains of this revision)"

declare -A PORTS=([student-authentication-service]=18102 [student-enrollment-service]=18104 [student-test-service]=18105
                  [student-attempt-service]=18106 [student-result-service]=18107 [student-notification-service]=18108)
for svc in "${!PORTS[@]}"; do
  (cd "$ROOT/student-backend/$svc" && DATABASE_URL="$SA/$DB" REDIS_URL="redis://127.0.0.1:$RPORT" CORS_ORIGINS=http://localhost \
     SESSION_COOKIE_SECURE=false APP_ENV=dev EMAIL_BACKEND=console FRONTEND_RESET_URL=http://localhost/reset-password \
     INSTITUTE_SERVICE_API_KEYS="$KEY" NOTIFICATION_CREATE_API_KEYS="$KEY" \
     exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "${PORTS[$svc]}" >"$WORK/$svc.log" 2>&1) &
  PIDS+=($!)
done
for svc in "${!PORTS[@]}"; do
  for _ in $(seq 1 40); do curl -s -o /dev/null "http://127.0.0.1:${PORTS[$svc]}/health" && break; sleep 0.5; done
done
echo "student services up: ${!PORTS[*]}"

# driver: pipeline_disposable.py (default) or another driver in tests/e2e, e.g. create_test_disposable.py
PIPELINE_DB="$SA/$DB" PIPELINE_KEY="$KEY" PIPELINE_AUTH_LOG="$WORK/student-authentication-service.log" "$PY" "$ROOT/tests/e2e/${1:-pipeline_disposable.py}"
