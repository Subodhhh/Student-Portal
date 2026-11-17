# Scientific notation in question-paper import

Engine version **2.5.0** (`inst_paper/services/math`). The engine only changes notation: it never simplifies, evaluates, balances,
corrects or completes content. Anything it cannot read in exactly one way is kept as typed and shown to the provider for a decision.

## Workflow

1. **Upload with preview** (`POST /api/v1/branches/{branch}/subjects/{subject}/papers/upload`, form field `preview=true`).
   - The file is extracted and recognised.
   - The response lists every question field with its outcome, the text to be stored, reason codes and the original.
   - Nothing is stored.
2. **Provider review** (Question Bank → Import Processing → *Validate & preview*).
   - Fields are shown typeset with KaTeX.
   - Each flagged field needs a decision: *keep exactly as typed* or *correct it*.
   - A correction is previewed live and must render; the server checks it again.
   - A formatted field can also be kept as typed instead.
3. **Import** (the same file, plus `review_token` from the preview and `resolutions` JSON:
   `{"<question number>:<field>": {"action": "keep_original"|"edit", "text": "..."}}`).
   - The token binds decisions to the exact file, answer key, subject and engine version (otherwise `409 REVIEW_STALE`).
   - Any field still needing review rejects the whole import (`422 REVIEW_REQUIRED`). The rejected upload is kept with its reasons.
   - Accepted content becomes a normal VALIDATED paper version, then approval and publication as before.
4. **Delivery**:
   - Question text is stored and delivered with LaTeX segments in `\( … \)`.
   - Both portals render it with the shared `MathText` component (KaTeX 0.19.1, strict, untrusted commands refused).
   - Anything KaTeX refuses is shown exactly as stored.
   - Paper PDFs turn the LaTeX back into readable text (`math/plain.py`).

The CSV `topic` column never decides the domain. The domain hint comes from the authoritative subject id through the explicit
`RECOGNITION_SUBJECT_CATEGORIES` mapping (`domain_of_subject_id`); subject names are never parsed, and an unmapped subject (e.g.
Biology) gets no subject conventions.

## Upload template, pictures and validation (engine 2.5.0)

- **Template columns:** `question`, `question_image`, `option_a`, `option_a_image` … `option_d`, `option_d_image`, `correct_answer`,
  `positive_marks`, `negative_marks`, `chapter`, `topic`, `sub_topic`, `explanation`. Older templates (`correct`, `marks`, no image
  columns) keep working. The Excel template is served at `/question-template.xlsx` by the Institute frontend.
- **Rules (all four subjects):** a question needs its text, its picture or both; each option needs exactly one of its text or its
  picture. The correct answer must be A–D, marks numeric. Every problem names the row and the column; any problem rejects the whole
  paper (nothing is silently skipped).
- **Where pictures come from:** `.xlsx` pictures placed over a cell (drawing anchor) or in a cell (Excel 365 "Place in Cell"); or a
  `.zip` package with exactly one `.csv` whose image columns name files in the package (exact names, folders allowed). Missing, extra,
  damaged, oversized, misplaced (outside an image column) or duplicated (two identical options) pictures are errors.
- **Checks:** PNG/JPEG/GIF/WebP sniffed from the bytes, ≤ 5 MB and ≤ 40 Mpx each, fully decoded inside the isolated extraction process;
  `.xlsx`/`.zip` uploads up to 25 MB (`MAX_PACKAGE_UPLOAD_BYTES`), text papers 2 MB.
- **Storage:** `question_image_assets` (BYTEA, one row per provider and SHA-256; `storage='db'` is the abstraction point for a future
  object store); `question_versions.stem_image_id` / `option_[a-d]_image_id`. Pictures are stored when a paper is previewed;
  `python -m inst_paper.sweep_stored_files --unused-pictures [--execute]` removes ones no question uses after 7 days.
