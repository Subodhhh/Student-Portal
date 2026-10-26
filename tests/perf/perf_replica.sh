#!/usr/bin/env bash
# Institute Portal page-load measurement on a DISPOSABLE REPLICA (never a shared database):
#   * a throwaway PostgreSQL 18 (tmpfs) restored from a verified backup (institute.dump + student.dump);
#   * every database connection goes through toxiproxy, which adds the round-trip latency measured from the live containers to Neon
#     (default 100 ms each way = ~200 ms per query), so query counts cost what they cost in the local deployment;
#   * the six Student services run as processes; the seven Institute services run in-process behind the in-process gateway, serving a
#     production build of the Institute portal; Chromium (Playwright container) navigates the portal and times every page.
# A disposable password is set for the measured staff accounts IN THE REPLICA ONLY. Everything is removed on exit.
#
#   PW_PYTHON=<python with playwright==1.48> tests/perf/perf_replica.sh <backup-dir> <label> [STAFF_CODE ...]
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"; PY="$ROOT/.venv/bin/python"; export PATH="$ROOT/.venv/bin:$PATH"
BACKUP="$1"; LABEL="$2"; shift 2; STAFF=("${@:-STF000001}")
LAT="${PERF_LATENCY_MS:-100}"; PGPORT=55437; RPORT=56382; NET=perf-net
KEY="perf-$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')"
WORK="$(mktemp -d)"; PIDS=()
cleanup() {
  for p in "${PIDS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null || true; done
  docker rm -f perf-pg perf-toxi perf-redis perf-pw >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  [ "${KEEP_LOGS:-}" ] && echo "logs kept in $WORK" || rm -rf "$WORK"
}
trap cleanup EXIT

docker network create "$NET" >/dev/null
docker run -d --rm --name perf-pg --network "$NET" -e POSTGRES_HOST_AUTH_METHOD=trust --tmpfs /var/lib/postgresql postgres:18 >/dev/null
docker run -d --rm --name perf-toxi --network "$NET" -p 127.0.0.1:$PGPORT:15432 -p 127.0.0.1:58474:8474 ghcr.io/shopify/toxiproxy:2.9.0 >/dev/null
docker run -d --rm --name perf-redis -p 127.0.0.1:$RPORT:6379 redis:7-alpine >/dev/null
for _ in $(seq 1 60); do docker exec perf-pg pg_isready -q && break; sleep 1; done

for db in institute student; do
  docker cp "$BACKUP/$db.dump" perf-pg:/tmp/$db.dump
  docker exec perf-pg createdb -U postgres "${db}_perf_verify"
  docker exec perf-pg pg_restore -U postgres --no-owner --no-acl -d "${db}_perf_verify" /tmp/$db.dump
done
echo "replica restored from ${BACKUP/#$HOME/~}"

for _ in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:58474/version && break; sleep 0.5; done
curl -s -X POST http://127.0.0.1:58474/proxies -d '{"name":"pg","listen":"0.0.0.0:15432","upstream":"perf-pg:5432"}' >/dev/null
for dir in upstream downstream; do
  curl -s -X POST http://127.0.0.1:58474/proxies/pg/toxics -d "{\"name\":\"lat_$dir\",\"type\":\"latency\",\"stream\":\"$dir\",\"attributes\":{\"latency\":$LAT}}" >/dev/null
done
SA="postgresql+psycopg://postgres@127.0.0.1:$PGPORT"

declare -A PORTS=([student-authentication-service]=18202 [student-enrollment-service]=18204 [student-test-service]=18205
                  [student-attempt-service]=18206 [student-result-service]=18207 [student-notification-service]=18208)
for svc in "${!PORTS[@]}"; do
  (cd "$ROOT/student-backend/$svc" && DATABASE_URL="$SA/student_perf_verify" REDIS_URL="redis://127.0.0.1:$RPORT" CORS_ORIGINS=http://localhost \
     SESSION_COOKIE_SECURE=false APP_ENV=dev EMAIL_BACKEND=console FRONTEND_RESET_URL=http://localhost/reset-password \
     INSTITUTE_SERVICE_API_KEYS="$KEY" NOTIFICATION_CREATE_API_KEYS="$KEY" \
     exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "${PORTS[$svc]}" >"$WORK/$svc.log" 2>&1) &
  PIDS+=($!)
done
for svc in "${!PORTS[@]}"; do
  for _ in $(seq 1 60); do curl -s -o /dev/null "http://127.0.0.1:${PORTS[$svc]}/health" && break; sleep 0.5; done
done
echo "student services up (through the latency proxy: ${LAT} ms each way)"

PERF_DB="$SA/institute_perf_verify" PERF_KEY="$KEY" \
  PERF_WORK="$WORK" PERF_LABEL="$LABEL" PERF_STAFF="${STAFF[*]}" "$PY" "$ROOT/tests/perf/institute_perf_bench.py"
