"""Reading bulk-import files - Excel (.xlsx) and Word (.docx) - one question row at a time, with the pictures in each row.

Why a separate reader: the paper-upload reader (extraction.py, documents.py) loads a whole file and is limited to 500 questions. A bulk
import may hold 100,000 questions, so this reader STREAMS: XML parts are parsed incrementally (iterparse) and each row is released
as soon as it has been handed over; Excel's shared strings go to a temporary on-disk SQLite table instead of a Python list; pictures are
only located here (which file part belongs to which cell) and their bytes are read one at a time by the caller. Memory therefore stays
bounded by one row plus small per-picture position maps, whatever the file size.

The same safety rules as the paper reader apply (uploaded files are hostile until proven otherwise):
  * no encrypted files, macros, embedded objects/ActiveX, external links or external relationships;
  * XML is parsed with defusedxml (no DTDs, entities or external references) with a nesting-depth limit;
  * every part is read through a byte limit and a compression-ratio check (a "zip bomb" stops reading early);
  * pictures must be PNG/JPEG/GIF/WebP by their bytes, at most 5 MB, and decode completely (question_images.check_image).

Template (the same in Excel and Word): a header row with these columns, in any order (spacing, case and punctuation ignored):
    Chapter | Topic | Subtopic | Difficulty | Question Type | Question Text | Question Image | Option A Text | Option A Image |
    Option B Text | Option B Image | Option C Text | Option C Image | Option D Text | Option D Image | Correct Option
Image columns may be left out when a file has no pictures. Excel: the sheet named "Questions" (or the only sheet); a picture belongs to
the cell under its top-left corner, or to the cell it was placed in ("Place in Cell"). Word: the first table; a picture belongs to the
table cell it is in.

Problems that concern ONE row (a formula, a picture in the wrong column, a corrupt picture ...) are reported on that row, so the rest of
the file can still be imported. Problems with the whole file raise FileRejected with a message safe to show to the user.
"""
import posixpath
import re
import sqlite3
import tempfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import iterparse

from inst_paper.services.extraction import _RICH_NS, _RICH_REL_NS, _RVB_NS, DML, NS, PKG_REL, REL, XDR, _col_index, _text_of
from inst_paper.services.math.office import W, docx_paragraph_text
from inst_paper.services.question_images import MAX_IMAGE_BYTES

# One template for every question type: each row names its type (Question Type: MCQ, MSQ or NAT). The header must have the first
# template's columns (so every MCQ file made before MSQ / NAT still reads exactly as before); the MSQ and NAT columns are optional.
# "Correct Answer" (correct_option) holds the key of every type: an MCQ letter, MSQ letters ("A, C") or a NAT number.
TEXT_COLUMNS = ("chapter", "topic", "subtopic", "difficulty", "question_type", "question_text", "option_a_text", "option_b_text",
                "option_c_text", "option_d_text", "correct_option")
OPTIONAL_TEXT_COLUMNS = ("option_e_text", "option_f_text", "option_g_text", "option_h_text", "tolerance_type", "tolerance", "minimum",
                         "maximum", "answer_format", "decimal_places", "unit")
IMAGE_COLUMNS = ("question_image", *(f"option_{x}_image" for x in "abcdefgh"))
TEMPLATE_COLUMNS = TEXT_COLUMNS + OPTIONAL_TEXT_COLUMNS + IMAGE_COLUMNS
HEADER_ALIASES = {"correct_option_s": "correct_option", "correct_options": "correct_option", "correct_answer": "correct_option",
                  "correct_answer_s": "correct_option", "sub_topic": "subtopic"}

MAX_MEMBERS = 700_000                       # 100,000 questions x 5 pictures + the document's own parts
MAX_STREAMED_PART = 1024 * 1024 * 1024      # 1 GiB of XML in one part (100,000 rows of text are far below)
MAX_SMALL_PART = 8 * 1024 * 1024            # workbook.xml and similar small parts
MAX_COMPRESSION_RATIO = 100
RATIO_CHECK_FROM = 1024 * 1024
MAX_XML_DEPTH = 100
MAX_SHARED_STRINGS = 2_000_000
MAX_CELL_CHARS = 20_000                     # longer than any accepted field; stops a single giant cell early

A = DML                                     # DrawingML main namespace (a:blip)
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
VML = "{urn:schemas-microsoft-com:vml}"


class FileRejected(Exception):
    """The whole file cannot be read. The message is safe to show to the uploader."""


