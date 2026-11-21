# Institute Portal: operations, security runbook and open decisions

> The Institute backend now runs as seven services (see [MICROSERVICES.md](MICROSERVICES.md)): configuration, migrations, workers and the audit log
> are per service. The remaining blockers below still apply, and the service split adds one: **the services have not been run in your environment**
> (images, compose networking, keys), only in a sandbox.

## Configuration (environment, never committed)

Each service documents its variables in its `.env.example`; real `.env` files are git-ignored and
`scripts/check-no-secrets.sh` (run in CI) fails on tracked env files and common secret patterns.

`institute-backend` validates settings at start-up. In `APP_ENV=qa|prod` it refuses to start when cookies are not
`Secure`, when CORS origins are not `https`, or when `EMAIL_BACKEND=console`. A wildcard CORS origin is always rejected.

| Variable | Purpose |
|---|---|
| `DATABASE_URL` / `MIGRATION_DATABASE_URL` | runtime role vs. separate, more privileged migration role |
| `REDIS_URL` | shared Redis (Institute keys are `inst_` prefixed) |
| `CORS_ORIGINS`, `FRONTEND_BASE_URL` | explicit Institute UI origin(s) and the base for email links |
| `SESSION_COOKIE_SECURE`, `SESSION_COOKIE_SAMESITE` | `strict` works only when the UI and API are same-site (see blockers) |
| `STUDENT_QUESTION_TYPES` (exam service) | question types exams may deliver: `MCQ_SINGLE` (default). Add `MCQ_MULTI,NUMERICAL` only after the Student test, attempt, result and ranking services with MSQ / NAT support (and their migrations g1 / h1 / i1) are deployed; generation refuses a paper with a type not listed |
| `ARGON2_*`, `HIBP_ENABLED` | password hashing cost and optional breached-password lookup |
| `EMAIL_BACKEND` (`console` dev only, `smtp`), `SMTP_*`, `EMAIL_FROM` | provider-agnostic email |
| `STUDENT_SERVICE_URL/API_KEY`, `STUDENT_TEST_SERVICE_URL/API_KEY`, `STUDENT_RESULT_SERVICE_URL/API_KEY`, `STUDENT_ATTEMPT_SERVICE_URL/API_KEY`, `STUDENT_NOTIFICATION_SERVICE_URL/API_KEY` | outbound service keys, one per receiving service (the test-service pair is also used for answer-key corrections, the result-service pair for result corrections) |
| `INSTITUTE_SERVICE_API_KEYS` (set on each *student* service: authentication, test, result, attempt, notification) | inbound keys accepted by `/internal/v1`; empty disables the API (404) |
| `FILE_STORE_DIR`, `MAX_UPLOAD_BYTES`, `MAX_PAPER_QUESTIONS` | private upload storage and limits |
| `OBJECT_STORE` (`local` default, `s3`), `OBJECT_STORE_DIR`, `OBJECT_STORE_BUCKET`, `OBJECT_STORE_REGION`, `OBJECT_STORE_ENDPOINT_URL`, `OBJECT_STORE_PREFIX`, `OBJECT_STORE_SIGNED_URL_SECONDS` | object storage for question pictures (`institute_common/objectstore.py`); set on the **paper and exam services and the exam worker** (the worker delivers pictures to the Student test service). Credentials are not settings: see below |

### Object storage: connecting the real S3 bucket (only after approval)

Development and tests use `OBJECT_STORE=local` (a private directory) or moto's in-memory fake; nothing talks to AWS until these
steps are done. Existing pictures stay in the database (`storage='db'`); only new object-stored pictures use the bucket.

1. **Bucket**: create ONE bucket in the chosen region (e.g. `ap-south-1`), *Block all public access* ON, *Object Ownership* "Bucket owner
   enforced", default encryption SSE-S3 (the free default). Versioning optional. No bucket policy granting public reads, no website hosting.
2. **Lifecycle** (keeps storage free of leftovers): expire objects under `staging/` after 7 days (used by bulk-import previews, Phase D).
3. **Access**: an IAM user (or, better, a role for the server) with exactly this policy - nothing account-wide:

       {"Version": "2012-10-17", "Statement": [
         {"Effect": "Allow", "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
          "Resource": "arn:aws:s3:::<bucket>/<prefix>*"},
         {"Effect": "Allow", "Action": "s3:ListBucket", "Resource": "arn:aws:s3:::<bucket>",
          "Condition": {"StringLike": {"s3:prefix": "<prefix>*"}}}]}

   `ListBucket` is what lets a missing object answer "not found" instead of "access denied".
