# Question Types: MCQ, MSQ and NAT — Architecture and Implementation Plan

Status: **PLAN, approved at the architecture level (decisions D1–D12, 2026-11-20). No code, migration, Neon change, rebuild or
deployment until the implementation plan (this document) is approved.**

**Scope: exactly three types.**
- **MCQ** (`MCQ_SINGLE`): single correct; existing.
- **MSQ** (`MCQ_MULTI`): multiple correct.
- **NAT** (`NUMERICAL`): numerical answer.

Assertion–Reason, Statement-based, Match, Matrix, comprehension groups and free-text are **out of scope**. The existing `pattern` classification label is left exactly as it is.

Legend: **[VERIFIED]** current code, read in the repository · **[PLAN]** to be implemented · **[OPEN]** needs your answer (§15).

---

## 1. Approved decisions

| # | Decision |
|---|---|
| D1 | New types use typed, structured documents on the immutable question version. MCQ keeps its existing columns. |
| D2 | `question_type` is stored on every `question_versions` row. |
| D3 | Only MCQ, MSQ, NAT. |
| D4 | MSQ has 2–8 options. |
| D5 | MSQ scoring is **all-or-nothing**: exactly the correct set gives full marks; anything else gives **0**. The scoring scheme is a stored, versioned value, so partial schemes can be added later without touching old exams. |
| D6 | NAT negative marking comes from the test's marking rules. |
| D7 | Matrix is out of scope. |
| D8 | NAT tolerance is set per question. Units are shown, never converted. |
| D9 | Marks awarded per question are stored with the result. |
| D10 | Equal marks give the same rank, consistently. |
| D11 | Duplicate identity includes the type. Same content and type with the same key is a duplicate; same content and type with a different key is flagged for review. |
| D12 | Submitted answers are validated server-side, MCQ included. |

## 2. Current state [VERIFIED]

Single-answer, 4-option MCQ is assumed end to end. File references are in the earlier assessment, and the important points are repeated here.

- **Institute paper service:**
  - `bank_questions.question_type` CHECK `IN ('MCQ_SINGLE')`;
  - `question_versions` has `option_a..d` NOT NULL and `correct_option IN ('A','B','C','D')`;
  - manual API `QuestionIn` uses `^[ABCD]$`;
  - bulk import: 16 columns, `QUESTION_TYPE_CHOICES = ("MCQ",)`, MSQ/NAT rejected;
  - paper PDF prints A–D;
  - duplicate hash covers stem and 4 options (the key is not part of it).
- **Institute exam service:**
  - marking `RULES` keyed by `(subject_id, question_type)`;
  - the frozen snapshot is in `exam_versions.settings.marking`;
  - the delivery payload carries `text`, `options{A..D}`, `correct`, `marks`, `negative_marks` and `images`, with **no type**;
  - answer-key corrections accept A–D.
- **Student test service:**
  - stores `question_type='MCQ'` (hard-coded), `option_a..d`, `correct_answer`;
  - `/start` lists its fields explicitly, so the **key is never served**.
- **Student attempt service:**
  - `selected_answer VARCHAR(10)`, **not validated** (only "the question belongs to this attempt");
  - mark-for-review keeps the answer.
- **Student result service:**
  - case-insensitive string equality, implemented **twice** (`result_service.evaluate_attempt` and `reevaluation.score`);
  - `test_results` stores totals only; per-question review is recomputed on read.
- **Student ranking service:** `ORDER BY obtained_marks DESC, created_at ASC`; rank = row position, so ties never share a rank.
- **Institute analytics:** totals only (attempted, correct, accuracy); there are no question-level statistics.

## 3. Target data model [PLAN]

### 3.1 Canonical shapes (one rulebook for every path)