@dataclass
class ReadRow:
    """One question row: its row number as the user sees it, the template's text columns, the picture (file part) of each image
    column, and any problems found while reading it."""
    row_no: int
    data: dict[str, str]
    pictures: dict[str, str] = field(default_factory=dict)
    problems: list[dict] = field(default_factory=list)


def column_name(header: str) -> str:
    """A header cell as a template column name: "Option A Text" -> option_a_text, "Correct Option(s)" -> correct_option."""
    name = "_".join(re.sub(r"[^0-9a-z]+", " ", (header or "").lower()).split())
    return HEADER_ALIASES.get(name, name)


def problem(column: str | None, message: str) -> dict:
    return {"field": column, "message": message}


# ------------------------------------------------------------------ safe access to the zip package

class _Limited:
    """A file-like reader over one zip member that stops at a byte limit and refuses a suspicious compression ratio."""

    def __init__(self, fh, limit: int, compressed: int, name: str):
        self._fh, self._limit, self._compressed, self._name, self._read = fh, limit, max(compressed, 1), name, 0

    def read(self, n: int = -1) -> bytes:
        data = self._fh.read(65536 if n is None or n < 0 else min(n, 65536))
        self._read += len(data)
        if self._read > self._limit:
            raise FileRejected("The file contains a part that is too large to process.")
        if self._read > RATIO_CHECK_FROM and self._read > MAX_COMPRESSION_RATIO * self._compressed:
            raise FileRejected("The file contains a part that expands far more than an ordinary document (a compression bomb).")
        return data


class OfficeFile:
    """An uploaded .xlsx/.docx opened for reading, after the package-level safety checks."""

    def __init__(self, path: str, kind: str):
        try:
            self.zf = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError):
            raise FileRejected("The file is not a valid Excel (.xlsx) or Word (.docx) file.") from None
        infos = self.zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise FileRejected("The file has too many parts.")
        self.names = {i.filename for i in infos}
        forbidden = ("xl/vbaproject", "xl/externallinks/", "xl/activex/", "xl/embeddings/", "word/vbaproject", "word/embeddings/",
                     "word/activex/")
        for info in infos:
            if info.flag_bits & 0x1:
                raise FileRejected("Encrypted (password-protected) files are not supported.")
            n = info.filename.lower()
            if n.startswith(forbidden) or n.endswith(".bin"):
                raise FileRejected("Files with macros, embedded objects or external links are not accepted.")
        main = "xl/workbook.xml" if kind == "XLSX" else "word/document.xml"
        if main not in self.names:
            raise FileRejected(f"The file is not a valid {'Excel (.xlsx)' if kind == 'XLSX' else 'Word (.docx)'} file.")

    def close(self) -> None:
        self.zf.close()

    def stream(self, name: str, limit: int = MAX_STREAMED_PART) -> _Limited:
        info = self.zf.getinfo(name)
        return _Limited(self.zf.open(info), limit, info.compress_size, name)

    def iterparse(self, name: str, events=("end",), limit: int = MAX_STREAMED_PART):
        """Incrementally parse one XML part safely: (event, element) pairs; the caller clears what it has used."""
        depth = 0
        try:
            for event, el in iterparse(self.stream(name, limit), events=("start", "end"), forbid_dtd=True, forbid_entities=True,
                                       forbid_external=True):
                if event == "start":
                    depth += 1
                    if depth > MAX_XML_DEPTH:
                        raise FileRejected("The file's structure is nested too deeply to process safely.")
                else:
                    depth -= 1
                if event in events:
                    yield event, el
        except DefusedXmlException:
            raise FileRejected("The file contains a DOCTYPE, entity or external reference, which is not allowed.") from None
        except ET.ParseError:
            raise FileRejected("The file contains malformed XML.") from None

    def small_xml(self, name: str) -> ET.Element:
        """A small part (workbook, metadata) parsed completely - still through the same limits."""
        root = None
        for _event, el in self.iterparse(name, limit=MAX_SMALL_PART):
            root = el
        return root

    def rels(self, part: str) -> dict[str, str]:
        """Relationship id -> internal part name for one part (streamed: a drawing of 100,000 pictures has 100,000 relationships)."""
        folder, base = posixpath.split(part)
        rels_name = posixpath.join(folder, "_rels", base + ".rels")
        out: dict[str, str] = {}
        if rels_name not in self.names:
            return out
        for _event, r in self.iterparse(rels_name):
            if r.tag != f"{PKG_REL}Relationship":
                continue
            kind = r.get("Type") or ""
            if r.get("TargetMode") == "External":
                if kind.endswith("/hyperlink"):            # a link in the text is harmless and never followed
                    r.clear()
                    continue
                raise FileRejected("The file links to external content, which is not allowed.")
            if kind.endswith(("/aFChunk", "/oleObject", "/package")):
                raise FileRejected("Files with embedded files are not accepted.")
            t = r.get("Target") or ""
            out[r.get("Id") or ""] = posixpath.normpath(t[1:] if t.startswith("/") else posixpath.join(folder, t))
            r.clear()
        return out

    def picture_bytes(self, name: str) -> bytes | str:
        """A picture's bytes, or a problem message when it is too large or missing (read with a hard limit)."""
        if name not in self.names:
            return "The picture could not be found inside the file."
        info = self.zf.getinfo(name)
        if info.file_size > MAX_IMAGE_BYTES:
            return f"The picture is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB; use a smaller picture."
        with self.zf.open(info) as fh:
            data = fh.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            return f"The picture is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB; use a smaller picture."
        return data