4. **Credentials**: give the paper service, exam service and exam worker `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` through the
   deployment's secret store (or an instance/task role, which needs no keys). Never in `.env.example`, compose files or the repository.
5. **Settings** on the same three services: `OBJECT_STORE=s3`, `OBJECT_STORE_BUCKET=<bucket>`, `OBJECT_STORE_REGION=<region>`,
   `OBJECT_STORE_PREFIX=prod/` (or `qa/`), optionally `OBJECT_STORE_SIGNED_URL_SECONDS` (default 300).
6. **Check**: open a question picture as staff: the API answers `307` to a signed URL that stops working after its lifetime; the same URL
   without the signature returns `AccessDenied`.
7. **Free tier**: S3's free tier is 5 GB-month of storage and limited requests for 12 months; beyond it storage and requests are billed.
   Watch the bucket size; set an AWS Budget alert before going live.

Cloudflare R2 later: create an R2 bucket and an API token scoped to it, set `OBJECT_STORE_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com`,
`OBJECT_STORE_REGION=auto` and the token as `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`. No code changes. Objects already in S3 must be
copied first (the database records `storage='s3'` plus the key; a different store reports them as unavailable rather than guessing).

### Local development on plain http://localhost (session cookie)

Chrome refuses the `__Host-` session cookie on plain `http://localhost`. On a local development stack set
`SESSION_COOKIE_LOCAL_HTTP=true` (with `APP_ENV=dev`): browsers whose `Origin` is `http://localhost`, `127.0.0.1` or `[::1]` then get the
same Secure, HttpOnly, SameSite session cookie without the prefix (`inst_sid`); every other request, e.g. https through a tunnel, keeps
`__Host-inst_sid`. The setting is refused when `APP_ENV` is `qa` or `prod`. Every Institute service reads the session, so all of them need
the setting (recreate them after changing it); signing in again afterwards is expected.

### Service-key rotation (no downtime)

1. Generate a new key (`openssl rand -base64 48`) and put `OLD,NEW` in the receiving service's `INSTITUTE_SERVICE_API_KEYS`; redeploy it.
2. Switch the Institute backend's `*_API_KEY` to `NEW`; redeploy it and the worker.
3. Remove `OLD` from the receiving service; redeploy. Each service pair uses its own key.

## Migrations and deployment order

The Institute chain assumes the enrollment chain (`providers`, `branches`, `batches`) already exists in the target database.

1. Back up the database and rehearse on a **copy or staging database** first. Do not apply to production before this.
2. `alembic upgrade head` with `MIGRATION_DATABASE_URL` (version table `m10_alembic_version`). Revisions: `0001` staff/audit,
   `0002` academics, `0003` enrollment history, `0004` papers, `0005` exams/notifications, `0006` operations,
   `0007` student-notification outbox + assignment sync columns, `0008` result corrections.
3. Apply the **Student result service's** new migration `b2d4e1a9c7f3` (creates `result_history` and its append-only trigger; additive) with that
   service's own tooling, then deploy the Student services that gained `/internal/v1` (optional setting, off until a key is set), then
   `institute-backend`, `institute-worker`, and finally the frontend. Without `result_history`, the correction endpoints of the result service fail
   (nothing else in the Student Portal reads it).
4. Create the first Super Admin with the CLI (see README).

Rollback: every revision has a tested `downgrade()` (SQLite). On PostgreSQL, restore from the pre-migration backup if a
downgrade would drop data you need. The history triggers (including `result_corrections_immutable`) are dropped by their revisions' downgrades.

Database roles (to be created by whoever owns the database): a *runtime* role with `SELECT/INSERT/UPDATE` on Institute tables and no DDL,
no `UPDATE/DELETE` on append-only history tables, `SELECT/INSERT/UPDATE` on the mapped enrollment tables it writes, and read-only on
`students`; a separate *migration* role for DDL. No superuser for the application.

## Background work

