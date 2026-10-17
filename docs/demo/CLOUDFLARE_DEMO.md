# Temporary development demo on Cloudflare (no custom domain)

Both portals on Cloudflare Pages, the existing local backend behind one Cloudflare Quick Tunnel, email through Resend,
the existing Neon branches unchanged (Student: `subodh_dev`, Institute: `institute_portal_test`).
Edge and tunnel runbook: [deploy/tunnel/README.md](../../deploy/tunnel/README.md).

> Status: code and instructions only. Nothing here has been deployed, no Cloudflare/GitHub/Resend resource exists yet,
> and email delivery has not been tested. Replace every `<student-project>` / `<institute-project>` placeholder with
> the real Pages project names once they are created.

## How it works (and why)

* Each frontend calls **its own Pages origin**: Student `https://<student-project>.pages.dev/api/...`, Institute
  `https://<institute-project>.pages.dev/api/v1/...`. A Pages Function forwards those calls to the tunnel.
  The session cookies (`session_id`, `__Host-inst_sid`) are therefore first-party, same-origin cookies: no third-party
  cookie, no CORS preflight, and the existing `SameSite` settings (Student `lax`, Institute `strict`) keep working in
  every browser, Safari included.
* The tunnel URL changes whenever `cloudflared` restarts. It lives in Workers KV (`edge_origin`), so a restart needs one
  `deploy/tunnel/set-tunnel-url.sh` call, never a rebuild.
* The Functions accept only a `https://*.trycloudflare.com` target (a tampered KV value cannot create an open proxy),
  refuse cross-origin state-changing requests, never follow redirects, cap bodies (1 MB Student, 3 MB Institute) and
  time out (30 s / 60 s).
* `portal-edge` (nginx, no host port) accepts only requests carrying the shared `EDGE_KEY` and forwards only an
  explicit route/method allowlist; internal APIs, docs, OpenAPI and health routes are never reachable.
* Client IPs: the Function sends Cloudflare's `CF-Connecting-IP` as `X-Client-IP`; the edge turns it into
  `X-Forwarded-For`. The Student services trust that header only from the edge's reserved address `10.231.47.2`
  (`FORWARDED_ALLOW_IPS`), the Institute gateway only from the same address (`set_real_ip_from`), and the Institute
  services only from inside the Docker network (they publish no port; the gateway overwrites the header). Login rate
  limits and the Institute audit log therefore see each user's real address, and nobody can spoof it.

## 1. Cloudflare (manual, free plan)

1. Create a Cloudflare account. Note the **Account ID** (dashboard → Workers & Pages → right-hand column).
2. **Workers KV**: create a namespace (e.g. `portal-demo-tunnel`); note its **namespace ID**.
3. **Two Pages projects, Direct Upload** (Workers & Pages → Create → Pages → "Upload assets"; or
   `wrangler pages project create <name> --production-branch main`). Suggested names: `<student-project>`,
   `<institute-project>`. The production URL is `https://<name>.pages.dev`; if a name is taken Cloudflare adds a suffix,
   so note the URL it actually shows. Use the same production branch name for both (default `main`).
4. In **each** project → Settings:
   * Bindings → add **KV namespace**, variable name `TUNNEL_KV`, the namespace from step 2 (Production environment).
   * Variables and Secrets → add **secret** `EDGE_KEY` = the value from `deploy/tunnel/edge.env` (Production).
   Bindings and secrets apply to deployments made after they are set: configure them before the first deploy.
5. **API tokens** (My Profile → API Tokens → Create custom token), each scoped to this account only:
   * `github-pages-deploy`: Account → **Cloudflare Pages: Edit**. Goes into GitHub (below).
   * `local-tunnel-kv`: Account → **Workers KV Storage: Edit**. Stays on your machine for `set-tunnel-url.sh`.
   Give both an expiry date after the demo. Never paste a token into code, chat, issues or a committed file.

Only the production deployments are used. Preview deployments (`<hash>.<project>.pages.dev`) are a different origin:
the Institute's Origin check refuses them and cookies are not shared, so do not hand out preview URLs.

## 2. GitHub (manual)

Workflow: [.github/workflows/cloudflare-pages-demo.yml](../../.github/workflows/cloudflare-pages-demo.yml).

**Branch policy:** everything stays on `feature/*` branches. Nothing is pushed or merged to `main`, `QA` or `dev`, and no
pull request targets them. (A manual "Run workflow" would need the file on the default branch, so it is not used.)

* Pushes to any `feature/*` branch and pull requests only **build and validate** (lint, tests, build, bundle check,
  Functions compile, proxy tests). They never deploy and never see a Cloudflare secret.
