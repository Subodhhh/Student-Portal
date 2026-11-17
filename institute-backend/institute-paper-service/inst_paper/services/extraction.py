"""Question-sheet extraction. Treats the upload as hostile: bounded size/rows/cells,
strict UTF-8, no formula evaluation, and ALL-OR-NOTHING validation: if any row fails the
whole paper is rejected and nothing is guessed or partially imported.

Expected header (CSV or the first row of a one-sheet .xlsx, case-insensitive):
  question, option_a, option_b, option_c, option_d, correct, marks, negative_marks[, explanation, topic]

Every format ends in the same validator (`_validate_rows`). Question and option pictures are checked and kept byte-for-byte; their
contents are never read (OCR and picture recognition were discontinued on 2026-11-16).
"""
import csv
import io
import posixpath
import re
import unicodedata
import zipfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from xml.etree import ElementTree as ET

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring as defused_fromstring
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from inst_paper.services.question_images import IMAGE_COLUMNS, MAX_IMAGES_PER_PAPER, ExtractedImage, ImageProblem, check_image

REQUIRED = ["question", "option_a", "option_b", "option_c", "option_d", "correct", "marks", "negative_marks"]
OPTIONAL = ["explanation", "topic", "question_id", "chapter", "sub_topic", *IMAGE_COLUMNS]
# A question needs its text OR its image; each option its text OR its image. A template may therefore leave out a text column when
# it has the matching image column (an image-only paper), but never both.
TEXT_OR_IMAGE = {"question": "question_image", "option_a": "option_a_image", "option_b": "option_b_image",
                 "option_c": "option_c_image", "option_d": "option_d_image"}
# Header spellings accepted for the same column (e.g. a paper exported with "correct_option").
ALIASES = {"correct_option": "correct", "answer": "correct", "question_text": "question", "qid": "question_id", "id": "question_id",
           "correct_answer": "correct", "answer_key": "correct", "negative": "negative_marks", "negative_mark": "negative_marks",
           "neg_marks": "negative_marks", "mark": "marks", "a": "option_a", "b": "option_b", "c": "option_c", "d": "option_d",
           "option_(a)": "option_a", "option_(b)": "option_b", "option_(c)": "option_c", "option_(d)": "option_d",
           "positive_marks": "marks", "positive_mark": "marks", "subtopic": "sub_topic", "sub-topic": "sub_topic",
           "question_img": "question_image", "image": "question_image"}


def _header(h: str) -> str:
    """One column name, as typed in a CSV/Excel/Word header ("Option A", "Negative Marks", "correct_option") -> its template name."""
    key = "_".join((h or "").strip().lower().replace("-", " ").split())
    return ALIASES.get(key, key)
MAX_CELL = 5000


@dataclass
class ExtractedQuestion:
    row: int
    stem: str
    options: dict[str, str]
    correct: str
    marks: Decimal
    negative: Decimal
    explanation: str | None
    topic: str | None
    chapter: str | None = None
    sub_topic: str | None = None
    # field ("question", "A".."D") -> its checked image; a field may have text, an image (options: exactly one of the two)
    images: dict[str, ExtractedImage] = field(default_factory=dict)


# Per row: template image column -> the checked image, or a teacher-readable problem with it (str)
RowImages = dict[str, "ExtractedImage | str"]


@dataclass
class ExtractionResult:
    questions: list[ExtractedQuestion] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    detected: int = 0          # question rows found (valid or not): the basis of the validation summary

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.questions)


def _err(row: int | None, field_: str | None, message: str) -> dict:
    return {"row": row, "field": field_, "message": message}


def _dec(value: str) -> Decimal | None:
    try:
        d = Decimal(value.strip())
    except (InvalidOperation, AttributeError):
        return None
    return d if d.is_finite() and abs(d) < Decimal("10000") else None


# ------------------------------------------------------------------ separate answer key
# When the uploader supplies a separate answer-key file, every format's extractor runs inside `answer_key(mapping)`: the paper may then
# omit the `correct` column/line, and the validator takes question N's answer from the key. A key answer that disagrees with an answer
# printed in the paper, a question without a key answer, or a key answer for a question that is not in the paper all reject the WHOLE
# paper, like every other validation problem. Questions are matched by their number in the paper (1, 2, 3, ... in document order).

@dataclass
class AnswerKey:
    """A parsed separate answer key: answers by question NUMBER (document order) or by explicit QUESTION ID, never both.
    Each entry is (answer, explanation or None)."""
    by_number: dict[int, tuple[str, str | None]] = field(default_factory=dict)
    by_id: dict[str, tuple[str, str | None]] = field(default_factory=dict)

    @property
    def keyed_by_id(self) -> bool:
        return bool(self.by_id)


_ANSWER_KEY: ContextVar[AnswerKey | None] = ContextVar("answer_key", default=None)
_KEY_LINE = re.compile(r"^\s*(?:q(?:uestion)?\s*\.?\s*)?(\d{1,4})\s*(?:[.):,;\-\t]|\s)\s*\(?([A-Za-z])\)?\s*[.,;]?\s*$")
_ID_COLS = {"question_id", "qid", "id"}
_NUM_COLS = {"question", "question_no", "question_number", "no", "no.", "number", "q", "#", "sr", "sr_no"}
_ANS_COLS = {"correct_option", "correct", "answer", "ans", "key", "correct_answer"}
_ID_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-/]{0,49}$")