- **Access:** `GET /api/v1/question-images/{asset_id}` for staff of the owning provider (404 otherwise). Students get pictures only
  through the Student test service: the exam outbox uploads missing pictures (by checksum) before the exam, and
  `GET /tests/{test_id}/images/{checksum}` serves a picture to a student in that test's audience, when one of its questions uses it.
- **Pictures are never read.** OCR and picture recognition were removed on 2026-11-16: every picture is stored, previewed and delivered
  exactly as uploaded, and nothing is said about its contents. The notation engine works on typed text only.

## Every reading of an ambiguous expression (engine 2.5.0)

- A number glued to a function name with an argument after it (`sin3 x`, `cos2x`, `log2x`, `sin2θ`) is `FLATTENED_FUNCTION_POWER`:
  never converted (it used to be read as `sin 3x`). `ln2/k`, `sin30°` (nothing after the number) still convert.
- A lowercase letter with a glued number next to `+`, `−`, `=` (`x2 + 2x + 1`) is now a formula candidate (`AMBIGUOUS_NUMBER_ADJACENT`).
- `suggest.candidate_set` lists every reading of the declared grammar: each doubtful place (power / index / product, function power /
  base / argument, Leibniz derivative digits, fraction extent, a letter after a bracket), **all combinations**, uniform readings first.
  Each reading is the whole field recognised again (the validators included); readings that store the same thing are one reading.
  Up to `MAX_CANDIDATES = 81` combinations are checked; above that the preview says the list is incomplete and how many readings exist.
  A flattened `limx→0` is written `lim_(x→0)` first (one reading).
- The preview shows every reading typeset (six at first, "Show all N"), and says "These are all N ways it can be read" or that the
  list is incomplete. The chosen reading's label is recorded (`selected_reading`) with the candidate sets and the engine version.

## Outcomes and reason codes

| Outcome | Meaning |
|---|---|
| `CONVERTED` | Recognised; passed the meaning-preservation check and the LaTeX allow-list |
| `PLAIN_TEXT` | Ordinary text; stored unchanged |
| `NEEDS_REVIEW` | Ambiguous or unsupported notation; kept as typed until the provider decides |
| `EXTRACTION_ERROR` | Content that could not be extracted (Word image, unsupported equation part, unknown symbol font) |

Reason codes:
- **Limits:** `BOUND_TYPED_ON_LINE`, `BOUND_INCOMPLETE`.
- **Ambiguous notation:** `FLATTENED_FUNCTION_POWER`, `AMBIGUOUS_FRACTION`, `AMBIGUOUS_POWER`, `AMBIGUOUS_NUMBER_ADJACENT`, `AMBIGUOUS_DERIVATIVE`, `AMBIGUOUS_MATRIX`, `AMBIGUOUS_NOTATION`.
- **Chemistry:** `AMBIGUOUS_CHEMICAL_CHARGE`, `UNKNOWN_ELEMENT_SYMBOL`, `AMBIGUOUS_DOMAIN`.
- **Segmentation and size:** `JOINED_TO_TEXT`, `EXPRESSION_TOO_COMPLEX`, `UNSUPPORTED_NOTATION`.
- **LaTeX:** `LATEX_WITHOUT_DELIMITERS`, `LATEX_VALIDATION_FAILED`, `STRUCTURE_NOT_PRESERVED`.
- **Word content:** `UNSUPPORTED_WORD_EQUATION`, `UNSUPPORTED_OBJECT`, `UNSUPPORTED_SYMBOL`.

## Validation of every conversion

- **Meaning preservation (`safety.py`, independent of the parser).** The LaTeX is read back. Its symbols, their order and their script level (normal, superscript, subscript, nested) must equal the source. This catches:
  - lost or added symbols and changed signs;
  - superscripts turned into subscripts, and the reverse;
  - Greek turned into Latin;
  - lost limits;
  - reordered matrix entries.

  Grouping (which brackets a fraction covers) is guaranteed by the grammar, which refuses every reading it cannot prove.
