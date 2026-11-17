"""Question extraction from Word (.docx) and PDF documents.

Both formats end in the same all-or-nothing validator as CSV/XLSX (`extraction._validate_rows`), so a document is accepted only if
EVERY question is complete and valid; nothing is guessed and no question is silently dropped.

Accepted layouts
  * .docx with a table whose first row holds the CSV column names (question, option_a ... negative_marks[, explanation, topic]);
  * .docx or text-based .pdf written as numbered question blocks:

        Default marks: 4            <- optional paper-level settings, before the first question
        Default negative: 1

        1. What is the SI unit of force?
        A) Newton
        B) Joule
        C) Watt
        D) Pascal
        Answer: A
        Marks: 4                    <- optional when a default is given
        Negative: 1
        Explanation: F = ma         <- optional
        Topic: Mechanics            <- optional

    Numbering must run 1, 2, 3 ... without gaps (a gap means a question was lost in extraction and the paper is rejected). Lines that
    belong to no field continue the previous one (multi-line questions/options), so text is never discarded once questions begin.

Scanned PDFs (no text layer) are rejected: they need image extraction (OCR), which is accepted only through a registered backend.
The .docx reader treats the file as hostile exactly like the .xlsx reader: bounded parts, no DOCTYPE/entities, no macros, embedded
objects or external relationships."""
import io
import re
import zipfile
from collections import Counter

from inst_paper.services.extraction import (
    MAX_ZIP_MEMBERS, OPTIONAL, PKG_REL, REQUIRED, ExtractionResult, _err, _finish, _header, _part, _Reject, _validate_rows, _xml,
    required_columns,
)
from inst_paper.services.math.office import docx_paragraph_text

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_PDF_PAGES = 60
MAX_TEXT_CHARS = 2_000_000
MAX_DOCX_RUNS = 100_000      # text runs in the document body; a 500-question paper uses a few tens of thousands at most

_Q = re.compile(r"^\s*(?:Q(?:uestion)?\s*)?(\d{1,4})\s*[.):]\s*(.*)$", re.IGNORECASE)
_OPT = re.compile(r"^\s*\(?([A-Da-d])\s*[).:]\s*(.*)$")
_FIELD = re.compile(r"^\s*(answer|ans|correct answer|correct|marks?|negative marks?|negative|neg|explanation|topic)\s*[:\-]\s*(.*)$",
                    re.IGNORECASE)
_DEFAULT = re.compile(r"^\s*default\s+(marks?|negative(?:\s+marks?)?)\s*[:\-]\s*(\S+)\s*$", re.IGNORECASE)


def _field_key(label: str) -> str:
    label = label.lower()
    if label.startswith(("answer", "ans", "correct")):
        return "correct"
    if label.startswith(("negative", "neg")):
        return "negative_marks"
    if label.startswith("mark"):
        return "marks"
    return label  # explanation / topic


# Fields whose value is a single code, where "(B)" or "(4)" means B or 4. Every other field is kept exactly as typed.
_CODE_FIELDS = ("correct", "marks", "negative_marks")


def _non_space(text: str) -> Counter:
    return Counter(c for c in text if not c.isspace())


# The text account's own label reader: independent of the parser's patterns, it only removes a line's leading question number
# ("12." "Q3)"), option letter ("A)" "(b)" "C.") or field label ("Answer:" "Explanation -"), so a fault in the parser's patterns
# shows up as missing characters instead of being hidden by the same fault.
_LABEL = re.compile(r"^\s*(?:(?:Q(?:uestion)?\s*)?[0-9]{1,4}\s*[.):]|\(?[A-Da-d]\s*[).:]|"
                    r"(?:answer|ans|correct answer|correct|marks?|negative marks?|negative|neg|explanation|topic)\s*[:\-])", re.IGNORECASE)


def _after_label(line: str, labelled: bool) -> str:
    m = _LABEL.match(line) if labelled else None
    return line[m.end():] if m else line