@contextmanager
def answer_key(mapping: "AnswerKey | dict[int, str] | None") -> Iterator[None]:
    if isinstance(mapping, dict):           # older callers: {number: answer}
        mapping = AnswerKey(by_number={k: (v, None) for k, v in mapping.items()})
    token = _ANSWER_KEY.set(mapping)
    try:
        yield
    finally:
        _ANSWER_KEY.reset(token)


def missing_columns(header: list[str]) -> list[str]:
    """The template columns a header lacks. A question/option text column may be replaced by its image column (e.g. a paper whose
    options are all pictures has option_a_image but no option_a); the message names both."""
    present = set(header)
    missing = []
    for c in required_columns():
        if c in present or (c in TEXT_OR_IMAGE and TEXT_OR_IMAGE[c] in present):
            continue
        missing.append(f"{c} (or {TEXT_OR_IMAGE[c]})" if c in TEXT_OR_IMAGE and any(col in present for col in IMAGE_COLUMNS) else c)
    return missing


def required_columns() -> list[str]:
    """Columns a CSV/XLSX/DOCX table must have; `correct` may be left out when a separate answer key supplies it, and an answer key
    that identifies questions by ID needs a `question_id` column in the paper."""
    key = _ANSWER_KEY.get()
    if key is None:
        return REQUIRED
    cols = [c for c in REQUIRED if c != "correct"]
    return cols + ["question_id"] if key.keyed_by_id else cols


def parse_answer_key(data: bytes, max_questions: int) -> tuple[AnswerKey, list[dict]]:
    """Separate answer key (UTF-8 CSV/text), parsed with a real CSV reader (quoted fields, commas inside explanations, whitespace).

    Formats:
      * by question ID:      header `question_id,correct_option[,explanation]` (also `id`/`qid`, `correct`/`answer`) - matched to the
                             paper's `question_id` column, never by position;
      * by question number:  header `question,answer[,explanation]` (also `no`, `number`, ...) or no header with lines such as
                             `1,B`, `1. B`, `1 - (b)`, `Q1 B` - matched to the question's position in the paper.
    Any unreadable, duplicate or invalid entry is an error; errors reject the paper the key belongs to."""
    key, errors = AnswerKey(), []
    if b"\x00" in data:
        return key, [_err(None, "answer_key", "The answer key must be a text or CSV file.")]
    try:
        text = data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        return key, [_err(None, "answer_key", "The answer key must be UTF-8 encoded text.")]
    csv.field_size_limit(MAX_CELL * 2)
    try:
        rows = [r for r in csv.reader(io.StringIO(text, newline=""))]
    except csv.Error as exc:
        return key, [_err(None, "answer_key", f"Unreadable answer key: {exc}")]
    rows = [(n, [c.strip() for c in r]) for n, r in enumerate(rows, start=1) if any(c.strip() for c in r)]
    if not rows:
        return key, [_err(None, "answer_key", "The answer key is empty.")]
    head = [c.lower().replace(" ", "_") for c in rows[0][1]]
    id_col = next((i for i, c in enumerate(head) if c in _ID_COLS), None)
    num_col = next((i for i, c in enumerate(head) if c in _NUM_COLS), None)
    ans_col = next((i for i, c in enumerate(head) if c in _ANS_COLS), None)
    exp_col = next((i for i, c in enumerate(head) if c in {"explanation", "solution", "explain"}), None)
    if ans_col is not None and (id_col is not None or num_col is not None):       # a header row: column-based key
        by_id = id_col is not None
        col = id_col if by_id else num_col
        for line_no, r in rows[1:]:
            if not by_id and len(r) == 1 and (m := _KEY_LINE.match(r[0])):     # `2. b` style line under a numbered header
                r = [""] * len(head)
                r[col], r[ans_col] = m.group(1), m.group(2)
            ref = r[col] if col < len(r) else ""
            answer = (r[ans_col] if ans_col < len(r) else "").upper().strip("() .")
            expl = (r[exp_col] if exp_col is not None and exp_col < len(r) else "") or None
            label = ref or f"line {line_no}"
            if len(r) > len(head):
                errors.append(_err(line_no, "answer_key", f"Question {label}: the line has more columns than the header (quote explanations that contain commas)."))
                continue
            if by_id:
                if not _ID_VALUE.match(ref):
                    errors.append(_err(line_no, "answer_key", f"Line {line_no}: question ID {ref!r} is missing or not a valid ID."))
                    continue
                target, ident = key.by_id, ref
            else:
                if not ref.isdigit() or not 1 <= int(ref) <= max_questions:
                    errors.append(_err(line_no, "answer_key", f"Line {line_no}: question number {ref!r} is missing or out of range."))
                    continue
                target, ident = key.by_number, int(ref)
            if answer not in {"A", "B", "C", "D"}:
                errors.append(_err(line_no, "answer_key", f"Question {label}: answer {answer or '(empty)'!r} must be A, B, C or D."))
            elif ident in target:
                errors.append(_err(line_no, "answer_key", f"Question {label} appears twice in the answer key."))
            else:
                target[ident] = (answer, expl)
    else:                                                                            # no header: `number,answer` lines
        for line_no, r in rows:
            m = _KEY_LINE.match(",".join(r[:2]) if len(r) >= 2 else r[0])
            if not m:
                if line_no == rows[0][0] and re.search(r"answer|ans|key|correct", " ".join(r), re.I):
                    continue                                                         # an unrecognised header line
                errors.append(_err(line_no, "answer_key", f"Unreadable answer-key line: {','.join(r)[:60]!r}. Use 'number,answer', e.g. 1,B, "
                                                          "or a header 'question_id,correct_option'."))
                continue
            number, answer = int(m.group(1)), m.group(2).upper()
            expl = (r[2] if len(r) > 2 else "") or None
            if number < 1 or number > max_questions:
                errors.append(_err(line_no, "answer_key", f"Question number {number} is out of range."))
            elif answer not in {"A", "B", "C", "D"}:
                errors.append(_err(line_no, "answer_key", f"Answer for question {number} must be A, B, C or D."))
            elif number in key.by_number:
                errors.append(_err(line_no, "answer_key", f"Question {number} appears twice in the answer key."))
            else:
                key.by_number[number] = (answer, expl)
    if not key.by_number and not key.by_id and not errors:
        errors.append(_err(None, "answer_key", "The answer key is empty."))
    return key, errors