**Bulk question import worker** (`institute-bulk-import-worker`, image `institute-paper-service`, `python -m inst_paper.bulk_import_worker loop`):
reads, validates and imports uploaded Excel/Word question files. The database is its queue (no Redis); several workers may run; a crashed
worker's job is resumed after its lease (`BULK_IMPORT_LEASE_SECONDS`, default 300 s). It needs the same `DATABASE_URL` and object-store
settings as the paper service. Unconfirmed previews and their files are deleted after `BULK_IMPORT_PREVIEW_DAYS` (7). Run it with a memory
cap (compose: `mem_limit: 1g`). Tuning: `BULK_IMPORT_BATCH_SIZE`, `BULK_IMPORT_MAX_ROWS`, `BULK_IMPORT_MAX_BYTES`, `BULK_IMPORT_MAX_ACTIVE_JOBS`.

Three import targets (`docs/institute-portal/THREE_SCOPE_QUESTION_BANK_PLAN.md`): **GLOBAL** (platform bank, platform administrators;
pictures become platform assets under `question-images/platform/<sha256>`), **provider-wide** (the provider's Provider Admin; `branch_id`
NULL) and **branch** (unchanged). Every provider question is shared with all branches of its provider. `BULK_IMPORT_MAX_ACTIVE_JOBS`
counts per provider, and separately for the platform. Deploying this needs paper migration `0014_three_scope_question_bank` (relaxing
only: the old code runs on it, so migrate first, then roll out paper service, bulk worker, gateway and frontend). Before rolling the
code back, finish or cancel GLOBAL and provider-wide imports; the `0014` downgrade refuses while provider-wide questions, platform
pictures or such imports exist.

`institute-worker` runs `python -m app.worker run-once` every 60 seconds: it generates plans whose `generate_at` has passed, delivers
the exam outbox, delivers queued student notifications, syncs assignment status from the attempt service and queues one "result available" notice per student.
All steps are idempotent, so overlapping or repeated runs are safe. Watch the dashboard counters "failed deliveries", "failed generation runs",
"failed student notifications" and "unfinished result corrections", and the `EXAM_PUBLISH_FAILED` / `EXAM_GENERATION_FAILED` / `MISSING_PAPER` /
`STUDENT_NOTIFICATION_FAILED` notifications. An unfinished correction is completed by repeating the same request with the same `Idempotency-Key`.

## Incident response

* **Exposed credential** (key, DB password, SMTP secret): rotate it immediately (above), revoke the old value, search the audit log and
  provider logs for use since exposure, and record the incident. For a leaked Student/Institute service key also review `/internal/v1` access logs.
* **Suspected staff-account compromise**: a Super Admin deactivates the identity (`POST /staff/{id}/deactivate`), which kills the session
  at once; then reset the password through the verified-email flow and review `audit-logs?actor=<staff_id>`.
* **Failed deployment**: roll the containers back to the previous image; migrations are additive, so older code keeps working against newer schema.
* Backups: use the managed database's encrypted automatic backups plus a pre-migration snapshot, and **test a restore periodically**; recovery
  objectives are not defined yet (depends on the hosting plan).

## Security notes and known limits

* Email OTP is a weaker second factor than an authenticator app or WebAuthn.
* The rate limiter keys on the TCP peer address. Behind a reverse proxy run uvicorn with trusted `--forwarded-allow-ips` (or add proxy-header
  handling) so each client is limited separately.
* The shared Redis in `docker-compose.yml` has no password and publishes port 6379 (pre-existing). Set a password and do not publish the port outside development.
* Older Student-service tests (`student-authentication-service/tests/test_password_reset.py`) use the configured `DATABASE_URL`; do not run them
  with live credentials. The new internal-API tests use in-memory SQLite only.
* Uploads: CSV and one-sheet `.xlsx` are parsed in-process with the standard library (size, part-count, expansion and row limits, no formulas/macros/entities).
  That is reasonable for a 2 MB staff-only upload, but a sandboxed worker would be stronger. A PNG/JPEG paper is refused: OCR was removed on 2026-11-16,
  and question/option pictures are stored and shown exactly as uploaded, never read. `.xls` is not supported.
