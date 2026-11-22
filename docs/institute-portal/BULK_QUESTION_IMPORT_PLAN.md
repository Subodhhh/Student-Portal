# Bulk Question Import — Implementation Plan (DRAFT, awaiting approval)

Status: proposal only. Nothing below is implemented yet. Decisions confirmed by the product owner on 2026-11-19 are marked **[decided]**.

## 1. What exists today (inspected)

| Area | Current state | Where |
|---|---|---|
| Question bank | `bank_questions` (identity + mutable classification) and immutable `question_versions` (stem, 4 options, 1 correct, marks, 5 image FKs). Papers reference exact versions, so historical papers are already snapshot-safe. | `institute_common/models/papers.py` |
| Question types | Only `MCQ_SINGLE`. `question_versions.correct_option IN ('A','B','C','D')`. The Student test/result services evaluate only this format. | `institute_common/core/taxonomy.py` |
| Taxonomy | Chapter/topic/subtopic are free-text labels on `bank_questions`, validated by `clean_label`. No taxonomy or alias tables. `GET /question-taxonomy` lists the distinct labels in use. | `routes/papers.py:665` |
| Marks | Per question (`marks_positive > 0`, `marks_negative >= 0`, NOT NULL). Exams already support `marking_mode = PER_QUESTION | UNIFORM`, frozen into `exam_versions.settings.marking` at generation. The outbox delivers resolved per-question marks, so **Student services evaluate whatever marks they receive**. | `models/exams.py`, `inst_exam/services/outbox.py:116` |
| Images | `question_image_assets`: deduplicated per provider by SHA-256, `storage` CHECK `IN ('db')`, bytes in BYTEA (deferred), served only through `GET /question-images/{asset_id}`. Unreferenced assets are swept after 7 days. | `services/image_assets.py` |
| File storage | `LocalFileStore` (local disk, server-generated keys) behind `get_store()`/`set_store()`. Metadata in `stored_files`. A sweeper removes orphans. | `services/filestore.py`, `jobs/sweep_stored_files.py` |
| Office parsing | Hardened `.xlsx` / `.docx` readers: bounded zip parts, no DTD/entities/macros/external links, picture-to-cell anchoring (`_sheet_pictures`). They load the whole sheet into memory and are capped by `max_questions`. | `services/extraction.py`, `services/documents.py` |
| Background jobs | Paper service has CLI jobs only (sweepers). Redis is used only by the identity service (and the Student auth service). No institute worker queue. | `inst_paper/jobs/` |
| Permissions | `question.read`, `question.manage`, `paper.*`. No taxonomy permission. | `institute_common/core` |

## 2. Confirmed scope decisions

1. **[decided] MCQ only.** Import accepts `MCQ_SINGLE` only. The template drops *Accepted Numeric Answer(s)* and *Numeric Tolerance*. MSQ/NAT/NVQ are deferred (section 9). The pipeline keeps the question type as a per-row field with a per-type validator registry, so adding types later means adding a validator and template columns, not redesigning the pipeline.
2. **[decided] Canonical taxonomy and alias tables**, provider-scoped, reusable by the regular Question Bank UI.
3. **[decided, revised 2026-11-19] Marks belong to the test, not the question.** Neither import nor manual question creation requires marks. The test creator configures every marking rule. There are **no presets, no seeded defaults and no preset tables**.
4. **[decided] Object-storage interface with S3 as the intended provider.** Develop on local disk + moto. No real AWS resources, credentials or uploads. Existing BYTEA images are not migrated.
5. Branch is an import-context field picked in the UI (a PROVIDER question requires a branch). It does not affect marks.

## 3. Phase A — Test-configured marking rules (IMPLEMENTED and approved 2026-11-19)

Imported questions have no marks, so this phase lands first. There are **no presets**: no `marking_scheme_presets` table, no preset versions, no seeded or hard-coded marks. The test creator enters every value.

### 3.1 Current flows inspected

| Where marking is configured | Model | API |
|---|---|---|
| Create Test wizard (Daily Test) | `exam_plans` | `inst_exam/routes/tests.py` (UI default UNIFORM), `routes/exams.py` (`MarkingIn`) |
| Weekly Test | `test_series` → copied to each generated `exam_plans` row | `routes/test_series.py` |
| Global Test (platform) | `global_tests` → locks the provider plan's marking | `routes/global_tests.py`, `routes/global_schedule.py` |