def same_text_key(text: str) -> str:
    """The comparison key for duplicate options and duplicate questions: case-SENSITIVE (Co is cobalt, CO is carbon monoxide; Hf is
    hafnium, HF is hydrogen fluoride), insignificant whitespace collapsed, and canonical Unicode composition (NFC: é typed as one
    character or as e + accent is the same text). Never compatibility folding: x² and x2, H₂O and H2O stay different."""
    return unicodedata.normalize("NFC", " ".join((text or "").split()))


def _validate_rows(res: ExtractionResult, rows, max_questions: int, images: dict[int, RowImages] | None = None) -> None:
    """The single validation path for every input format: `rows` yields (row_number, dict-of-stripped-text) or
    (row_number, None) for a structurally broken row. `images` holds each row's pictures by template image column (already checked
    by question_images.check_image, or a problem string). Any problem anywhere rejects the whole paper (see callers)."""
    if images is None:                    # never `images or {}`: the CSV reader fills this same dict while rows are read
        images = {}
    seen: dict[str, int] = {}
    ids: dict[str, int] = {}
    used_ids: set[str] = set()
    akey = _ANSWER_KEY.get()
    qno = 0   # question number in document order (matches a numbered answer key)
    for idx, row in rows:
        if idx - 1 > max_questions:
            res.errors.append(_err(idx, None, f"Too many questions (limit {max_questions})."))
            break
        if row is None:
            qno += 1
            res.detected = qno
            res.errors.append({**_err(idx, None, "Row has the wrong number of columns."), "question": str(qno)})
            continue
        if not any(row.values()):
            continue  # blank line: ignorable, never a question
        qno += 1
        res.detected = qno
        problems = []
        qid = (row.get("question_id") or "").strip()
        label = qid or str(qno)
        if qid:
            if qid in ids:
                problems.append(("question_id", f"The question ID {qid} appears more than once (also row {ids[qid]})."))
            ids.setdefault(qid, idx)
        if akey is not None:
            given = (row.get("correct") or "").strip().upper()
            if akey.keyed_by_id:
                entry = akey.by_id.get(qid) if qid else None
                if not qid:
                    problems.append(("question_id", "The question has no question_id, so the answer key cannot be matched to it."))
                elif entry is None:
                    problems.append(("correct", f"The answer key has no answer for question {qid}."))
                used_ids.add(qid)
            else:
                entry = akey.by_number.get(qno)
                if entry is None:
                    problems.append(("correct", f"The answer key has no answer for question {qno}."))
            keyed, key_expl = entry if entry else (None, None)
            if keyed and given and given != keyed:
                problems.append(("correct", f"Answer mismatch for question {label}: the paper says {given}, the answer key says {keyed}."))
            row = {**row, "correct": keyed or given, "explanation": row.get("explanation") or key_expl or ""}
        if any(len(v) > MAX_CELL for v in row.values()):
            problems.append(("question", f"A cell exceeds {MAX_CELL} characters."))
        row_images = images.get(idx, {})
        pictures: dict[str, ExtractedImage] = {}
        for column, image in row_images.items():
            if isinstance(image, str):
                problems.append((column, image))
            else:
                pictures[IMAGE_COLUMNS[column]] = image
        has_question_image = "question" in pictures or isinstance(row_images.get("question_image"), str)
        row.setdefault("question", "")
        if not row["question"] and not has_question_image:
            problems.append(("question", "Question text is empty." if not row_images and "question_image" not in row
                             else "Question text is empty and there is no question image: give the question text, its image, or both."))
        opts = {k[-1].upper(): row.get(k, "") for k in ("option_a", "option_b", "option_c", "option_d")}
        for key, text_ in opts.items():
            column = f"option_{key.lower()}"
            image_given = key in pictures or isinstance(row_images.get(f"{column}_image"), str)
            if text_ and image_given:
                problems.append((column, f"Option {key} has both text and an image: give only one of them."))
            elif not text_ and not image_given:
                problems.append((column, f"Option {key} is empty." if f"{column}_image" not in row and not row_images
                                 else f"Option {key} is empty: give its text or its image."))
        # identical options: text compared as text, pictures by their exact bytes
        option_keys = [("image", pictures[k].sha256) if k in pictures else ("text", same_text_key(v)) for k, v in opts.items()
                       if v or k in pictures]
        if len(set(option_keys)) < len(option_keys):
            problems.append(("options", "Two options are identical."))
        correct = (row.get("correct") or "").upper()
        if correct not in {"A", "B", "C", "D"}:
            problems.append(("correct", "Correct answer must be A, B, C or D."))
        marks, neg = _dec(row["marks"]), _dec(row["negative_marks"])
        if marks is None or marks <= 0:
            problems.append(("marks", "Marks must be a positive number."))
        if neg is None or neg < 0:
            problems.append(("negative_marks", "Negative marks must be zero or a positive number."))
        norm = same_text_key(row["question"]) + ("\x00" + pictures["question"].sha256 if "question" in pictures else "")
        if (row["question"] or "question" in pictures) and norm in seen:
            problems.append(("question", f"Duplicate of row {seen[norm]}."))
        seen.setdefault(norm, idx)
        if problems:
            res.errors.extend({**_err(idx, f, m), "question": label} for f, m in problems)
            continue
        res.questions.append(ExtractedQuestion(idx, row["question"], opts, correct, marks, neg,
                                               row.get("explanation") or None, row.get("topic") or None,
                                               chapter=(row.get("chapter") or None), sub_topic=(row.get("sub_topic") or None),
                                               images=pictures))
    if akey is not None and akey.keyed_by_id:
        extra = sorted(set(akey.by_id) - used_ids)
        if extra:
            shown = ", ".join(extra[:10]) + (" ..." if len(extra) > 10 else "")
            res.errors.append(_err(None, "answer_key", f"The answer key has answers for question IDs that are not in the paper: {shown}."))
    elif akey is not None:
        extra = sorted(n for n in akey.by_number if n > qno)
        if extra:
            shown = ", ".join(map(str, extra[:10])) + (" ..." if len(extra) > 10 else "")
            res.errors.append(_err(None, "answer_key", f"The answer key has answers for questions that are not in the paper: {shown}."))


