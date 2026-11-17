#!/usr/bin/env bash
# The portal on THIS machine only, with its own PostgreSQL - never Neon, never the live containers (see docker-compose.local.yml).
#
#   scripts/local-stack.sh up       build images from this working tree, start, migrate, create the demo logins (first run), print URLs
#   scripts/local-stack.sh status   containers and health
#   scripts/local-stack.sh logins   where the local logins are
#   scripts/local-stack.sh otp      the latest sign-in code (the local email backend prints mail to the identity service's log)
#   scripts/local-stack.sh down     stop (the local database is kept: docker volume portal-local_portal-local-pgdata)
#
# Settings: deploy/local/*.local.env (git-ignored), created on first use with a random local database password; the service keys
# are taken from the repository's .env (the same keys both ends of each contract already use). Nothing here is printed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PY:-$ROOT/.venv/bin/python}"
DC=(docker compose -p portal-local -f docker-compose.yml -f docker-compose.local.yml)
SERVICES=(postgres redis student-authentication-service student-profile-service student-enrollment-service student-test-service
          student-attempt-service student-result-service student-ranking-service student-notification-service
          institute-identity-service institute-organization-service institute-academics-service institute-enrollment-service
          institute-paper-service institute-exam-service institute-exam-worker institute-operations-service institute-gateway
          institute-portal-frontend student-portal-frontend)

init() {
  mkdir -p deploy/local
  "$PY" - <<'PY'
import re, secrets
from pathlib import Path
local = Path("deploy/local")
keys = {}
if Path(".env").is_file():
    for line in Path(".env").read_text().splitlines():
        if (m := re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*)", line)):
            keys[m.group(1)] = m.group(2).strip().strip('"').strip("'")
pg = local / "postgres.local.env"
if not pg.exists():
    pg.write_text(f"POSTGRES_USER=portal\nPOSTGRES_PASSWORD={secrets.token_urlsafe(24)}\nPOSTGRES_DB=portal_local\n")
pw = re.search(r"POSTGRES_PASSWORD=(.*)", pg.read_text()).group(1)
url = f"postgresql+psycopg://portal:{pw}@postgres:5432/portal_local"            # the local container, inside this project only
inst = local / "institute.local.env"
if not inst.exists():
    inst.write_text("\n".join([
        "APP_ENV=dev", f"DATABASE_URL={url}", f"MIGRATION_DATABASE_URL={url}",
        "CORS_ORIGINS=http://localhost:18081", "FRONTEND_BASE_URL=http://localhost:18081",
        "SESSION_COOKIE_SECURE=false", "SESSION_COOKIE_SAMESITE=strict", "EMAIL_BACKEND=console", "EMAIL_FROM=no-reply@example.invalid",
        "STUDENT_SERVICE_URL=http://student-authentication-service:8000", f"STUDENT_SERVICE_API_KEY={keys.get('STUDENT_AUTH_SERVICE_KEY', '')}",
        "STUDENT_TEST_SERVICE_URL=http://student-test-service:8001", f"STUDENT_TEST_SERVICE_API_KEY={keys.get('STUDENT_TEST_SERVICE_KEY', '')}",
        "STUDENT_RESULT_SERVICE_URL=http://student-result-service:8001", f"STUDENT_RESULT_SERVICE_API_KEY={keys.get('STUDENT_RESULT_SERVICE_KEY', '')}",
        "STUDENT_ATTEMPT_SERVICE_URL=http://student-attempt-service:8001", f"STUDENT_ATTEMPT_SERVICE_API_KEY={keys.get('STUDENT_ATTEMPT_SERVICE_KEY', '')}",
        "STUDENT_NOTIFICATION_SERVICE_URL=http://student-notification-service:8001",
        f"STUDENT_NOTIFICATION_SERVICE_API_KEY={keys.get('STUDENT_NOTIFICATION_SERVICE_KEY', '')}",
        "RECOGNITION_SUBJECT_CATEGORIES={}", ""]))
stu = local / "student.local.env"
if not stu.exists():
    stu.write_text("\n".join([f"DATABASE_URL={url}", "CORS_ORIGINS=http://localhost:18080", "SESSION_COOKIE_SECURE=false", "APP_ENV=dev",
                              "EMAIL_BACKEND=console", "RESEND_API_KEY=", "EMAIL_FROM=no-reply@example.invalid",
                              "FRONTEND_RESET_URL=http://localhost:18080/reset-password", ""]))
for f in (pg, inst, stu):
    assert "neon" not in f.read_text().lower(), f"{f} must not point at Neon"
PY
}

