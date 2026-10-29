"""Question-sheet extraction. Treats the upload as hostile: bounded size/rows/cells,
strict UTF-8, no formula evaluation, and ALL-OR-NOTHING validation: if any row fails the
whole paper is rejected and nothing is guessed or partially imported.

Expected header (CSV or the first row of a one-sheet .xlsx, case-insensitive):
  question, option_a, option_b, option_c, option_d, correct, marks, negative_marks[, explanation, topic]

Every format ends in the same validator (`_validate_rows`). Image papers need an OCR backend that is NOT bundled or enabled by default
(see `OcrBackend`); without one they are rejected with a clear message.
"""
import csv
import io
import posixpath
import re
import struct
import zipfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Protocol
from xml.etree import ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

REQUIRED = ["question", "option_a", "option_b", "option_c", "option_d", "correct", "marks", "negative_marks"]
OPTIONAL = ["explanation", "topic", "question_id"]
# Header spellings accepted for the same column (e.g. a paper exported with "correct_option").
ALIASES = {"correct_option": "correct", "answer": "correct", "question_text": "question", "qid": "question_id", "id": "question_id"}
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


def _validate_rows(res: ExtractionResult, rows, max_questions: int) -> None:
    """The single validation path for every input format: `rows` yields (row_number, dict-of-stripped-text) or
    (row_number, None) for a structurally broken row. Any problem anywhere rejects the whole paper (see callers)."""
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
        if not row["question"]:
            problems.append(("question", "Question text is empty."))
        opts = {k[-1].upper(): row[k] for k in ("option_a", "option_b", "option_c", "option_d")}
        for key, text_ in opts.items():
            if not text_:
                problems.append((f"option_{key.lower()}", f"Option {key} is empty."))
        if len(set(v.lower() for v in opts.values() if v)) < len([v for v in opts.values() if v]):
            problems.append(("options", "Two options are identical."))
        correct = (row.get("correct") or "").upper()
        if correct not in {"A", "B", "C", "D"}:
            problems.append(("correct", "Correct answer must be A, B, C or D."))
        marks, neg = _dec(row["marks"]), _dec(row["negative_marks"])
        if marks is None or marks <= 0:
            problems.append(("marks", "Marks must be a positive number."))
        if neg is None or neg < 0:
            problems.append(("negative_marks", "Negative marks must be zero or a positive number."))
        norm = " ".join(row["question"].lower().split())
        if row["question"] and norm in seen:
            problems.append(("question", f"Duplicate of row {seen[norm]}."))
        seen.setdefault(norm, idx)
        if problems:
            res.errors.extend({**_err(idx, f, m), "question": label} for f, m in problems)
            continue
        res.questions.append(ExtractedQuestion(idx, row["question"], opts, correct, marks, neg,
                                               row.get("explanation") or None, row.get("topic") or None))
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


def validate_paper(rows: list[tuple[int, dict]], max_questions: int) -> ExtractionResult:
    """Run the one validator over an already-structured paper (used for corrections): any problem rejects the whole paper."""
    res = ExtractionResult()
    _validate_rows(res, rows, max_questions)
    return _finish(res)


def extract_csv(data: bytes, max_questions: int) -> ExtractionResult:
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
        header = [ALIASES.get(h.strip().lower(), h.strip().lower()) for h in (reader.fieldnames or [])]
    except csv.Error as exc:
        res.errors.append(_err(None, None, f"Unreadable CSV: {exc}"))
        return res
    missing = [c for c in required_columns() if c not in header]
    if missing:
        res.errors.append(_err(1, None, f"Missing required column(s): {', '.join(missing)}"))
        return res
    reader.fieldnames = header

    def rows():
        for idx, raw in enumerate(reader, start=2):  # row 1 is the header
            if None in raw or any(v is None for v in raw.values()):
                yield idx, None
            else:
                yield idx, {k: (v or "").strip() for k, v in raw.items()}

    try:
        _validate_rows(res, rows(), max_questions)
    except csv.Error as exc:
        res.errors.append(_err(None, None, f"Unreadable CSV: {exc}"))
    return _finish(res)


# ------------------------------------------------------------------ .xlsx (stdlib only; no formula evaluation, no macros, no external data)

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
MAX_ZIP_MEMBERS = 100
MAX_PART_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_COLUMNS = 40
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
    budget[0] += len(data)
    if budget[0] > MAX_TOTAL_BYTES:
        raise _Reject("The workbook expands to too much data.")
    if b"\x00" in data or _XML_DANGER.search(data):
        raise _Reject("The workbook contains a DOCTYPE or XML entity declaration, which is not allowed.")
    return data


def _xml(data: bytes) -> ET.Element:
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        raise _Reject("The workbook contains malformed XML.")


