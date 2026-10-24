#!/usr/bin/env bash
# Start the demo backend edge (nginx) and ONE Cloudflare Quick Tunnel in front of it. See deploy/tunnel/README.md.
#   * Neither container publishes a host port; both join only the student-portal-edge network (docker-compose.yml).
#   * portal-edge gets the reserved address the backends trust for X-Forwarded-For (10.231.47.2).
#   * The edge key comes from deploy/tunnel/edge.env (git-ignored) and is never printed.
# Prints the tunnel URL (not a secret, but keep it private) and how to publish it to Workers KV.
#   --edge-only   start only portal-edge (no tunnel, nothing reachable from the Internet), e.g. for verify-edge.sh
set -euo pipefail
cd "$(dirname "$0")"

NETWORK=student-portal-edge
EDGE_IP=10.231.47.2
NGINX_IMAGE=nginx:1.28.2-alpine
CLOUDFLARED_IMAGE=cloudflare/cloudflared:2026.9.3

docker network inspect "$NETWORK" >/dev/null 2>&1 || {
  echo "network $NETWORK not found: run 'docker compose up -d' in the repository root first" >&2; exit 1; }
[ -f edge.env ] || { echo "deploy/tunnel/edge.env is missing: copy edge.env.example and set EDGE_KEY" >&2; exit 1; }
[ "$(stat -c %a edge.env)" = 600 ] || { echo "deploy/tunnel/edge.env must be chmod 600" >&2; exit 1; }
key=$(grep -m1 '^EDGE_KEY=' edge.env | cut -d= -f2-)
if ! printf '%s' "$key" | grep -Eq '^[A-Za-z0-9_-]{32,128}$'; then
  echo "EDGE_KEY must be 32-128 characters of [A-Za-z0-9_-] (e.g. openssl rand -hex 32)" >&2; exit 1
fi
unset key
for name in portal-edge portal-tunnel; do
  if docker container inspect "$name" >/dev/null 2>&1; then echo "$name already exists: run demo-down.sh first" >&2; exit 1; fi
done

docker run -d --name portal-edge --restart unless-stopped \
  --network "$NETWORK" --ip "$EDGE_IP" \
  --env-file edge.env -e NGINX_ENVSUBST_FILTER='^EDGE_KEY$' \
  -v "$PWD/edge.conf.template:/etc/nginx/templates/default.conf.template:ro" \
  --read-only --tmpfs /var/cache/nginx --tmpfs /var/run --tmpfs /run --tmpfs /etc/nginx/conf.d \
  --security-opt no-new-privileges --cap-drop ALL --cap-add CHOWN --cap-add SETUID --cap-add SETGID --cap-add NET_BIND_SERVICE \
  "$NGINX_IMAGE" >/dev/null

sleep 2
docker exec portal-edge nginx -t >/dev/null 2>&1 || { echo "portal-edge configuration is invalid:" >&2; docker logs portal-edge 2>&1 | tail -5 >&2; exit 1; }
if [ "${1:-}" = "--edge-only" ]; then echo "portal-edge is running (no tunnel). Verify with deploy/tunnel/verify-edge.sh"; exit 0; fi

docker run -d --name portal-tunnel --restart unless-stopped \
  --network "$NETWORK" \
  --security-opt no-new-privileges --cap-drop ALL \
  "$CLOUDFLARED_IMAGE" tunnel --no-autoupdate --url http://portal-edge:80 >/dev/null

url=""
for _ in $(seq 1 45); do
  url=$(docker logs portal-tunnel 2>&1 | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | grep -v '^https://api\.' | tail -1 || true)
  [ -n "$url" ] && break
  sleep 2
done
[ -n "$url" ] || { echo "no Quick Tunnel URL after 90 s; check: docker logs portal-tunnel" >&2; exit 1; }
echo "Quick Tunnel: $url"
echo "Publish it to both Pages projects (Workers KV):  deploy/tunnel/set-tunnel-url.sh"