| | MCQ (`MCQ_SINGLE`) | MSQ (`MCQ_MULTI`) | NAT (`NUMERICAL`) |
|---|---|---|---|
| Stem | `stem` + `stem_image_id` (existing columns) | the same columns | the same columns |
| Choices | `option_a..d` (+ images), existing columns | `content.options`: 2–8 × `{id: "A".."H", text, image}`; each option has text or a picture, never neither | — |
| Extra display | — | — | `content`: `{format: "INTEGER" \| "DECIMAL", decimals: 0–6, unit: str \| null}` |
| Answer key (never served before review) | `correct_option` (existing) | `answer_key`: `{correct: ["A","C"]}`, sorted, unique, ≥ 1, ⊆ option ids | `answer_key`: `{value: "12.5", tolerance: {kind: "EXACT"} \| {kind: "ABS", amount: "0.05"} \| {kind: "RANGE", min: "12.40", max: "12.60"}}` |
| Student answer (`selected_answer`) | `"B"` | sorted letters, no separators: `"AC"` | canonical decimal string: `"-12.5"`, `"7"` |
| Scoring scheme | (none: classic) | `"ALL_OR_NOTHING"` (D5) | `"NUMERIC_TOLERANCE"` |

**NAT numbers [OPEN R4: defaults proposed]:**
- Decimal only. Up to 12 significant digits and up to 6 decimal places.
- Optional leading `-`. No exponent, no thousands separators.
- `NaN`/`Infinity` are rejected.
- If `format = INTEGER`, the key, range bounds and student answers must be integers.
- Comparison is exact `Decimal` and **inclusive**.

### 3.2 Shared registry `institute_common/core/question_types.py` [PLAN]

For each type the registry provides:
- the Pydantic models for `content`, `answer_key` and `answer`;
- `validate_*`;
- `normalise_answer(raw) -> str | None` (raises a per-field error);
- `content_identity(stem, stem_picture, content)` for the duplicate hash, excluding the key;
- `key_identity(answer_key)`;
- `SCHEMA_VERSION`;
- `SCHEMES` (`MCQ_MULTI: {"ALL_OR_NOTHING"}`).

**Callers:** the manual API, bulk validation (Excel and Word), version creation, paper build, exam generation (re-validation before freeze) and the delivery builder.

**The Student services** (separate codebase) get the same rules from a mirrored module `app/question_types.py` in the test, attempt and result services. Agreement is enforced by **shared golden vectors**, `contracts/question-types/golden.json` at the repository root. Both test suites run that file (§12), so the two implementations can't drift silently.

## 4. Database changes [PLAN]

All changes are expand-only, and existing rows stay as they are.

| Chain (version table) | Revision | Changes | Backfill |
|---|---|---|---|
| Institute paper (`inst_paper_alembic_version`), after `0014` | `0015_question_types` | see list below | `question_versions.question_type = 'MCQ_SINGLE'` (437 rows, via DEFAULT; no rewrite in PG ≥ 11) |
| Institute exam (`inst_exam_alembic_version`) | **none** | `marking_rules` and `settings.marking` are JSON documents; `marking.py` accepts the new types and the snapshot gains `schemes` | — |
| Student test (`m4_alembic_version`), after `e4b9a6c1d8f3` | `g1_question_types` | `questions.content JSONB NULL`, `questions.answer_key JSONB NULL`, `questions.scheme VARCHAR(30) NULL`; `question_pictures(question_id FK CASCADE, checksum FK, role, PRIMARY KEY (question_id, role))`; `question_type` existing (`'MCQ'` rows unchanged; new rows store the real type) | none |
| Student attempt (`m5_alembic_version`), after `attempt_lookup_index` | `h1_answer_length` | `attempt_answers.selected_answer VARCHAR(10) → VARCHAR(64)` (widening: catalogue-only in PG) | none |
| Student result (`m6_alembic_version`), after `f5c0b7d2e9a4` | `i1_question_results` | new `test_result_questions(result_id FK CASCADE, question_id, question_order, question_type, selected_answer VARCHAR(64), status CHECK IN ('CORRECT','INCORRECT','UNANSWERED'), marks_obtained NUMERIC(8,2), PRIMARY KEY (result_id, question_id))` | none: results made before this keep being recomputed on read (identical numbers) |

**`0015_question_types` changes in detail:**
- `question_versions`:
  - add `question_type VARCHAR(20) NOT NULL DEFAULT 'MCQ_SINGLE'`;
  - add `content JSONB NULL`, `answer_key JSONB NULL`, `schema_version SMALLINT NULL`;
  - `option_a..d` and `correct_option` become nullable.
