
#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

TUNNEL_DIR="$ROOT/deploy/tunnel"
CREDENTIALS="$TUNNEL_DIR/cloudflare-kv.env"

echo "=== Starting Student and Institute Portal demo ==="

# Load Cloudflare KV credentials without displaying them.
if [[ ! -f "$CREDENTIALS" ]]; then
    echo "ERROR: Missing $CREDENTIALS" >&2
    exit 1
fi

if [[ "$(stat -c %a "$CREDENTIALS")" != "600" ]]; then
    echo "ERROR: Credentials file must have permission 600." >&2
    echo "Run: chmod 600 deploy/tunnel/cloudflare-kv.env" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "$CREDENTIALS"
set +a

for name in CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID TUNNEL_KV_NAMESPACE_ID; do
    if [[ -z "${!name:-}" ]]; then
        echo "ERROR: $name is missing from the credentials environment." >&2
        exit 1
    fi
done

# Remove the previous demo edge/tunnel if they exist.
if docker container inspect portal-edge >/dev/null 2>&1 ||
   docker container inspect portal-tunnel >/dev/null 2>&1; then
    echo "Stopping previous demo tunnel..."
    "$TUNNEL_DIR/demo-down.sh"
fi

# Start the application services and create the edge network.
echo "Starting Docker Compose services..."
docker compose up -d

# Start the edge and a fresh Quick Tunnel.
echo "Starting Cloudflare Quick Tunnel..."
"$TUNNEL_DIR/demo-up.sh"

# Extract the actual URL from the running tunnel logs.
echo "Waiting for the tunnel URL..."
url=""

for _ in $(seq 1 15); do
    url="$(
        docker logs portal-tunnel 2>&1 |
        grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' |
        grep -v '^https://api\.' |
        tail -1 || true
    )"

    if [[ -n "$url" ]]; then
        break
    fi

    sleep 2
done

if [[ -z "$url" ]]; then
    echo "ERROR: Could not extract the Quick Tunnel URL." >&2
    echo "Check: docker logs portal-tunnel" >&2
    exit 1
fi

# Update the shared Cloudflare Workers KV value used by both portals.
echo "Updating Cloudflare KV with the new tunnel URL..."
"$TUNNEL_DIR/set-tunnel-url.sh" "$url"

echo
echo "=== Demo startup complete ==="
echo "Student Portal:  https://student-portal-demo.pages.dev"
echo "Institute Portal: https://institute-portal-demo.pages.dev"
echo "Backend tunnel URL has been updated."
echo "Allow up to 60 seconds for the Pages Functions to pick up the new URL."