- Freeze: `exam_generation.plan_settings()` writes `{"marking": {...}}` into `exam_versions.settings` (immutable per version).
- Delivery: `outbox.py:116` resolves integer marks per question and sends `marks` / `negative_marks`. The Student result service scores exactly what it receives (+marks right, −negative wrong, **0 unanswered**, `FIXED_POLICIES["unanswered"] = "ZERO"`).
- Results: `exams._papers_total()` recomputes totals from the frozen marking.
- Paper totals: `paper_versions.total_marks` (NOT NULL) = sum of `question_versions.marks_positive`.

### 3.2 Rules format (shared, extensible)

A new mode **`RULES`** sits beside the untouched `PER_QUESTION` and `UNIFORM`. A plan/series/global test in RULES mode stores `marking_rules` JSON:

```json
{"format": 1,
 "rules": [
   {"subject_id": null,  "question_type": "MCQ_SINGLE", "correct": "4",    "wrong": "1"},
   {"subject_id": "…",   "question_type": "MCQ_SINGLE", "correct": "1.25", "wrong": "0.25"}
 ]}
```
(The values above only show the shape. Nothing is seeded.)

- **Section = subject.** That is how exams are already divided: one paper per subject, and `global_test_sections` is unique per subject. A section rule overrides the section-less rule for the same question type.
- `question_type` is validated against `QUESTION_TYPES` (only `MCQ_SINGLE` today). Adding MSQ/NAT/NVQ later adds allowed types and type-specific fields (e.g. `partial`) under a new `format`, without a schema change.
- `correct` is an integer 1–100 and `wrong` an integer 0–100, the same bounds and integer rule as today's UNIFORM marking. Student delivery requires whole numbers.
- **Unanswered isn't a rule field.** It stays the fixed policy 0, which is what the Student result service implements. A configurable value would require a Student result-service change, which is out of scope for Phase A.
- **Completeness check:** every (section, question type) that actually occurs in the exam's papers must resolve to a rule. This runs at plan check/save when papers are known, and again at generation. A gap blocks generation with a message naming the section, never a silent default.
- One validator/resolver module, `institute_common/core/marking.py`, is used by all three APIs, generation, the outbox and results. Pydantic model: `MarkingRulesIn`.

### 3.3 Freezing (immutable snapshot)

- `plan_settings()` freezes `{"mode": "RULES", "format": 1, "rules": [...]}` into `exam_versions.settings.marking`. That row is already never updated, so the snapshot is immutable. Later edits to the plan affect only versions generated after the edit, which is today's behaviour for UNIFORM.
- Weekly Test: the series' rules are copied into each plan it creates. Already-created plans are never changed (existing rule).
- Global Test: rules are locked at PUBLISHED, like the existing marking fields.
- `exam_versions.total_marks` is computed for RULES from each section's question count × the resolved `correct`.

### 3.4 Question marks become optional

- `question_versions.marks_positive` and `marks_negative` become nullable. The CHECK becomes `(marks_positive IS NULL AND marks_negative IS NULL) OR (marks_positive > 0 AND marks_negative >= 0)`, so marks are both set or both empty. **Existing rows keep their marks. There's no data update.**
- `POST /questions` and `POST /questions/{id}/versions`: marks become optional (omit both or give both). Bulk import writes NULL.
- `paper_versions.total_marks` becomes nullable: NULL when any question in the paper has no marks (shown as "Marks set by the test"). Existing rows are unchanged.
- **Guard:** a `PER_QUESTION` plan, series or global test can't include a paper with unmarked questions. It's rejected at plan check/save and at generation with a clear message to use UNIFORM or RULES. Existing PER_QUESTION exams keep working because their questions all have marks.
- Paper PDFs and answer keys print "marks set by the test" for unmarked questions.

### 3.5 Database changes

| Chain | Migration | Change |
|---|---|---|
| exam | `0013_marking_rules` | `exam_plans`, `test_series`, `global_tests`: add `marking_rules` JSON (nullable). Replace the marking CHECKs with `marking_mode IN ('PER_QUESTION','UNIFORM','RULES')` and `(marking_mode = 'RULES') = (marking_rules IS NOT NULL)`. Keep the UNIFORM marks CHECK. RULES leaves `marks_correct`/`marks_wrong` NULL. |
| paper | `0010_optional_question_marks` | `question_versions.marks_positive`/`marks_negative` → nullable, CHECK replaced as in 3.4. `paper_versions.total_marks` → nullable. |

Both migrations are additive or loosen constraints and have a downgrade. The downgrade refuses to run if RULES rows or unmarked questions exist, rather than corrupting them. They'll be tested upgrade → downgrade → upgrade on a disposable PostgreSQL only.