- CHECK `question_versions_shape_check`:
  - (`question_type='MCQ_SINGLE'` AND options and `correct_option` NOT NULL AND `content` and `answer_key` IS NULL)
  - OR (`question_type IN ('MCQ_MULTI','NUMERICAL')` AND `answer_key` and `schema_version` NOT NULL AND `correct_option` and `option_a..d` IS NULL).
- `bank_questions_type_check` admits the 3 codes.
- A trigger-free consistency rule: a question's versions all have the bank question's type. The API enforces it; a CHECK can't span tables, and the integration test proves it.
- New `question_version_assets(question_version_id FK, asset_id FK, role VARCHAR(20), PRIMARY KEY (question_version_id, role))`. These are the pictures referenced inside `content` (MSQ option images E–H, and A–D for MSQ). MCQ keeps its 5 picture columns.

**Not changed:** `bank_questions` identities and IDs (all 437), `paper_versions`, `paper_version_questions`, `exam_versions` and every exam snapshot, `test_results` columns, `attempt_answers` rows.

**Every downgrade is guarded:** it refuses while any non-MCQ version, question, picture link, per-question result row or long answer exists, the same pattern as `0010` and `0014`.

## 5. Migration and deployment strategy [PLAN]

1. All migrations on a disposable PostgreSQL first, with migration tests (upgrade, guarded downgrade, adoption, `alembic check`). The full suite must pass.
2. Back up both Neon branches over the **direct** endpoint, as done before.
3. **Student first** (separately approved). Migrate `m4`, `m5`, `m6`, then deploy the test, attempt, result and ranking services. They accept payload v1 and v2 and validate MCQ answers (D12 is live from here).
4. **Institute next.** Migrate paper `0015`, then deploy the paper service, bulk worker, exam service and exam worker. A setting `STUDENT_QUESTION_TYPES=MCQ_SINGLE` keeps generation MCQ-only until step 6.
5. Frontends: Institute and Student.
6. Turn on `STUDENT_QUESTION_TYPES=MCQ_SINGLE,MCQ_MULTI,NUMERICAL`. Generation then admits papers containing MSQ/NAT.
7. Run the browser verification on the dev branches.

Each step is independently reversible (§13).

## 6. Service-by-service implementation plan [PLAN]

### 6.1 Institute common
- `core/question_types.py` (registry, §3.2).
- `core/taxonomy.QUESTION_TYPES` gains the 2 codes.
- `core/marking.py`:
  - rules accept the 3 types;
  - `subjects_without_rules` checks the types actually present;
  - the frozen snapshot records `schemes: {"MCQ_MULTI": "ALL_OR_NOTHING", "NUMERICAL": "NUMERIC_TOLERANCE"}`. Absent means MCQ-only, as in every existing exam.
- Models: as §4.
- `objectstore`: no change.

### 6.2 Institute paper service
- **Questions API:**
  - `QuestionIn` and `QuestionVersionIn` become a discriminated union on `question_type`. MCQ bodies are unchanged; MSQ adds `options[]` + `correct[]`; NAT adds `answer`, `tolerance`, `format`, `decimals`, `unit`.
  - A new version must keep the question's type (409 otherwise).
  - `GET` outputs add `question_type`, `content` and `answer_key` (staff only). The reserved-Global-Test answer policy hides `answer_key` exactly as it hides `correct_option`.
- **Picture rule:** extended to MSQ options. Each option has text or a picture, never neither.
- **Duplicates (D11):**
  - identity = type + `content_identity`;
  - same identity + same `key_identity` → `DUPLICATE` (unchanged behaviour for exact copies);
  - same identity + different key → **flag** (§8.4);
  - the paper build's D7 check uses the same functions.
- **Bulk import:** reader for the sheets and tables of §7; per-type validation; row `data` holds the typed fields; `_import_row` writes the typed version; option pictures A–H are staged and finalised as today.
- **Paper build:** unchanged in shape; the content hash includes type, content and key.
- **Paper PDF:**
  - MSQ prints "(one or more correct)" with options A–H;
  - NAT prints an answer line with the unit;
  - the answer-key PDF prints sets, values and tolerances.
