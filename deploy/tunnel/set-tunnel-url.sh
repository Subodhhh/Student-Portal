#!/usr/bin/env bash
# Write the current Quick Tunnel URL to Workers KV (key "edge_origin"), which both Pages Functions read.
# No frontend rebuild or redeploy is needed; the Functions pick the new value up within about a minute.
#
#   CLOUDFLARE_API_TOKEN=...  CLOUDFLARE_ACCOUNT_ID=...  TUNNEL_KV_NAMESPACE_ID=...  deploy/tunnel/set-tunnel-url.sh [URL]
#
# Without URL the newest one is read from `docker logs portal-tunnel`. The token needs only
# "Account > Workers KV Storage > Edit"; it is read from the environment and never printed.
set -euo pipefail
WRANGLER_VERSION=4.145.0

url="${1:-}"
if [ -z "$url" ]; then
  url=$(docker logs portal-tunnel 2>&1 | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | grep -v '^https://api\.' | tail -1 || true)
fi
url="${url%/}"
if ! printf '%s' "$url" | grep -Eq '^https://[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.trycloudflare\.com$'; then
  echo "not a Quick Tunnel URL: '${url}'" >&2; exit 1
fi
# cloudflared's own registration endpoint is never a tunnel (it appears in cloudflared error messages).
[ "$url" != "https://api.trycloudflare.com" ] || { echo "refusing api.trycloudflare.com: the tunnel did not start" >&2; exit 1; }
for v in CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID TUNNEL_KV_NAMESPACE_ID; do
  [ -n "${!v:-}" ] || { echo "$v is not set" >&2; exit 1; }
done

# wrangler runs as a child process: export the credentials explicitly (a non-exported shell variable would make it fall
# back to an interactive browser login) and skip the first-run telemetry prompt.
export CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID
export WRANGLER_SEND_METRICS=false
npx --yes "wrangler@${WRANGLER_VERSION}" kv key put edge_origin "$url" \
  --namespace-id "$TUNNEL_KV_NAMESPACE_ID" --remote >/dev/null
echo "edge_origin updated to $url (Functions use it within about 60 s; check https://<pages host>/api... in a browser)"