### 3.6 Code changes

- **institute-common:** `core/marking.py` (validator, resolver, completeness check, total). `models/exams.py` and `models/papers.py` get the column and CHECK updates.
- **exam service:** `tests.py`, `exams.py`, `test_series.py`, `global_tests.py`, `global_schedule.py` accept `RULES`. Also `exam_generation.plan_settings` and the totals, `outbox.py` (resolve per question from the snapshot) and `exams._papers_total`.
- **paper service:** optional marks in question create/version, a null-safe paper total in `papers.py` (create, check, revisions, PDF `_marking`), and `paper_pdf.py`.
- **Student test / result services: no change.** The payload keeps the same `marks`/`negative_marks` integers.
- **institute-frontend:** a shared `MarkingRulesEditor` (rows of section × question type with required Correct/Wrong inputs, all empty by default, plus an "Add section rule" action). It's used in TestWizardPage, WeeklyTestPage and globalTests. The question create/edit form gets optional marks fields, and paper and question displays handle missing marks.

### 3.6a Decimal marks (added 2026-11-19, approved and implemented)

RULES `correct`/`wrong` accept decimals with at most 2 decimal places (0.25, 0.5, 1.25, 4.5, …), stored as `NUMERIC(6,2)` in the rules JSON as **strings** (`"0.25"`) and computed only with Python `Decimal`, never float. UNIFORM stays integer, as today.

**Finding: the Student services are integer-only end to end.** Decimal marks would be rejected at delivery (`marks: int` in the test service internal schema) or truncated:

| Service | Integer today |
|---|---|
| student-test-service | `questions.marks`, `questions.negative_marks`, `tests.total_marks` (INTEGER); internal delivery schema `marks/negative_marks/total_marks: int`; read schemas |
| student-result-service | `test_results.total_marks`, `obtained_marks` (INTEGER); `subject_results` JSON values; schemas; `global_results.deltas()` casts adjustments with `int()`; internal adjust endpoint `obtained_marks: int` |
| student-ranking-service | response schemas `obtained_marks/max_marks/my_score: int` (its SQL `SUM`s already work on NUMERIC) |
| student-attempt-service | schemas that echo marks |
| institute exam service | `corrections.py` `SubjectMarks`/`AdjustIn` `obtained_marks: int` |

**Smallest safe change proposed** (evaluation rules unchanged: +marks right, −negative wrong, 0 unanswered):
1. Three student migrations, tested on a disposable PG only: `INTEGER → NUMERIC(6,2)` for question marks and `INTEGER → NUMERIC(8,2)` for test/result totals (`USING col::numeric`, lossless, existing values unchanged).
2. Change `int` to `Decimal` in those models and schemas. Scoring in `result_service`/`reevaluation`/`global_results` uses `Decimal`, and `int()` casts are removed. Responses serialize marks as JSON numbers (2 decimals), so the student frontend and ranking keep working. The frontend only needs number formatting (`4` vs `4.5`).
3. Wire format from the institute outbox: whole marks are sent as integers, exactly as today, so existing exams' payloads are unchanged. Fractional marks are sent as exact decimal strings (`"0.25"`), which `Decimal` fields accept.
4. Deployment order: the Student services first, then the institute side, so a decimal payload never reaches an integer-only service.

### 3.7 Tests

- Unit: rule validation (types, bounds, duplicates, unknown subject), resolution precedence, completeness gaps, totals.
- Integration (disposable PG, `institute-integration-tests`): create/edit plans and weekly and global tests with RULES; generation freezes the snapshot; editing the plan after generation doesn't change the frozen version; the outbox payload has the resolved marks; the PER_QUESTION guard rejects unmarked papers; existing PER_QUESTION/UNIFORM tests still pass; question creation without marks.
- Migrations: upgrade/downgrade on a disposable PG.
- Frontend: editor unit tests plus the updated wizard tests.
- Student services: run the existing result-service tests unchanged as a regression check.

## 4. Phase B — Canonical taxonomy (IMPLEMENTED and approved 2026-11-19)

**Rules (owner decisions):** provider-scoped records with stable ids, hierarchy Subject → Chapter → Topic → Subtopic, case-, spacing- and
Unicode-width-insensitive uniqueness (`core.taxonomy.label_key`), matching by exact name or approved alias only (similar names are
suggestions, never applied), records created only by holders of `taxonomy.manage`, reusable outside bulk import.