- **Copy to platform** (`routes/internal.py` copy-global): copies `question_type`, `content`, `answer_key` and the picture links.
- **Recognition, photo and review drafts, and the paper upload (CSV/XLSX/DOCX/PDF + answer key):** stay MCQ-only [OPEN R2].

### 6.3 Institute exam service and worker
- **Generation:**
  - re-validates every version with the registry before freezing;
  - refuses types outside `STUDENT_QUESTION_TYPES` with a clear message;
  - RULES mode requires a rule for each type present (existing mechanism).
- **Marking modes per type:**

  | Mode | MCQ | MSQ | NAT |
  |---|---|---|---|
  | `PER_QUESTION` | the question's own marks | the question's own positive marks; **wrong gives 0** (D5) | the question's own marks, negative included |
  | `UNIFORM` | `correct`/`wrong` | `correct` and 0 | `correct`/`wrong` |
  | `RULES` | rule per (subject, type) | rule per (subject, type); `wrong` must be 0 (the editor fixes it) | rule per (subject, type), negative configurable (D6) |

- **Delivery v2 per question:**
  - adds `type` and `scheme`;
  - MSQ: `content.options` + `key.correct`;
  - NAT: `content.{format, decimals, unit}` + `key.{value, tolerance}`;
  - pictures by checksum as today, extended to option E–H roles;
  - MCQ entries stay byte-identical to v1.
- **Corrections:**
  - `KeyChange` becomes typed: MCQ `correct_option`; MSQ `correct[]`; NAT `value` + `tolerance`;
  - the Student side applies it and re-evaluates (existing flow);
  - audit includes the before and after keys.
- **Analytics:**
  - totals are unchanged;
  - a new question-level table per exam (attempted / correct / incorrect / unanswered / average marks), from `test_result_questions` through the result service's internal API. This is what D9 enables.

### 6.4 Student test service
- `internal.py` (PUBLISH):
  - stores the real type, `content`, `answer_key`, `scheme`, picture links;
  - accepts v1 (`type` absent → MCQ).
- `/start` and `/details`:
  - add `question_type` (real) and `content` (MSQ options / NAT format, decimals, unit);
  - the explicit field list still **never includes** `correct_answer`, `answer_key` or `scheme`. A test asserts this.
- Picture endpoint: authorised through `question_pictures` as well as the existing columns.
- Answer-key correction: typed.

### 6.5 Student attempt service (D12)
- `save_answer`:
  - loads the question's type (and option ids or format);
  - `normalise_answer` (§3.1);
  - **422** with a field-level reason for anything invalid;
  - stores the canonical form.
- **MCQ:** a letter in the question's options. "Z", "AB", lowercase and anything longer than 1 character are rejected (lowercase is normalised to uppercase first).
- **MSQ:** unique letters ⊆ the option ids, at least 1; stored sorted.
- **NAT:** §3.1 numbers; `format=INTEGER` rejects decimals.
- **Clearing:** `null` clears, as today.
- **Mark-for-review:** unchanged and keeps the answer. Request bodies are capped (64 characters).

### 6.6 Student result service (one scorer, D9)
- New `app/services/scoring.py`: `score_question(qtype, scheme, key, answer, marks, negative) -> (status, marks_obtained)`.
  - `evaluate_attempt` and `reevaluation.score` **both call it**; the duplicated logic is removed.
  - Evaluation writes `test_result_questions` rows in the same transaction as `test_results`.
  - Re-evaluation replaces them; `result_history` keeps the totals as today.
- **Review payload (after the window):** per type: MCQ letter; MSQ selected and correct sets; NAT given value, correct value and tolerance.
- **Results made before the migration:** no rows; the review keeps recomputing them with the same scorer, so the numbers are identical.

### 6.7 Student ranking service (D10)
- **Standard competition ranking** (1, 2, 2, 4) via `RANK() OVER (ORDER BY obtained_marks DESC)` [OPEN R3: or dense ranking 1, 2, 2, 3].
- Display order within a tie: student name, then id, which is stable.
- Applied to the overall and subject rankings and to `my_rank`.

## 7. Template layout [IMPLEMENTED: one template for every type]

