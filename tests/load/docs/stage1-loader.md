# Stage 1 loader

Creates the Stage 1 dataset through the REAL Institute APIs on the two Neon load-test branches, so every business rule, audit
entry, Student identity creation and the Institute → Student reference sync run exactly as in production.
Status: written and tested against an in-process fake API only. **Never executed.** Every execution step needs explicit approval.

## Commands (`LT_TARGET=neon tests/load/lt.sh stage1 [--questions-per-paper N] <command>`)
| Command | Effect |
|---|---|
| `plan [--canary]` | deterministic plan summary (no network) |
| `preflight` | read-only checks (below) |
| `run --staff-code STF… [--canary]` | dry run: plan + preflight; sends nothing, never opens the journal |
| `run --canary --staff-code STF… --workers 1 --execute` | the canary (stops at its end; `--max-failures` defaults to 1) |
| `validate --canary --wait-sync-minutes 10` | read-only canary validation; only a full pass opens the gate for the full run |
| `run --staff-code STF… --workers 1 --execute` | full run (refused until the canary validated); later re-runs may use `--workers 3` |
| `validate --wait-sync-minutes 90` | read-only full validation |
| `status` | journal summary (read-only, no lock) |
`--questions-per-paper` (default 20, must divide 1,000) is part of the plan identity: a journal is tied to one paper size.
`--adopt-existing` accepts marker rows already on the target that the journal does not know (server-side checks still apply).
Outputs (git-ignored, 0700 directory, 0600 files): `tests/load/results/stage1/{journal.json, journal.json.bak-*, loader.log,
run-*.json, run-canary-*.json, validation-*.json}`: IDs, counts and timings only.

## Plan (seed 20261103, `generator/plan.py`)
| Entity | Count | Rule |
|---|---:|---|
| Providers / branches | 100 / 100 | 70 providers with 1 branch, 15 with 2, 15 without a branch yet |
| Provider subjects | 300 | 3 per provider from 4 profiles (NEET-I, JEE-I, NEET-II, JEE-II) covering all 8 subjects |
| Batches / batch subjects | 256 / 721 | 2–3 per branch, class 11th/12th, year 2026-2027; the first batch offers all provider subjects |
| Staff / role assignments / teaching | 334 / 355 / 424 | 1 Provider Admin per provider; 1 teacher per (provider, subject); ~10 % teach at 2 providers |
| Students / enrollments | 1,000 / 2,000 | 350×1, 400×2, 150×3, 100×4 memberships at distinct providers (average 2.0) |
| Papers / questions | **400 / 8,000** | 20 questions per paper: 50 papers × 20 = 1,000 per subject for all 8; approved |
Why 20 questions per paper: the gateway's `proxy_read_timeout` is 30 s and saving a paper costs ~2–4 database round trips per
question (~200 ms each to Neon); 20 questions ≈ 8–16 s. The canary measures the real upload time before the full run.

