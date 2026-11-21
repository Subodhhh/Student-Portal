# Stage 1 execution checklist (Neon load-test branches, real APIs)

Status: **NOT approved yet.** Nothing below runs until the user approves Stage 1 explicitly.
Figures marked *estimate* are planning numbers; the Stage 1 reports contain only measurements.

## 0. Preconditions (all must be true)
- [ ] Group B items for Stage 1 approved: stop the dev stack, write to the two Neon load-test branches, Stage 1 loader run.
- [ ] Neon usage recorded from the console (project red-cloud-37130434). Last reading 2026-11-03: compute 14.84 CU-hrs,
      storage 0.14 GB, transfer 0.16 GB (Free plan: 100 CU-hrs/month, 0.5 GB storage, 5 GB transfer per project).
- [ ] `tests/load/config/compose.env` (new random keys) and `config/neon/{student,institute}.env` created by the operator.
- [ ] Load-test images built one at a time while the dev stack is stopped (`LT_TARGET=neon lt.sh build <service>`), or reused.
- [x] Stage 1 loader written, reviewed and hardened (tests/load/generator/, docs/stage1-loader.md). Never executed.
- [ ] Dry run passes: `LT_TARGET=neon lt.sh stage1 run --canary --staff-code <Super Admin>` prints PASS for every preflight
      check, including "clean target".

## 0a. Canary (before the full run; each step needs approval)
1. Record Neon usage and both database sizes (read-only).
2. `LT_TARGET=neon lt.sh stage1 run --canary --staff-code <Super Admin> --workers 1 --execute` (password and one-time code are
   typed; the code is read from `LT_TARGET=neon lt.sh logs institute-identity-service`). Stops automatically at its end.
3. `LT_TARGET=neon lt.sh stage1 validate --canary --wait-sync-minutes 10`: 0 missing / 0 unexpected, 0 rule violations,
   projection 0/0/0. Only a full pass opens the gate for the full run.