def parse_question_blocks(text: str, res: ExtractionResult) -> list[tuple[int, dict[str, str] | None]]:
    """Numbered question blocks -> rows for the shared validator. Structural problems are added to `res.errors`.

    Text integrity: question, option, explanation and topic text is taken exactly as typed (only surrounding whitespace is
    trimmed; continuation lines are joined with a space). Brackets are removed only around a one-code value such as
    "Answer: (B)" or "Marks: (4)". Every question is checked afterwards: each non-space character of its lines (after the
    numbers and labels) must appear in its fields, otherwise the paper is rejected with TEXT_LOST - never imported short."""
    defaults = {"marks": "", "negative_marks": ""}
    rows: list[tuple[int, dict[str, str] | None]] = []
    current: dict[str, str] | None = None
    consumed: list[str] = []             # the text of the current question's lines, after their number/label
    removed: Counter = Counter()         # characters deliberately removed (brackets around a one-code value)
    number = 0
    last_key: str | None = None
    expected = 1

    def close():
        if current is None:
            return
        typed = _non_space("".join(consumed))
        kept = _non_space("".join(current.values())) + removed       # only the brackets taken off "(B)"-style codes
        lost = typed - kept
        if lost or kept - typed:
            shown = "".join(sorted(lost.elements()))[:20] or "(changed text)"
            res.errors.append({**_err(number, None, f"Question {number}: some of the text in the file could not be placed in a field "
                                                    f"({shown!r}); nothing was imported. Check this question's layout."),
                               "question": str(number), "code": "TEXT_LOST"})
        for k, v in defaults.items():
            if not current.get(k):
                current[k] = v
        rows.append((number, {k: (v or "").strip() for k, v in current.items()}))

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if current is None:
            d = _DEFAULT.match(line)
            if d:
                defaults["negative_marks" if d.group(1).lower().startswith("neg") else "marks"] = d.group(2)
                continue
        q = _Q.match(line)
        # A new question starts only with the next expected number, so "2." inside a question's text cannot split it.
        if q and int(q.group(1)) >= expected:
            n = int(q.group(1))
            close()
            if n != expected:
                res.errors.append(_err(n, None, f"Question numbering jumps from {expected - 1} to {n}: a question may be missing."))
            number, expected = n, n + 1
            current = {k: "" for k in REQUIRED + OPTIONAL}
            current["question"] = q.group(2)
            consumed = [_after_label(line, True)]
            removed = Counter()
            last_key = "question"
            continue
        if current is None:
            continue  # title / instructions before the first question
        o = _OPT.match(line)
        if o:
            key = f"option_{o.group(1).lower()}"
            if current[key]:
                res.errors.append(_err(number, key, f"Option {o.group(1).upper()} appears twice."))
            current[key] = o.group(2)
            consumed.append(_after_label(line, True))
            last_key = key
            continue
        f = _FIELD.match(line)
        if f:
            key = _field_key(f.group(1))
            value = f.group(2).strip()
            if key in _CODE_FIELDS:                  # "Answer: (B)", "B)", "(4)" -> B, 4; never applied to explanations or topics
                core = value.lstrip("(").rstrip(")")
                removed += _non_space(value) - _non_space(core)
                value = core.strip()
            current[key] = value
            consumed.append(_after_label(line, True))
            last_key = key
            continue
        if last_key in ("correct", "marks", "negative_marks"):
            res.errors.append(_err(number, None, f"Unrecognised line after {last_key}: \"{line[:60]}\"."))
            continue
        current[last_key] = f"{current[last_key]} {line}".strip()   # continuation of a multi-line field
        consumed.append(_after_label(line, False))
    close()
    return rows


# ------------------------------------------------------------------ .docx

def _docx_paragraph_text(p) -> str:
    """Paragraph text with superscript/subscript runs kept, native equations converted and images/objects marked (math.office)."""
    return docx_paragraph_text(p)


def _docx_body(data: bytes):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        if len(zf.namelist()) > MAX_ZIP_MEMBERS:
            raise _Reject("The document has too many parts.")
        for info in zf.infolist():
            if info.flag_bits & 0x1:
                raise _Reject("Encrypted documents are not supported.")
            n = info.filename.lower()
            if "vbaproject" in n or n.startswith(("word/embeddings/", "word/activex/")) or n.endswith(".bin"):
                raise _Reject("Documents with macros or embedded objects are not accepted.")
        budget = [0]
        if "word/_rels/document.xml.rels" in zf.namelist():
            rels = _xml(_part(zf, "word/_rels/document.xml.rels", budget))
            for r in rels.findall(f"{PKG_REL}Relationship"):
                if r.get("TargetMode") == "External" and not (r.get("Type") or "").endswith("/hyperlink"):
                    raise _Reject("Documents with external content are not accepted.")
                if (r.get("Type") or "").endswith(("/aFChunk", "/oleObject", "/package")):
                    raise _Reject("Documents with embedded files are not accepted.")
        doc = _xml(_part(zf, "word/document.xml", budget))
        numbering = _xml(_part(zf, "word/numbering.xml", budget)) if "word/numbering.xml" in zf.namelist() else None
        if sum(1 for _ in doc.iter(f"{W}r")) > MAX_DOCX_RUNS:
            raise _Reject("The document is too complex to process safely (too many text runs).")
    body = doc.find(f"{W}body")
    if body is None:
        raise _Reject("The document has no body.")
    return body, numbering