**DB (paper chain `0011_question_taxonomy`, additive):**
- `question_taxonomy_nodes`: `node_id`, `provider_id`, `subject_id`, `parent_id` (NULL only for chapters, CHECK), `level`, `name`, `name_key`,
  `status` (ACTIVE/RETIRED, soft retire, never deleted), `created_by/at/ip`, `updated_by/at`. Partial unique indexes: chapters on
  `(provider_id, subject_id, name_key)`, topics/subtopics on `(parent_id, name_key)`.
- `question_taxonomy_aliases`: `alias_id`, `node_id`, `provider_id`, `subject_id`, `parent_id`, `alias`, `alias_key`, `approved_by/at/ip`, with the
  same partial unique indexes. The service also refuses an alias equal to another record's name (active or retired) at that place.
- `bank_questions.chapter_id / topic_id / subtopic_id`: nullable FKs. The text labels stay (existing APIs unchanged) and hold the canonical
  name once linked.

**API (paper service, `routes/taxonomy.py`):** `GET /question-taxonomy/nodes`, `GET /question-taxonomy/match` (record + `matched_by`
NAME/ALIAS, or suggestions), `POST /question-taxonomy/nodes`, `PATCH /question-taxonomy/nodes/{id}` (rename: linked questions' labels
follow; retire: refused while active children exist), `POST /question-taxonomy/nodes/{id}/aliases`. Reads need `question.read`, changes
`taxonomy.manage` (new permission: Super Admin, Admin, Provider Admin; **not** Branch Admin or Teacher, because records are provider-wide).
Other providers' records answer 404. Every change is audited.

**Question linking:** question create, re-classify, paper upload and correction forks call `services.taxonomy.link_question`. A label that
names a record (by name or alias) is linked and rewritten to the canonical name; a label that matches nothing keeps its text and gets no
link. Nothing is created implicitly. GLOBAL questions are never linked.

**Backfill (operator-run, reviewable):**

    python -m inst_paper.backfill_taxonomy --actor-staff-id <uuid> [--provider-id <uuid>]             # dry run: JSON report, writes nothing
    python -m inst_paper.backfill_taxonomy --actor-staff-id <uuid> [--provider-id <uuid>] --execute   # create records, link questions

The report lists, per provider/subject, the records to create, the existing records reused, every group of merged spellings (the name is
the most-used spelling; ties go to the oldest question's), orphan labels (a topic without a chapter, which is kept as text and not
created) and invalid labels. It commits per subject and is idempotent (a re-run creates nothing).

## 5. Phase C — Object storage interface (IMPLEMENTED and approved 2026-11-20)

- **Shared module** `institute_common/objectstore.py`, because both the paper service (staff image endpoint) and the exam service/worker
  (picture delivery to the Student test service) read pictures. Interface `ObjectStore`: `put`, `get`, `delete` (idempotent), `exists`,
  `signed_url`.
  - `LocalObjectStore`: private directory (0700/0600), atomic writes, keys confined to the root.
  - `S3ObjectStore` (boto3): AWS S3 now; Cloudflare R2 or MinIO by setting `OBJECT_STORE_ENDPOINT_URL` (no code change).
  - Keys are server-made only (`question-images/<provider>/<sha256>`, strict pattern); errors are generic (`ObjectStoreError`), details
    only in server logs; credentials come from the standard AWS chain, never from settings.
- **Config**: `OBJECT_STORE=local|s3` (default `local`) and `OBJECT_STORE_*` (see OPERATIONS.md, "Object storage").
- **DB (paper chain `0012_object_storage_keys`)**: `question_image_assets.storage_key`; storage CHECK `IN ('db','local','s3')`; new CHECK
  `(storage = 'db') = (storage_key IS NULL)`. **Existing pictures are untouched (still `db`).** `0008` now tolerates the later column when
  it adopts a table the baseline created (adoption check only, no schema change).
- **Write path**: `image_assets.store_images(..., object_store=store)` puts the bytes first, then the row (deduplicated per provider and
  checksum). Existing uploads still store in the database; bulk import (Phase D) will pass the configured store.
- **Read path**: `GET /question-images/{id}` authorises first, then streams (`db`, `local`) or answers `307` to a signed URL (`s3`,
  `Cache-Control: private, no-store`); missing → 404, store down → 503. The exam outbox reads object-stored pictures too (missing →
  permanent failure, store down → retried).
- **Cleanup**: the unused-picture sweeper deletes the object before the row; a failed object delete keeps the row so the next run retries
  (never a row-less, orphaned object).
