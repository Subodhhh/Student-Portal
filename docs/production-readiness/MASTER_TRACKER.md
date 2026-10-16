# Master tracker — both portals

Resume point for any session. Working repo: `~/Student-Portal-Review/Student-Portal` (the original `~/Student-Portal` is never touched).
Nothing is committed or pushed without explicit authorization. Secrets live only in git-ignored env files.

## Verified facts (2026-10-16)

* Neon (read-only inspection, credentials never printed):
  * `student-backend/student-authentication-service/.env` → Neon branch **subodh_dev** (20 Student tables only).
  * `deploy/institute/institute.env` → Neon branch **institute_portal_test** (77 tables: Institute tables plus a
    stale copy of the Student tables taken when the branch was created).
  * Same role/database name on both; server TimeZone = GMT.
* `subodh_dev` FKs: `tests.provider_id → providers`, `tests.branch_id → branches`, `enrollments.student_id → students`, … so any exam the
  Institute publishes needs its provider/branch to exist on the Student side.
* `institute_portal_test`: `enrollments.student_id → students` FK points at the stale copy → enrolling a newly created student fails.
* Student lookup 503 root causes: `STUDENT_SERVICE_API_KEY` empty in the Institute env (→ `UnconfiguredStudentClient`), and
  `INSTITUTE_SERVICE_API_KEYS` empty in the Student auth service (→ internal API answers 404).
* 313 files show a WSL mode change only; 21 files carry real pre-existing edits; 36 untracked paths (whole Institute Portal). Preserve all.

## Integration design (decided)

* Institute DB is the source of truth for providers, branches, batches, enrollments, enrollment_batches.
* Student side keeps a **read projection** of those rows in `subodh_dev`, written only through an authenticated
  Student-enrollment-service internal API (`/internal/v1/reference/*`) by an Institute reconciliation job (idempotent upserts,
  replace-set for batch membership). Existing Student queries (eligibility, My Institutes, results) keep working; FKs stay valid.
* Student identities stay in `subodh_dev` (auth service internal API). The Institute stores only `student_id`.
* Exams are delivered to the Student test service with explicit STUDENT audiences (eligibility computed by the Institute).

## Checklist

Status: [ ] todo · [~] in progress · [x] done (verified) · [!] blocked

### A. Configuration / infra
- [x] Service keys generated and wired (Institute⇄Student, Institute internal), env files git-ignored (`*.env`)
- [x] All Student services pointed at subodh_dev; all Institute services at institute_portal_test
- [x] docker-compose: build sections + healthchecks for Student services
### B. Data separation
- [x] Institute migration: drop `enrollments.student_id → students` FK (tested on disposable PG first)
- [x] Reference projection sync Institute → Student (7 unit + 3 PostgreSQL tests, live-verified)
### C. Student exam lifecycle
- [x] Attempt start enforces availability window; resume keeps full duration
- [x] Server-side expiry + automatic evaluation (no client dependency)
- [x] Answer review gating, ranking authorization
### D. Institute workflows (E2E on the live stack)
- [x] Lookup + enrollment end to end
- [x] Paper upload/extraction/preview/validation/approval: CSV, XLSX, DOCX (table or numbered), text PDF, PNG/JPEG via Tesseract OCR (opt-in, confidence-gated)
- [x] Exam plans, generation, scheduling, publication, missing-paper notifications (teacher + provider admin notified, live)
- [x] Composite tests (A+B live-verified; A+B+C in suite; response deadline + EXPIRED state added)
- [x] Role-specific pages / permissions (Super Admin, Provider Admin, Teacher: API scoping in E2E, nav + pages in Chromium)
### E. Frontends
- [x] Student portal: real data (Performance page rewritten), provider filter, empty states, review gating UI
- [x] Institute portal: 48-page browser walkthrough clean; Super Admin scope selector fixed; enrollment Manage dialog added
### F. Tests
- [x] All suites run: Institute 274 (SQLite) / 277 (PostgreSQL 18), legacy monolith 207, Student services 88, OCR 4 (in image)
### G. Running deliverable
- [ ] Both portals up at :8081 / :8080, all services healthy, verified flows

## Log
* 2026-10-16: inspection complete; design above recorded.
* 2026-10-16: migrations applied (tested on disposable PG18 copies first): subodh_dev auth → 562cc1f6c4b7 (password reset table; broken
  UUID/VARCHAR migrations repaired), result → b2d4e1a9c7f3 (result_history); institute_portal_test enrollment → 0002 (student FK dropped).
* 2026-10-16: full stack up via docker compose; `scripts/e2e/platform_e2e.py STF000003 <pw>` → 66/66 (QA super admin STF000003 created
  for verification; password in the session scratchpad only).
* 2026-10-16: security fixes: student session could add itself to any test audience (now service-key only); ranking leaked other tests'
  leaderboards; profile email change needed no password; destructive password-reset test ran against the configured (Neon) DB.
* 2026-10-16: gateway resolves upstreams at request time (survives container recreation); Student images pinned + non-root.
* 2026-10-16: institute_portal_test exam chain → 0003_exam_composite_expiry (additive).