# ------------------------------------------------------------------ header

def _header_columns(cells: dict[int, str]) -> dict[int, str]:
    """Header row -> {column index: template column}. FileRejected for unknown, duplicate or missing columns."""
    columns: dict[int, str] = {}
    unknown = []
    for index, text in sorted(cells.items()):
        name = column_name(text)
        if not name:
            continue
        if name not in TEMPLATE_COLUMNS:
            unknown.append(text.strip())
        elif name in columns.values():
            raise FileRejected(f"The column “{text.strip()}” appears twice in the header row.")
        else:
            columns[index] = name
    if unknown:
        raise FileRejected("Unknown column(s) in the header row: " + ", ".join(f"“{u}”" for u in unknown[:10])
                           + ". Use the template's column names.")
    missing = [c for c in TEXT_COLUMNS if c not in columns.values()]
    if missing:
        raise FileRejected("The header row is missing these template columns: " + ", ".join(missing) + ".")
    return columns


def _row_from_cells(row_no: int, columns: dict[int, str], cells: dict[int, str], pictures: dict[int, list[str]],
                    problems: list[dict]) -> ReadRow:
    """Assemble one ReadRow: text of the text columns, one picture per image column, and every misplaced value as a problem."""
    data = {c: "" for c in TEXT_COLUMNS}
    data.update({c: "" for c in OPTIONAL_TEXT_COLUMNS if c in columns.values()})
    found: dict[str, str] = {}
    for index, value in cells.items():
        name = columns.get(index)
        if name is None:
            problems.append(problem(None, "A value is typed outside the template's columns; it was ignored."))
        elif name in IMAGE_COLUMNS:
            problems.append(problem(name, "Image columns hold pictures only; text typed there was ignored."))
        else:
            data[name] = value
    for index, media in pictures.items():
        name = columns.get(index)
        if name not in IMAGE_COLUMNS:
            problems.append(problem(name, f"A picture is placed in {('the ' + name) if name else 'a column outside the template'}; "
                                          "pictures belong only in the image columns."))
        elif len(media) > 1:
            problems.append(problem(name, f"{len(media)} pictures are placed in this cell; keep exactly one."))
        else:
            found[name] = media[0]
    return ReadRow(row_no, data, found, problems)


# ------------------------------------------------------------------ Excel

class _SharedStrings:
    """Excel's shared-string table kept in a temporary SQLite file, so 2,000,000 strings never sit in memory together."""

    def __init__(self, office: OfficeFile, workdir: str):
        self.db = sqlite3.connect(f"{workdir}/shared_strings.sqlite")
        self.db.execute("CREATE TABLE s (i INTEGER PRIMARY KEY, t TEXT NOT NULL)")
        if "xl/sharedStrings.xml" not in office.names:
            return
        batch, index = [], 0
        for _event, si in office.iterparse("xl/sharedStrings.xml"):
            if si.tag != f"{NS}si":
                continue
            batch.append((index, _text_of(si)[:MAX_CELL_CHARS + 1]))
            si.clear()
            index += 1
            if index > MAX_SHARED_STRINGS:
                raise FileRejected("The workbook has too many text cells to process safely.")
            if len(batch) >= 5000:
                self.db.executemany("INSERT INTO s VALUES (?, ?)", batch)
                batch = []
        self.db.executemany("INSERT INTO s VALUES (?, ?)", batch)

    def get(self, index: int) -> str | None:
        row = self.db.execute("SELECT t FROM s WHERE i = ?", (index,)).fetchone()
        return row[0] if row else None

    def close(self) -> None:
        self.db.close()