Owner decision (2026-11-21), replacing the earlier "one sheet / table per type" plan: **one template** holds MCQ, MSQ and NAT rows
together, in any order. Each row names its own type in **Question Type** and its key in **Correct Answer**.

### 7.1 Excel: one `Questions` sheet
| Column(s) | MCQ | MSQ | NAT |
|---|---|---|---|
| Chapter, Topic, Subtopic, Difficulty, Question Text, Question Image | ✓ | ✓ | ✓ |
| Question Type (dropdown MCQ / MSQ / NAT) | `MCQ` | `MSQ` | `NAT` |
| Option A–D Text / Image | required | A, B required; C, D optional | must be empty |
| Correct Answer | one letter A–D | every correct letter, e.g. `A, C` | the number, e.g. `9.8` |
| Option E–H Text / Image | must be empty | optional (contiguous from A) | must be empty |
| Tolerance Type (Exact / Plus-minus / Range), Tolerance, Minimum, Maximum, Answer Format (Integer / Decimal), Decimal Places, Unit | must be empty | must be empty | per §3.1 |

- The first template's 16 columns come first and keep their names (`Correct Option` and `Correct Option(s)` are read as Correct
  Answer), so every MCQ file made before MSQ / NAT imports exactly as before. The MSQ / NAT columns are optional in an uploaded file.
- A column that does not belong to a row's type and is filled is a row error naming the column (never silently ignored).
- `Instructions` sheet: every column's rule; `Examples` sheet: MCQ (text, picture, picture-only, picture options), MSQ and NAT rows
  mixed, never imported. Dropdowns: Difficulty, Question Type, Tolerance Type, Answer Format.
- Limits and pictures as before (100,000 rows; pictures anchored or placed in their cells).

### 7.2 Word: the first table, same columns
- The first table holds the questions (any template column, any order); the examples table below it is never imported.
- The template's table has the columns most questions need (options A–D, Correct Answer, Answer Format, Tolerance Type, Tolerance,
  Unit); Option E–H, Minimum, Maximum and Decimal Places columns may be added when a question needs them.

### 7.3 Import preview and correction
- Row cards per type: MSQ shows its options with ticks on the correct set; NAT shows the answer, tolerance, format and unit.
- The correction dialog shows the fields of the type typed in Question Type; changing the type re-reads the row as that type.
- KEY_CONFLICT rows offer "Import anyway" (R1). The CSV report keeps the file's row numbers.

## 8. Rules [PLAN]

### 8.1 Scoring (one implementation; `Decimal`; 2 decimal places)

| Type | Correct → | Wrong → | Unanswered → | Notes |
|---|---|---|---|---|
| MCQ | +marks | −negative | 0 | **unchanged** |
| MSQ, `ALL_OR_NOTHING` | exactly the correct set → +marks | any other set → **0** | 0 | counted as "incorrect" in the totals; never negative (D5) |
| NAT | `EXACT`: a = v; `ABS`: \|a − v\| ≤ amount; `RANGE`: min ≤ a ≤ max (inclusive) → +marks | −negative (rule-configurable, D6) | 0 | |

- Statuses: CORRECT, INCORRECT, UNANSWERED (unchanged vocabulary).
- Accuracy = correct ÷ attempted (unchanged).

### 8.2 Answer validation
- As in §6.5. An invalid answer is **never stored**, so it can never be scored.

### 8.3 Immutability
- `content`, `answer_key` and `question_type` live on the immutable version.
- The scheme and marks are frozen in `exam_versions.settings.marking`.
- The Student copy is written once per exam version.
- Corrections go through the audited correction flow only.

### 8.4 Duplicates (D11)

| Case | Bulk import | Paper build |
|---|---|---|
| Same type, content and key | `DUPLICATE` (skipped, as today) | refused ("Same question as position N", as today) |
| Same type and content, **different key** | flagged: the row is INVALID with code `KEY_CONFLICT`, naming the existing question and its key. You correct the key or the content, or choose **Import anyway** [OPEN R1] | flagged for review: "Same question with a different answer key" (never blocking, like the existing flags) |
| Different type, same text | not a duplicate | not a duplicate |

Today, an MCQ row with the same content but a different key is silently skipped as `DUPLICATE`. With D11 it is **flagged** instead. This is the only intended change to MCQ import behaviour.