def _finish(res: ExtractionResult) -> ExtractionResult:
    if not res.questions and not res.errors:
        res.errors.append(_err(None, None, "The file contains no questions."))
    if res.errors:
        res.questions = []  # reject the entire paper
    return res


def validate_paper(rows: list[tuple[int, dict]], max_questions: int, images: dict[int, RowImages] | None = None) -> ExtractionResult:
    """Run the one validator over an already-structured paper (used for corrections): any problem rejects the whole paper."""
    res = ExtractionResult()
    _validate_rows(res, rows, max_questions, images)
    return _finish(res)


def _no_package_images(name: str) -> str:
    return (f"The image column names {name!r}, but a .csv file cannot carry pictures. Upload a .zip package containing the .csv and "
            "the image files, or use an .xlsx workbook with the pictures in their cells.")


def extract_csv(data: bytes, max_questions: int, resolve_image=None) -> ExtractionResult:
    """A CSV paper. `resolve_image(name)` returns the checked image a cell of an image column names (or a problem string); without
    it (a plain .csv upload) an image column must stay empty."""
    resolve_image = resolve_image or _no_package_images
    images: dict[int, RowImages] = {}
    res = ExtractionResult()
    if b"\x00" in data:
        res.errors.append(_err(None, None, "File contains binary data; upload a CSV export."))
        return res
    try:
        text = data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        res.errors.append(_err(None, None, "File must be UTF-8 encoded text."))
        return res
    csv.field_size_limit(MAX_CELL * 2)
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""))
        header = [_header(h) for h in (reader.fieldnames or [])]
    except csv.Error as exc:
        res.errors.append(_err(None, None, f"Unreadable CSV: {exc}"))
        return res
    missing = missing_columns(header)
    if missing:
        res.errors.append(_err(1, None, f"Missing required column(s): {', '.join(missing)}"))
        return res
    reader.fieldnames = header

    def rows():
        for idx, raw in enumerate(reader, start=2):  # row 1 is the header
            if None in raw or any(v is None for v in raw.values()):
                yield idx, None
            else:
                row = {k: (v or "").strip() for k, v in raw.items()}
                named = {c: resolve_image(row[c]) for c in IMAGE_COLUMNS if row.get(c)}
                if named:
                    images[idx] = named
                yield idx, row

    try:
        _validate_rows(res, rows(), max_questions, images)
    except csv.Error as exc:
        res.errors.append(_err(None, None, f"Unreadable CSV: {exc}"))
    return _finish(res)


# ------------------------------------------------------------------ .zip package: one CSV plus the image files it names