## Canary (`plan.canary`)
A deterministic slice of the FULL plan, using the full plan's own entities and keys: the provider pair (one with two branches,
different subject profiles) with the most students wholly inside it → 2 providers, 3 branches, 8 batches, 8 staff (roles at these
providers only), 16 teaching assignments, 5 students / 6 enrollments (one student in both providers, one with chosen subjects),
2 papers (40 questions). It stops automatically at its end.
**Reuse by the full run**: same journal (same identity: the canary carries its full plan's fingerprint), same natural keys. Every
canary row is in the journal, so the full run skips it; a staff member who also works outside the canary keeps the same identity
and only receives the missing roles (`roles` are journaled per staff × provider). Tested: canary twice → nothing new; full run
after it → no duplicates, exact plan totals.

## Duplicate prevention
* **Database-unique creates** (provider/staff e-mail, role assignment, teaching, subject mappings, student e-mail): a re-sent
  request is answered with a known 409 and treated as "already there".
* **Creates the database cannot de-duplicate** (branches, batches, enrollments, paper uploads) are sent with client retries OFF.
  After an uncertain outcome (no response, 502/503/504, 500) the item is marked *uncertain* in the journal and the server is
  polled every 10 s for up to 3 minutes (paper list by title, branch/batch list by name, branch roster by student). Found → used.
  Not found → the item fails as UNCERTAIN and is **not re-sent in this run** (the original request may still be running). The
  next run looks on the server first (uncertain marker) and creates it only if it is still absent.
* Branches and batches: one cached listing per parent; a create only when the name is absent.
* Items blocked by a failed parent are recorded as *blocked* (not counted again by the stop rules). A phase is recorded complete
  only if none of its items failed or were blocked.

## Journal
Exclusive lock (a second loader is refused), 0600 file in a 0700 directory, atomic writes, timestamped backup on every open.
Identity = stage + seed + full-plan fingerprint (includes the paper size) + **both pinned Neon branch IDs**; any mismatch is
refused. At start-up the loader verifies the journal against the server: every journaled provider must exist and sample
branches, batches, staff and papers must exist (a reset branch is detected: nothing is written); marker providers on the server
that the journal does not know are refused unless `--adopt-existing`.

## Preflight (all must pass; read-only)
branch guard (both pinned branches, dev deny-list) · env files (`APP_ENV=dev`, `EMAIL_BACKEND=console`, no Resend key,
`SESSION_COOKIE_SECURE=false`, sync interval > 0, no migration URL) · `compose.env` keys complete and different from dev ·
stack binding: the 14 app containers belong to `student-portal-loadtest`, run, and were started with exactly the guarded
`DATABASE_URL`s, console e-mail, insecure cookie (institute) and a non-zero sync interval (enrollment service); the gateway
container belongs to the project and publishes only 127.0.0.1:18010 · base URL is that gateway and answers `/health` ·
clean target (read-only count of marker providers equals the journal's). The stack binding is re-checked before every phase.

## API endpoints used (gateway `/api/v1`)
auth `POST /auth/login`, `POST /auth/mfa/verify` · `GET /subjects` · `GET`/`POST /providers` · `PUT /providers/{id}/subjects/{sid}` ·
`GET`/`POST /providers/{id}/branches`, `GET /branches/{id}` · `GET`/`POST /branches/{id}/batches` · `PUT /batches/{id}/subjects/{sid}` ·
`GET /staff/lookup`, `POST /staff`, `GET /staff/{id}`, `POST /staff/{id}/assignments` · `POST /teaching` · `POST /students/lookup`,
`POST`/`GET /branches/{id}/enrollments` · `POST /branches/{id}/subjects/{sid}/papers/upload`, `GET /branches/{id}/papers`,
`POST /paper-versions/{id}/approve`, `GET /paper-versions/{id}`.

## Reports and timings
`run-*.json` holds API calls, duration, per-kind counts, failures, uncertain items and **per-endpoint timings** (count, p50, p95,
max, status counts; IDs folded into `{id}`). Secrets (password, one-time code, CSRF token, session id) are scrubbed by exact value
and by pattern from every error, log line, journal entry and report.

## Estimated cost (estimates; measured in the Stage 1 report)
~6,650 API calls (enrollments + student lookups ~3,000; papers 400 uploads + 400 approvals; batch subjects 721; staff ~690;
teaching 424; branch/batch listings 185 + 356). About 2–3 h with 3 workers after the first phases ran with 1; the reference sync
needs ~30–60 min more to catch up. Neon ≈ 2–8 CU-hrs, +40–100 MB, < 0.3 GB transfer.

## Known limitations
* Paper/question ownership is the Super Admin; synthetic staff and students have no passwords in Stage 1.
* No exam plans / Global Tests are created in Stage 1.
* An uncertain create is resolved by a later run; if its original request were still running at that time (hours later) a
  duplicate would still be possible; validation reports any duplicate.
* The validator's SQL has not yet run against a database; the canary validation is its first (read-only) run.