* Result corrections are Super Admin only. Re-evaluation deliberately skips results a person adjusted by hand (reported as `skipped_manual`); to change those, adjust them again.
  A correction changes only `test_results` (plus `result_history`) through the result service.
* The existing Student notification route `POST /notifications` (not the internal one) predates this work and is **unauthenticated by default**: anyone who can reach
  that service can create notifications for any student. Nothing in this repository calls it. It now accepts an **opt-in** `NOTIFICATION_CREATE_API_KEYS`
  (comma-separated; `X-Service-Key` is then required, 401 otherwise). It is opt-in only because the default has to keep the existing contract for callers outside this repository;
  **set it in every deployed environment**.

## Verification performed (this branch)

| Check | Result |
|---|---|
| `institute-backend` pytest, SQLite + fake Redis | 207 passed, 2 skipped (PostgreSQL-only trigger tests); `ruff` clean |
| Same suite on a **disposable PostgreSQL 16** (real Student chains, then Institute chain incl. `0007`/`0008`, triggers/FKs/INET/TIMESTAMPTZ) | 205 passed (4 SQLite-only migration tests deselected), including the append-only trigger tests for `result_corrections` and the audit log |
| `alembic check` on that PostgreSQL (ORM vs migrated schema) | no differences |
| Result re-evaluation vs production evaluator on real PostgreSQL tables (`student-result-service/tests/test_corrections_pg.py`) | 1 passed: re-evaluated results are identical to a fresh `evaluate_attempt` with the corrected key, replay changes nothing |
| Student services, each in a clean venv from **its own** `requirements.txt` | authentication 12 (the 4 existing `test_auth.py` tests with a real Redis + 8 internal), test 21, result 17, attempt 2, notification 10: all passed |
| Route tables (OpenAPI incl. request/response schemas) of the 5 modified Student services vs the original commit | only new `/internal/v1/...` operations added; every pre-existing operation identical |
| Migration up/down on a disposable PostgreSQL with data (4 students, 24 results/attempts, 6 history rows): result-service `b2d4e1a9c7f3` and Institute `0007`/`0008` downgraded then upgraded again | Student tables' column set, row counts and a hash of every `test_results` row identical before, during and after; only the new tables/columns (and the 6 history rows, by design) come and go; `alembic check` clean after |
| Student-facing result query and ranking SQL after a correction (PostgreSQL) | corrected marks visible to both |
| Round trip on PostgreSQL (earlier run): Institute outbox payload -> real Student test service endpoint -> the **existing** Student queries | only the assigned students see the exam (UTC and Asia/Kolkata session zones), unassigned student denied, no answer key in the start payload |
| `institute-frontend`: type-check, lint, 16 vitest tests, production build, `npm audit` | clean, 0 vulnerabilities |
| Pipeline guards (`scripts/check_workflow_triggers.py`, mutation-checked) and secret scan over tracked **and untracked** files | pass; Student Pages deployment is path-filtered, no CI file deploys, uses a secret or a non-local database |
| CI workflows (`institute-backend-ci`, `institute-frontend-ci`, `student-services-ci`) | written and YAML-validated; **never executed on GitHub** (the push to GitHub was refused, so no Actions run exists). Every command in them was run locally in an equivalent environment |
| Real-stack browser run (real Redis, uvicorn, built UI, headless Chromium, SQLite), scripted earlier, before the corrections/notification UI | 18/18 checks (two weak, see below); **not re-run** for the newer screens |
| Docker image builds, `docker compose up` | **not run: no Docker daemon in the environment** |
| `test_password_reset.py` (existing Student test) | **not run against a real DB by design**: it needs a seeded database and uses `DATABASE_URL`; it fails identically before and after my changes on an empty SQLite |
| `.xlsx` with Microsoft Excel / LibreOffice-produced files | tested with `openpyxl`-generated workbooks and hand-built hostile variants; no Excel-produced file was available. Shared-string (Excel-style) encoding is covered by a re-encoding test |
| OCR | **no real engine exists to test**; the all-or-nothing behaviour is tested with a test double only |

