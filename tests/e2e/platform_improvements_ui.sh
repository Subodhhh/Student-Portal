#!/usr/bin/env bash
# Batches, paper downloads, All Branches and role isolation in a REAL browser on DISPOSABLE infrastructure: throwaway PostgreSQL and Redis,
# the real service images behind the real gateway and frontend nginx, Chromium in the Playwright container. Nothing touches a configured
# database. Needs the images built (docker compose build) and tests/e2e/.venv with playwright==1.48. Everything is removed on exit.
set -euo pipefail
cd "$(dirname "$0")/../.."
N=pi-e2e; NET=$N-net; WORK=${WORK:-$(mktemp -d)}; KEY=$(openssl rand -hex 16)
SERVICES=(identity organization academics enrollment paper exam operations)
cleanup() { docker rm -f $(docker ps -aq --filter "name=$N-") >/dev/null 2>&1 || true; docker network rm $NET >/dev/null 2>&1 || true; }
trap cleanup EXIT; cleanup
docker network create $NET >/dev/null
docker run -d --name $N-pg --network $NET --tmpfs /var/lib/postgresql -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=inst postgres:18 >/dev/null
docker run -d --name $N-redis --network $NET redis:7-alpine >/dev/null
until docker exec $N-pg pg_isready -U postgres -d inst >/dev/null 2>&1; do sleep 1; done; sleep 2
docker exec -i $N-pg psql -U postgres -d inst -q -v ON_ERROR_STOP=1 < tests/e2e/student_chain_schema.sql

common=(-e APP_ENV=dev -e DATABASE_URL=postgresql+psycopg://postgres@$N-pg/inst -e CORS_ORIGINS=http://localhost -e FRONTEND_BASE_URL=http://localhost
        -e SESSION_COOKIE_SECURE=false -e EMAIL_BACKEND=console -e REDIS_URL=redis://$N-redis:6379
        -e ARGON2_TIME_COST=1 -e ARGON2_MEMORY_COST_KIB=1024 -e ARGON2_PARALLELISM=1 -e AUDIT_FORWARD_INTERVAL_SECONDS=1 -e REFERENCE_SYNC_INTERVAL_SECONDS=0
        -e IDENTITY_SERVICE_URL=http://$N-identity:8000 -e IDENTITY_SERVICE_API_KEY=$KEY -e ACADEMICS_SERVICE_URL=http://$N-academics:8000 -e ACADEMICS_SERVICE_API_KEY=$KEY
        -e OPERATIONS_SERVICE_URL=http://$N-operations:8000 -e OPERATIONS_SERVICE_API_KEY=$KEY -e PAPER_SERVICE_URL=http://$N-paper:8000 -e PAPER_SERVICE_API_KEY=$KEY
        -e ORGANIZATION_SERVICE_URL=http://$N-organization:8000 -e ENROLLMENT_SERVICE_URL=http://$N-enrollment:8000 -e EXAM_SERVICE_URL=http://$N-exam:8000)
for s in "${SERVICES[@]}"; do docker run --rm --network $NET "${common[@]}" institute-$s-service:latest alembic upgrade head >/dev/null 2>&1; echo "migrated $s"; done
for s in "${SERVICES[@]}"; do docker run -d --name $N-$s --memory 220m --network $NET "${common[@]}" -e INTERNAL_API_KEYS=$KEY institute-$s-service:latest >/dev/null; done
for s in "${SERVICES[@]}"; do for i in $(seq 1 60); do docker exec $N-$s python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/ready', timeout=2)" >/dev/null 2>&1 && break; sleep 1; done; done

# As on localhost: the portal on http://localhost, the gateway on http://localhost:8010. The gateway and the browser share the frontend's
# network namespace so both are "localhost" to Chromium (one site, like :8081 and :8010).
docker build -q --build-arg VITE_INSTITUTE_API_URL=http://localhost:8010 -t $N-frontend-image institute-frontend >/dev/null
docker run -d --name $N-frontend --network $NET -p 127.0.0.1:18081:80 -p 127.0.0.1:13013:13013 $N-frontend-image >/dev/null
sed -e "s/institute-\([a-z]*\)-service:8000/$N-\1:8000/" -e "s/listen 80;/listen 8010;/" institute-backend/institute-gateway/nginx.conf > "$WORK/gateway.conf"
docker run -d --name $N-gateway --network container:$N-frontend -v "$WORK/gateway.conf:/etc/nginx/conf.d/default.conf:ro" nginx:alpine >/dev/null
docker run -d --name $N-pw --network container:$N-frontend -v "$PWD/tests/e2e/.venv/lib/python3.12/site-packages:/pwpkgs:ro" -e PYTHONPATH=/pwpkgs \
  mcr.microsoft.com/playwright/python:v1.48.0-noble python3 -m playwright run-server --port 13013 --host 0.0.0.0 >/dev/null
for i in $(seq 1 90); do curl -s -o /dev/null -m 2 http://127.0.0.1:13013/ && break; sleep 1; done

docker exec -i -e PYTHONPATH=/app $N-identity python - < tests/e2e/platform_improvements_seed.py > "$WORK/seed.json"
SEED="$WORK/seed.json" SHOTS="$WORK/shots" IDENTITY=$N-identity tests/e2e/.venv/bin/python tests/e2e/platform_improvements_browser.py