## 9. APIs, summarised [PLAN]

| Endpoint | Change (additive) |
|---|---|
| `POST /questions`, `POST /questions/{id}/versions` | typed body (MCQ body unchanged) |
| `GET /questions`, `GET /questions/{id}` | adds `question_type`, `content`, `answer_key` (staff) |
| `POST /bulk-imports` etc. | unchanged shapes; rows carry typed data |
| `POST /papers`, `/papers/check` | unchanged; the review flags gain "different answer key" |
| exam plans / marking rules | rules accept the 3 types; MSQ `wrong` must be 0 |
| `POST /exams/{id}/answer-key-corrections` | typed key changes |
| Delivery (internal) | v2 fields `type`, `content`, `key`, `scheme` |
| Student `GET /tests/{id}/start` | adds `question_type` (real), `content`; never the key |
| Student `POST /attempts/{id}/answers` | validated (422) and canonicalised |
| Student result review | per-type review objects |
| Result internal API | new `GET /internal/v1/tests/{id}/results/questions` (question-level analytics) |

## 10. Institute Portal UX [PLAN]

- **Add/edit question:** a type selector (MCQ / MSQ / NAT). It is locked once the question exists, because a new version can't change the type.
  - MCQ: today's editor.
  - MSQ: 2–8 option rows (add/remove), a checkbox "correct" on each, a picture per option.
  - NAT: Answer, Answer format, Decimal places, Tolerance type + amount or min/max, Unit.
- **Question Bank:**
  - a type filter and a type badge in the list;
  - the preview renders MSQ (correct ticks) and NAT (answer ± tolerance) with pictures.
- **Paper builder and wizard:** the type badge; the duplicate panel shows the new "different answer key" flag.
- **Marking rules editor:** one row per (subject × type) present. The MSQ "wrong" mark is fixed at 0 and explained; NAT has a negative field.
- **Corrections:** a typed key editor.
- **Results:** a question-level analytics table.

## 11. Student Portal UX [PLAN]

| Type | Rendering | Input | Review (after the window) |
|---|---|---|---|
| MCQ | unchanged | radio | unchanged |
| MSQ | options A–H with pictures; the hint "Select all correct options" | checkboxes; "Clear response" | the student's set vs the correct set |
| NAT | the stem; a numeric box with the unit after it; the hint for the format/decimals | a text box with `inputmode="decimal"` and client checks (the server decides) | given value vs correct value (± tolerance) |

- Palette states, autosave, timer, refresh-restore and submission are unchanged.
- A server 422 on save shows "This answer isn't valid: …" and keeps the previous saved answer.
- Phone layout: the options stack; the numeric box is full width.

## 12. Test cases [PLAN]

**Golden vectors** (`contracts/question-types/golden.json`), run by the Institute and Student suites. Each entry is (type, scheme, key, answer, marks, negative → status, marks_obtained):
- **MCQ:** right; wrong; blank; lowercase normalised; "Z" rejected; "AB" rejected.
- **MSQ:**
  - exact set; superset; subset; disjoint; blank → 0;
  - order-independent (`"CA"` = `"AC"`);
  - repeated letter rejected; letter outside options rejected; 8 options;
  - 2-option minimum enforced at creation.
- **NAT:**
  - exact; ABS edges (v ± amount inclusive, just outside); RANGE edges;
  - negative numbers; `-0` = `0`; trailing zeros `12.50` = `12.5`;
  - integer format rejecting `3.5`;
  - `1e3`, `NaN`, `Infinity`, `1,000`, empty, 65+ characters rejected;
  - negative marking applied; precision limits.

**Institute integration:**
- manual create/version per type, with type locked (409 on change);
- picture rules for MSQ options;
- Excel: each sheet, the old `Questions`-only file, mixed sheets, every validation error, pictures in A–H;
- Word: per-table, old 16-column table, ambiguous header error;
- preview correction per type;
- D11 cases (§8.4);
- paper build/check with mixed types;
- PDF and answer-key PDF;
- generation guard (`STUDENT_QUESTION_TYPES`); RULES coverage per type; MSQ wrong forced 0;
- delivery v2 golden payload with MCQ entries byte-identical to v1;
- corrections per type;
- copy-to-platform keeps the typed content;
- three-scope isolation for typed questions and option pictures.