- **LaTeX allow-list.** Converter output may use only the commands it is allowed to emit. Provider-typed LaTeX and Word equations may use only `PROVIDER_COMMANDS`, all verified to render in KaTeX 0.19.1 strict mode.
- **Limits must be explicit.** `∫₀π`, `∫₀1`, `∫₀∞`, `∫ᵃb`, `∑ᵢ₌₁n` and `∏ₖ₌₁n` (a limit typed on the line) are flagged, never read as a factor.

## Supported input

| Format | What is read |
|---|---|
| CSV | UTF-8 template columns. Header spellings such as `Option A`, `Negative`, `correct_answer` are accepted. All Unicode notation is kept |
| XLSX | One visible sheet. Superscript/subscript **rich-text runs** become x², a₁ or `^( )` |
| DOCX | Template-header tables (`Question`, `Option A` … `Negative`) or numbered blocks. Superscript/subscript runs. Symbol-font Greek and operators. **Native equations** (OMML) converted to LaTeX: fractions, scripts, pre-scripts, radicals, ∫ ∬ ∭ ∮ ∑ ∏ ⋃ ⋂ with limits, delimiters, functions, lim/max/min, accents (hat, bar, tilde, dot, vector), bars, matrices, equation arrays, boxes, under/over-braces and arrows. Unsupported equation parts and images become `EXTRACTION_ERROR`, never dropped |

Unchanged rules:
- merged cells, formulas, macros and embedded OLE objects are rejected;
- PDF keeps the existing text-layer reader (no recognition-specific work);
- OCR and handwriting are out of scope.

## Recognised notation

- **Mathematics:**
  - Unicode and caret powers and indices;
  - fractions with explicit grouping, nested fractions and roots;
  - integrals (single to triple, contour), sums and products with limits;
  - limits, including `limₓ→∞`;
  - derivatives and partial derivatives;
  - matrices, determinants and absolute values;
  - trigonometric, inverse, logarithmic and exponential functions;
  - relations, set symbols, arrows, Greek letters, primes, degrees and vectors (a⃗).
- **Physics:**
  - quantities with units (`10 m s⁻¹`, `9.8 m/s²`, `100 Ω`, `10 μF` when written as a quantity);
  - unit groups without a number (`mol L⁻¹ s⁻¹`);
  - scientific notation;
  - letter equations typed without spaces (`PV=nRT`, `v=u+at`, `Q=It`, `F=|q|vB`);
  - word subscripts (`Vᵣₘₛ`).
- **Chemistry** (`chem.py`):
  - case-sensitive element symbols (all 118, plus D/T);
  - formulas, groups and hydrates (`CuSO₄·5H₂O`);
  - charges written number-then-sign (`SO₄²⁻`, `[Fe(CN)₆]³⁻`);
  - isotopes (`²³⁵U`, `²³⁸₉₂U`), state symbols, coefficients, electrons;
  - reactions with → / ⇌, also typed without spaces;
  - organic bonds (`CH₂=CH₂`).

  Output uses `\mathrm{}` (mhchem not used). A formula is recognised only with chemical evidence: in Chemistry papers a count, charge, state or isotope; elsewhere only a charge or a two-letter element with a count.

## Units, variables and dimensions (engine 2.1.0)

- **Unit after a number and a space.** The letter is read as a unit only when the context proves it:
  - another unit follows, or the formula ends;
  - there is a slash (`2 m/s²`);
  - the other side of `=` or `+` is a quantity (`5 × 10⁻⁷ m = 500 nm`, `2 m + 3 m`);
  - the other side is a counted amount (`1 h = 3 half-lives`).

  With a bare number on the other side (`3 m = 6`) both readings are possible, so the expression is flagged `AMBIGUOUS_UNIT`. Without a space (`3m`), or with a letter that is never a unit symbol (`2 x`), the letter is a variable.
