# Institute Portal

A separate staff-facing application in this monorepo, alongside the Student Portal. It has its own
frontend (`institute-frontend/`) and one backend (`backend/institute-backend/`). Student identities,
exam delivery, attempts and evaluation stay in the existing Student services.

> **The backend is now split into seven services behind a gateway: see [MICROSERVICES.md](MICROSERVICES.md).** The sections below describe the
> behaviour (unchanged) and were written for the monolith, which is kept in `backend/institute-backend` until the services are verified.
>
> Status: working on branch `feature/institute-portal`; runs locally against the Neon test branches (Institute: `institute_portal_test`,
> Student: `subodh_dev`) and is verified end to end by `scripts/e2e/platform_e2e.py` and `scripts/e2e/ui_walkthrough.py`. Still **not
> production-ready** (see [OPERATIONS.md](OPERATIONS.md#blockers-and-open-decisions)): no production database, email provider or domain.

## Architecture and ownership

| Concern | Owner | Notes |
|---|---|---|
| Staff identity, roles, sessions, MFA, audit | `institute-backend` | New tables; Redis keys prefixed `inst_` |
| Providers, branches, batches, enrollments | tables created by the enrollment service's migration chain; **written** by `institute-backend` | Mapped only; never created or altered by this service's migrations |
| Subjects, teaching, questions, papers, exam plans, exams, result corrections, billing, support, settings | `institute-backend` | Migrations `0001`-`0008`, own version table `m10_alembic_version` |
| Student identity (search/create) | **Student authentication service** | New additive `/internal/v1` API, service-key protected |
| Exam delivery, attempts, shared timer, resume, negative marking, results | **Student test / attempt / result services (evaluation logic unchanged)** | Institute publishes into `tests`/`questions`/`test_audiences` via `/internal/v1/exams`, reads results via `/internal/v1/tests/{id}/results` and attempt status via `/internal/v1/tests/{id}/attempts`, and asks them to apply authorized corrections (below) |
| Student inbox notifications | **Student notification service** | Institute queues them in a local outbox and delivers through `POST /internal/v1/notifications` (idempotent by `dedupe_key`) |
| Result history | **Student result service** (new table `result_history`) | Immutable record of every correction; the original evaluation is never overwritten without a history row |
| Redis | the existing shared instance | Institute keys `inst_session:`, `inst_active:`, `inst_mfa:`, `inst_rl:`; student keys untouched |

Existing Student Portal files changed (all additive and backward compatible): in the authentication, test, result,
attempt and notification services, one optional setting (`INSTITUTE_SERVICE_API_KEYS`, empty = the internal API answers 404) in
`app/config.py`, one `include_router` line in `app/main.py`, a new `app/routes/internal.py`, new tests, and an optional line in
`.env.example`. Two of those services gained more than routes: the **test service** has an answer-key correction endpoint
(`POST /internal/v1/exams/{id}/answer-key`, only reachable with a service key) and the **result service** has a new table
`result_history` (migration `b2d4e1a9c7f3`, append-only trigger on PostgreSQL), and the notification service has an opt-in `NOTIFICATION_CREATE_API_KEYS` guard for its legacy `POST /notifications`, a re-evaluation service and the
`/internal/v1/results/adjust`, `/tests/{id}/reevaluate` and `/tests/{id}/results/history` endpoints. The Student login API, session
format, public routes, attempts, evaluation and scheduled-exam behaviour are not modified (route tables were compared with the
original commit). `docker-compose.yml` gains three services. `deploy-pages.yml` now has a path filter (`frontend/**` and itself) so
Institute-only changes no longer redeploy the Student Portal; every Student frontend change still triggers it
(`scripts/check_workflow_triggers.py` asserts both).

## Roles and authorization

Five roles: **Super Admin, Admin, Provider Admin, Branch Admin, Teacher** (single source:
`app/core/roles.py`). Authorization is default-deny and enforced in the backend on every request
(`Principal.can` / `require`, `scope_clause`, object-level checks). The UI hides what a role cannot do but
is never the boundary.

* **Global identities**: one `staff` row per person (immutable `STFnnnnnn`) with many role assignments across
  providers and branches. Students are global in the Student service; enrolling an existing student *links*
  them and never creates a duplicate (email match is a hard stop; phone match needs explicit confirmation).
* **Scope**: Super Admin/Admin platform-wide; Provider Admin one provider; Branch Admin one branch; a Teacher
  reaches branch/subject/batch data only through teaching assignments.
* **Hierarchy**: only a Super Admin can create, modify or deactivate a Super Admin; Admin cannot override paper
  status, refund/adjust, correct results or change platform settings; Provider/Branch admins grant only roles
  below them inside their scope; nobody changes their own roles; a Teacher approves/publishes only their own papers.
* Cross-branch student transfers need Provider Admin approval; the old enrollment is closed and events are appended.

## Authentication and sessions

* Staff ID + password. Argon2id (configurable cost). 12-128 characters, offline common-password list, optional
  HIBP k-anonymity lookup (`HIBP_ENABLED`), no composition rules, rejects passwords containing the person's own
  name, email or Staff ID.
* Email-OTP MFA (5-minute validity, 5 attempts per OTP, 60-second resend cooldown, a new OTP invalidates the old
  one, single use) is mandatory for Super Admin, Admin, Provider Admin and Branch Admin. **Email OTP is weaker than
  TOTP/WebAuthn**: monitor `auth.login` / `auth.mfa_verify` failures and plan an authenticator-app upgrade.
* Verification links last 30 minutes, reset links 15 minutes, single use, stored as SHA-256 hashes, with generic responses
  that do not reveal whether an account exists.
* Redis sessions: opaque id in an HttpOnly host-only cookie (`__Host-` prefix when Secure), SameSite=Strict by default,
  30-minute sliding idle timeout, 12-hour absolute lifetime, **one active session per account**, new id at every login;
  invalidated by logout, password change/reset, deactivation and role changes.
* CSRF: session-bound `X-CSRF-Token` on every state-changing request plus an Origin allow-list. CORS: explicit origin
  list, credentials only for listed origins, never a wildcard.
* Rate limits (Redis): login per IP and per account, MFA issue/verify/resend, forgot/reset/verify-email, password change.
* Audit: append-only `institute_audit_logs` (a PostgreSQL trigger rejects UPDATE/DELETE); secret-like keys are redacted and
  raw OTPs, tokens, passwords and session ids are never logged (covered by a test).

## Exam workflow (implemented rules)

1. **Papers**: CSV or one-sheet `.xlsx` upload (max 2 MB, 500 questions). *Any* failing row rejects the whole paper: nothing is
   imported and row/field errors are returned. Success creates immutable question versions and a `VALIDATED` paper version, then
   `APPROVED`, then `PUBLISHED` (a previously published version becomes `SUPERSEDED`). Full status history; a Super Admin override
   needs a reason and is audited. Both formats go through the same validator. `.xlsx` is read with the standard library only:
   formulas, macros, external links/embedded objects, DOCTYPE/entity declarations, merged or hidden cells/rows/columns/sheets,
   several sheets, oversized or heavily compressed parts are all rejected rather than interpreted. **Word (.docx)** papers are accepted as
   a table with the CSV columns or as numbered question blocks (`1. …`, `A) … D) …`, `Answer: B`, optional `Marks:`/`Negative:` or
   paper-level `Default marks:`/`Default negative:`); **text PDFs** use the same numbered layout. A numbering gap is reported as a possibly
   missing question and rejects the paper. Scanned PDFs (no text layer) are rejected. **Images** (PNG/JPEG) are accepted when
   `OCR_ENGINE=tesseract` (docker-compose enables it): magic bytes and size are verified first, each question must reach
   `OCR_MIN_CONFIDENCE` on its scoring-critical lines, and one unreliable question rejects the whole paper. `.xls`/`.xlsm` are not supported.
2. **Plans**: subject slots (an exact paper version, or "latest published version of this paper"), batches, window,
   one shared duration, auto-publish, optional `generate_at`.
3. **Generation** (`app/services/exam_generation.py`): only approved/published versions the provider owns or was granted by an
   active share; missing or unauthorized subjects are recorded and notified while the exam is built from the rest; the manifest
   is immutable; eligible students = ACTIVE enrollment in the branch and ACTIVE membership of a plan batch; idempotent per
   `(plan, run_key)`; a late-approved paper only affects the *next* exam.
4. **Delivery**: transactional outbox to the Student test service (idempotent, bounded retries with exponential backoff and
   jitter, permanent failures alert staff). The Student schema stores integer marks, so fractional marks are refused.
5. **Student side (unchanged)**: shared timer, resume, negative marking and missed-exam handling already live there.
6. **Student inbox notifications**: once an exam is delivered (or cancelled) the assigned students get a notification in their existing
   inbox (`TEST_SCHEDULED` / announcement; times shown in IST). When results exist, each student is told *once* that their result is
   available (never the score). Notifications are written to `student_notification_outbox` in the same transaction as the event,
   delivered by the worker with bounded retries, and deduplicated by key on both sides; a permanent refusal alerts staff and is counted
   on the dashboard.
7. **Assignment status sync**: `student_exam_assignments.status` (`ASSIGNED` -> `STARTED` / `COMPLETED` / `MISSED`) follows the students' real
   attempts, read from the attempt service (no answers or marks cross the boundary). It only moves forward (`ASSIGNED < MISSED < STARTED <
   COMPLETED`), `MISSED` is set only after the window closed, cancelled exams are left alone, and it runs in the worker every minute
   or on demand with `POST /exams/{id}/sync`.
8. **Results**: read through the result service's internal API and filtered to assigned students (and a Teacher's batches).
9. **Result corrections** (Super Admin only, `result.correct`; Admin, Provider/Branch Admin and Teacher are refused and the refusal is
   audited). Two operations, both requiring a reason (5-1000 characters) and an `Idempotency-Key` header:
   * *Marks adjustment* for one assigned student with an existing result (bounded to +/- the exam's total marks, no-op refused).
   * *Answer-key correction*: the test service updates the key, then the result service re-scores every result of that exam with
     exactly the production evaluation rules (parity-tested on real PostgreSQL). **Results a person adjusted by hand are not
     re-scored**; they are reported as `skipped_manual`, never silently overwritten.
   The request, reason and actor are stored first in `result_corrections` (immutable columns, DB trigger on PostgreSQL), then applied
   through the Student services, which write `result_history` (previous and new values, reason, actor, correction reference). If a
   Student service is down the correction stays `FAILED`/`PENDING` and the API answers 503 `CORRECTION_PENDING`; repeating the same
   request with the same `Idempotency-Key` completes it using the same correction reference, so nothing is applied twice. Changed
   students receive an "updated" inbox notification (no score). Every outcome is audited (`result.adjust`,
   `result.answer_key_correction`). The exam page shows the history to roles that can read results (Teachers excluded) and the forms
   to Super Admin.

## Tables created by this service

`staff`, `staff_tokens`, `staff_role_assignments`, `institute_audit_logs` · `subjects`, `provider_subjects`, `batch_subjects`,
`teacher_subject_assignments`, `enrollment_events`, `enrollment_transfers` · `stored_files`, `bank_questions`, `question_versions`,
`papers`, `paper_versions`, `paper_version_questions`, `paper_uploads`, `paper_version_events`, `paper_shares` · `exam_plans`,
`exam_plan_batches`, `exam_plan_subjects`, `exam_generation_jobs`, `exam_generation_events`, `exams`, `exam_versions`,
`exam_version_papers`, `student_exam_assignments`, `exam_outbox`, `institute_notifications`, `student_notification_outbox`, `result_corrections` · `support_tickets`,
`support_ticket_messages`, `subscription_plans`, `provider_subscriptions`, `invoices`, `payment_records`,
`billing_adjustments`, `institute_settings`.

Conventions: UUID keys, `TIMESTAMPTZ`, `NUMERIC(12,2)` money, `JSONB` event details, CHECK constraints for statuses and scope,
partial unique indexes, PostgreSQL triggers that make history tables append-only. `bank_questions` is deliberately not called `questions`: the Student test service owns that name in the shared database. The migrations were verified on SQLite (in the test-suite) **and on a disposable PostgreSQL 16**: the real Student-service chains,
then the Institute chain last, then `alembic check` (zero ORM/schema differences) and the whole Institute test-suite against that
schema (see `scripts/verify-institute-on-postgres.sh`). They have **not** been run against the live database or a copy of it.

Exam times handed to the Student test service are converted by PostgreSQL into the connection's *session time zone* (the zone the
Student queries use when comparing with `NOW()`), so they are correct whether the database runs in UTC or Asia/Kolkata.

## API

Interactive docs are served at `/api/docs` (OpenAPI JSON at `/api/openapi.json`). Errors have the shape
`{"detail": {"code": "...", "message": "..."}}`; lists are server-paginated (max 100 per page) with allow-listed filters.
The list below is generated from the running application's OpenAPI schema.

**Academics**

- `PATCH /api/v1/batches/{batch_id}`
- `GET /api/v1/batches/{batch_id}/subjects`
- `DELETE /api/v1/batches/{batch_id}/subjects/{subject_id}`
- `PUT /api/v1/batches/{batch_id}/subjects/{subject_id}`
- `GET /api/v1/branches/{branch_id}/batches`
- `POST /api/v1/branches/{branch_id}/batches`
- `GET /api/v1/providers/{provider_id}/subjects`
- `PUT /api/v1/providers/{provider_id}/subjects/{subject_id}`
- `GET /api/v1/subjects`
- `POST /api/v1/subjects`

**Authentication**

- `POST /api/v1/auth/change-password`
- `POST /api/v1/auth/forgot-password`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/me`
- `POST /api/v1/auth/mfa/resend`
- `POST /api/v1/auth/mfa/verify`
- `POST /api/v1/auth/reset-password`
- `POST /api/v1/auth/verify-email`

**Exams**

- `GET /api/v1/branches/{branch_id}/exam-plans`
- `POST /api/v1/branches/{branch_id}/exam-plans`
- `GET /api/v1/branches/{branch_id}/exams`
- `GET /api/v1/exam-plans/{plan_id}`
- `PATCH /api/v1/exam-plans/{plan_id}`
- `POST /api/v1/exam-plans/{plan_id}/generate`
- `GET /api/v1/exam-plans/{plan_id}/jobs`
- `PUT /api/v1/exam-plans/{plan_id}/slots`
- `GET /api/v1/exams/{exam_id}`
- `GET /api/v1/exams/{exam_id}/assignments`
- `POST /api/v1/exams/{exam_id}/cancel`
- `POST /api/v1/exams/{exam_id}/publish`
- `GET /api/v1/exams/{exam_id}/results`
- `POST /api/v1/exams/{exam_id}/sync`
- `GET /api/v1/jobs/{job_id}/events`

**Operations**

- `GET /api/v1/audit-logs`
- `GET /api/v1/billing/plans`
- `POST /api/v1/billing/plans`
- `GET /api/v1/dashboard`
- `GET /api/v1/invoices/{invoice_id}`
- `POST /api/v1/invoices/{invoice_id}/adjustments`
- `POST /api/v1/invoices/{invoice_id}/payments`
- `GET /api/v1/notifications`
- `POST /api/v1/notifications/read-all`
- `GET /api/v1/notifications/unread-count`
- `POST /api/v1/notifications/{notification_id}/read`
- `GET /api/v1/providers/{provider_id}/invoices`
- `POST /api/v1/providers/{provider_id}/invoices`
- `GET /api/v1/providers/{provider_id}/subscription`
- `POST /api/v1/providers/{provider_id}/subscription`
- `GET /api/v1/settings/branches/{branch_id}`
- `PUT /api/v1/settings/branches/{branch_id}`
- `GET /api/v1/settings/platform`
- `PUT /api/v1/settings/platform`
- `GET /api/v1/settings/providers/{provider_id}`
- `PUT /api/v1/settings/providers/{provider_id}`
- `GET /api/v1/support/tickets`
- `POST /api/v1/support/tickets`
- `GET /api/v1/support/tickets/{ticket_id}`
- `PATCH /api/v1/support/tickets/{ticket_id}`
- `POST /api/v1/support/tickets/{ticket_id}/messages`

**Providers & Branches**

- `GET /api/v1/branches/{branch_id}`
- `PATCH /api/v1/branches/{branch_id}`
- `POST /api/v1/branches/{branch_id}/deactivate`
- `GET /api/v1/providers`
- `POST /api/v1/providers`
- `GET /api/v1/providers/{provider_id}`
- `PATCH /api/v1/providers/{provider_id}`
- `POST /api/v1/providers/{provider_id}/activate`
- `GET /api/v1/providers/{provider_id}/branches`
- `POST /api/v1/providers/{provider_id}/branches`
- `POST /api/v1/providers/{provider_id}/deactivate`

**Questions & Papers**

- `GET /api/v1/branches/{branch_id}/papers`
- `GET /api/v1/branches/{branch_id}/questions`
- `POST /api/v1/branches/{branch_id}/subjects/{subject_id}/papers/upload`
- `GET /api/v1/paper-versions/{pv_id}`
- `POST /api/v1/paper-versions/{pv_id}/approve`
- `POST /api/v1/paper-versions/{pv_id}/override-status`
- `POST /api/v1/paper-versions/{pv_id}/publish`
- `POST /api/v1/paper-versions/{pv_id}/withdraw`
- `POST /api/v1/papers/{paper_id}/shares`
- `DELETE /api/v1/papers/{paper_id}/shares/{provider_id}`
- `POST /api/v1/questions`
- `POST /api/v1/questions/{question_id}/versions`
- `GET /api/v1/uploads/{upload_id}`
- `GET /api/v1/uploads/{upload_id}/file`

**Result corrections**

- `POST /api/v1/exams/{exam_id}/answer-key-corrections`
- `GET /api/v1/exams/{exam_id}/result-corrections`
- `POST /api/v1/exams/{exam_id}/results/{student_id}/adjustments`

**Staff**

- `GET /api/v1/staff`
- `POST /api/v1/staff`
- `GET /api/v1/staff/lookup`
- `GET /api/v1/staff/{staff_id}`
- `PATCH /api/v1/staff/{staff_id}`
- `POST /api/v1/staff/{staff_id}/assignments`
- `DELETE /api/v1/staff/{staff_id}/assignments/{assignment_id}`
- `POST /api/v1/staff/{staff_id}/deactivate`
- `POST /api/v1/staff/{staff_id}/reactivate`
- `POST /api/v1/staff/{staff_id}/resend-verification`
- `GET /api/v1/teaching`
- `POST /api/v1/teaching`
- `DELETE /api/v1/teaching/{teaching_id}`

**Students & Enrollments**

- `GET /api/v1/branches/{branch_id}/enrollments`
- `POST /api/v1/branches/{branch_id}/enrollments`
- `PATCH /api/v1/enrollments/{enrollment_id}`
- `DELETE /api/v1/enrollments/{enrollment_id}/batches/{batch_id}`
- `PUT /api/v1/enrollments/{enrollment_id}/batches/{batch_id}`
- `POST /api/v1/enrollments/{enrollment_id}/transfers`
- `GET /api/v1/providers/{provider_id}/transfers`
- `POST /api/v1/students/lookup`
- `POST /api/v1/transfers/{transfer_id}/approve`
- `POST /api/v1/transfers/{transfer_id}/reject`

**Other**

- `GET /health`
- `GET /ready`

_Total: 113 operations._


## Local development and verification

```bash
# backend: SQLite + fake Redis, never touches a real database
cd backend/institute-backend && pip install -r requirements-dev.txt && ruff check app tests && pytest -q
# frontend
cd institute-frontend && npm ci && npm run lint && npm test && npm run build
# Student-service tests (each service in its own environment; in-memory SQLite). Needs `pip install pytest httpx`.
cd backend/student-test-service && DATABASE_URL=sqlite:// pytest tests/test_internal_exams.py tests/test_internal_answer_key.py
cd backend/student-result-service && DATABASE_URL=sqlite:// pytest tests/test_internal_results.py tests/test_internal_corrections.py
cd backend/student-attempt-service && DATABASE_URL=sqlite:// REDIS_URL=redis://x pytest tests/test_internal_attempts.py
cd backend/student-notification-service && DATABASE_URL=sqlite:// pytest tests/test_internal_notifications.py
# everything against a disposable local PostgreSQL (refuses non-local, non-empty or non-disposable databases); also runs the
# result-service re-evaluation parity test and the trigger tests
PGURL=postgresql+psycopg://postgres@127.0.0.1:5432/inst_verify scripts/verify-institute-on-postgres.sh
# pipeline guarantees (path filter on the Student deployment, no secrets/deploys/non-local DB URLs in CI)
python scripts/check_workflow_triggers.py && scripts/check-no-secrets.sh
```

CI (`.github/workflows/`): `institute-backend-ci.yml` (lint, SQLite suite, **PostgreSQL service job** running the verify script,
dependency audit, guards), `institute-frontend-ci.yml`, and `student-services-ci.yml` (the existing Student authentication test plus all
internal-API tests, in each service's own environment, with a Redis service container). No workflow uses a repository secret, deploys, or
connects to anything but its own throwaway containers; `deploy-pages.yml` is the only deploying workflow and is path-filtered.

To run the stack: copy each service's `.env.example` to `.env` (never commit it), set distinct `*_API_KEY` values, then
`docker compose up --build`. The Institute UI is on port 8081 and the API on 8010. Create the first Super Admin inside the
container with `INSTITUTE_ADMIN_PASSWORD=... python -m app.cli create-super-admin you@example.com "Your Name"`.