_ROMAN = ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"),
          (4, "iv"), (1, "i"))


def _list_number(n: int, fmt: str) -> str | None:
    """A list counter as Word displays it; None for formats that carry no number (bullets, none) or are not supported."""
    if fmt in ("decimal", "decimalZero"):
        return f"{n:02d}" if fmt == "decimalZero" and n < 10 else str(n)
    if fmt in ("upperLetter", "lowerLetter") and n >= 1:
        s = chr(65 + (n - 1) % 26) * ((n - 1) // 26 + 1)                 # Word: A..Z, AA..ZZ
        return s if fmt == "upperLetter" else s.lower()
    if fmt in ("upperRoman", "lowerRoman") and 1 <= n < 4000:
        out = ""
        for v, r in _ROMAN:
            while n >= v:
                out, n = out + r, n - v
        return out.upper() if fmt == "upperRoman" else out
    return None


def _list_labels(numbering):
    """Word's automatic numbering ("1.", "A)") is not text in document.xml: it is computed from numbering.xml. Returns a function
    paragraph -> displayed label ("" when the paragraph is not in a numbered list), following Word's rules: one counter per list
    definition and level, starting at its start value; a list instance's startOverride restarts it; a level resets the deeper ones.
    Lists Word cannot resolve (unknown id, bullets, unsupported formats) give no label - a number is never invented."""
    if numbering is None:
        return lambda p: ""
    val = lambda e, tag: (e.find(f"{W}{tag}").get(f"{W}val") if e is not None and e.find(f"{W}{tag}") is not None else None)
    levels = {}
    for a in numbering.findall(f"{W}abstractNum"):
        for lvl in a.findall(f"{W}lvl"):
            start = val(lvl, "start")
            levels[(a.get(f"{W}abstractNumId"), lvl.get(f"{W}ilvl"))] = (val(lvl, "numFmt") or "decimal", val(lvl, "lvlText") or "",
                                                                       int(start) if (start or "").isdigit() else 1)
    nums = {}
    for n in numbering.findall(f"{W}num"):
        overrides = {o.get(f"{W}ilvl"): int(val(o, "startOverride")) for o in n.findall(f"{W}lvlOverride")
                     if (val(o, "startOverride") or "").isdigit()}
        nums[n.get(f"{W}numId")] = (val(n, "abstractNumId"), overrides)
    counters: dict[tuple[str, int], int] = {}
    restarted: set[tuple[str, str]] = set()

    def label(p) -> str:
        num_id, ilvl = val(p.find(f"{W}pPr/{W}numPr"), "numId"), val(p.find(f"{W}pPr/{W}numPr"), "ilvl") or "0"
        if num_id is None or num_id == "0" or num_id not in nums or not ilvl.isdigit():
            return ""
        abstract, overrides = nums[num_id]
        lvl = int(ilvl)
        if (abstract, ilvl) not in levels:
            return ""
        fmt, text, start = levels[(abstract, ilvl)]
        if ilvl in overrides and (num_id, ilvl) not in restarted:             # this instance restarts the level once
            restarted.add((num_id, ilvl))
            counters[(abstract, lvl)] = overrides[ilvl] - 1
        counters[(abstract, lvl)] = counters.get((abstract, lvl), start - 1) + 1
        for deeper in [k for k in counters if k[0] == abstract and k[1] > lvl]:
            del counters[deeper]
        def part(m):                                                           # %1 .. %9: the counters of levels 0 .. 8
            k = int(m.group(1)) - 1
            fmt_k, _t, start_k = levels.get((abstract, str(k)), ("decimal", "", 1))
            return _list_number(counters.get((abstract, k), start_k), fmt_k) or "\0"
        shown = re.sub(r"%([1-9])", part, text)
        return "" if "\0" in shown or _list_number(1, fmt) is None else shown + " "
    return label


def _docx_table_rows(tbl) -> list[list[str]]:
    rows = []
    for tr in tbl.findall(f"{W}tr"):
        cells = []
        for tc in tr.findall(f"{W}tc"):
            if tc.find(f"{W}tcPr/{W}gridSpan") is not None or tc.find(f"{W}tcPr/{W}vMerge") is not None:
                raise _Reject("Merged table cells are not supported; every question needs its own row of plain cells.")
            cells.append("\n".join(_docx_paragraph_text(p) for p in tc.findall(f"{W}p")).strip())
        rows.append(cells)
    return rows


def extract_docx(data: bytes, max_questions: int) -> ExtractionResult:
    res = ExtractionResult()
    if data[:4] != b"PK\x03\x04":
        res.errors.append(_err(None, None, "This is not a .docx document."))
        return res
    try:
        body, numbering = _docx_body(data)
    except zipfile.BadZipFile:
        res.errors.append(_err(None, None, "This is not a .docx document."))
        return res
    except _Reject as exc:
        res.errors.append(_err(None, None, str(exc)))
        return res
    except (RuntimeError, NotImplementedError, OSError, ValueError):
        res.errors.append(_err(None, None, "The document could not be read safely."))
        return res

    for tbl in body.findall(f"{W}tbl"):
        try:
            grid = _docx_table_rows(tbl)
        except _Reject as exc:
            res.errors.append(_err(None, None, str(exc)))
            return res
        if not grid:
            continue
        header = [_header(h) for h in grid[0]]          # "Option A", "Negative" ... are the template's columns
        if all(c in header for c in required_columns()):
            if len(set(h for h in header if h)) != len([h for h in header if h]):
                res.errors.append(_err(1, None, "The table header has duplicate column names."))
                return res
            rows = []
            for idx, values in enumerate(grid[1:], start=2):
                if len(values) != len(header):
                    rows.append((idx, None))
                    continue
                row = {h: v.strip() for h, v in zip(header, values, strict=False) if h}
                if any(row.values()):
                    rows.append((idx, row))
            _validate_rows(res, rows, max_questions)
            return _finish(res)

    label = _list_labels(numbering)
    text = "\n".join(label(p) + _docx_paragraph_text(p) for p in body.iter(f"{W}p"))
    rows = parse_question_blocks(text[:MAX_TEXT_CHARS], res)
    if res.errors:
        return _finish(res)
    _validate_rows(res, rows, max_questions)
    return _finish(res)


# ------------------------------------------------------------------ .pdf (text layer only)

def extract_pdf(data: bytes, max_questions: int) -> ExtractionResult:
    res = ExtractionResult()
    if not data.startswith(b"%PDF-"):
        res.errors.append(_err(None, None, "This is not a PDF file."))
        return res
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError:  # pragma: no cover - the dependency is pinned in the image
        res.errors.append(_err(None, None, "PDF extraction is not available on this server."))
        return res
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            res.errors.append(_err(None, None, "Password-protected PDFs are not supported."))
            return res
        if len(reader.pages) > MAX_PDF_PAGES:
            res.errors.append(_err(None, None, f"The PDF has more than {MAX_PDF_PAGES} pages."))
            return res
        chunks, size = [], 0
        for page in reader.pages:
            t = page.extract_text() or ""
            size += len(t)
            if size > MAX_TEXT_CHARS:
                res.errors.append(_err(None, None, "The PDF contains too much text."))
                return res
            chunks.append(t)
    except (PdfReadError, ValueError, KeyError, TypeError, OSError, RecursionError):
        res.errors.append(_err(None, None, "The PDF could not be read safely."))
        return res
    text = "\n".join(chunks)
    if not text.strip():
        res.errors.append(_err(None, None, "This PDF has no text layer (it looks scanned). Scanned papers need image extraction, "
                                             "which is not enabled on this server; upload a text PDF, Word, CSV or Excel file."))
        return res
    rows = parse_question_blocks(text, res)
    if res.errors:
        return _finish(res)
    _validate_rows(res, rows, max_questions)
    return _finish(res)