- **Units without a number.** A group such as `N m² C⁻²` or `mol L⁻¹ s⁻¹` is read as units when it contains a unit word or a negative power. A group of capitals only (`M L T⁻²`, `A B⁻¹`) is never assumed to be units. `kg/m³`: a unit word over a unit.
- **Dimensional formulas (decision D4).** Square brackets containing only `M L T I N J Θ`, each with an optional integer power, and at least one power written, are typeset upright: `[M L² T⁻²]`, `[M L T⁻² Θ⁻¹]`. These are not dimensional formulas and are left as before:
  - `[MLT]` (no power);
  - `MLT⁻²` (no brackets);
  - `[ABC²]`;
  - `[M L T⁻² K⁻¹]` (K is the kelvin; the temperature dimension is Θ).
- **Boundaries.**
  - **Differentials:** after an integral sign, `d` plus a capital letter is a differential (`∮ E · dA` keeps the dot product inside the integral).
  - **Glued operators:** a formula cut by an operator glued to the word before it (`word/x`) is never converted from the operator on. Camel-case letter groups next to `/` or `×` (`nRT/V`) are variables.
  - **Numbers ending a phrase:** a bare number ending a phrase, followed by `=` and a quantity or a different number (`from 0 to 2 = 8 J`), is never typeset as the false equation `2 = 8 J`.

## Engine 2.2.0 changes (from the independent Holdout D discrepancy analysis; all tests synthetic)

- **One notation, one reading (S4).** `x^2`, `s^-1`, `10^-7` and `a_1` (an ASCII integer script, not followed by a digit or letter) are the same tokens as `x²`, `s⁻¹`, `10⁻⁷`, `a₁`. Units, dimensions, derivative orders and the chained-power rule therefore behave identically for CSV and for Word/Excel formatting. A permanent CSV/XLSX/DOCX parity test enforces this.
- **Chained scripts are flagged (S1).** `3^2^2`, `a^b^c`, `x²^3` and `a_i_j` are flagged `AMBIGUOUS_POWER`. Explicit brackets (`3^(2^2)`, `(3^2)^2`) convert.
- **Office script runs.** Two adjacent superscript (or subscript) runs are kept apart and flagged the same way:
  - in Excel, any two separate runs (Excel never splits runs by itself);
  - in Word, runs whose formatting differs. Runs with identical formatting are one script, because Word splits runs for spelling and revision marks. Two identically formatted Word runs therefore display, and are read, as one exponent.
- **Subscript and superscript on one base (S2)** form one stacked node: `[t²]₁³` → `\left[t^{2}\right]_{1}^{3}`; `x₁²` → `x_{1}^{2}`.
- **Bracketed numbers (S3).** `(4)(5) + …` is a product, not a list label. A converted piece never starts with a binary operator that continues the text before it.
- **Units (S5, S6).** A unit word carries the unit symbols after it (`40π rad s⁻¹`). A unit letter after an algebraic term (`k = 5t N`, `P = I V`) is flagged `AMBIGUOUS_UNIT`. Glued prefix + unit:
  - `6 ms` is the millisecond;
  - `6 ms⁻¹`, `6 ms^-1` and `6 ms-1` are flagged (metre per second or per millisecond).

  A glued letter after a unit is never taken as another unit. In a relation whose other side is only units, a lone unit letter is a unit (`J s⁻¹ = W`).
- **`÷` followed by `/` (S7)** is flagged `AMBIGUOUS_FRACTION`.
- **Detectors (F1).** Powers typed with a hyphen after units (`6 ms-1`, `4 m s-2`) are flagged `INLINE_EXPONENT`. Dimensional formulas without brackets or superscripts (`MLT-2`) are flagged `DIMENSION_NOTATION`. Ranges and labels (`10-12`, `2019-20`, `XI-A`, `NH-48`) stay plain.
- **Coverage.**
  - Unit vectors (`î`, `k̂`) and Greek vectors (`ω⃗`).
  - `∪` and `∩`.
  - A quantity over a quantity (`12 m / 4 s`).
  - Lettered subscript labels (`C_eq`; `x_ij` stays italic indices) and caret derivative orders (`d^2x/dt^2`).
  - A whole option that is exactly a quantity (`7 J`, `4.5 eV`), in Physics and Chemistry papers only.