Defects found and fixed during final verification (all invisible to the SQLite-only suite): `exam_versions` append-only trigger blocked publishing a
scheduled exam; audit/INET writes failed for non-IP peer addresses; the Institute `questions` table collided with the Student-owned `questions` table
(renamed `bank_questions`, with a guard test); exam times assumed Asia/Kolkata instead of the database session zone; `alembic revision --autogenerate`
would have proposed dropping the enrollment service's `m3_alembic_version`; the new authentication-service route needed `email-validator`, which that
service does not install (removed); the CI jobs for the test/result services lacked `pytest`/`httpx`.

## Blockers and open decisions

Verified facts about the existing Student migrations (found by replaying them on a disposable PostgreSQL):

* The authentication chain had two root revisions that both create `students`, and `562cc1f6c4b7` declared `student_id` as UUID with a
  foreign key to the real `VARCHAR(20)` column, so it could never apply: `subodh_dev` therefore had **no `password_setup_reset_tokens`
  table** and forgot/reset-password could not work. Both migrations are now schema-aware and type-correct and were applied (2026-10-16).
* Schema-only dumps of both Neon branches were restored into a disposable PostgreSQL 18; every migration added here was replayed there,
  followed by `alembic check` (no drift) and the full test suites against that schema, before being applied to the Neon branches.

**Production blockers** (need information or access that is not in the repository; none can be closed from code alone):

1. **Production databases.** Everything runs against the Neon *test* branches `subodh_dev` (Student) and `institute_portal_test`
   (Institute). A production rollout needs its own branches/roles (runtime vs migration role), the same migration order, a backup, and
   the reference projection seeded once (the enrollment service pushes everything on its first pass).
2. **Email delivery.** SMTP is generic and untested against a real provider; no provider, sending domain, SPF/DKIM or bounce handling is configured. OTP, staff invitations
   and password resets cannot work in `qa`/`prod` until this exists (the app refuses to start with `EMAIL_BACKEND=console` there).
3. **Domain and cookie configuration.** `SameSite=Strict` host-only cookies require the Institute UI and API to be same-site (for example `portal.example.com` and
   `api.example.com`); otherwise a reverse proxy on the UI origin is needed. HTTPS, `Secure` cookies, exact CORS origins and `FRONTEND_BASE_URL` depend on domains that are not decided.
   The rate limiter also needs trusted proxy headers behind a reverse proxy (see security notes).
4. **Object storage.** Uploads use local disk behind an interface (`app/services/filestore.py`); a private bucket with short-lived signed URLs, encryption and retention is not wired,
   and container disks are ephemeral.
5. **Student verified-email limitation.** The `students` table has no verified-email flag, so "account recovery only via a verified email" cannot be enforced for students; the
   Institute side can only create/link students and cannot prove their email is verified.
6. **Student notification service `POST /notifications` is open unless `NOTIFICATION_CREATE_API_KEYS` is set** (pre-existing; the guard is opt-in to keep the contract). Set it in every environment.
7. **Result consumers.** The ranking service reads `test_results` live (checked in the PostgreSQL test: a correction moves the ranking at once, and the student's own result query shows the corrected marks). The performance service is an empty stub with no code that reads results. Anything outside this repository that copies results is unknown.

Other limitations that are not production blockers but should be known:

* **No OCR or picture recognition** (product decision, 2026-11-16). Papers are typed text plus original pictures; pictures are never read.
* `.xlsx` support is limited to one plain sheet with the CSV columns (no formulas, merged/hidden cells, macros). Question images/assets and topic tagging are not built.
* The CI workflows have not run on GitHub (see verification table); the first run may need small environment fixes.
* Docker images and `docker compose up` were never built or started here.

Not implemented (independent work remaining):

* Composite-test sharing is per paper to another provider; there is no approval handshake or per-version share.
* Staff email-change flow, operator MFA reset (use deactivate/reactivate plus a new password), authenticator-app MFA, protection against deactivating the last Super Admin
  (the CLI refuses an existing email, so recovery would need a database operation).
* A CI browser end-to-end suite, load testing, analytics beyond per-exam results and the dashboards.
* Re-evaluation of results a person adjusted by hand (skipped by design); bulk or multi-exam corrections.
* `SERVICE_API_KEYS` (inbound keys for this backend) is configured but no inbound endpoint uses it yet.
* Two of the 18 scripted browser checks are weak (the Origin-spoof check cannot work from a browser, and the Provider Admin navigation check cannot distinguish roles); origin rejection
  and role gating are covered by the API/unit tests instead.