def _col_index(ref: str) -> int:
    letters = re.match(r"^([A-Z]{1,3})\d+$", ref or "")
    if not letters:
        raise _Reject("The sheet contains an invalid cell reference.")
    n = 0
    for ch in letters.group(1):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _text_of(el: ET.Element) -> str:
    """Text of a shared/inline string item; phonetic runs (rPh) are not part of the value."""
    parts = []
    for child in el:
        if child.tag == f"{NS}t":
            parts.append(child.text or "")
        elif child.tag == f"{NS}r":
            parts.extend(t.text or "" for t in child if t.tag == f"{NS}t")
    return "".join(parts)


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

    sheet = _xml(_part(zf, target, budget))
    if sheet.find(f"{NS}mergeCells") is not None:
        raise _Reject("Merged cells are not supported; every question needs its own row of plain cells.")
    if any(c.get("hidden") in ("1", "true") for c in sheet.findall(f"{NS}cols/{NS}col")):
        raise _Reject("Hidden columns are not supported.")
    data = sheet.find(f"{NS}sheetData")
    if data is None:
        raise _Reject("The sheet has no data.")

    grid: list[tuple[int, list[str]]] = []
    for row in data.findall(f"{NS}row"):
        try:
            r = int(row.get("r") or 0)
        except ValueError:
            raise _Reject("The sheet contains an invalid row number.")
        if row.get("hidden") in ("1", "true"):
            raise _Reject(f"Row {r} is hidden; hidden rows are not supported.")
        cells: dict[int, str] = {}
        for c in row.findall(f"{NS}c"):
            col = _col_index(c.get("r", ""))
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
    if not grid:
        return [], []
    if grid[0][0] != 1:
        raise _Reject("The header must be in row 1.")
    header = [h.lower() for h in grid[0][1]]
    named = [h for h in header if h]
    if len(set(named)) != len(named):
        raise _Reject("The header row has duplicate column names.")
    out: list[tuple[int, dict[str, str] | None]] = []
    for r, values in grid[1:]:
        if len(values) > len(header) and any(values[len(header):]):
            out.append((r, None))
            continue
        out.append((r, {h: (values[i] if i < len(values) else "") for i, h in enumerate(header) if h}))
    return header, out


def extract_xlsx(data: bytes, max_questions: int) -> ExtractionResult:
    res = ExtractionResult()
    if data[:4] != b"PK\x03\x04":
        res.errors.append(_err(None, None, "This is not an .xlsx workbook."))
        return res
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            header, rows = _sheet_rows(zf)
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
    missing = [c for c in required_columns() if c not in header]
    if missing:
        res.errors.append(_err(1, None, f"Missing required column(s): {', '.join(missing)}"))
        return res

    def counted() -> Iterable[tuple[int, dict | None]]:
        for idx, row in rows:
            if row is not None and not any(row.values()):
                continue
            yield idx, row

    _validate_rows(res, counted(), max_questions)
    return _finish(res)


# ------------------------------------------------------------------ images (OCR only through an explicitly configured backend)

MAX_IMAGE_PIXELS = 40_000_000


@dataclass
class OcrQuestion:
    stem: str
    options: dict[str, str]
    correct: str
    marks: str
    negative: str
    confidence: float          # 0..1, the engine's own confidence for this whole question including the marked answer


class OcrBackend(Protocol):
    def read_questions(self, image: bytes, mime: str) -> list[OcrQuestion]: ...

    # Photo papers (services/photo_drafts.py) need the page's text lines: [text, weakest word confidence, mean confidence] (0..100).
    def read_lines(self, image: bytes, mime: str) -> list[list]: ...


class OcrRejected(Exception):
    """Raised by a backend that read the page but found it structurally unusable (e.g. a numbering gap): carries row errors."""

    def __init__(self, errors: list[dict]):
        super().__init__("ocr rejected")
        self.errors = errors


_ocr: OcrBackend | None = None


def get_ocr_backend() -> OcrBackend | None:
    """None unless a backend was registered at start-up: no engine is bundled, so image papers are rejected by default."""
    return _ocr


def set_ocr_backend(b: OcrBackend | None) -> None:
    global _ocr
    _ocr = b


def _image_size(data: bytes, mime: str) -> tuple[int, int] | None:
    if mime == "image/png":
        if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
            return None
        return struct.unpack(">II", data[16:24])
    if mime == "image/jpeg":
        if data[:3] != b"\xff\xd8\xff":
            return None
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            length = struct.unpack(">H", data[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            i += 2 + length
    return None


def extract_image(data: bytes, mime: str, backend: OcrBackend | None, max_questions: int, min_confidence: float = 0.98) -> ExtractionResult:
    res = ExtractionResult()
    if backend is None:
        res.errors.append(_err(None, None, "Image extraction is not enabled on this server. Upload a CSV, .xlsx, .docx or text PDF file instead."))
        return res
    size = _image_size(data, mime)
    if size is None or size[0] <= 0 or size[1] <= 0 or size[0] * size[1] > MAX_IMAGE_PIXELS:
        res.errors.append(_err(None, None, "The image is not a valid PNG/JPEG of a supported size."))
        return res
    try:
        items = backend.read_questions(data, mime)
    except OcrRejected as exc:
        res.errors.extend(exc.errors or [_err(None, None, "The image could not be read reliably.")])
        return res
    except Exception:  # noqa: BLE001 - an engine failure must reject the paper, never import a partial one
        res.errors.append(_err(None, None, "The image could not be read reliably."))
        return res
    if not items:
        res.errors.append(_err(None, None, "No questions could be read from the image."))
        return res
    rows = []
    for n, q in enumerate(items, start=1):
        if not (q.confidence >= min_confidence):
            res.errors.append(_err(n, None, f"Question {n} was read with too little confidence ({q.confidence:.2f} < {min_confidence:.2f})."))
        opts = q.options or {}
        rows.append((n + 1, {"question": (q.stem or "").strip(), **{f"option_{k.lower()}": (opts.get(k) or "").strip() for k in "ABCD"},
                             "correct": (q.correct or "").strip(), "marks": str(q.marks or "").strip(), "negative_marks": str(q.negative or "").strip()}))
    _validate_rows(res, rows, max_questions)
    return _finish(res)