MAX_PACKAGE_MEMBERS = 3_000
MAX_PACKAGE_BYTES = 60 * 1024 * 1024           # all members expanded
_PACKAGE_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def extract_zip_package(data: bytes, max_questions: int) -> ExtractionResult:
    """A .zip holding exactly one .csv (the question sheet) and the pictures its image columns name, e.g. `images/q1.png`.

    Names are matched exactly as written in the CSV (folders included, case-sensitive). Every picture must be used by some row and every
    named picture must exist: a missing, extra, damaged or wrongly named image is an error on the row and column concerned, never a
    silently dropped picture. Encrypted members, absolute paths, '..' and oversized or highly compressed members are refused."""
    res = ExtractionResult()
    if data[:4] != b"PK\x03\x04":
        res.errors.append(_err(None, None, "This is not a .zip package."))
        return res
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            if len(infos) > MAX_PACKAGE_MEMBERS:
                raise _Reject(f"The package has more than {MAX_PACKAGE_MEMBERS} files.")
            members: dict[str, zipfile.ZipInfo] = {}
            for info in infos:
                name = info.filename
                if info.flag_bits & 0x1:
                    raise _Reject("Encrypted .zip packages are not supported.")
                if name.startswith(("/", "\\")) or "\\" in name or ".." in name.split("/") or ":" in name:
                    raise _Reject(f"The package contains an unsafe file name: {name[:80]!r}.")
                if posixpath.basename(name).startswith((".", "__MACOSX")) or name.startswith("__MACOSX/"):
                    continue                                          # macOS/Finder metadata, never content
                members[name] = info
            sheets = [n for n in members if n.lower().endswith(".csv")]
            if len(sheets) != 1:
                raise _Reject("The package must contain exactly one .csv question sheet.")
            other = [n for n in members if n not in sheets and not n.lower().endswith(_PACKAGE_IMAGE_EXT)]
            if other:
                raise _Reject(f"The package may contain only the .csv and PNG, JPEG, GIF or WebP images; remove {other[0][:80]!r}.")
            budget = [0]

            def read(info: zipfile.ZipInfo, limit: int) -> bytes:
                if info.file_size > limit:
                    raise _Reject(f"{info.filename} is too large.")
                with zf.open(info) as fh:
                    blob = fh.read(limit + 1)                         # the declared size is not trusted
                if len(blob) > limit:
                    raise _Reject(f"{info.filename} is too large.")
                if len(blob) > RATIO_CHECK_FROM and len(blob) > MAX_COMPRESSION_RATIO * max(info.compress_size, 1):
                    raise _Reject("The package contains a file that expands far more than an ordinary one (a compression bomb).")
                budget[0] += len(blob)
                if budget[0] > MAX_PACKAGE_BYTES:
                    raise _Reject("The package expands to too much data.")
                return blob

            sheet = read(members[sheets[0]], MAX_PART_BYTES)
            checked: dict[str, ExtractedImage | str] = {}
            used: set[str] = set()

            def resolve(name: str) -> "ExtractedImage | str":
                name = name.strip().replace("\\", "/")
                if name not in members or name in sheets:
                    return f"The image {name!r} is not in the package (names must match exactly, including folders and capitals)."
                used.add(name)
                if name not in checked:
                    if len(checked) >= MAX_IMAGES_PER_PAPER:
                        return f"The package has more than {MAX_IMAGES_PER_PAPER} images."
                    try:
                        checked[name] = check_image(read(members[name], MAX_PART_BYTES), name)
                    except ImageProblem as exc:
                        checked[name] = str(exc)
                return checked[name]

            res = extract_csv(sheet, max_questions, resolve)
            unused = sorted(n for n in members if n not in sheets and n not in used)
            if unused and not res.errors:
                res.errors.append(_err(None, "images", f"{len(unused)} image(s) in the package are not used by any question, e.g. "
                                                       f"{unused[0][:80]!r}: check the file names in the image columns."))
                res.questions = []
    except zipfile.BadZipFile:
        res.errors.append(_err(None, None, "This is not a .zip package."))
    except _Reject as exc:
        res.errors.append(_err(None, None, str(exc)))
    except (RuntimeError, NotImplementedError, OSError, ValueError):
        res.errors.append(_err(None, None, "The package could not be read safely."))
    return res


# ------------------------------------------------------------------ .xlsx (stdlib only; no formula evaluation, no macros, no external data)

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
MAX_ZIP_MEMBERS = 100
MAX_PART_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_COLUMNS = 40
# Complexity limits (security workstream S). A 500-question paper is far inside every one of them; they only stop crafted files.
MAX_COMPRESSION_RATIO = 100          # expanded/compressed size of one part; ordinary Office XML is 5-30x. Checked above 1 MiB.
RATIO_CHECK_FROM = 1024 * 1024
MAX_XML_DEPTH = 100                  # element nesting; Word/Excel documents (incl. nested tables and equations) stay well under 40
MAX_XML_ELEMENTS = 400_000           # elements in one part
MAX_SHEET_ROWS = 5_000               # rows in the one sheet (a paper has at most max_paper_questions + header)
MAX_SHARED_STRINGS = 100_000
_XML_DANGER = re.compile(rb"<!\s*(doctype|entity)", re.IGNORECASE)


class _Reject(Exception):
    pass


def _part(zf: zipfile.ZipFile, name: str, budget: list[int]) -> bytes:
    try:
        info = zf.getinfo(name)
    except KeyError:
        raise _Reject(f"The workbook is missing {name}.")
    if info.file_size > MAX_PART_BYTES:
        raise _Reject("The workbook contains an oversized part.")
    with zf.open(info) as fh:
        data = fh.read(MAX_PART_BYTES + 1)           # bounded read: the declared size is not trusted
    if len(data) > MAX_PART_BYTES:
        raise _Reject("The workbook contains an oversized part.")
    if len(data) > RATIO_CHECK_FROM and len(data) > MAX_COMPRESSION_RATIO * max(info.compress_size, 1):
        raise _Reject("The file contains a part that expands far more than an ordinary document (a compression bomb).")
    budget[0] += len(data)
    if budget[0] > MAX_TOTAL_BYTES:
        raise _Reject("The workbook expands to too much data.")
    if b"\x00" in data or _XML_DANGER.search(data):
        raise _Reject("The workbook contains a DOCTYPE or XML entity declaration, which is not allowed.")
    return data


