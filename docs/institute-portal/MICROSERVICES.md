# Institute Portal: microservices

The Institute backend is now seven independently deployable FastAPI services behind one gateway. The original monolith
(`backend/institute-backend`) is kept unchanged and can still be started with `docker compose --profile legacy up`; remove it once the
services are verified in your environment.

| Service (`backend/…`) | Package | Owns (writes) | Public API (via the gateway) |
|---|---|---|---|
| `institute-identity-service` | `inst_identity` | `staff`, `staff_tokens`, `staff_role_assignments`, `institute_audit_logs` | `/auth/*`, `/staff*`, `/audit-logs`; internal: session introspection, audit ingestion |
| `institute-organization-service` | `inst_organization` | `providers`, `branches`\* | `/providers*`, `/branches*` |
| `institute-academics-service` | `inst_academics` | `subjects`, `provider_subjects`, `batch_subjects`, `teacher_subject_assignments`, `batches`\* | subjects, batches, `/teaching*`; internal: retire teaching |
| `institute-enrollment-service` | `inst_enrollment` | `enrollments`\*, `enrollment_batches`\*, `enrollment_events`, `enrollment_transfers` | students lookup/enrol, transfers (Student identity is reused through the Student authentication service API) |
| `institute-paper-service` | `inst_paper` | `stored_files`, `bank_questions`, `question_versions`, `papers`, `paper_versions`, `paper_version_questions`, `paper_uploads`, `paper_version_events`, `paper_shares` | questions, CSV/XLSX upload, versions, **revisions**, sharing; internal: grant share |
| `institute-exam-service` | `inst_exam` | `exam_plans*`, `exam_generation_*`, `exams`, `exam_versions`, `exam_version_papers`, `student_exam_assignments`, `exam_outbox`, `student_notification_outbox`, `exam_notification_outbox`, `result_corrections`, `composite_requests`, `composite_request_slots` | plans, generation, exams, results, corrections, **composite requests**; worker `python -m inst_exam.worker run-once` |
| `institute-operations-service` | `inst_operations` | `institute_notifications`, `support_*`, `subscription_plans`, `provider_subscriptions`, `invoices`, `payment_records`, `billing_adjustments`, `institute_settings` | notifications, support, billing, settings, dashboard, **`/system/health`**; internal: deliver staff notifications |

\* DDL of `providers`, `branches`, `batches`, `enrollments`, `enrollment_batches` (and the Student-owned `students`) belongs to the existing Student
migration chain; the Institute services write some of them (as the monolith did) but never create or alter them.
Every service except identity also owns a small audit spool table `inst_<service>_audit_spool`.

## Rules that are enforced, not just documented

* **Single writer per table.** `institute_common/ownership.py` is the registry. Every request/job runs in a service context and its DB session
  refuses to flush rows of tables the service does not own (`OwnershipViolation`). A test asserts each mapped table has exactly one owner.
* **Cross-service writes use APIs** (`/internal/v1`, `X-Service-Key`, 404 unless `INTERNAL_API_KEYS` is set, two keys accepted during rotation,
  never reachable through the gateway): audit events → identity, staff notifications → operations, paper sharing → paper, ending a teacher
  role → academics.
* **Authentication.** Browser sessions live in the identity service (Redis, MFA, rate limits). Every other service asks identity
  (`POST /internal/v1/introspect`) who is calling; if identity is down they fail closed with 503. Service keys exist only in backend environments.
* **Audit.** Services append events to their own spool table in the same transaction as the action; a forwarder delivers them to identity
  (idempotent by event id, at-least-once). Identity writes its own events directly.
* **Notifications.** Staff notifications raised by the exam service go through `exam_notification_outbox` and are delivered to the operations
  service with retries; student notifications keep their own outbox to the Student notification service.

## Gateway

`backend/institute-gateway/nginx.conf` is **generated** from the services' route definitions (`python scripts/gen_gateway_conf.py`, `--check`
in CI). One public origin (`:8010`) keeps cookies, CORS and CSRF exactly as they were. `/internal/` is never proxied.

## Running it