- **Dependencies**: `boto3==1.44.0` (runtime, institute-common), `moto[s3]==5.2.0` (CI/test only).
- **Connecting the real bucket**: OPERATIONS.md, "Object storage: connecting the real S3 bucket" (bucket, IAM policy, lifecycle, settings,
  free-tier note). Nothing was created in AWS.

## 6. Phase D — Bulk import backend (IMPLEMENTED and approved 2026-11-20)

**Flow:** upload (API stores the file in the object store, job `UPLOADED`) → worker reads and validates (`PREVIEW`) → uploader corrects
rows / resolves taxonomy (worker revalidates) → confirm (`IMPORT_QUEUED`) → worker imports → `COMPLETED` / `PARTIAL` / `FAILED`, staged
files deleted. Unconfirmed previews expire after 7 days (`EXPIRED`, files deleted). **Nothing reaches the Question Bank before confirm.**

**DB (paper chain `0013_bulk_imports`, additive):** `bulk_import_jobs` (status, counts, file errors, job-only taxonomy mappings, worker
lease `locked_until`/`worker`/`attempts`, uploaded/confirmed by/at/ip, finished/cleaned), `bulk_import_rows` (PK job+row number: text
columns, picture SHA-256 per image column, read errors, validation errors, content hash, resolved taxonomy ids, created question id,
edited by/at), `bulk_import_images` (one staged picture per job and SHA-256).

**Reader (`services/bulk_reader.py`):** streaming. iterparse for every large XML part, Excel shared strings in a temporary on-disk SQLite
table, pictures located by position (Excel: over the cell or "Place in Cell"; Word: inside the table cell) and read one at a time.
Same safety rules as the paper importer: no macros/embedded objects/external links/encryption, defusedxml (no DTD/entities), depth
limit, per-part byte limits and compression-ratio checks, pictures checked by `question_images.check_image` (signature, size ≤ 5 MB,
pixels, full decode). Excel: sheet "Questions" (or the only sheet). Word: the first table (≤ 5,000 rows; Excel ≤ 100,000).

**Validation (`services/bulk_validation.py`):** MCQ only (MSQ/NAT/NVQ named and refused); required Chapter, Difficulty, Question Type,
Correct Option, question (text/picture) and four options (text or picture, never both); taxonomy via names, approved aliases or job
mappings (unmatched → error with suggestions); duplicates inside the file (first occurrence wins) and against the bank (latest version
of ACTIVE questions; case-sensitive like the paper importer). Content format (TEXT / IMAGE / TEXT_WITH_IMAGE) is derived.

**Worker (`inst_paper/bulk_import_worker.py`, own container `institute-bulk-import-worker`, `mem_limit: 1g`):** DB-backed queue,
`SELECT … FOR UPDATE SKIP LOCKED`, lease renewed after every batch (commit = checkpoint), resume after a crash (read skips stored rows;
import takes only VALID rows without a question), at most 3 claims per job, a failing batch retried row by row (a bad row becomes
FAILED: partial success). Pictures: staged under `staging/bulk-imports/<job>/images/<sha256>`, copied into the provider's question pictures
at import through `store_images(object_store=…)` (deduplicated per provider), staged copies and orphans removed at the end.

**API (`routes/bulk_imports.py`):** `POST /bulk-imports`, `GET /bulk-imports?branch_id=`, `GET /bulk-imports/{id}`, `GET …/rows`,
`PATCH …/rows/{row_no}`, `GET …/images/{sha256}`, `GET …/taxonomy-issues`, `POST …/taxonomy-resolutions` (MAP / ALIAS / CREATE,
`taxonomy.manage`), `POST …/revalidate`, `POST …/confirm` (one conditional UPDATE: exactly once), `POST …/cancel`, `GET …/report.csv`
(CSV with BOM, formula-injection safe). All need `question.manage` for the job's provider/branch/subject; teachers see their own jobs;
other jobs answer 404. Limits: 2 GB per file, 3 unfinished imports per provider (settings `BULK_IMPORT_*`). The gateway allows
2,100 MB on `POST /api/v1/bulk-imports` only (streamed to the paper service, 15-minute response wait).

**Security correction (2026-11-20, owner request):** every time the worker claims a job it re-checks the uploader and, once
confirmed, the confirmer with the API's own rules (`services/bulk_import.permission_problem`): ACTIVE account, `question.manage` for the
job's provider/branch/subject, the provider, branch and subject still active and offered, and a teacher only on their own upload.
A failing check stops the job (`FAILED`, files deleted, audited as `bulk_import.stopped`); questions an interrupted import created
earlier, while allowed, stay.