4. Inspect `results/stage1/run-canary-*.json`: per-endpoint timings, especially
   `POST /branches/{id}/subjects/{id}/papers/upload` (must stay well below the gateway's 30 s); the identity log shows the
   console e-mails (nothing sent); Neon usage delta.
5. Repeat step 2: it must report 0 created (idempotency on the real API).
6. STOP for inspection and approval of the full run (`--workers 1` first; paper size confirmed from step 4).
Rollback: Neon console → Reset from parent on both branches, then move `results/stage1/journal.json` aside (the loader refuses
the stale journal anyway).

## 1. Services (profile `app`, 16 containers)
| Service | Why it is needed in Stage 1 | Memory limit |
|---|---|---|
| redis (alias student-auth-redis) | staff + student sessions, MFA challenges, rate limits, idempotency keys | 128 MB |
| institute-identity-service | staff login/MFA, staff + role assignments, audit sink | 256 MB |
| institute-organization-service | providers, branches | 192 MB |
| institute-academics-service | subjects per provider, batches, batch subjects, teaching assignments | 192 MB |
| institute-enrollment-service | enrollments + **reference sync to Student (ON, 15 s)** | 256 MB |
| institute-paper-service | question CSV uploads, paper approval | 256 MB |
| institute-exam-service, institute-exam-worker | exams for eligibility checks; outbox delivery to Student | 256 + 256 MB |
| institute-operations-service | staff notifications, service health page | 160 MB |
| institute-gateway | the single API origin (127.0.0.1:18010) | 64 MB |
| student-authentication-service | global student identity creation (internal API) | 256 MB |
| student-enrollment-service | receives the reference sync | 160 MB |
| student-test / attempt / result / notification | exam delivery, attempt/result sync, student notifications | 192/192/160/160 MB |
Not started: student-profile, student-ranking (`optional`), both frontends (`never`), local PostgreSQL, monitoring stack.
Limits sum 3.1 GB; observed dev usage of the same services ≈ 1.3 GB, so the dev stack **must** be stopped first.

## 2. Branch verification (both branches, before anything starts)
1. `LT_TARGET=neon tests/load/lt.sh guard identify neon-student` and `… identify neon-institute`
   (read-only session; prints project/branch/endpoint/database only).
2. Operator compares with Neon console → Branches: `load_test_student` / `load_test_institute` IDs, their endpoints, and that
   their parents are `subodh_dev` / `institute_portal_test`.
3. `… guard pin neon-student --confirm <br-…>` and `… guard pin neon-institute --confirm <br-…>` (records IDs in targets.json).
4. `… guard check neon` must print `OK` for both, and not "SAME database" / "DEVELOPMENT".
5. `lt.sh up app` runs the same check again and starts nothing if it fails.

## 3. Dev stack stop and restoration
Before stopping (evidence, no secrets):
```bash
docker compose ps --format '{{.Name}} {{.ID}} {{.Image}} {{.Status}}' > <scratch>/dev-before.txt
docker compose config --hash '*'                                    > <scratch>/dev-hash-before.txt
```
Stop (containers, networks and volumes are kept; `down`, `-v` and prune are never used):
```bash
docker compose stop          # project student-portal only; portal-tunnel/portal-edge (deploy/tunnel) are left as they are
```
Restore after Stage 1:
```bash
LT_TARGET=neon tests/load/lt.sh stop
docker compose start
for p in 8002 8003 8004 8005 8006 8007 8008 8009; do curl -fsS -o /dev/null -w "$p %{http_code}\n" http://localhost:$p/health; done
curl -fsS -o /dev/null -w "gateway %{http_code}\n" http://localhost:8010/health
docker compose ps --format '{{.Name}} {{.ID}} {{.Image}} {{.Status}}'   # same container IDs and images as dev-before.txt
docker compose config --hash '*'                                        # identical to dev-hash-before.txt
```

## 4. Workload, duration and resources (*estimates*)
Exact plan counts and the per-endpoint call estimate are in `docs/stage1-loader.md`: 100 providers, 100 branches, 256 batches,
334 staff, 424 teaching assignments, 1,000 students, 2,000 enrollments, 400 papers of 20 questions / 8,000 questions; **~6,650 API calls**.
- Latency to Neon ≈ 200 ms per database round trip (tests/perf notes): ~0.5–3 s per call, 3–6 s per enrollment with a new
  student, ~8–16 s per 20-question upload (gateway limit 30 s). With 3 loader workers after a 1-worker start: **~2–3 h** for the API load (*estimate*).
- Reference sync: ~2,450 rows in 25-row requests ≈ 100 requests, measured in the code comment at ~30 s each: finishes
  **~30–60 min after** the last enrollment (*estimate*; measured in the report).
- Neon (*estimate*): both computes awake ~3–4 h ≈ 2–8 CU-hrs; storage +40–100 MB; transfer < 0.3 GB.
- Computer 1: ~1.3–1.6 GB RAM for the stack, low CPU (I/O bound on Neon).
- Staff logins need MFA: the loader asks the operator for the Super Admin password (not stored) and reads the one-time code
  from the load stack's identity log (`EMAIL_BACKEND=console`); one session is reused for the whole run.

## 5. Neon monitoring and stop conditions
- Baseline (above) and a console reading every 30 min plus `pg_database_size` of both branches (guarded, read-only).
- **Stop immediately** if any of: compute ≥ 80 CU-hrs, storage ≥ 0.40 GB, transfer ≥ 4 GB this month; a guard refusal;
  API error rate > 2 % over 5 min; sync rejections that persist for 2 passes; WSL available memory < 300 MB.
- Stop = interrupt the loader (resumable: deterministic IDs + idempotency keys), `LT_TARGET=neon lt.sh stop`; computes
  suspend after 5 idle minutes. Then restore the dev stack (section 3).

## 6. Post-load validation
- `LT_TARGET=neon lt.sh stage1 validate --wait-sync-minutes 90` (read-only, guard first) covers this section.
- Every planned row exists exactly once (`LT1 ` name prefix, `@loadtest.example` e-mails) and nothing extra; sizes per branch.
- Business rules: enrollment ↔ batch in the same branch; one ACTIVE enrollment per student and branch; batch subjects ⊆
  provider subjects; every enrollment has class + academic year; teaching ↔ TEACHER role + enabled subject + batch in branch;
  question rules (4 distinct options, A–D, marks > 0, negative ≥ 0); papers approved.
- Institute → Student projection: only-in-Institute / only-in-Student / field differences = 0 / 0 / 0 (includes the rows
  inherited from the parent branches); `sync_status`: nothing pending, `rejected = []`.
- Student ID allocator above the highest issued ID; every Student enrollment's `student_id` exists.
- Dev branches unchanged: read-only counts of the dev databases before and after Stage 1 are identical.

## 7. Rollback
- Neon console → `load_test_student` / `load_test_institute` → **Reset from parent** (operator action; discards all Stage 1
  rows; the branches return to the parents' current state). Re-run `guard check neon` afterwards (IDs do not change).
- Load-test Redis has no volume (sessions vanish on stop). The `student-portal-loadtest_institute-files` volume (uploaded
  CSVs) is removed only with approval.