host_url() {   # the same local database, from this machine (127.0.0.1:55460), for the migrations
  "$PY" -c "import re;p=re.search(r'POSTGRES_PASSWORD=(.*)',open('deploy/local/postgres.local.env').read()).group(1);print(f'postgresql+psycopg://portal:{p}@127.0.0.1:55460/portal_local')"
}

migrate() {   # every service's own chain, in the order the PostgreSQL verification uses (scripts/verify-institute-services-on-postgres.sh)
  local url; url="$(host_url)"
  for svc in student-authentication-service student-enrollment-service student-test-service student-attempt-service student-result-service student-notification-service; do
    (cd "student-backend/$svc" && PYTHONPATH=. DATABASE_URL="$url" REDIS_URL=redis://x CORS_ORIGINS=http://x SESSION_COOKIE_SECURE=false \
        FRONTEND_RESET_URL=http://localhost "$PY" -m alembic upgrade head >/dev/null)
    echo "  migrated $svc"
  done
  for s in identity organization academics enrollment paper exam operations; do
    (cd "institute-backend/institute-$s-service" && PYTHONPATH=".:$ROOT/institute-backend/institute-common" DATABASE_URL="$url" MIGRATION_DATABASE_URL="$url" \
        CORS_ORIGINS=http://x "$PY" -m alembic upgrade head >/dev/null)
    echo "  migrated institute-$s-service"
  done
}

wait_url() {
  for _ in $(seq 1 120); do curl -fsS -o /dev/null "$1" && return 0; sleep 2; done
  echo "timed out waiting for $1" >&2; return 1
}

case "${1:-up}" in
  up)
    init
    "${DC[@]}" up -d postgres
    for _ in $(seq 1 60); do [ "$(docker inspect -f '{{.State.Health.Status}}' local-portal-postgres 2>/dev/null)" = healthy ] && break; sleep 2; done
    echo "migrating the local database"; migrate
    "${DC[@]}" up -d --build "${SERVICES[@]}"
    wait_url http://127.0.0.1:18010/health
    wait_url http://127.0.0.1:18081/login
    if [ ! -f deploy/local/logins.local.env ]; then
      echo "creating the demo logins and subjects"
      "$PY" scripts/local_seed.py
      "${DC[@]}" up -d --force-recreate institute-paper-service      # the new subject categories (RECOGNITION_SUBJECT_CATEGORIES)
      wait_url http://127.0.0.1:18010/health
    fi
    echo "Institute Portal: http://localhost:18081   (gateway http://localhost:18010)   logins: deploy/local/logins.local.env"
    echo "Student Portal:   http://localhost:18080   (student services on 127.0.0.1:18002-18009)"
    ;;
  config) init; "${DC[@]}" config "${SERVICES[@]}" ;;            # the resolved configuration (contains the local secrets: do not share)
  status) "${DC[@]}" ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}' ;;
  logins) echo "$ROOT/deploy/local/logins.local.env" ;;
  otp) docker logs local-institute-identity-service 2>&1 | grep -o "code is [0-9]\{6\}" | tail -1 ;;
  down) "${DC[@]}" stop ;;
  *) sed -n 2,10p "$0"; exit 2 ;;
esac
