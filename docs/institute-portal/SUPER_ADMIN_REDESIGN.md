# Super Admin redesign, question bank and test creation — rollout notes

Status: implemented on `feature/super-admin-redesign`, **not deployed, migrations not applied anywhere shared**.

## What needs a migration

| Service | Revision | Change (all additive, nothing dropped) |
|---|---|---|
| paper | `0002_paper_question_bank` | `bank_questions`: `scope` (PROVIDER/GLOBAL, existing rows PROVIDER), provider/branch nullable for GLOBAL rows only (check constraint), taxonomy columns (exam type, class, chapter, topic (200), subtopic, difficulty, pattern, subtype, question type), `topic` back-filled from each question's latest version, `forked_from_question_id` (a provider's own copy of a question it corrected but does not own). `papers`: same scope rule + exam type/class. `paper_uploads`: `answer_key_file_id`, `title`. |
| exam | `0004_exam_tests_audience` | New `global_tests`, `global_test_sections`, `exam_plan_students`. `exam_plans`: `DRAFT` status, `audience_type` (default BATCHES = previous behaviour), `marking_mode` (default PER_QUESTION = previous behaviour), `marks_correct`/`marks_wrong`, exam type/class, `global_test_id`. `exam_versions.settings` (frozen exam configuration, added to the append-only trigger). `exam_version_papers.source_provider_id` nullable (platform papers). PostgreSQL guards `global_tests_guard` / `global_test_sections_guard`: a global test and its sections change only as a DRAFT; after publishing the only change is PUBLISHED → WITHDRAWN. |

No other service changes its schema. The Student services are unchanged: exam-level marking is applied to the marks
delivered with each question, which is exactly what the Student result service evaluates.

## Rollout order (after approval)

1. Back up the Institute database (Neon branch `institute_portal_test`, then the production equivalent).
2. Deploy the new service images **without** traffic, or stop the scheduler worker, so no generation runs mid-migration.
3. Apply the migrations with the migration role (`MIGRATION_DATABASE_URL`), paper before exam:

   ```bash
   cd institute-backend/institute-paper-service && PYTHONPATH=.:../institute-common alembic upgrade head   # -> 0002_paper_question_bank
   cd ../institute-exam-service           && PYTHONPATH=.:../institute-common alembic upgrade head   # -> 0004_exam_tests_audience
   ```

   Both revisions are idempotent (safe to re-run) and only add or relax; existing rows keep their meaning.
4. Verify: `alembic check` for paper and exam reports no differences; `alembic current` shows the revisions above.
5. Regenerated gateway configuration (`institute-backend/institute-gateway/nginx.conf`, 117 routes) ships with the gateway image.
6. Deploy the services, the gateway and the Institute frontend together (the frontend calls the new endpoints).

## How it was verified (disposable PostgreSQL 18, never a shared database)

* Fresh database: Student chains + all seven Institute chains to head, `alembic check` clean for every service, full integration
  suite on PostgreSQL (290 passed; the document-format tests need the PDF extractor and were run separately on SQLite).
* Upgrade path: schema built with the previous code (`14b9275`: paper `0001`, exam `0003`), representative rows inserted (question
  with two versions, published paper, plan, generated exam), upgraded with the new code: data preserved, defaults applied, topic
  back-filled from the latest version only, scope check and the append-only trigger on `exam_versions.settings` enforced,
  `alembic check` clean, a second `upgrade head` runs nothing.

## Review fixes (all enforced in the backend)

* Corrections never re-version someone else's question: a platform, other-branch or other-teacher question corrected inside a
  provider paper becomes a provider-owned copy linked by `forked_from_question_id`; the original is unchanged.
* Generation is idempotent per plan and window start: manual and scheduled runs share the canonical key `window:<UTC start>`
  (unique per plan in jobs and exams); runs recorded under the older `manual:` / `scheduled:` keys are recognised.
* Global Daily Test content is reserved: provider staff get questions without correct answers/explanations until an exam of their
  provider using that version has closed; platform roles always see them. Reserved questions cannot be built into provider papers
  and reserved papers cannot be scheduled outside their test's own plan.
* Withdrawn tests: plans cannot be reactivated and generation re-checks the test under a row lock inside the transaction;
  exams already generated are kept. A paper version used by a published test cannot be withdrawn (withdraw the test first).
* Whole-institute exams are visible to every branch admin of the provider (detail, assignments, results restricted to the students of
  their own branches); notifications reach the same people. Student detail shows only exams assigned through enrollments the
  viewer manages. Student search accepts partial IDs ("STU"), e-mail and phone; blank means no filter.
* Topics: up to 200 characters, any language and common punctuation (labels stay at 80).
* The wizard validates the plan (`POST /branches/{id}/exam-plans/check`) and every paper (`POST /papers/check`) before building,
  approving or publishing anything; platform-paper actions are shown only to platform roles.

## Known limits (not implemented)

* Question format stays "one stem, four options, one correct answer" (what the Student services evaluate); patterns such as
  assertion/reason are classifications of that format. Multi-correct or numeric answers need Student-service changes.
* A paper covers one subject; multi-subject tests combine several papers in one exam (one shared timer).
* No suspend state for accounts (deactivate/reactivate only); no provider "type"; subjects have no description column.
* Policies fixed by the Student services and therefore shown, not configurable: 0 for unanswered, one resumable attempt,
  results after evaluation, answer review after the window closes, no question shuffling.
* Platform (global) questions are added one by one or through papers built from them; file import targets a provider branch.
* A whole-institute exam is stored once, in the branch it was planned in; other branches see it through provider-level visibility.
