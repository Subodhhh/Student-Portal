# Demo backend edge and Quick Tunnel

Runbook for the temporary development demo. The overall setup (Cloudflare Pages, GitHub, Resend, Access, demo accounts)
is in [docs/demo/CLOUDFLARE_DEMO.md](../../docs/demo/CLOUDFLARE_DEMO.md).

```
browser ─► https://<student>.pages.dev/api/*      ─┐ Pages Function: tunnel URL from Workers KV, adds X-Edge-Key and
browser ─► https://<institute>.pages.dev/api/v1/* ─┤ X-Client-IP (from CF-Connecting-IP), same-origin + path checks
                                                   ▼
                     https://<random>.trycloudflare.com   (portal-tunnel: cloudflared, ONE Quick Tunnel)
                                                   ▼
          portal-edge (nginx, 10.231.47.2 on student-portal-edge, no host port)
            X-Edge-Key required, explicit route + method allowlist, sets X-Forwarded-For itself
            /student/...           -> the owning Student service
            /institute/api/v1/...  -> institute-gateway (its own generated allowlist)
                                                   ▼
          existing services -> Neon subodh_dev (Student) / institute_portal_test (Institute), unchanged
```

| File | Purpose |
|---|---|
| `edge.conf.template` | nginx config (only `${EDGE_KEY}` is substituted at start-up) |
| `edge.env.example` | template for the git-ignored `edge.env` holding `EDGE_KEY` |
| `demo-up.sh` | starts `portal-edge` (and, without `--edge-only`, `portal-tunnel`) |
| `set-tunnel-url.sh` | writes the current tunnel URL to Workers KV (`edge_origin`) |
| `demo-down.sh` | removes only the two demo containers (`--clear-kv` also clears the KV value) |
| `verify-edge.sh` | local checks of the edge, the route allowlist and client-IP handling (no tunnel needed) |
| `test_pages_functions.mjs` | offline tests of both Pages Functions and the edge route table (`node --test`) |

## What the edge exposes

Only these Student routes (taken from `student-backend/*/app/routes`), with exactly these methods:

| Methods | Paths (under `/student`) | Service |
|---|---|---|
| POST | `/auth/login`, `/auth/logout`, `/auth/forgot-password`, `/auth/reset-password` | authentication |
| GET | `/auth/me` | authentication |
| GET, PUT | `/profile` | profile |
| GET | `/enrollments` | enrollment |
| GET | `/tests`, `/tests/missed`, `/tests/scheduled`, `/tests/{id}`, `/tests/{id}/details`, `/tests/{id}/start` | test |
| GET | `/attempts/completed`, `/attempts/test/{id}`, `/attempts/{id}`, `/attempts/{id}/{answers,summary,workspace}` | attempt |
| POST | `/attempts/start`, `/attempts/{id}/{answers,submit}`, `/attempts/{id}/questions/{q}/{clear,mark-review,visit}` | attempt |
| GET | `/results`, `/results/attempts/{id}`, `/results/attempts/{id}/details` | result |
| POST | `/results/attempts/{id}/evaluate` | result |
| GET | `/ranking/{id}` | ranking |
| GET | `/notifications`, `/notifications/unread-count` | notification |
| PATCH | `/notifications/read-all`, `/notifications/{id}/read` | notification |

Institute: `GET/POST/PUT/PATCH/DELETE /institute/api/v1/...` → `institute-gateway`, which routes only the
generated `/api/v1` table and answers 404 for anything else.

Never exposed: `/internal/*` (service-to-service), `/docs`, `/openapi.json`, `/health`, `/ready`,
`POST /tests/{id}/audiences` and `POST /notifications` (service-key routes), Redis, databases, any host port.
Bodies: 1 MB for Student routes, 3 MB for Institute routes (paper uploads: 2 MB file limit in the paper service).

## One-time preparation

1. Apply the compose changes (creates the `student-portal-edge` network, attaches the Student services and the
   gateway, sets `FORWARDED_ALLOW_IPS`); the gateway image is rebuilt for its `set_real_ip_from` line:
   ```bash
   docker compose build institute-gateway && docker compose up -d
   ```
2. Create the edge key (never commit, never paste it into chat or tickets):
   ```bash
   cp deploy/tunnel/edge.env.example deploy/tunnel/edge.env && chmod 600 deploy/tunnel/edge.env
   sed -i "s/^EDGE_KEY=.*/EDGE_KEY=$(openssl rand -hex 32)/" deploy/tunnel/edge.env
   ```
   Store the same value as the `EDGE_KEY` secret of both Pages projects (copy it straight from the file into the
   Cloudflare dashboard).
3. Verify locally, with nothing reachable from the Internet:
   ```bash
   deploy/tunnel/demo-up.sh --edge-only
   deploy/tunnel/verify-edge.sh
   deploy/tunnel/demo-down.sh
   ```

## Start the demo

```bash
deploy/tunnel/demo-up.sh                        # prints https://<random>.trycloudflare.com
CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... TUNNEL_KV_NAMESPACE_ID=... deploy/tunnel/set-tunnel-url.sh
```

Set the three variables in your shell without echoing them (e.g. `read -rs CLOUDFLARE_API_TOKEN; export CLOUDFLARE_API_TOKEN`).
Workers KV is eventually consistent: allow up to about a minute before the Functions use a new URL.

## After a restart (laptop, Docker or cloudflared)

A Quick Tunnel gets a new random URL every time `portal-tunnel` starts. Re-run `set-tunnel-url.sh`; no frontend
rebuild or redeploy is needed. If only the services restarted, nothing has to be done.

## Stop the demo

```bash
deploy/tunnel/demo-down.sh            # or: demo-down.sh --clear-kv (with the three variables set)
```

This removes only `portal-tunnel` and `portal-edge`. The local stack keeps running on http://localhost:8080 and
http://localhost:8081; databases, Redis, uploaded papers and QA data are untouched. The Pages sites stay online but
answer "The demo backend is not available right now" for every API call.

## Limits

* Quick Tunnels have no uptime guarantee and are rate-limited (about 200 concurrent requests); the machine must stay on.
* The edge key compare is a plain string match; keep the key long and random and rotate it after the demo
  (new `edge.env` value + both Pages secrets, then `demo-down.sh && demo-up.sh`).
