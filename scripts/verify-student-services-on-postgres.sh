#!/usr/bin/env bash
# Runs every Student service's tests the way its Docker image would run them, against a DISPOSABLE local PostgreSQL:
#   * each service gets its own virtualenv from its own requirements.txt (+ pytest httpx fakeredis, as CI installs), so a test that
#     checks the image's dependency set (e.g. "no email-validator") sees exactly that set;
#   * the throwaway database gets the live `students` table: the authentication chain cannot build it on an empty database (its two
#     roots create `students` with UUID ids, the live table has VARCHAR(20) ids, and `date_of_birth` exists in no migration), so the
#     table is created as live has it, the chain's root branch is stamped, and the rest of every chain is applied normally;
#   * non-database tests run on SQLite (as CI does), *_pg.py tests on the throwaway PostgreSQL.
# Never touches a shared database: the container is local, started here and removed on exit.
# Usage: scripts/verify-student-services-on-postgres.sh [service ...]     (venvs cached in $STUDENT_TEST_VENVS)
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
VENVS=${STUDENT_TEST_VENVS:-$HOME/.cache/student-service-test-venvs}
PORT=${STUDENT_TEST_PG_PORT:-55436}
NAME=student-services-verify-pg
SERVICES=("$@")
[ ${#SERVICES[@]} -eq 0 ] && SERVICES=(student-authentication-service student-enrollment-service student-test-service student-attempt-service
                                       student-result-service student-notification-service student-ranking-service student-profile-service)
CHAINS=(student-authentication-service student-enrollment-service student-test-service student-attempt-service student-result-service
        student-notification-service)

docker run -d --rm --name "$NAME" -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=stu_verify -p "127.0.0.1:$PORT:5432" postgres:16 >/dev/null
trap 'docker stop "$NAME" >/dev/null 2>&1 || true' EXIT
BASE=postgresql+psycopg://postgres@127.0.0.1:$PORT
URL=$BASE/stu_verify
for _ in $(seq 1 60); do docker exec "$NAME" psql -U postgres -d stu_verify -qtc "select 1" >/dev/null 2>&1 && break; sleep 1; done

venv() {   # the service's own dependency set
  local svc=$1 dir=$VENVS/$1
  if [ ! -x "$dir/bin/python" ] || [ "$ROOT/student-backend/$svc/requirements.txt" -nt "$dir/.built" ]; then
    rm -rf "$dir" && python3 -m venv "$dir"
    "$dir/bin/pip" install -q -r "$ROOT/student-backend/$svc/requirements.txt" pytest httpx fakeredis
    touch "$dir/.built"
  fi
  echo "$dir/bin/python"
}

export REDIS_URL=redis://127.0.0.1:1/0 CORS_ORIGINS=http://localhost SESSION_COOKIE_SECURE=false RESEND_API_KEY= EMAIL_FROM= \
       FRONTEND_RESET_URL=http://localhost/reset

# The live `students` table (read from the live catalogue: columns, types, defaults, keys), which no migration chain reproduces.
docker exec -i "$NAME" psql -U postgres -d stu_verify -q -v ON_ERROR_STOP=1 <<'SQL'
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE TYPE student_status AS ENUM ('ACTIVE');
CREATE TABLE students (
    student_id varchar(20) PRIMARY KEY DEFAULT gen_random_uuid(),
    first_name varchar(255) NOT NULL, last_name varchar(255) NOT NULL, email varchar(255) NOT NULL UNIQUE, phone varchar(20),
    status student_status NOT NULL,
    created_ip_address inet, created_latitude numeric, created_longitude numeric,
    updated_ip_address inet, updated_latitude numeric, updated_longitude numeric,
    deleted_at timestamp, deleted_ip_address inet, deleted_latitude numeric, deleted_longitude numeric,
    created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    password_hash varchar(255), date_of_birth date);
SQL
for svc in "${CHAINS[@]}"; do
  py=$(venv "$svc")
  (cd "$ROOT/student-backend/$svc" && export DATABASE_URL=$URL PYTHONPATH=. &&
   if [ "$svc" = student-authentication-service ]; then "$py" -m alembic stamp 9ffccd3f23f5 >/dev/null 2>&1; fi &&
   "$py" -m alembic upgrade head >/dev/null 2>&1) || { echo "MIGRATION FAILED: $svc"; exit 1; }
  echo "migrated $svc"
done

status=0
for svc in "${SERVICES[@]}"; do
  py=$(venv "$svc")
  cd "$ROOT/student-backend/$svc"
  plain=$(ls tests/test_*.py 2>/dev/null | grep -v '_pg\.py$' || true)
  pg=$(ls tests/test_*_pg.py 2>/dev/null || true)
  for group in plain pg; do
    files=${!group}
    [ -z "$files" ] && continue
    if [ $group = plain ]; then db=sqlite://; else db=$URL; fi
    # shellcheck disable=SC2086
    line=$(DATABASE_URL=$db TEST_DATABASE_URL=$URL STUDENT_AUTH_TEST_PG=$BASE/postgres PYTHONPATH=. \
           "$py" -m pytest -p no:cacheprovider -q -rs $files 2>&1 | tee "/tmp/$svc-$group.log" | tail -1) || status=1
    echo "$svc [$group]: $line"
  done
done
exit $status
