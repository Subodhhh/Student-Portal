# Chapters, Topics & Aliases — smart matching and management (plan)

Status: **X1–X5 built and tested locally (2026-11-22); X6 (Neon backup + migration 0017 + rebuild) awaits approval.** The Syllabus
Library (pre-filled CBSE / ICSE / State boards / NEET / JEE lists) is deferred by the owner.

### Implementation notes and deviations (2026-11-22)

* **Scoring**: `0.55 × word overlap + 0.45 × letter similarity`, misspelt words of 5+ letters count as shared words (≥ 0.8 alike),
  and a name wholly contained in the other is always offered (never pre-selected on that alone). Pre-select needs ≥ **0.85** and a
  0.10 lead (plan said 0.90; the golden cases in `contracts/taxonomy/match_cases.json` set the threshold).
* **`question_taxonomy_events` was not added**: every change (create, rename, retire/restore, alias add/remove, merge, request
  decisions, unlinked resolutions) goes to the existing audit log with actor and IP, which already is append-only.
* **Where records do not exist yet, typed names are still accepted** (Add question / Classify, and platform Bulk Upload): a subject
  whose chapters are not set up keeps working; its names wait in the "Unlinked names" queue. Provider Bulk Upload keeps its stricter
  rule (every name must be a record), as before.
* **Restore** of a retired record was added (`PATCH …/nodes/{id}` `status: ACTIVE`).
* **Search filters** still list the labels used on questions; since linked questions carry the official name, these are the records'
  names in practice. A records-only filter is left for later.
* API parameter names: `provider_id` (absent = platform list) instead of `scope`; `value` instead of `q`.

## 1. Goal

When anyone types a Chapter, Topic or Subtopic name — in Bulk Upload, or while adding or classifying a question — the portal
recognises the real record even when the spelling differs ("Newton's 1st Law", "Newtons1st Law of Motion",
"newton first law of motion"), shows **"Did you mean …?"**, and never silently creates a second name for the same thing. Admins get
one place to see and tidy every chapter/topic/subtopic of every subject, with their aliases, and to merge duplicates.

## 2. Owner decisions (2026-11-22)

| # | Decision |
|---|---|
| A1 | One strong match: **pre-select it and still ask** (never linked without a person confirming). |
| A2 | **Provider Admins approve aliases** of their provider. Anyone else's "remember this spelling" becomes an **alias request** for the Provider Admin. Platform aliases: platform administrators. |
| A3 | **Include the platform**: platform (GLOBAL) questions get their own chapter/topic/subtopic list, like providers. |
| A4 | Matching only ever **suggests**; an exact name or approved alias links automatically (as today); nothing is created, renamed or merged without a person choosing it. |

## 3. Current state (verified)

* Records: `question_taxonomy_nodes` (Chapter → Topic → Subtopic, per provider and subject; `status` ACTIVE / RETIRED, never deleted)
  and `question_taxonomy_aliases` (approved alternative names). Both require a provider today.
* Matching (`inst_paper/services/taxonomy.py`): exact on `label_key` (trim, collapse spaces, NFKC, case-fold) by name or alias;
  suggestions by letter similarity (`difflib`, cutoff 0.6, 5 suggestions).
* Server APIs exist: list / create / rename / retire records, add an alias, match a name.
* Bulk Upload preview already stops unmatched rows (`TAXONOMY_UNMATCHED`) and lets a `taxonomy.manage` holder MAP (this import),
  ALIAS (always) or CREATE.
* Gaps: no management screen; suggestions miss "1st"/"First", "Law"/"Laws", word order; manual Add question / Classify save any text
  unlinked; no merge; platform questions have only free text.
* Data today (Institute dev branch): 2 chapters, 2 topics, 0 aliases; 1 provider question and 11 platform questions carry a chapter
  name linked to no record.

## 4. Design

### 4.1 Matching engine (one implementation, `inst_paper/services/taxonomy_match.py`)

1. **Match key** (for suggestions only; the stored `name_key` / `alias_key` stay as they are): `label_key`, then
   * punctuation and apostrophes removed ("Newton's" = "Newtons");
   * ordinals and numbers as words: 1st/first, 2nd/second, 3rd/third, … (and "1" = "one" inside names);
   * simple singular/plural folding ("laws" = "law", "motions" = "motion");
   * filler words ignored: of, the, a, an, and, in, on, for, to.
2. **Score** each candidate record (its name and every alias) in the same place of the hierarchy:
   `0.6 × word-overlap (order ignored) + 0.4 × letter similarity` on match keys, 0..1. Equal match keys score 1.0.
3. **Outcome**

   | Situation | Result |
   |---|---|
   | exact `label_key` on a name or approved alias | linked (as today) |
   | one candidate ≥ 0.90, next one ≥ 0.10 lower | **"Did you mean X?"** with X pre-selected (A1) |
   | several candidates ≥ 0.60 | **dropdown** of all, best first, with the score shown as High / Medium |
   | none ≥ 0.60 | choose from the full list, or (admin) create |
   | the name exists **under another chapter** | said explicitly: "‘Inertia’ is a topic of *Newton's Laws*, not of *Kinematics*" |