**Student integration:**
- PUBLISH v1 and v2;
- `/start` contains no key field for any type (asserted on the JSON);
- answer save: every invalid vector rejected and nothing stored; canonical storage;
- mark-for-review keeps MSQ/NAT answers;
- evaluation equals re-evaluation (same vectors);
- `test_result_questions` written and replaced on re-evaluation;
- review payloads per type;
- ranking ties: 1, 2, 2, 4 and stable order;
- question-level analytics endpoint.

**Regression:**
- the full existing PostgreSQL suite and frontend suite unchanged;
- a re-evaluation of **every existing result** on a disposable copy gives identical totals before and after (read-only proof on real data shapes).

**Migrations:** each new revision on a disposable PostgreSQL (upgrade, guarded downgrade, adoption, `alembic check`); the 437-row backfill keeps IDs.

**Frontend (vitest):**
- the editors (type switch, validation messages);
- the bulk preview per type;
- the marking editor (MSQ wrong fixed);
- the Student renderers (MSQ checkboxes, NAT box, 422 message, review views), keyboard labels, phone width.

**Browser end to end (dev branches, after approval):** one exam with MCQ + MSQ + NAT:
- created manually and by Excel and Word;
- built into a paper and generated;
- answered by a student [OPEN R6: a student account is needed];
- submitted, evaluated, then result, review, ranking with a tie, and analytics;
- a key correction then re-evaluates.

## 13. Rollback plan [PLAN]

| Layer | Rollback |
|---|---|
| Feature | `STUDENT_QUESTION_TYPES=MCQ_SINGLE` stops new MSQ/NAT exams immediately. The Institute UI hides the type selector when the paper service reports MCQ-only. |
| Code | Image tags before each rebuild (`:pre-qtypes-<date>`) → retag + `up -d --no-deps`. MCQ paths are byte-compatible, so older code reads MCQ data unchanged. |
| Database | Guarded downgrades (refuse while MSQ/NAT data exists). Restore from the direct-endpoint backup as a last resort. Widening `selected_answer` is reversible only while no answer is longer than 10 characters. |
| Data created by the feature | MSQ/NAT questions can be archived (soft delete); exams cancelled through the existing flow. |

## 14. Implementation phases (approved order)

| Phase | Work | Gate |
|---|---|---|
| T1 Shared model and validation | registry, golden vectors, models, migrations (disposable PG), `marking.py` | unit + migration tests |
| T2 Student backend | test/attempt/result/ranking changes, D12 validation, single scorer, `test_result_questions`, ties | Student tests + golden vectors |
| T3 Institute backend | questions API, paper build, PDF, generation guard, delivery v2, corrections, analytics API, copy-global | integration suite |
| T4 Templates | Excel sheets and Word tables, reader, validation, examples | template tests (built files read back through the real reader) |
| T5 Frontends | Institute editors, bank, marking editor, corrections, analytics; Student renderers and review | vitest, tsc, eslint |
| T6 Evaluation, results, ranking integration | end-to-end evaluation, re-evaluation, analytics | integration |
| T7 Full regression | full PostgreSQL suite, all chains, `alembic check`, frontend suites, gateway/secrets, read-only re-evaluation proof | report |
| T8 Deployment (separate approval) | §5 steps 2–7, browser verification | report |

## 15. Open points for you

| # | Question | Proposed answer |
|---|---|---|
| R1 | Bulk import, same question with a different key | **Flag the row (KEY_CONFLICT, named existing question); allow "Import anyway" per row**, recorded in the audit |
| R2 | Paper upload (CSV/XLSX/DOCX/PDF + answer key), photo and review drafts | **Stay MCQ-only in this work** (the bulk templates and the manual editor cover MSQ/NAT) |
| R3 | Tie ranks | **Standard competition ranking 1, 2, 2, 4** (dense 1, 2, 2, 3 is the alternative) |
| R4 | NAT number limits | **12 significant digits, ≤ 6 decimal places, no exponent; inclusive tolerance** |
| R5 | MSQ never negative in any marking mode (D5: "otherwise zero") | **Yes**: the MSQ "wrong" mark is fixed at 0 everywhere |
| R6 | Student account for the end-to-end browser test | **You provide one test student credential (or approve creating one on the dev branch)**; otherwise the Student journey stays verified by integration tests only |