def _questions_sheet(office: OfficeFile) -> str:
    """The worksheet part holding the questions: the sheet named "Questions", else the only visible sheet."""
    book = office.small_xml("xl/workbook.xml")
    sheets = [s for s in book.iter(f"{NS}sheet")]
    chosen = [s for s in sheets if (s.get("name") or "").strip().lower() == "questions"]
    if not chosen:
        visible = [s for s in sheets if s.get("state", "visible") == "visible"]
        if len(visible) != 1:
            raise FileRejected("Put the questions on a sheet named “Questions”.")
        chosen = visible
    if chosen[0].get("state", "visible") != "visible":
        raise FileRejected("The “Questions” sheet must not be hidden.")
    target = office.rels("xl/workbook.xml").get(chosen[0].get(f"{REL}id") or "")
    if not target or not target.startswith("xl/worksheets/") or target not in office.names:
        raise FileRejected("The questions sheet could not be located in the workbook.")
    return target


def _anchored_pictures(office: OfficeFile, sheet: str) -> dict[int, dict[int, list[str]]]:
    """Pictures placed over cells, by row: {row number: {0-based column of the picture's top-left corner: [media parts]}}.
    Indexed by row so each streamed row finds its pictures directly (never a scan of every picture per row)."""
    out: dict[int, dict[int, list[str]]] = {}
    for drawing in office.rels(sheet).values():
        if not drawing.startswith("xl/drawings/") or drawing not in office.names:
            continue
        targets = office.rels(drawing)
        for _event, anchor in office.iterparse(drawing):
            tag = anchor.tag.removeprefix(XDR)
            if tag not in ("twoCellAnchor", "oneCellAnchor", "absoluteAnchor"):
                continue
            pic = anchor.find(f"{XDR}pic")
            start = anchor.find(f"{XDR}from")
            if pic is None:
                raise FileRejected("The sheet contains a chart, shape or text box; only pictures in the image columns are supported.")
            if tag == "absoluteAnchor" or start is None:
                raise FileRejected("A picture in the sheet is not attached to a cell; place each picture in its question's row.")
            try:
                cell = (int(start.findtext(f"{XDR}row") or "") + 1, int(start.findtext(f"{XDR}col") or ""))
            except ValueError:
                raise FileRejected("A picture in the sheet has an invalid position.") from None
            blip = pic.find(f"{XDR}blipFill/{A}blip")
            media = targets.get(blip.get(f"{REL}embed") or "") if blip is not None else None
            out.setdefault(cell[0], {}).setdefault(cell[1], []).append(media or "")    # "" = cannot be found: reported on its row
            anchor.clear()
    return out


def _in_cell_pictures(office: OfficeFile) -> dict[int, str]:
    """Pictures placed IN cells (Excel 365 "Place in Cell"): a cell's value-metadata index (vm, 1-based) -> media part.
    vm -> valueMetadata block -> futureMetadata rich-value block -> rich value -> picture relationship -> media."""
    needed = ("xl/metadata.xml", "xl/richData/rdrichvalue.xml", "xl/richData/richValueRel.xml")
    if not all(n in office.names for n in needed):
        return {}
    value_blocks: list[int | None] = []
    future: list[int | None] = []
    section = None
    for event, el in office.iterparse("xl/metadata.xml", events=("start", "end")):
        if event == "start" and el.tag in (f"{NS}valueMetadata", f"{NS}futureMetadata"):
            section = el.tag
        elif event == "end" and el.tag == f"{NS}bk":
            if section == f"{NS}valueMetadata":
                rc = el.find(f"{NS}rc")
                value_blocks.append(_int(rc.get("v")) if rc is not None else None)
            elif section == f"{NS}futureMetadata":
                rvb = el.find(f".//{_RVB_NS}rvb")
                future.append(_int(rvb.get("i")) if rvb is not None else None)
            el.clear()
    rich_values: list[int | None] = []
    for _event, rv in office.iterparse("xl/richData/rdrichvalue.xml"):
        if rv.tag == f"{_RICH_NS}rv":
            rich_values.append(_int(rv.findtext(f"{_RICH_NS}v")))
            rv.clear()
    rel_ids = [r.get(f"{REL}id") for _e, r in office.iterparse("xl/richData/richValueRel.xml") if r.tag == f"{_RICH_REL_NS}rel"]
    targets = office.rels("xl/richData/richValueRel.xml")
    out: dict[int, str] = {}
    for vm in range(1, len(value_blocks) + 1):
        try:
            rv = rich_values[future[value_blocks[vm - 1]]]
            out[vm] = targets.get(rel_ids[rv] or "", "")
        except (IndexError, TypeError):
            out[vm] = ""
    return out


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _read_xlsx(office: OfficeFile, workdir: str, max_rows: int) -> Iterator[ReadRow]:
    """Every question row of an uploaded workbook, in order, streamed. FileRejected for problems with the whole file."""
    strings = _SharedStrings(office, workdir)
    try:
        sheet = _questions_sheet(office)
        anchored = _anchored_pictures(office, sheet)
        in_cell = _in_cell_pictures(office)
        yield from _xlsx_rows(office, sheet, strings, anchored, in_cell, max_rows)
    finally:
        strings.close()


