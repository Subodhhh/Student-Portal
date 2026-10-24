#!/usr/bin/env bash
# Stop the public demo path: remove ONLY the two demo containers. The portals' own stack, databases, Redis, uploaded
# papers and the student-portal-edge network are left untouched. Once the tunnel is gone the Pages Functions answer 503/502.
# Optional: also clear the KV value so a stale tunnel URL is never reused:
#   CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... TUNNEL_KV_NAMESPACE_ID=... deploy/tunnel/demo-down.sh --clear-kv
set -euo pipefail
WRANGLER_VERSION=4.145.0

for name in portal-tunnel portal-edge; do
  if docker container inspect "$name" >/dev/null 2>&1; then
    docker rm -f "$name" >/dev/null && echo "removed $name"
  fi
done

if [ "${1:-}" = "--clear-kv" ]; then
  for v in CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID TUNNEL_KV_NAMESPACE_ID; do
    [ -n "${!v:-}" ] || { echo "$v is not set" >&2; exit 1; }
  done
  export CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID
  export WRANGLER_SEND_METRICS=false
  npx --yes "wrangler@${WRANGLER_VERSION}" kv key delete edge_origin --namespace-id "$TUNNEL_KV_NAMESPACE_ID" --remote >/dev/null
  echo "edge_origin cleared"
fi