```bash
cp backend/institute-services.env.example backend/institute-services.env      # DATABASE_URL, Student service keys…  (never commit)
# root .env: INSTITUTE_IDENTITY_KEY, INSTITUTE_ACADEMICS_KEY, INSTITUTE_OPERATIONS_KEY, INSTITUTE_PAPER_KEY  (openssl rand -base64 48)
docker compose build && docker compose up -d
# per service, with the migration role:  alembic upgrade head   (order: identity, organization, academics, enrollment, paper, exam, operations)
docker compose exec -e INSTITUTE_ADMIN_PASSWORD=... institute-identity-service python -m inst_identity.cli create-super-admin you@example.com "Your Name"
```

Migrations: each service has its own chain (`inst_<service>_alembic_version`). The baseline **adopts** tables created by the legacy
`m10_alembic_version` chain (nothing is dropped or rewritten) and creates what is missing on a fresh database; later revisions
(`0002_exam_composite_requests`) are forward-only. `scripts/verify-institute-services-on-postgres.sh` proves on a disposable PostgreSQL that
the service chains reproduce the legacy schema object for object (863 objects), that adopting an existing schema changes nothing, and that
`alembic check` passes for every service.

## New behaviour

* **Paper pipeline:** upload (CSV or one-sheet XLSX; images only if an OCR backend is registered) → validate every row (any failure rejects the whole
  paper) → private storage → draft version → **preview** (questions + history) → **correct** with `POST /paper-versions/{id}/revisions` (creates the
  next version; untouched questions are shared, nothing is overwritten; the whole paper is validated again) → approve → publish. Published versions
  are immutable (database triggers on PostgreSQL); editing creates a new version.
* **Composite tests:** `POST /branches/{id}/composite-requests` (initiating provider names subjects and suppliers) → invited providers' admins are
  notified → each contributes a **published** paper of that subject or declines → a contribution grants the initiator *use* (a paper share) but never
  access to questions or files → the initiator builds an ordinary plan from the contributed versions; generation records every contributing provider
  and the exact paper versions. Idempotency-Key, duplicate-open-name rule, per-event notification dedupe, audit on every action.
* **Service health** page (Super Admin): readiness of all services and the failure queues.

## Institute and Student databases are separate

The Institute services use their own database (Neon branch `institute_portal_test`); the Student services use theirs (`subodh_dev`).
Nothing crosses the boundary except authenticated APIs:

| Direction | Contract | Purpose |
|---|---|---|
| Institute -> Student auth | `/internal/v1/students*` | look up / create global student identities (the Institute stores only the `STUnnnnnn` id; `enrollments.student_id` has no FK since `0002_enrollment_student_identity`) |
| Institute -> Student enrollment | `POST /internal/v1/reference/sync` | **read projection** of Institute-owned providers, branches, batches, enrollments and batch membership into the Student database (eligibility, "My Institutes", result listings, and the FKs of delivered tests need them there). Pushed by `inst_enrollment/services/reference_sync.py`: state-based, idempotent, only changed rows, membership as a complete set, rejected rows retried and shown in `GET /internal/v1/reference-sync` on the enrollment service. Runs every `REFERENCE_SYNC_INTERVAL_SECONDS` (15) and immediately after enrollment changes; the exam worker triggers it before delivering exams. |
| Institute -> Student test | `/internal/v1/exams` | publish/cancel generated exams with explicit STUDENT audiences; a provider/branch not yet projected answers `409 REFERENCE_MISSING`, which the outbox retries |
| Institute -> Student attempt / result / notification | existing `/internal/v1` APIs | attempt status, results, corrections, inbox |

Keys are per contract (root `.env`), interpolated into both ends by docker-compose.

## Known limits (see OPERATIONS.md for the production blockers)

* Inside the Institute database the services share one schema. Writes are single-owner and enforced; **reads of other services' tables
  are still direct SQL** (for example exam generation reads papers, enrollments and batches).
* Cross-service foreign keys inherited from the monolith remain inside the Institute database.
* OCR: `OCR_ENGINE=tesseract` (enabled in docker-compose) reads printed pages in the numbered layout; handwriting and poor scans are
  rejected by the per-question confidence bar, and every OCR draft still needs human preview and approval.
* Contributions share a *paper* (the existing share granularity), not a single version.
* Docker image builds and the 18-check black-box run (`scripts/e2e/run-stack.sh`) were executed in the sandbox, not in your environment.