def _xlsx_rows(office, sheet, strings, anchored, in_cell, max_rows) -> Iterator[ReadRow]:
    columns: dict[int, str] | None = None
    seen_rows: set[int] = set()
    count = 0
    sheet_data = None
    for event, el in office.iterparse(sheet, events=("start", "end")):
        if event == "start":
            if el.tag == f"{NS}sheetData":
                sheet_data = el
            continue
        if el.tag == f"{NS}mergeCells" and len(el):
            raise FileRejected("Merged cells are not supported; every question needs its own row of plain cells.")
        if el.tag != f"{NS}row":
            continue
        row_no = _int(el.get("r"))
        if row_no is None:
            raise FileRejected("The sheet contains an invalid row number.")
        problems: list[dict] = []
        if el.get("hidden") in ("1", "true"):
            problems.append(problem(None, "This row is hidden in Excel; unhide it (hidden rows are not imported silently)."))
        cells: dict[int, str] = {}
        pictures: dict[int, list[str]] = {}
        for c in el.iter(f"{NS}c"):
            col = _col_index(c.get("r", "")) if c.get("r") else None
            if col is None:
                continue
            if c.get("vm"):
                pictures.setdefault(col, []).append(in_cell.get(_int(c.get("vm")) or 0, ""))
                continue
            value, issue = _cell_value(c, strings)
            if issue:
                problems.append(problem(None, issue))
            elif value:
                cells[col] = value
        for col, media in anchored.get(row_no, {}).items():
            pictures.setdefault(col, []).extend(media)
        # release the row: iterparse keeps parsed elements attached to sheetData otherwise
        el.clear()
        if sheet_data is not None:
            del sheet_data[:]
        if columns is None:
            if row_no != 1:
                raise FileRejected("The header must be in row 1.")
            if pictures:
                raise FileRejected("A picture is placed in the header row; put each picture in its question's row.")
            columns = _header_columns(cells)
            seen_rows.add(row_no)
            continue
        seen_rows.add(row_no)
        if not cells and not pictures and not problems:
            continue                                           # an empty row is not a question
        count += 1
        if count > max_rows:
            raise FileRejected(f"The file has more than {max_rows:,} questions; split it into smaller files.")
        yield _row_from_cells(row_no, columns, cells, pictures, problems)
    if columns is None:
        raise FileRejected("The sheet is empty: it needs the template's header row and at least one question.")
    # pictures in rows that have no cells at all (Excel writes no <row> for them): still reported, on their own row
    for row_no in sorted(set(anchored) - seen_rows):
        if row_no == 1:
            raise FileRejected("A picture is placed in the header row; put each picture in its question's row.")
        count += 1
        if count > max_rows:
            raise FileRejected(f"The file has more than {max_rows:,} questions; split it into smaller files.")
        yield _row_from_cells(row_no, columns, {}, {col: list(media) for col, media in anchored[row_no].items()}, [])