## 16. Implementation status (T1–T7 done; stages A–D deployed to the dev app 2026-11-21)

Deployment 2026-11-21 (approved: stages A–C): backups `~/backups/neon-20261121-pre-qtypes` (restore-verified); Student `g1`, `h1`,
`i1` and Institute paper `0015` applied to the dev branches; Student test / attempt / result / ranking, Institute paper + bulk-import
worker, exam + exam worker, gateway and both frontends recreated; rollback images tagged `:pre-qtypes-20261121`. Counts unchanged
(452 bank questions, 417 Student questions, 10 results) and the 172 existing answers re-score identically. Stage D (approved
separately): `STUDENT_QUESTION_TYPES=MCQ_SINGLE,MCQ_MULTI,NUMERICAL` in `deploy/institute/institute.env`, exam service and exam worker
recreated (rollback: set `MCQ_SINGLE` and recreate them; the previous env file is kept beside the Neon backups).


Everything below runs only in the working tree and on disposable databases. No Neon branch, live container or migration was touched;
`STUDENT_QUESTION_TYPES` defaults to `MCQ_SINGLE`, so the running portal cannot generate MSQ / NAT exams until it is enabled.

| Area | What is in place |
|---|---|
| Rulebook | `institute_common/core/question_types.py`, copied byte-identical to the Student test / attempt / result services; golden vectors `contracts/question-types/golden.json` |
| Delivery contract | `contracts/question-types/delivery_v2.json`: pinned by the Institute exam-service test, published by the Student test-service test |
| Institute | migration paper `0015_question_types`; manual MSQ / NAT editor and new versions (type locked); one bulk template for every type with KEY_CONFLICT + audited "Import anyway"; paper review with the key; typed PDFs; paper corrections refuse typed positions (edit in the bank); generation guard; payload v2; typed key corrections; question analysis (full-scope readers); copy to platform; competition ranking in results |
| Student | migrations test `g1`, attempt `h1`, result `i1`; answers validated on save (422 `INVALID_ANSWER`); one scorer for evaluation, re-evaluation, review and per-question rows; ranking from stored per-question marks with competition ranks; exam screen with MSQ checkboxes and a NAT number box; typed review |

Deviations from the plan above:
- §7: one template for all types (owner decision 2026-11-21) instead of a sheet / table per type.
- §3/§4: MSQ / NAT versions keep the four MCQ option columns as `''` (not NULL); only `correct_option` became nullable.
- Question analysis is available only to readers who see every student of the exam (provider admins, platform roles, the exam's
  branch admins); teachers and Global Test contributors keep their scoped result rows.
- The marking-rules editor's live totals use the MCQ rule (the page does not know each paper's MSQ / NAT counts); generation computes
  the real total.
- R6 closed 2026-11-21 (approved): test student STU000974 (QTypes Tester) enrolled by the test Provider Admin in Orbit / Chennai
  Branch with no batch (assign tests to it with audience "Selected students"); credential in a 600 file outside the repository.

### 16.1 End-to-end acceptance on the dev app (2026-11-21, through the portal APIs; Chrome was not connected)
A Physics-I MCQ, MSQ and NAT (Orbit / Chennai Branch, all titled "[QTypes test] …") were created, built into a paper, approved and
published (question paper and answer-key PDFs checked), and scheduled for test student STU000974 with uniform +4 / −1 marking. The
student received no keys; invalid answers (`1e3`, MSQ `AE`, MCQ `Z`) were refused with 422; answers MCQ A, MSQ A (of A, C), NAT 9.84
scored 4 + 0 + 4 = 8 / 12 (MSQ never negative, NAT tolerance inclusive). Review after the window: "A, C" and "9.8 ± 0.05"; ranking 1;
Institute results 8 / 12 rank 1; question analysis per type. One defect found and fixed: the answer-key PDF failed for NAT
questions (500); fixed, covered by a new download test, paper service redeployed. The test data stays on the dev branch.
