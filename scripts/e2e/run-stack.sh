#!/usr/bin/env bash
# Boots the REAL images (no mocks): PostgreSQL + Redis + the seven Institute services + gateway, applies each service's migrations,
# creates a Super Admin and runs the black-box flow through the gateway. Needs a Docker daemon and the images built:
#   docker compose build institute-identity-service institute-organization-service institute-academics-service \
#       institute-enrollment-service institute-paper-service institute-exam-service institute-operations-service institute-gateway
# Everything is local and throwaway; no credentials other than random values generated here.
set -euo pipefail
cd "$(dirname "$0")/../.."
N=inst-e2e; NET=$N-net; PW="Correct-Horse-Battery-9"; KEY_ID=$(openssl rand -hex 16); KEY_AC=$(openssl rand -hex 16); KEY_OP=$(openssl rand -hex 16); KEY_PA=$(openssl rand -hex 16)
PG_IMAGE=${PG_IMAGE:-postgres:16}; REDIS_IMAGE=${REDIS_IMAGE:-redis:7-alpine}
cleanup() { docker rm -f $(docker ps -aq --filter "name=$N-") >/dev/null 2>&1 || true; docker network rm $NET >/dev/null 2>&1 || true; }
trap cleanup EXIT; cleanup
docker network create $NET >/dev/null
docker run -d --name $N-pg --network $NET -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=inst $PG_IMAGE >/dev/null
docker run -d --name $N-redis --network $NET $REDIS_IMAGE >/dev/null
until docker exec $N-pg pg_isready -U postgres -d inst >/dev/null 2>&1; do sleep 1; done; sleep 2
docker exec -i $N-pg psql -U postgres -d inst -q -v ON_ERROR_STOP=1 < scripts/e2e/student_chain_schema.sql

DB="postgresql+psycopg://postgres@$N-pg/inst"
common=(-e APP_ENV=dev -e DATABASE_URL=$DB -e CORS_ORIGINS=http://localhost:8081 -e SESSION_COOKIE_SECURE=false -e EMAIL_BACKEND=console
        -e REDIS_URL=redis://$N-redis:6379 -e AUDIT_FORWARD_INTERVAL_SECONDS=1
        -e IDENTITY_SERVICE_URL=http://$N-identity:8000 -e IDENTITY_SERVICE_API_KEY=$KEY_ID
        -e ACADEMICS_SERVICE_URL=http://$N-academics:8000 -e ACADEMICS_SERVICE_API_KEY=$KEY_AC
        -e OPERATIONS_SERVICE_URL=http://$N-operations:8000 -e OPERATIONS_SERVICE_API_KEY=$KEY_OP
        -e PAPER_SERVICE_URL=http://$N-paper:8000 -e PAPER_SERVICE_API_KEY=$KEY_PA
        -e ORGANIZATION_SERVICE_URL=http://$N-organization:8000 -e ENROLLMENT_SERVICE_URL=http://$N-enrollment:8000 -e EXAM_SERVICE_URL=http://$N-exam:8000
        -e ARGON2_TIME_COST=1 -e ARGON2_MEMORY_COST_KIB=1024 -e ARGON2_PARALLELISM=1)
declare -A INTERNAL=([identity]=$KEY_ID [academics]=$KEY_AC [operations]=$KEY_OP [paper]=$KEY_PA)
for s in identity organization academics enrollment paper exam operations; do
  docker run --rm --network $NET "${common[@]}" institute-$s-service:latest alembic upgrade head >/dev/null
  echo "migrated $s"
done
for s in identity organization academics enrollment paper exam operations; do
  docker run -d --name $N-$s --network $NET "${common[@]}" -e INTERNAL_API_KEYS="${INTERNAL[$s]:-}" institute-$s-service:latest >/dev/null
done
sed "s/institute-\([a-z]*\)-service:8000/$N-\1:8000/" backend/institute-gateway/nginx.conf > /tmp/$N-nginx.conf
docker run -d --name $N-gateway --network $NET -p 18010:80 -v /tmp/$N-nginx.conf:/etc/nginx/conf.d/default.conf:ro nginx:alpine >/dev/null
for s in identity organization academics enrollment paper exam operations; do
  for i in $(seq 1 40); do docker exec $N-$s python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/ready', timeout=2)" >/dev/null 2>&1 && break; sleep 1; done
  docker exec $N-$s python -c "import urllib.request as u; print('$s ready', u.urlopen('http://127.0.0.1:8000/ready', timeout=2).status)"
done
docker exec -e INSTITUTE_ADMIN_PASSWORD=$PW $N-identity python -m inst_identity.cli create-super-admin e2e-admin@example.com "E2E Admin"
python3 scripts/e2e/institute_e2e.py http://127.0.0.1:18010 $N-identity STF000001 "$PW"