def _cell_value(c: ET.Element, strings: _SharedStrings) -> tuple[str, str | None]:
    """(text, problem) of one cell. Formulas, booleans, errors and dates are row problems, not guesses."""
    if c.find(f"{NS}f") is not None:
        return "", "A cell contains a formula; formulas are not evaluated. Paste the values instead."
    kind, v = c.get("t"), c.find(f"{NS}v")
    if kind == "inlineStr":
        inline = c.find(f"{NS}is")
        value = _text_of(inline) if inline is not None else ""
    elif kind == "s":
        index = _int(v.text) if v is not None else None
        value = strings.get(index) if index is not None else None
        if value is None:
            return "", "A cell refers to text that is missing from the workbook."
    elif kind in ("b", "e", "d"):
        return "", "A cell holds a true/false value, an error or a date; use plain text and numbers."
    else:
        value = (v.text or "") if v is not None else ""
    value = value.strip()
    if len(value) > MAX_CELL_CHARS:
        return "", f"A cell has more than {MAX_CELL_CHARS:,} characters."
    return value, None


# ------------------------------------------------------------------ Word

def _docx_rows(office: OfficeFile, rels: dict[str, str], max_rows: int) -> Iterator[ReadRow]:
    depth = 0                # table nesting: 1 = the questions table
    tables_done = 0
    columns: dict[int, str] | None = None
    row_no = 0
    count = 0
    for event, el in office.iterparse("word/document.xml", events=("start", "end")):
        if el.tag != f"{W}tbl":
            if event == "end" and el.tag == f"{W}tr" and depth == 1 and tables_done == 0:
                row_no += 1
                cells, pictures, problems = _docx_cells(el, rels)
                el.clear()
                if columns is None:
                    columns = _header_columns(cells)
                    continue
                if not cells and not pictures and not problems:
                    continue
                count += 1
                if count > max_rows:
                    raise FileRejected(f"A Word file may hold at most {max_rows:,} questions; use the Excel template for more.")
                yield _row_from_cells(row_no, columns, cells, pictures, problems)
            continue
        if event == "start":
            depth += 1
            if depth > 1 and tables_done == 0:
                raise FileRejected("Tables inside the questions table are not supported; keep one plain table.")
        else:
            depth -= 1
            if depth == 0:
                tables_done += 1
                el.clear()
                break                        # only the first table holds questions; the rest of the document is not read
    if columns is None:
        raise FileRejected("The document has no questions table: add the template's table with its header row.")


def _docx_cells(tr: ET.Element, rels: dict[str, str]) -> tuple[dict[int, str], dict[int, list[str]], list[dict]]:
    """Text, pictures and problems of one table row."""
    cells: dict[int, str] = {}
    pictures: dict[int, list[str]] = {}
    problems: list[dict] = []
    for index, tc in enumerate(tr.findall(f"{W}tc")):
        props = tc.find(f"{W}tcPr")
        if props is not None and (props.find(f"{W}gridSpan") is not None or props.find(f"{W}vMerge") is not None):
            problems.append(problem(None, "Merged table cells are not supported; give every value its own cell."))
        media = []
        for blip in tc.iter(f"{A}blip"):
            media.append(rels.get(blip.get(f"{REL}embed") or "", ""))
        if any(True for _ in tc.iter(f"{VML}imagedata")):
            problems.append(problem(None, "A picture uses an old Word format; delete it and insert it again (Insert > Pictures)."))
        if media:
            pictures[index] = media
        text = "\n".join(t for t in (docx_paragraph_text(p).strip() for p in tc.findall(f"{W}p")) if t)
        if media:
            text = ""                       # the text form of a picture cell is only Word's picture marker
        if len(text) > MAX_CELL_CHARS:
            problems.append(problem(None, f"A cell has more than {MAX_CELL_CHARS:,} characters."))
        elif text:
            cells[index] = text
    return cells, pictures, problems


class BulkFile:
    """An uploaded bulk-import file opened for reading: `rows()` streams its question rows and `picture_bytes()` reads one picture
    the rows refer to. Use as a context manager so the file and the temporary shared-string table are always released."""

    def __init__(self, path: str, file_format: str):
        self.format = file_format
        self.office = OfficeFile(path, file_format)
        self._workdir = tempfile.TemporaryDirectory(prefix="bulk-import-")

    def rows(self, max_rows: int) -> Iterator[ReadRow]:
        if self.format == "XLSX":
            return _read_xlsx(self.office, self._workdir.name, max_rows)
        return _docx_rows(self.office, self.office.rels("word/document.xml"), max_rows)

    def picture_bytes(self, name: str) -> bytes | str:
        return self.office.picture_bytes(name)

    def __enter__(self) -> "BulkFile":
        return self

    def __exit__(self, *exc) -> None:
        self.office.close()
        self._workdir.cleanup()