* A deployment happens only for a push to **`feature/cloudflare-demo-release`**, a branch that only ever points at a
  commit already reviewed on `feature/institute-portal` (pushing the same commit creates no new commits). The deploy job
  runs in the protected `cloudflare-demo` environment and **waits for your approval**.

Settings:
* Repository → Settings → Secrets and variables → Actions → **Variables**:
  `STUDENT_PAGES_URL=https://<student-project>.pages.dev`, `INSTITUTE_PAGES_URL=https://<institute-project>.pages.dev`,
  `STUDENT_PAGES_PROJECT=<student-project>`, `INSTITUTE_PAGES_PROJECT=<institute-project>`,
  optional `PAGES_PRODUCTION_BRANCH` (default `main`). `PAGES_PRODUCTION_BRANCH` is the Cloudflare Pages project's
  production-branch label, not a GitHub branch. These are public values; they end up in the bundles.
* Settings → Environments → **New environment `cloudflare-demo`**:
  * Required reviewers: yourself (every deployment waits for this approval).
  * Deployment branches and tags: "Selected branches and tags" → add the branch rule `feature/cloudflare-demo-release`
    only. No other branch can then deploy or read the environment's secrets, even with an edited workflow.
  * Untick "Allow administrators to bypass configured protection rules".
  * Environment secrets: `CLOUDFLARE_API_TOKEN` (the `github-pages-deploy` token), `CLOUDFLARE_ACCOUNT_ID`.
    Use environment secrets, not repository secrets, so only the approved deploy job can read them.