4. **Golden tests** (`contracts/taxonomy/match_cases.json`): real spelling variants with their expected result, run on every
   change, so suggestion quality can never silently drop.

### 4.2 Where it is used

| Place | Behaviour |
|---|---|
| **Bulk Upload preview** | One decision per distinct unknown spelling, not per row ("used in 37 rows"); dropdown per §4.1; **Remember this spelling**: Provider Admin → alias now; anyone else → alias **request** (their choice still applies to this upload at once). |
| **Add question / Classify** | Live "Did you mean …?" under Chapter, Topic, Subtopic while typing; saving an unmatched name is refused with the same choices; a non-admin may also send an alias request. |
| **Search filters** | The Chapter/Topic/Subtopic filters list the records (plus their questions' counts), not raw typed text. |

### 4.3 "Chapters & Topics" tab (Question Bank & Papers)

`Question Bank | Papers | Import Processing | Bulk Upload | Chapters & Topics`

* Subject picker (every subject the provider offers; platform admins can switch to the platform list), then a searchable tree
  Chapter → Topic → Subtopic: name, aliases, question count, status.
* Actions (Provider Admin; platform admins for the platform list): add, rename, retire / restore; add or remove an alias;
  **Merge** a duplicate into the real record (its questions are relinked, its name becomes an alias of the kept one, it is retired;
  audited, and listed in the record's history).
* **Alias requests** queue: who asked, the spelling, the record they chose, where it came from; Approve (creates the alias) or Reject
  (with reason). The requester's bell tells them the outcome.
* **Unlinked names** queue: names typed on existing questions that match no record, grouped by spelling with suggestions; resolving
  one links every question that uses it (and can remember it as an alias).
* Everyone else with `question.read` sees the tree read-only, so teachers know the official names.

### 4.4 Platform list (A3)

`provider_id` becomes nullable on nodes and aliases (NULL = platform). Platform records get their own unique indexes (one platform
chapter name per subject, one child name per parent), exactly like platform pictures in migration 0014. Platform questions link to
platform records; Bulk Upload into the platform bank uses the platform list (today it keeps typed text unvalidated).

## 5. Data model — paper migration `0017_taxonomy_aliases` (additive)

| Change | Notes |
|---|---|
| `question_taxonomy_nodes.provider_id`, `question_taxonomy_aliases.provider_id` → nullable | NULL = platform; partial unique indexes for platform names / aliases |
| `question_taxonomy_nodes.merged_into_id` (FK, nullable), status `MERGED` | a merged record keeps existing; old links are moved, its name becomes an alias |
| new `question_taxonomy_alias_requests` | node, alias text/key, requested_by, source (BULK_UPLOAD / QUESTION / CLASSIFY, job/question id), status PENDING / APPROVED / REJECTED, decided_by / at / reason |
| new `question_taxonomy_events` (append-only) | create / rename / retire / alias / merge / request decisions, with actor and IP (also audited) |

Existing rows keep every value. Downgrade refuses while platform records, merged records or requests exist.

## 6. APIs (paper service; gateway regenerated)

* `GET /question-taxonomy/tree?subject_id=&scope=` — the tree with aliases and counts.
* `GET /question-taxonomy/suggest?subject_id=&level=&parent_id=&q=&scope=` — §4.1 result (used live by the forms).
* `POST /question-taxonomy/nodes/{id}/merge` `{into}` · `DELETE /question-taxonomy/aliases/{id}`.
* `POST /question-taxonomy/alias-requests` · `GET …/alias-requests?status=` · `POST …/alias-requests/{id}/approve|reject`.
* `GET /question-taxonomy/unlinked?subject_id=` · `POST /question-taxonomy/unlinked/resolve` `{label, level, node_id, remember}`.
* Question create / classify: refuse an unmatched chapter/topic/subtopic with `TAXONOMY_UNMATCHED` + suggestions (same shape as Bulk Upload).

Permissions: reading the tree — `question.read`; managing records and approving — `taxonomy.manage` (Provider Admin for its provider;
platform administrators for the platform list); requesting an alias — anyone who may add questions there.

## 7. Phases

| Phase | Work | Gate |
|---|---|---|
| X1 | Matching engine + golden variants; `suggest` API | unit tests, golden cases |
| X2 | Migration 0017 (platform list, merge, requests, events) on disposable PostgreSQL | migration tests, `alembic check` |
| X3 | Bulk Upload: one decision per spelling, dropdown, remember / request | integration + frontend tests |
| X4 | Add question / Classify: live suggestions, unmatched refused | integration + frontend tests |
| X5 | Chapters & Topics tab: tree, manage, merge, alias requests, unlinked queue, filters from records | frontend + integration tests |
| X6 | Full regression (PostgreSQL suite, frontend), then localhost deploy with separate approval (Neon backup + migration) | report |

## 8. Risks and how they are handled

* **Wrong suggestion accepted** → never automatic (A1, A4); merges and aliases are audited and can be undone (remove alias,
  un-retire); the merged record is never deleted.
* **Old clients / existing questions** → nothing changes until someone resolves a name; existing links stay.
* **Platform list** → platform questions' typed names stay as text until resolved in the Unlinked queue.
* **Language** → the engine is for English names; other scripts still match exactly and by letter similarity.