- **Flag quality.**
  - A spaced unmatched `(` starts a prose remark.
  - Letter groups glued to scripts are variables (`½at²`, `Kq₁q₂/d`).
  - An apostrophe in a name (`D'Alembert`) is not a prime; `y''` is.
  - A signed matrix entry separated only by a space (`|1 -2; 3 4|`) is flagged.
- **Validator.** Beyond the symbol/order/level check, every conversion must pass structural invariants:
  - no script on an already-scripted base;
  - no unit followed by a unit letter set as a variable;
  - no fraction directly after `÷`;
  - no span starting with a continuing binary operator;
  - no negative-power unit letter set as a variable;
  - the same reading with `^` powers as with superscripts.

  Word equations are checked against the equation's own symbols, read independently from the OMML tree. A mismatch becomes `UNSUPPORTED_WORD_EQUATION` (shown as extracted, never imported as converted).
- **Importer.** Duplicate options and duplicate questions are compared case-sensitively (Co ≠ CO, Hf ≠ HF), with whitespace collapsed and Unicode NFC composition, by one shared key (`extraction.same_text_key`). It is used for CSV, XLSX, DOCX, photo drafts, paper corrections and the re-check after provider review.

## Known limitations (engine 2.5.0)

- **Not yet independently validated.** Holdout D (consumed) found the defects fixed in 2.2.0. A new independent holdout (E) is needed before relying on the engine.
- **No general grouping verifier.** The validator checks symbols, order, script levels and the structural invariants above, but has no independent re-parser of grouping. Fraction extent and bracket grouping still rely on the grammar refusing every reading it cannot prove.
- **Word split runs.** Two identically formatted adjacent superscript runs in Word are read as one exponent, as they display. Excel keeps every run boundary.
- **More review flags.** The safer unit rules flag legitimate products such as `P = I V` and `W = F s`, and `6 ms⁻¹`, which is common in Indian textbooks.
- **Word equations:** a function name typed in an ordinary italic run (`log`) stays italic, and a `°` inside an equation superscript is raised twice.
- **Kept as typed without a flag (not detected):** `a/b/c`, ASCII formulas (`H2O`, `CuSO4.5H2O`, `Fe2O3`), inline subscripts (`Kc`, `Ksp`, `E°cell`), `[Ar] 3d⁶4s²`, `km/h`, `10 μF` inside a sentence, and simple negative numbers. Plain text renders them as typed.
- **Partial conversion:** a formula next to an unrecognised word is converted only in part, with the rest left as text. Example: `W = [x³] from 0 to 2 = 8 J` typesets only `W = [x³]`.
- **Unnecessary flags** for some correct notation (sent to the provider, never imported unreviewed):
  - concentration powers `[NH₃]²`;
  - `ΔfH°`, `pH = −log[H⁺]`, `5e⁻` in equations, `Zn²⁺(1 M)`;
  - `√(2meV)`;
  - limits written in words;
  - `3 m = 6` (unit or variable).
- **Out of scope:** manual question editing (bank editor, paper corrections) and photo drafts don't run recognition. Text typed there with `\( \)` is rendered as written.

## Persistence

- **Schema:** one additive nullable column, `paper_uploads.recognition` (JSONB), migration `0006_upload_recognition` (paper service).
- **Contents per field:**
  - the original (`source`, with extraction markers) and the readable original;
  - the stored text, outcome, reasons and spans;
  - the provider's decision and any edit;
  - for the whole record: engine version, domain, subject, review token and the deciding staff member.
- **Original file:** kept as before (`stored_files`).
- **Shared databases:** the migration must be applied (with approval) before this code runs against a database. Without the column, paper uploads fail.