Deploy (from `~/Student-Portal-Review/Student-Portal`, after the commit is on GitHub's `feature/institute-portal`):
```bash
git push <github-remote> feature/institute-portal:refs/heads/feature/cloudflare-demo-release
```
Then Actions → "Cloudflare Pages demo" → the run for `feature/cloudflare-demo-release` → review → approve the
`cloudflare-demo` deployment. Later deployments: push the same refspec again (a fast-forward). Never force-push the
release branch without explicit approval; to go back, use "Rollback to this deployment" in the Cloudflare Pages project.

The old GitHub Pages workflow (`deploy-pages.yml`) is disabled (no push trigger, jobs `if: false`); it is kept for
history. `scripts/check_workflow_triggers.py` validates both workflows in CI.

## 3. Local backend settings (git-ignored files; never commit or paste their contents)

Edit with an editor; do not print the files. Afterwards run `docker compose up -d` (recreates the affected services).

`deploy/student/student.env`
| Variable | Value |
|---|---|
| `CORS_ORIGINS` | keep the local entries, add `https://<student-project>.pages.dev` (the proxy is same-origin, this only matters for direct calls) |
| `SESSION_COOKIE_SECURE` | `true` (the Pages origin is https) |
| `FRONTEND_RESET_URL` | `https://<student-project>.pages.dev/reset-password` |
| `EMAIL_BACKEND`, `RESEND_API_KEY`, `EMAIL_FROM` | see Resend below |
| `DATABASE_URL` | unchanged (`subodh_dev`) |

`deploy/institute/institute.env`
| Variable | Value |
|---|---|
| `CORS_ORIGINS` | keep `http://localhost:8081`, add `https://<institute-project>.pages.dev` (**required**: the write-request Origin check uses this list) |
| `SESSION_COOKIE_SECURE` | `true` (cookie becomes `__Host-inst_sid`) |
| `SESSION_COOKIE_SAMESITE` | keep `strict` |
| `FRONTEND_BASE_URL` | `https://<institute-project>.pages.dev` (verification and reset links) |
| `EMAIL_BACKEND`, `EMAIL_FROM`, `SMTP_*` | see Resend below |
| `APP_ENV` | keep `dev` |
| `DATABASE_URL` | unchanged (`institute_portal_test`) |

`deploy/tunnel/edge.env`: `EDGE_KEY` (see the runbook).

After switching `SESSION_COOKIE_SECURE=true`, re-check local sign-in on http://localhost:8080 and :8081 once: browsers
generally accept Secure cookies on `http://localhost`, but this was not verified for the `__Host-` cookie.

## 4. Resend (manual)

Without a verified domain, Resend is in **testing mode**: the sender must be `onboarding@resend.dev` and mail is
delivered **only to the email address of your Resend account**. Every other recipient is rejected. Verifying a domain
removes the restriction; we have none.

Consequences in this codebase:
* Institute sign-in codes (Super Admin, Admin, Provider Admin, Branch Admin) are sent synchronously; if SMTP rejects
  the recipient, the sign-in request fails with a server error. Teachers need no code.
* Student "Forgot password" also fails with a server error for any recipient other than your own address.

Setup:
1. Create a Resend account; create an API key with **Sending access** only. Store it only in the two env files.
2. Check eligibility: Resend → Domains (no verified domain = testing mode) and note your account email.
3. Student (`deploy/student/student.env`): `EMAIL_BACKEND=resend`, `RESEND_API_KEY=<key>`,
   `EMAIL_FROM=onboarding@resend.dev`.
4. Institute (`deploy/institute/institute.env`, SMTP is already supported): `EMAIL_BACKEND=smtp`,
   `SMTP_HOST=smtp.resend.com`, `SMTP_PORT=587` (STARTTLS), `SMTP_USERNAME=resend`, `SMTP_PASSWORD=<key>`,
   `EMAIL_FROM=onboarding@resend.dev`.
5. `docker compose up -d`, then test delivery to your own address only, before relying on it:
   * Student: on the local or Pages Student portal use "Forgot password" for a demo student whose email is your
     Resend account address; the link must arrive and work.
   * Institute: sign in as a demo Provider Admin whose email is your Resend account address; the code must arrive.
   * Resend → Emails shows each attempt and any rejection reason.

## 5. Optional: Cloudflare Access for the two Pages hostnames

Cloudflare Zero Trust has a free plan (up to 50 users). Signing up may ask for a payment method even on the free plan;
check this in your account before relying on it. Steps (verify the options in your dashboard; they change over time):
1. Zero Trust → Settings → Authentication: enable **One-time PIN** (email codes).
2. Access → Applications → Add → **Self-hosted**: domain `<student-project>.pages.dev`; a second application (or a
   second domain in the same application) for `<institute-project>.pages.dev`.
3. Policy: Action **Allow**, Include → **Emails** → the invited reviewers' addresses. Session duration e.g. 24 h.
4. Test in a private window: the Access login appears, the portal and its `/api` calls work afterwards.

Access covers `/api/*` too; because the calls are same-origin, the Access cookie travels with them and no application
change is needed. The application does not depend on Access: without it, the portals rely on their own login.

## 6. Demo accounts (only after the infrastructure is approved; not created yet)

* Create them while the backend still uses the console email backend, so activation and password links appear in
  `docker logs` and nobody depends on Resend: Institute staff via the Super Admin UI ("Staff"), activation link from
  `docker logs institute-identity-service`; students via "Students & enrollments", password via "Forgot password" with
  the link from `docker logs student-authentication-service`. Then switch to Resend.
* Use dedicated records, clearly named (e.g. provider "Demo Institute", branch "Demo Branch", batch "Demo Batch"):
  * one **Teacher** (no sign-in code; safest role to share),
  * optionally one **Provider Admin** whose email is your Resend address (codes reach only you; relay them),
  * one or two **students** enrolled in the demo batch, plus one published demo exam.
* Never share `STF000003`, your own accounts or QA accounts. Record the IDs you create for later clean-up.
* Use strong, unique passwords and change or deactivate the accounts after the demo.

## 7. End-to-end checks after the first deploy (manual)

1. `https://<student-project>.pages.dev` and `https://<institute-project>.pages.dev` load; a deep link (e.g. `/tests`,
   `/providers`) survives a refresh.
2. Sign in on both; the browser shows the session cookie for the `pages.dev` host; data loads.
3. Sign out: the cookie disappears; `/api/.../auth/me` answers 401.
4. From a phone on mobile data and from Safari: repeat 1–3.
5. Restart `portal-tunnel` (`demo-down.sh && demo-up.sh`), run `set-tunnel-url.sh`, wait a minute: the portals work again.
6. `https://<student-project>.pages.dev/api/internal/v1/students`, `/api/docs`, `/api/openapi.json`, `/api/health`
   answer 404.

## 8. Stopping and clean-up

* Pause: `deploy/tunnel/demo-down.sh` (the Pages sites stay up but cannot reach any backend).
* End of the demo: also delete or expire both API tokens, rotate `EDGE_KEY`, revoke the Resend key, deactivate the demo
  accounts, and optionally delete the Pages projects and the KV namespace. Revert the env files to the local values.
* The Neon branches, QA records and uploaded papers are never touched by any of these steps.

## Risks and limits

* The demo depends on your machine, Docker and the Quick Tunnel (no uptime guarantee, ~200 concurrent requests).
* Resend testing mode: only your own inbox receives mail (see above).
* Anyone with the Pages URL reaches the login pages unless Access is configured.
* The Institute services trust `X-Forwarded-For` from any peer inside the Docker network (they are unpublished and the
  gateway overwrites the header); a compromised container on that network could spoof addresses in their logs.
