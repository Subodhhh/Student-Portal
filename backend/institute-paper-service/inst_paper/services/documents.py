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

from inst_paper.services.extraction import (
    MAX_ZIP_MEMBERS, OPTIONAL, PKG_REL, REQUIRED, ExtractionResult, _err, _finish, _part, _Reject, _validate_rows, _xml,
)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_PDF_PAGES = 60
MAX_TEXT_CHARS = 2_000_000

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


def parse_question_blocks(text: str, res: ExtractionResult) -> list[tuple[int, dict[str, str] | None]]:
    """Numbered question blocks -> rows for the shared validator. Structural problems are added to `res.errors`."""
    defaults = {"marks": "", "negative_marks": ""}
    rows: list[tuple[int, dict[str, str] | None]] = []
    current: dict[str, str] | None = None
    number = 0
    last_key: str | None = None
    expected = 1

    def close():
        if current is not None:
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
            last_key = key
            continue
        f = _FIELD.match(line)
        if f:
            key = _field_key(f.group(1))
            current[key] = f.group(2).strip().strip("()")
            last_key = key
            continue
        if last_key in ("correct", "marks", "negative_marks"):
            res.errors.append(_err(number, None, f"Unrecognised line after {last_key}: \"{line[:60]}\"."))
            continue
        current[last_key] = f"{current[last_key]} {line}".strip()   # continuation of a multi-line field
    close()
    return rows


# ------------------------------------------------------------------ .docx

def _docx_paragraph_text(p) -> str:
    out = []
    for node in p.iter():
        if node.tag == f"{W}t":
            out.append(node.text or "")
        elif node.tag in (f"{W}tab",):
            out.append(" ")
        elif node.tag in (f"{W}br", f"{W}cr"):
            out.append("\n")
    return "".join(out)


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
    body = doc.find(f"{W}body")
    if body is None:
        raise _Reject("The document has no body.")
    return body


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
        body = _docx_body(data)
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
        header = [h.strip().lower() for h in grid[0]]
        if all(c in header for c in REQUIRED):
            if len(set(h for h in header if h)) != len([h for h in header if h]):
                res.errors.append(_err(1, None, "The table header has duplicate column names."))
                return res
            rows = []
            for idx, values in enumerate(grid[1:], start=2):
                if len(values) != len(header):
                    rows.append((idx, None))
                    continue
                row = {h: v.strip() for h, v in zip(header, values) if h}
                if any(row.values()):
                    rows.append((idx, row))
            _validate_rows(res, rows, max_questions)
            return _finish(res)

    text = "\n".join(_docx_paragraph_text(p) for p in body.iter(f"{W}p"))
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