def _xml(data: bytes) -> ET.Element:
    """Parse one OOXML part with defusedxml (DTDs, entity declarations and external references are refused even if the byte
    check in _part were bypassed), then enforce nesting-depth and element-count limits without recursion."""
    try:
        root = defused_fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException:
        raise _Reject("The file contains a DOCTYPE, entity or external reference, which is not allowed.")
    except ET.ParseError:
        raise _Reject("The workbook contains malformed XML.")
    count, stack = 0, [(root, 1)]
    while stack:
        el, depth = stack.pop()
        count += 1
        if depth > MAX_XML_DEPTH:
            raise _Reject("The file's structure is nested too deeply to process safely.")
        if count > MAX_XML_ELEMENTS:
            raise _Reject("The file is too complex to process safely (too many elements).")
        stack.extend((c, depth + 1) for c in el)
    return root


def _col_index(ref: str) -> int:
    letters = re.match(r"^([A-Z]{1,3})\d+$", ref or "")
    if not letters:
        raise _Reject("The sheet contains an invalid cell reference.")
    n = 0
    for ch in letters.group(1):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _text_of(el: ET.Element) -> str:
    """Text of a shared/inline string item; phonetic runs (rPh) are not part of the value. Superscript/subscript rich-text runs keep
    their meaning (x + raised 2 -> x², not x2): see math.office."""
    from inst_paper.services.math.office import xlsx_string_text
    return xlsx_string_text(el)


def _sheet_rows(zf: zipfile.ZipFile) -> tuple[list[str], list[tuple[int, dict[str, str] | None]]]:
    names = zf.namelist()
    if len(names) > MAX_ZIP_MEMBERS:
        raise _Reject("The workbook has too many parts.")
    for info in zf.infolist():
        if info.flag_bits & 0x1:
            raise _Reject("Encrypted workbooks are not supported.")
        n = info.filename.lower()
        if n.startswith(("xl/vbaproject", "xl/externallinks/", "xl/activex/", "xl/embeddings/")) or n.endswith(".bin") and "vba" in n:
            raise _Reject("Workbooks with macros, external links or embedded objects are not accepted.")
    budget = [0]
    book = _xml(_part(zf, "xl/workbook.xml", budget))
    sheets = book.findall(f"{NS}sheets/{NS}sheet")
    if len(sheets) != 1:
        raise _Reject("The workbook must contain exactly one sheet.")
    if sheets[0].get("state", "visible") != "visible":
        raise _Reject("The sheet must not be hidden.")
    rid = sheets[0].get(f"{REL}id")
    target = None
    rels = _xml(_part(zf, "xl/_rels/workbook.xml.rels", budget))
    for r in rels.findall(f"{PKG_REL}Relationship"):
        if r.get("Id") == rid and r.get("TargetMode") != "External":
            t = r.get("Target") or ""
            target = posixpath.normpath(t[1:] if t.startswith("/") else posixpath.join("xl", t))
    if not target or not target.startswith("xl/worksheets/") or target not in names:
        raise _Reject("The sheet could not be located in the workbook.")

    shared: list[str] = []
    if "xl/sharedStrings.xml" in names:
        for si in _xml(_part(zf, "xl/sharedStrings.xml", budget)).findall(f"{NS}si"):
            shared.append(_text_of(si))
            if len(shared) > MAX_SHARED_STRINGS:
                raise _Reject("The workbook has too many text cells to process safely.")

    sheet = _xml(_part(zf, target, budget))
    if sheet.find(f"{NS}mergeCells") is not None:
        raise _Reject("Merged cells are not supported; every question needs its own row of plain cells.")
    if any(c.get("hidden") in ("1", "true") for c in sheet.findall(f"{NS}cols/{NS}col")):
        raise _Reject("Hidden columns are not supported.")
    data = sheet.find(f"{NS}sheetData")
    if data is None:
        raise _Reject("The sheet has no data.")

    grid: list[tuple[int, list[str]]] = []
    vm_cells: dict[tuple[int, int], str] = {}
    rows_xml = data.findall(f"{NS}row")
    if len(rows_xml) > MAX_SHEET_ROWS:
        raise _Reject(f"The sheet has more than {MAX_SHEET_ROWS} rows.")
    for row in rows_xml:
        try:
            r = int(row.get("r") or 0)
        except ValueError:
            raise _Reject("The sheet contains an invalid row number.")
        if row.get("hidden") in ("1", "true"):
            raise _Reject(f"Row {r} is hidden; hidden rows are not supported.")
        cells: dict[int, str] = {}
        for c in row.findall(f"{NS}c"):
            col = _col_index(c.get("r", ""))
            if c.get("vm"):                                   # a picture placed IN the cell (Excel "Place in Cell")
                vm_cells[(r, col)] = c.get("vm") or ""
                continue
            if c.find(f"{NS}f") is not None:
                raise _Reject(f"Row {r} contains a formula; formulas are not evaluated. Paste values instead.")
            kind, v = c.get("t"), c.find(f"{NS}v")
            if kind == "inlineStr":
                is_ = c.find(f"{NS}is")
                value = _text_of(is_) if is_ is not None else ""
            elif kind == "s":
                try:
                    value = shared[int(v.text)] if v is not None and v.text is not None else ""
                except (ValueError, IndexError):
                    raise _Reject("The workbook has a broken shared-string reference.")
            elif kind in ("b", "e", "d"):
                raise _Reject(f"Row {r} contains a boolean, error or date cell; use plain text and numbers.")
            else:                                     # numbers and cached "str" values without formulas
                value = (v.text or "") if v is not None else ""
            if value.strip():
                cells[col] = value.strip()
        if cells:
            if max(cells) >= MAX_COLUMNS:
                raise _Reject(f"Row {r} uses more than {MAX_COLUMNS} columns.")
            grid.append((r, [cells.get(i, "") for i in range(max(cells) + 1)]))
    pictures = _sheet_pictures(zf, target, names, budget, vm_cells)
    # a row whose only content is pictures (an image-only question with image options) is still a question row
    text_rows = {r for r, _ in grid}
    for r in sorted({r for r, _ in pictures} - text_rows):
        grid.append((r, []))
    grid.sort(key=lambda item: item[0])
    if not grid:
        return [], [], {}
    if grid[0][0] != 1:
        raise _Reject("The header must be in row 1.")
    header = [_header(h) for h in grid[0][1]]
    named = [h for h in header if h]
    if len(set(named)) != len(named):
        raise _Reject("The header row has duplicate column names.")
    out: list[tuple[int, dict[str, str] | None]] = []
    for r, values in grid[1:]:
        if len(values) > len(header) and any(values[len(header):]):
            out.append((r, None))
            continue
        out.append((r, {h: (values[i] if i < len(values) else "") for i, h in enumerate(header) if h}))
    return header, out, pictures


