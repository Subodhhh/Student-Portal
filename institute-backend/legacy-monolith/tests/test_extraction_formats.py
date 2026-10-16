"""Spreadsheet (.xlsx) and image extraction (tests first). Both are all-or-nothing and treat the upload as hostile."""
import io
import struct
import zipfile
import zlib

import pytest

from app.config import settings
from app.core.roles import Role
from app.services import extraction
from tests.test_papers import API, setup, tmp_store, upload  # noqa: F401  (fixtures)

HEADER = ["question", "option_a", "option_b", "option_c", "option_d", "correct", "marks", "negative_marks", "explanation", "topic"]
ROWS = [["What is 2+2?", "1", "2", "3", "4", "D", 4, 1, "Basic sum", "Arithmetic"],
        ["Capital of France?", "Paris", "Rome", "Berlin", "Madrid", "A", 4, 0.25, None, "Geography"]]
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def workbook(rows=ROWS, header=HEADER, sheets=1, customise=None) -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)
    for r in rows:
        ws.append(r)
    for i in range(sheets - 1):
        wb.create_sheet(f"extra{i}")
    if customise:
        customise(wb, ws)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def rewrite(data: bytes, member: str, fn) -> bytes:
    """Return the same workbook with one XML part transformed (to build hostile variants)."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            body = src.read(info.filename)
            dst.writestr(info.filename, fn(body) if info.filename == member else body)
    return out.getvalue()


def add_member(data: bytes, name: str, body: bytes) -> bytes:
    out = io.BytesIO(data)
    with zipfile.ZipFile(out, "a", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, body)
    return out.getvalue()


def with_shared_strings(data: bytes) -> bytes:
    """Re-encode the inline strings the way Excel writes them (xl/sharedStrings.xml + t="s")."""
    import re
    strings: list[str] = []

    def sheet(body: bytes) -> bytes:
        def repl(m):
            strings.append(m.group(2).decode())
            return b'<c r="%s" t="s"><v>%d</v></c>' % (m.group(1), len(strings) - 1)
        return re.sub(rb'<c r="([A-Z]+\d+)" t="inlineStr"><is><t[^>]*>(.*?)</t></is></c>', repl, body)

    out = rewrite(data, "xl/worksheets/sheet1.xml", sheet)
    xml = "".join(f"<si><t>{t}</t></si>" for t in strings)
    return add_member(out, "xl/sharedStrings.xml", f'<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(strings)}" uniqueCount="{len(strings)}">{xml}</sst>'.encode())


# ------------------------------------------------------------------ xlsx

def test_excel_style_shared_strings_give_the_same_result_as_inline_strings():
    plain, shared = extraction.extract_xlsx(workbook(), 500), extraction.extract_xlsx(with_shared_strings(workbook()), 500)
    assert shared.ok and [(q.stem, q.options, q.correct) for q in shared.questions] == [(q.stem, q.options, q.correct) for q in plain.questions]

def test_xlsx_yields_exactly_the_same_questions_as_the_equivalent_csv():
    x = extraction.extract_xlsx(workbook(), 500)
    c = extraction.extract_csv(("\n".join([",".join(HEADER), "What is 2+2?,1,2,3,4,D,4,1,Basic sum,Arithmetic",
                                           "Capital of France?,Paris,Rome,Berlin,Madrid,A,4,0.25,,Geography"]) + "\n").encode(), 500)
    assert x.ok, x.errors
    assert [(q.stem, q.options, q.correct, q.marks, q.negative, q.explanation, q.topic) for q in x.questions] == \
           [(q.stem, q.options, q.correct, q.marks, q.negative, q.explanation, q.topic) for q in c.questions]
    assert [q.row for q in x.questions] == [2, 3]


def test_xlsx_numeric_option_cells_and_integer_marks_are_read_as_text_and_numbers():
    rows = [["Pick one", 1, 2, 3, 4, "b", 4.0, 0, None, None]]
    r = extraction.extract_xlsx(workbook(rows), 500)
    assert r.ok and r.questions[0].options == {"A": "1", "B": "2", "C": "3", "D": "4"} and r.questions[0].correct == "B"


def test_one_bad_row_rejects_the_whole_xlsx_paper():
    rows = ROWS + [["Bad?", "a", "b", "c", "d", "Z", 4, 1, None, None], ["Fine?", "a", "b", "c", "d", "A", 4, 1, None, None]]
    r = extraction.extract_xlsx(workbook(rows), 500)
    assert not r.ok and r.questions == [] and any(e["row"] == 4 and e["field"] == "correct" for e in r.errors)


def test_blank_rows_are_ignored_but_missing_cells_are_not():
    r = extraction.extract_xlsx(workbook(ROWS + [[None] * 10]), 500)
    assert r.ok and len(r.questions) == 2
    r = extraction.extract_xlsx(workbook([["Q?", "a", "b", None, "d", "A", 4, 1, None, None]]), 500)
    assert not r.ok and any(e["field"] == "option_c" for e in r.errors)


def test_xlsx_with_formulas_is_rejected():
    r = extraction.extract_xlsx(workbook([["Q?", "a", "b", "c", "d", "A", "=1+3", 1, None, None]]), 500)
    assert not r.ok and any("formula" in e["message"].lower() for e in r.errors)


def test_xlsx_with_more_than_one_sheet_is_rejected():
    r = extraction.extract_xlsx(workbook(sheets=2), 500)
    assert not r.ok and any("one sheet" in e["message"].lower() for e in r.errors)


def test_xlsx_with_hidden_rows_columns_or_merged_cells_is_rejected():
    def hide_row(wb, ws): ws.row_dimensions[2].hidden = True
    def hide_col(wb, ws): ws.column_dimensions["B"].hidden = True
    def merge(wb, ws): ws.merge_cells("H1:I1")
    for fn in (hide_row, hide_col, merge):
        r = extraction.extract_xlsx(workbook(customise=fn), 500)
        assert not r.ok, fn.__name__


def test_xlsx_missing_required_column_is_rejected():
    r = extraction.extract_xlsx(workbook(header=[h for h in HEADER if h != "correct"], rows=[r[:5] + r[6:] for r in ROWS]), 500)
    assert not r.ok and "correct" in r.errors[0]["message"]


def test_xlsx_question_limit_is_enforced():
    r = extraction.extract_xlsx(workbook(ROWS * 3), 5)
    assert not r.ok


@pytest.mark.parametrize("data", [b"", b"PK\x03\x04 not really a zip", b"%PDF-1.7 ...", b"plain text,not a spreadsheet\n"])
def test_non_xlsx_bytes_are_rejected(data):
    assert not extraction.extract_xlsx(data, 500).ok


def test_xlsx_xml_entities_and_doctype_are_rejected():
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
    ok_shared = b'<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"></sst>'
    for part in ("xl/sharedStrings.xml", "xl/worksheets/sheet1.xml", "xl/workbook.xml"):
        base = add_member(workbook(), "xl/sharedStrings.xml", ok_shared)
        evil = rewrite(base, part, lambda body: bomb + (body.split(b"?>", 1)[1] if b"?>" in body else body))
        r = extraction.extract_xlsx(evil, 500)
        assert not r.ok and any("entit" in e["message"].lower() or "doctype" in e["message"].lower() for e in r.errors), part


def test_xlsx_zip_bomb_and_macro_parts_are_rejected(monkeypatch):
    bomb = add_member(workbook(), "xl/sharedStrings.xml", b'<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"></sst>' + b" " * 30_000_000)
    assert len(bomb) < 100_000 and not extraction.extract_xlsx(bomb, 500).ok            # tiny on the wire, huge when expanded
    buf = io.BytesIO(workbook())
    with zipfile.ZipFile(buf, "a") as z:
        z.writestr("xl/vbaProject.bin", b"macro")
    assert not extraction.extract_xlsx(buf.getvalue(), 500).ok
    many = io.BytesIO()
    with zipfile.ZipFile(many, "w") as z:
        for i in range(400):
            z.writestr(f"f{i}.txt", b"x")
    assert not extraction.extract_xlsx(many.getvalue(), 500).ok


# ------------------------------------------------------------------ image (OCR behind an interface)

def png(width=40, height=30) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff" * width for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class FakeOcr:
    def __init__(self, items):
        self.items, self.calls = items, 0

    def read_questions(self, image: bytes, mime: str):
        self.calls += 1
        return self.items


def item(n=1, confidence=0.995, **over):
    base = {"stem": f"Question {n}?", "options": {"A": "a", "B": "b", "C": "c", "D": "d"}, "correct": "B", "marks": "4", "negative": "1", "confidence": confidence}
    return extraction.OcrQuestion(**{**base, **over})


def test_images_are_rejected_when_no_ocr_backend_is_configured():
    r = extraction.extract_image(png(), "image/png", None, 500)
    assert not r.ok and "not enabled" in r.errors[0]["message"].lower()


def test_image_extraction_accepts_confident_complete_questions_and_applies_the_same_validation():
    ocr = FakeOcr([item(1), item(2)])
    r = extraction.extract_image(png(), "image/png", ocr, 500)
    assert r.ok and len(r.questions) == 2 and r.questions[0].correct == "B" and ocr.calls == 1
    dup = extraction.extract_image(png(), "image/png", FakeOcr([item(1), item(1)]), 500)
    assert not dup.ok                                              # duplicate stems rejected by the shared validator


@pytest.mark.parametrize("bad", [item(2, confidence=0.5), item(2, correct="Z"), item(2, stem=""), item(2, marks="abc"),
                                 item(2, options={"A": "a", "B": "b", "C": "c"})])
def test_one_unreliable_question_rejects_the_entire_image_paper(bad):
    r = extraction.extract_image(png(), "image/png", FakeOcr([item(1), bad, item(3)]), 500)
    assert not r.ok and r.questions == [] and r.errors


def test_ocr_failure_or_empty_output_rejects_the_paper():
    class Boom:
        def read_questions(self, image, mime): raise RuntimeError("engine crashed")
    assert not extraction.extract_image(png(), "image/png", Boom(), 500).ok
    assert not extraction.extract_image(png(), "image/png", FakeOcr([]), 500).ok


@pytest.mark.parametrize("data,mime", [(b"not an image at all", "image/png"), (b"\xff\xd8\xff" + b"x" * 10, "image/png"), (png(), "image/jpeg"),
                                       (png(30000, 30000), "image/png")])
def test_image_magic_bytes_mime_and_dimensions_are_verified_before_ocr(data, mime):
    ocr = FakeOcr([item(1)])
    assert not extraction.extract_image(data, mime, ocr, 500).ok and ocr.calls == 0


# ------------------------------------------------------------------ upload endpoint

def put(client, h, world, setup, data: bytes, name: str, ctype: str, title="Spreadsheet paper"):
    return client.post(f"{API}/branches/{world['p1b1']}/subjects/{setup['phy']}/papers/upload",
                       files={"file": (name, io.BytesIO(data), ctype)}, data={"title": title}, headers=h)


def test_xlsx_upload_creates_a_validated_version(client, world, setup, make_staff, login):
    _, code = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    h = login(code)
    r = put(client, h, world, setup, workbook(), "paper.xlsx", XLSX_MIME)
    assert r.status_code == 201 and r.json()["question_count"] == 2 and r.json()["total_marks"] == "8.00"


def test_bad_xlsx_upload_is_rejected_whole_and_reported(client, world, setup, make_staff, login):
    _, code = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    h = login(code)
    r = put(client, h, world, setup, workbook(ROWS + [["Bad?", "a", "b", "c", "d", "Z", 4, 1, None, None]]), "paper.xlsx", XLSX_MIME)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "EXTRACTION_FAILED"
    up = client.get(f"{API}/uploads/{r.json()['detail']['upload_id']}", headers=h).json()
    assert up["status"] == "REJECTED" and up["question_count"] == 0


@pytest.mark.parametrize("name,ctype", [("paper.xlsx", "text/csv"), ("paper.xls", "application/vnd.ms-excel"), ("paper.xlsm", XLSX_MIME), ("paper.docx", XLSX_MIME),
                                        ("paper.pdf", "application/pdf"), ("paper.svg", "image/svg+xml")])
def test_unsupported_or_mismatched_file_types_are_refused(client, world, setup, make_staff, login, name, ctype):
    _, code = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    assert put(client, login(code), world, setup, workbook(), name, ctype).status_code == 415


def test_a_renamed_non_xlsx_file_is_rejected_by_content_not_by_name(client, world, setup, make_staff, login):
    _, code = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    r = put(client, login(code), world, setup, b"just some text", "paper.xlsx", XLSX_MIME)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "EXTRACTION_FAILED"


def test_image_upload_without_an_ocr_backend_is_rejected_clearly(client, world, setup, make_staff, login):
    _, code = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    r = put(client, login(code), world, setup, png(), "scan.png", "image/png")
    assert r.status_code == 422 and "not enabled" in r.json()["detail"]["errors"][0]["message"].lower()


def test_image_upload_with_a_backend_imports_only_when_everything_is_reliable(client, world, setup, make_staff, login):
    from app.services.extraction import set_ocr_backend
    _, code = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    h = login(code)
    try:
        set_ocr_backend(FakeOcr([item(1), item(2, confidence=0.4)]))
        assert put(client, h, world, setup, png(), "scan.png", "image/png").status_code == 422
        set_ocr_backend(FakeOcr([item(1), item(2)]))
        ok = put(client, h, world, setup, png(), "scan.png", "image/png", title="Scanned paper")
        assert ok.status_code == 201 and ok.json()["question_count"] == 2
    finally:
        set_ocr_backend(None)


def test_oversized_spreadsheet_is_refused_before_parsing(client, world, setup, make_staff, login, monkeypatch):
    _, code = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    monkeypatch.setattr(settings, "max_upload_bytes", 1000)
    assert put(client, login(code), world, setup, workbook(), "paper.xlsx", XLSX_MIME).status_code == 413