**Deviations from the plan, for review:** the error report is CSV (opens in Excel; no new dependency) instead of .xlsx; images are
limited to 5 MB (the existing picture rule) rather than 2 MB; recognition of scientific notation (used by paper uploads) is not
applied to bulk-imported text, which is stored as typed.

## 7. Phase E — Templates and instructions (IMPLEMENTED 2026-11-20, awaiting review)

- **Files:** `inst_paper/import_templates/question_import_template.xlsx` and `.docx`, generated by `python -m inst_paper.import_templates.build`
  (developer tool: openpyxl, Pillow) from `content.py` (column help, rules, picture steps and examples - one source for both files).
  Served by `GET /api/v1/bulk-import-templates/{xlsx|docx}` (`question.manage`; `kind` is a fixed choice mapped to a fixed file).
- **Columns:** exactly the 16 finalised columns, named as the parser reads them; no Subject, marks, numeric-answer or tolerance column.
- **Excel:** `Questions` sheet (header only, frozen, drop-down lists for Difficulty `Easy/Medium/Hard`, Question Type `MCQ` and Correct
  Option `A/B/C/D` on rows 2-100,001), `Instructions` sheet (every column: required?, what to enter, example; how to add pictures in
  Excel - "Place in Cell" or over the cell; rules), `Examples` sheet (four filled rows with pictures). Only `Questions` is imported.
- **Word:** instructions, then the `Your questions` table (the first table - the only one imported), then an examples table with
  pictures inside the cells. A4 landscape; fixed file dates, so regenerating gives the identical file.
- **Examples:** text only; text with a picture (diagram); picture only (the whole question with its options in the picture, options
  typed A-D); options as pictures (four graphs).
- **Portal:** `ImportTemplateDownloads` on the Question Bank page (provider bank, staff with `question.manage`).
- **Verified** with the real reader, validator and importer: every example of both files imports as shown, each picture attached to its
  own field; the blank templates upload with no errors; downloads checked in a real (headless) browser.

## 8. Phase F — Frontend (institute-frontend, no new project)

New `BulkImportPage` under the Question Bank nav, reusing existing components (`questionBank.tsx`, image renderer, tables, toasts). Order on the page: Subject + Branch dropdowns, template downloads, instructions panel, file upload, validation summary, paginated preview with inline edit and a taxonomy-resolution panel, revalidate, Upload (confirm), progress (polling `GET /bulk-imports/{id}`), history.

The marking-scheme UI goes in the test-creation pages (Phase A).

## 9. Deferred / out of scope

- **MSQ, NAT, NVQ**: need new answer storage, delivery-payload fields and Student result-service evaluation. Planned as a separate cross-service phase. The import pipeline's per-type validator registry and template generator are the extension points.
- Migrating existing BYTEA images to object storage: needs a separate migration plan and your approval.
- Platform (GLOBAL) taxonomy and bulk import of GLOBAL questions.
- Real S3 connection: config only, done when you supply and approve credentials.
- Explanation images (not in the model or the finalized template).

## 10. Migrations summary

| Chain | Migration | Risk |
|---|---|---|
| exam service | `0013_marking_rules`: `marking_rules` JSON + `RULES` mode on `exam_plans`, `test_series`, `global_tests` | additive |
| paper service | `0010_optional_question_marks`: `question_versions.marks_*` and `paper_versions.total_marks` nullable + relaxed CHECK | loosens a constraint, existing data untouched |
| paper service | `0011_question_taxonomy`: taxonomy nodes + aliases + `bank_questions` FK columns | additive |
| paper service | taxonomy backfill (data): `python -m inst_paper.backfill_taxonomy`, not a migration | dry-run report first, disposable DB first |
| paper service | `0012_object_storage_keys`: `question_image_assets.storage_key` + storage CHECKs | additive |
| paper service | `0013_bulk_imports`: `bulk_import_jobs`, `bulk_import_rows`, `bulk_import_images` | additive |

All are tested on a disposable PostgreSQL. None run against Neon branches without your explicit approval.

## 11. Delivery order and verification

A → B → C → D → E → F, each phase stopping for review. Tests per phase: unit tests (validators, normaliser, store adapters with moto, streaming reader), integration tests in `institute-integration-tests` (disposable PG), a generated 100k-row workbook to check memory and throughput in the worker, and frontend tests for the new page.