XDR = "{http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing}"
DML = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_RELS_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_RICH_NS = "{http://schemas.microsoft.com/office/spreadsheetml/2017/richdata}"
_RICH_REL_NS = "{http://schemas.microsoft.com/office/spreadsheetml/2022/richvaluerel}"
_RVB_NS = "{http://schemas.microsoft.com/office/spreadsheetml/2017/richdata}"
# A picture's cell -> the workbook part(s) holding it, or a problem string for a picture that cannot be placed in one cell
Pictures = dict[tuple[int, int], "list[str] | str"]


def _rels(zf: zipfile.ZipFile, part: str, names: list[str], budget: list[int]) -> dict[str, str]:
    """Relationship id -> normalised internal target part, for one part's .rels file (missing file: no relationships)."""
    folder, base = posixpath.split(part)
    rels_name = posixpath.join(folder, "_rels", base + ".rels")
    if rels_name not in names:
        return {}
    out = {}
    for r in _xml(_part(zf, rels_name, budget)).findall(f"{_RELS_NS}Relationship"):
        if r.get("TargetMode") == "External":
            raise _Reject("The workbook links to an external file, which is not allowed.")
        t = r.get("Target") or ""
        out[r.get("Id") or ""] = posixpath.normpath(t[1:] if t.startswith("/") else posixpath.join(folder, t))
    return out


def _sheet_pictures(zf: zipfile.ZipFile, sheet_part: str, names: list[str], budget: list[int],
                    vm_cells: dict[tuple[int, int], str]) -> Pictures:
    """Every picture of the sheet by the cell it belongs to: (row number, 0-based column) -> [media part names].

    * Placed over cells (the usual Insert > Pictures): the cell under the picture's top-left corner (drawing anchor `from`).
    * Placed in a cell (Excel 365 "Place in Cell"): the cell's value-metadata index leads through xl/metadata.xml and
      xl/richData to the picture's part.
    Charts, shapes, text boxes and pictures not anchored to a cell are refused (they cannot belong to one question field)."""
    pictures: Pictures = {}
    for drawing in _rels(zf, sheet_part, names, budget).values():
        if not drawing.startswith("xl/drawings/") or drawing not in names:
            continue
        targets = _rels(zf, drawing, names, budget)
        for anchor in _xml(_part(zf, drawing, budget)):
            tag = anchor.tag.removeprefix(XDR)
            if tag not in ("twoCellAnchor", "oneCellAnchor", "absoluteAnchor"):
                continue
            pic = anchor.find(f"{XDR}pic")
            if pic is None:
                raise _Reject("The sheet contains a chart, shape or text box; only pictures in the image columns are supported.")
            start = anchor.find(f"{XDR}from")
            if tag == "absoluteAnchor" or start is None:
                raise _Reject("A picture in the sheet is not attached to a cell; place each picture in its question's row.")
            try:
                cell = (int(start.findtext(f"{XDR}row") or "") + 1, int(start.findtext(f"{XDR}col") or ""))
            except ValueError:
                raise _Reject("A picture in the sheet has an invalid position.")
            blip = pic.find(f"{XDR}blipFill/{DML}blip")
            media = targets.get(blip.get(f"{REL}embed") or "") if blip is not None else None
            if not media or not media.startswith("xl/media/") or media not in names:
                raise _Reject(f"A picture in row {cell[0]} could not be found inside the workbook.")
            pictures.setdefault(cell, []).append(media)
    if vm_cells:
        for cell, media in _in_cell_pictures(zf, names, budget, vm_cells).items():
            pictures.setdefault(cell, []).append(media)
    return pictures


def _in_cell_pictures(zf: zipfile.ZipFile, names: list[str], budget: list[int],
                      vm_cells: dict[tuple[int, int], str]) -> dict[tuple[int, int], str]:
    """Resolve Excel "Place in Cell" pictures: cell vm (1-based) -> valueMetadata block -> futureMetadata rich-value block
    (0-based) -> rich value -> its first value = index of the picture relationship -> media part."""
    for part in ("xl/metadata.xml", "xl/richData/rdrichvalue.xml", "xl/richData/richValueRel.xml"):
        if part not in names:
            raise _Reject("A picture placed in a cell could not be read (the workbook's picture data is incomplete).")
    meta = _xml(_part(zf, "xl/metadata.xml", budget))
    value_blocks = [bk.find(f"{NS}rc") for bk in meta.findall(f"{NS}valueMetadata/{NS}bk")]
    future = [bk.find(f".//{_RVB_NS}rvb") for bk in meta.findall(f"{NS}futureMetadata/{NS}bk")]
    rich_values = _xml(_part(zf, "xl/richData/rdrichvalue.xml", budget)).findall(f"{_RICH_NS}rv")
    rel_ids = [r.get(f"{REL}id") for r in _xml(_part(zf, "xl/richData/richValueRel.xml", budget)).findall(f"{_RICH_REL_NS}rel")]
    targets = _rels(zf, "xl/richData/richValueRel.xml", names, budget)
    out = {}
    for cell, vm in vm_cells.items():
        try:
            rc = value_blocks[int(vm) - 1]
            rvb = future[int(rc.get("v") or "")]
            rv = rich_values[int(rvb.get("i") or "")]
            media = targets.get(rel_ids[int(rv.findtext(f"{_RICH_NS}v") or "")] or "")
        except (ValueError, IndexError, AttributeError, TypeError):
            media = None
        if not media or not media.startswith("xl/media/") or media not in names:
            raise _Reject(f"The picture in row {cell[0]} could not be found inside the workbook.")
        out[cell] = media
    return out


def _media(zf: zipfile.ZipFile, name: str, budget: list[int]) -> bytes:
    """A picture's bytes (binary, so not passed through the XML checks of _part), with the same size and budget limits."""
    info = zf.getinfo(name)
    if info.file_size > MAX_PART_BYTES:
        raise _Reject("The workbook contains an oversized picture.")
    with zf.open(info) as fh:
        blob = fh.read(MAX_PART_BYTES + 1)
    if len(blob) > MAX_PART_BYTES:
        raise _Reject("The workbook contains an oversized picture.")
    budget[0] += len(blob)
    if budget[0] > MAX_TOTAL_BYTES:
        raise _Reject("The workbook expands to too much data.")
    return blob


def _xlsx_row_images(zf: zipfile.ZipFile, header: list[str], pictures: Pictures, errors: list[dict]) -> dict[int, RowImages]:
    """Map each picture to its row and template image column, checking it. A picture outside an image column, two pictures in one
    cell, or text typed into an image column are errors on that row and column."""
    images: dict[int, RowImages] = {}
    budget = [0]
    checked: dict[str, ExtractedImage | str] = {}
    for (r, col), media in sorted(pictures.items()):
        column = header[col] if col < len(header) else ""
        if r == 1:
            errors.append(_err(1, column or None, "A picture is placed in the header row; put each picture in its question's row."))
            continue
        if column not in IMAGE_COLUMNS:
            errors.append(_err(r, column or None, f"A picture is placed in the column {column or _column_letter(col)!r}; pictures belong "
                                                  f"only in {', '.join(IMAGE_COLUMNS)}."))
            continue
        if len(media) > 1:
            images.setdefault(r, {})[column] = f"{len(media)} pictures are placed in this cell; keep exactly one."
            continue
        if len(checked) >= MAX_IMAGES_PER_PAPER and media[0] not in checked:
            errors.append(_err(r, column, f"The workbook has more than {MAX_IMAGES_PER_PAPER} pictures."))
            break
        if media[0] not in checked:
            try:
                checked[media[0]] = check_image(_media(zf, media[0], budget), posixpath.basename(media[0]))
            except ImageProblem as exc:
                checked[media[0]] = str(exc)
        images.setdefault(r, {})[column] = checked[media[0]]
    return images


def _column_letter(col: int) -> str:
    letters = ""
    col += 1
    while col:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def extract_xlsx(data: bytes, max_questions: int) -> ExtractionResult:
    res = ExtractionResult()
    if data[:4] != b"PK\x03\x04":
        res.errors.append(_err(None, None, "This is not an .xlsx workbook."))
        return res
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            header, rows, pictures = _sheet_rows(zf)
            images = _xlsx_row_images(zf, header, pictures, res.errors) if header else {}
    except zipfile.BadZipFile:
        res.errors.append(_err(None, None, "This is not an .xlsx workbook."))
        return res
    except _Reject as exc:
        res.errors.append(_err(None, None, str(exc)))
        return res
    except (RuntimeError, NotImplementedError, OSError, ValueError):
        res.errors.append(_err(None, None, "The workbook could not be read safely."))
        return res
    if not header:
        res.errors.append(_err(None, None, "The file contains no questions."))
        return res
    missing = missing_columns(header)
    if missing:
        res.errors.append(_err(1, None, f"Missing required column(s): {', '.join(missing)}"))
        return res
    for idx, row in rows:                         # text typed into an image column is not a picture
        for column in IMAGE_COLUMNS:
            if row is not None and row.get(column) and column not in images.get(idx, {}):
                images.setdefault(idx, {})[column] = (f"The {column} cell holds text ({row[column][:40]!r}) instead of a picture: "
                                                      "insert the picture into the cell, or move the text to its text column.")

    def counted() -> Iterable[tuple[int, dict | None]]:
        for idx, row in rows:
            if row is not None and not any(row.values()) and idx not in images:
                continue
            yield idx, row

    _validate_rows(res, counted(), max_questions, images)
    return _finish(res)
