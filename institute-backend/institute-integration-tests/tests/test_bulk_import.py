"""Bulk question import (Phase D): upload -> worker reads and validates -> preview with corrections -> confirm -> worker imports.

Covers Excel and Word files with pictures (placed over cells, placed in cells, inside Word table cells), validation, duplicates (in the
file and against the bank), taxonomy resolution, authorisation and provider isolation, hostile files, limits, the exactly-once
confirmation, partial success, worker crashes and resumption, expiry, cancellation and cleanup, and an S3 (moto) run.
The worker's functions are called directly with the test database (the real worker runs the same functions in its own container)."""
import hashlib
import io
import zipfile
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage

from institute_common.core.roles import Role
from institute_common.database import utcnow
from institute_common.models.bulk_import import BulkImportJob, BulkImportRow
from institute_common.models.papers import Question, QuestionImageAsset, QuestionVersion
from institute_common.objectstore import LocalObjectStore, set_object_store
from inst_paper.services import bulk_import, taxonomy
from tests.test_question_images import png

API = "/api/v1"
HEADERS = ["Chapter", "Topic", "Subtopic", "Difficulty", "Question Type", "Question Text", "Question Image", "Option A Text",
           "Option A Image", "Option B Text", "Option B Image", "Option C Text", "Option C Image", "Option D Text", "Option D Image",
           "Correct Option"]
COLUMN = {h: chr(65 + i) for i, h in enumerate(HEADERS)}        # header -> Excel column letter


def q(stem="What is the SI unit of force?", a="Newton", b="Joule", c="Watt", d="Pascal", correct="A", chapter="Laws of Motion",
      topic="Force", difficulty="Easy", qtype="MCQ", **over) -> dict:
    row = {"Chapter": chapter, "Topic": topic, "Subtopic": "", "Difficulty": difficulty, "Question Type": qtype, "Question Text": stem,
           "Option A Text": a, "Option B Text": b, "Option C Text": c, "Option D Text": d, "Correct Option": correct}
    row.update(over)
    return row


def xlsx(rows: list[dict], pictures: list[tuple[int, str, bytes]] = (), sheet="Questions", headers=HEADERS) -> bytes:
    """A workbook in the template's shape; pictures = (row number, header, png bytes) placed over that cell."""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(headers)
    for r in rows:
        ws.append([r.get(h, "") or None for h in headers])
    for row_no, header, data in pictures:
        img = XLImage(io.BytesIO(data))
        img.anchor = f"{COLUMN[header]}{row_no}"
        ws.add_image(img)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


W_NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"')


def docx(rows: list[dict], pictures: dict[tuple[int, str], bytes] | None = None) -> bytes:
    """A Word document with the template table; pictures = {(table row number, header): png} inside that cell."""
    pictures = pictures or {}
    rels, media = [], {}

    def cell(row_no, header, text):
        if (row_no, header) in pictures:
            rid = f"rId{len(rels) + 10}"
            name = f"media/image{len(rels) + 1}.png"
            rels.append(f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                        f'Target="{name}"/>')
            media[f"word/{name}"] = pictures[(row_no, header)]
            return (f'<w:tc><w:p><w:r><w:drawing><wp:inline><a:graphic><a:graphicData><pic:pic><pic:blipFill><a:blip r:embed="{rid}"/>'
                    '</pic:blipFill></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p></w:tc>')
        return f'<w:tc><w:p><w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p></w:tc>'

    table = "<w:tbl>" + "<w:tr>" + "".join(cell(1, h, h) for h in HEADERS) + "</w:tr>"
    for i, r in enumerate(rows, start=2):
        table += "<w:tr>" + "".join(cell(i, h, r.get(h, "")) for h in HEADERS) + "</w:tr>"
    table += "</w:tbl>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("word/document.xml", f'<?xml version="1.0" encoding="UTF-8"?><w:document {W_NS}><w:body>'
                                        f'<w:p><w:r><w:t>Instructions above the table are ignored.</w:t></w:r></w:p>{table}</w:body></w:document>')
        z.writestr("word/_rels/document.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                                   + "".join(rels) + "</Relationships>")
        for name, data in media.items():
            z.writestr(name, data)
    return buf.getvalue()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------------ fixtures

@pytest.fixture()
def store(tmp_path):
    s = LocalObjectStore(root=str(tmp_path / "objects"), prefix="")
    set_object_store(s)
    yield s
    set_object_store(None)


@pytest.fixture()
def bi(client, world, make_staff, login, db_factory, store):
    """p1/p1b1 offers Physics with chapter "Laws of Motion" > topic "Force"; staff of p1 (provider admin, branch admin, a Physics
    teacher) and of p2."""
    from institute_common.models.academics import ProviderSubject, Subject, TeacherSubjectAssignment
    c = {}
    with db_factory() as db:
        phy = Subject(code="PHYSICS", name="Physics")
        db.add(phy)
        db.flush()
        for prov in ("p1", "p2"):
            db.add(ProviderSubject(provider_id=world[prov], subject_id=phy.subject_id))
        actor = uuid4()
        ch = taxonomy.create_node(db, provider_id=world["p1"], subject_id=phy.subject_id, level="CHAPTER", parent_id=None,
                                  name="Laws of Motion", staff_id=actor, ip=None)
        taxonomy.create_node(db, provider_id=world["p1"], subject_id=phy.subject_id, level="TOPIC", parent_id=ch.node_id, name="Force",
                             staff_id=actor, ip=None)
        db.commit()
        c["phy"], c["chapter"] = phy.subject_id, ch.node_id
    c["pa_id"], c["pa"] = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    _, c["ba"] = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    t, c["teacher"] = make_staff([(Role.TEACHER, world["p1"], None)])
    _, c["pa2"] = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    with db_factory() as db:
        db.add(TeacherSubjectAssignment(staff_id=t, provider_id=world["p1"], branch_id=world["p1b1"], subject_id=c["phy"]))
        db.commit()
    state: dict = {"who": None, "headers": None}

    def headers(who="pa"):
        # The test client holds ONE session cookie: log in again only when switching user (logins are rate limited).
        if state["who"] != who:
            state["who"], state["headers"] = who, login(c[who])
        return state["headers"]
    c["h"] = headers
    return c


def upload(client, world, bi, data: bytes, name="questions.xlsx", who="pa", subject=None):
    return client.post(f"{API}/bulk-imports", headers=bi["h"](who),
                       data={"branch_id": str(world["p1b1"]), "subject_id": str(subject or bi["phy"])},
                       files={"file": (name, io.BytesIO(data), "application/octet-stream")})


def work(db_factory, store, rounds: int = 10) -> None:
    """Run the worker until no job has work left (what the worker container does in its loop)."""
    for _ in range(rounds):
        with db_factory() as db:
            job = bulk_import.claim_next(db, "test-worker")
            if job is None:
                return
            bulk_import.run_job(db, job, store, "test-worker")


def job_of(client, bi, job_id, who="pa"):
    return client.get(f"{API}/bulk-imports/{job_id}", headers=bi["h"](who)).json()


def all_rows(client, bi, job_id, status=None):
    url = f"{API}/bulk-imports/{job_id}/rows?page_size=200" + (f"&status={status}" if status else "")
    return client.get(url, headers=bi["h"]()).json()["items"]


def bank_count(db_factory) -> int:
    with db_factory() as db:
        return db.query(Question).count()


# ------------------------------------------------------------------ the whole flow

def test_excel_import_with_pictures_preview_correct_confirm_and_import(client, world, bi, db_factory, store):
    red, blue, green, gold = png("red"), png("blue"), png("green"), png("gold")
    rows = [q(), q(stem="", **{"Option A Text": "", "Option B Text": ""}, difficulty="Hard"),
            q(stem="Which unit measures energy?", difficulty="Impossible"), q(), q(stem="Which is a vector?", correct="C")]
    data = xlsx(rows, [(3, "Question Image", red), (3, "Option A Image", blue), (3, "Option B Image", green), (6, "Question Image", gold)])
    r = upload(client, world, bi, data)
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    assert r.json()["status"] == "UPLOADED" and bank_count(db_factory) == 0          # the request itself reads nothing

    work(db_factory, store)
    job = job_of(client, bi, job_id)
    assert job["status"] == "PREVIEW"
    assert (job["total_rows"], job["valid_rows"], job["invalid_rows"], job["duplicate_rows"]) == (5, 3, 1, 1)
    assert bank_count(db_factory) == 0                                                # a preview never creates bank questions
    by_row = {x["row_no"]: x for x in all_rows(client, bi, job_id)}
    picture_row = by_row[3]
    assert {c: i["sha256"] for c, i in picture_row["images"].items()} == {"question_image": sha(red), "option_a_image": sha(blue),
                                                                          "option_b_image": sha(green)}
    assert picture_row["content_format"] == "IMAGE" and by_row[2]["content_format"] == "TEXT"
    assert by_row[6]["content_format"] == "TEXT_WITH_IMAGE"
    assert by_row[4]["errors"][0]["field"] == "difficulty"
    assert by_row[5]["status"] == "DUPLICATE" and "row 2" in by_row[5]["errors"][0]["message"]
    picture = client.get(picture_row["images"]["option_a_image"]["url"], headers=bi["h"]())
    assert picture.status_code == 200 and picture.content == blue

    fixed = client.patch(f"{API}/bulk-imports/{job_id}/rows/4", json={"difficulty": "medium"}, headers=bi["h"]())
    assert fixed.status_code == 200 and fixed.json()["status"] == "VALID"
    after_fix = job_of(client, bi, job_id)                                        # the job's counts follow the correction at once
    assert (after_fix["valid_rows"], after_fix["invalid_rows"]) == (4, 0)
    first = client.post(f"{API}/bulk-imports/{job_id}/confirm", headers=bi["h"]())
    again = client.post(f"{API}/bulk-imports/{job_id}/confirm", headers=bi["h"]())     # double click: the same answer, no second import
    assert first.status_code == again.status_code == 200 and again.json()["status"] == "IMPORT_QUEUED"
    assert client.patch(f"{API}/bulk-imports/{job_id}/rows/4", json={"difficulty": "hard"}, headers=bi["h"]()).status_code == 409

    work(db_factory, store)
    job = job_of(client, bi, job_id)
    assert job["status"] == "PARTIAL" and job["imported_rows"] == 4 and job["duplicate_rows"] == 1      # the duplicate is reported
    with db_factory() as db:
        questions = db.query(Question).all()
        assert len(questions) == 4 and {x.chapter for x in questions} == {"Laws of Motion"} and all(x.chapter_id for x in questions)
        assert {x.difficulty for x in questions} == {"EASY", "HARD", "MEDIUM"}
        picture_q = db.query(BulkImportRow).filter_by(row_no=3).one().question_id
        v = db.query(QuestionVersion).filter_by(question_id=picture_q).one()
        assets = {a.asset_id: a for a in db.query(QuestionImageAsset)}
        assert assets[v.stem_image_id].checksum == sha(red) and assets[v.option_a_image_id].checksum == sha(blue)
        assert v.stem == "" and v.option_a == "" and v.option_c == "Watt" and v.marks_positive is None
        assert all(a.storage == "local" and a.image_data is None for a in assets.values())
        staged = [k for k in (bulk_import.staging_key(job_id, sha(x)) for x in (red, blue, green, gold))]
        job_row = db.get(BulkImportJob, UUID(job_id))
        assert job_row.cleaned_at is not None and not any(store.exists(k) for k in staged) and not store.exists(job_row.source_key)
    report = client.get(f"{API}/bulk-imports/{job_id}/report.csv", headers=bi["h"]())
    assert report.status_code == 200 and "5,DUPLICATE" in report.text and report.text.startswith("﻿")


def test_word_import_reads_the_table_and_pictures_in_cells(client, world, bi, db_factory, store):
    pic_q, pic_c = png("purple"), png("orange")
    data = docx([q(stem="Identify the instrument."), q(stem="Which law?", **{"Option C Text": ""})],
                {(2, "Question Image"): pic_q, (3, "Option C Image"): pic_c})
    job_id = upload(client, world, bi, data, name="questions.docx").json()["job_id"]
    work(db_factory, store)
    rows = {x["row_no"]: x for x in all_rows(client, bi, job_id)}
    assert job_of(client, bi, job_id)["valid_rows"] == 2
    assert rows[2]["images"]["question_image"]["sha256"] == sha(pic_q) and rows[2]["content_format"] == "TEXT_WITH_IMAGE"
    assert rows[3]["images"]["option_c_image"]["sha256"] == sha(pic_c) and rows[3]["data"]["option_c_text"] == ""


def test_a_picture_placed_in_the_cell_is_attached_to_its_column(client, world, bi, db_factory, store):
    base = xlsx([q(**{"Option A Text": ""})])
    src, out = zipfile.ZipFile(io.BytesIO(base)), io.BytesIO()
    pic = png("teal")
    with zipfile.ZipFile(out, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                data = data.replace(b'<c r="J2"', b'<c r="I2" t="e" vm="1"><v>#VALUE!</v></c><c r="J2"', 1)   # I = Option A Image
            dst.writestr(item, data)
        dst.writestr("xl/metadata.xml",
                     '<metadata xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                     'xmlns:xlrd="http://schemas.microsoft.com/office/spreadsheetml/2017/richdata">'
                     '<futureMetadata name="XLRICHVALUE" count="1"><bk><extLst><ext uri="{x}"><xlrd:rvb i="0"/></ext></extLst></bk>'
                     '</futureMetadata><valueMetadata count="1"><bk><rc t="1" v="0"/></bk></valueMetadata></metadata>')
        dst.writestr("xl/richData/rdrichvalue.xml", '<rvData xmlns="http://schemas.microsoft.com/office/spreadsheetml/2017/richdata">'
                                                    '<rv s="0"><v>0</v><v>5</v></rv></rvData>')
        dst.writestr("xl/richData/richValueRel.xml",
                     '<richValueRels xmlns="http://schemas.microsoft.com/office/spreadsheetml/2022/richvaluerel" '
                     'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><rel r:id="rId1"/></richValueRels>')
        dst.writestr("xl/richData/_rels/richValueRel.xml.rels",
                     '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" '
                     'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image1.png"/>'
                     '</Relationships>')
        dst.writestr("xl/media/image1.png", pic)
    job_id = upload(client, world, bi, out.getvalue()).json()["job_id"]
    work(db_factory, store)
    [row] = all_rows(client, bi, job_id)
    assert row["status"] == "VALID" and row["images"]["option_a_image"]["sha256"] == sha(pic)


# ------------------------------------------------------------------ validation

def test_row_problems_are_reported_per_row_and_the_rest_still_imports(client, world, bi, db_factory, store):
    rows = [q(qtype="TF"), q(stem="Two same options?", b="Newton"), q(stem="Missing an option", c=""), q(stem="Bad answer", correct="E"),
            q(stem="Text and picture in one option?"), q(stem="A fine question about force.", correct="(b)")]
    data = xlsx(rows, [(6, "Option A Image", png("navy")), (7, "Chapter", png("lime"))])        # row 7: picture in a text column
    job_id = upload(client, world, bi, data).json()["job_id"]
    work(db_factory, store)
    by_row = {x["row_no"]: x for x in all_rows(client, bi, job_id)}
    messages = {n: " ".join(e["message"] for e in by_row[n]["errors"]) for n in by_row}
    assert "Unknown question type" in messages[2] and "identical" in messages[3] and "Option C is empty" in messages[4]
    assert "A, B, C or D" in messages[5] and "both text and a picture" in messages[6]
    assert by_row[7]["status"] == "INVALID" and "image columns" in messages[7]
    client.patch(f"{API}/bulk-imports/{job_id}/rows/7", json={"question_text": "Still has the misplaced picture."}, headers=bi["h"]())
    assert all_rows(client, bi, job_id, "VALID") == []                                   # read problems survive text corrections


def test_duplicates_of_bank_questions_are_found(client, world, bi, db_factory, store):
    r = client.post(f"{API}/questions", headers=bi["h"](), json={
        "branch_id": str(world["p1b1"]), "subject_id": str(bi["phy"]), "stem": "What is the SI unit of force?", "option_a": "Newton",
        "option_b": "Joule", "option_c": "Watt", "option_d": "Pascal", "correct_option": "A"})
    assert r.status_code == 201
    # spacing is ignored; case is NOT (the paper importer's rule: Co is cobalt, CO is carbon monoxide)
    job_id = upload(client, world, bi, xlsx([q(stem="What is the  SI unit of force?")])).json()["job_id"]
    work(db_factory, store)
    [row] = all_rows(client, bi, job_id)
    assert row["status"] == "DUPLICATE" and "already in the Question Bank" in row["errors"][0]["message"]


def test_unmatched_taxonomy_is_resolved_only_by_taxonomy_managers(client, world, bi, db_factory, store):
    job_id = upload(client, world, bi, xlsx([q(chapter="Newton Laws"), q(stem="Second?", chapter="Newton Laws"),
                                             q(stem="Third?", chapter="Optics", topic="")])).json()["job_id"]
    work(db_factory, store)
    issues = client.get(f"{API}/bulk-imports/{job_id}/taxonomy-issues", headers=bi["h"]()).json()
    assert [(i["level"], i["value"], i["rows"]) for i in issues] == [("CHAPTER", "Newton Laws", 2), ("CHAPTER", "Optics", 1)]
    url = f"{API}/bulk-imports/{job_id}/taxonomy-resolutions"
    alias = {"level": "CHAPTER", "value": "Newton Laws", "action": "ALIAS", "node_id": str(bi["chapter"])}
    assert client.post(url, json=alias, headers=bi["h"]("ba")).status_code == 403                 # branch admin: no taxonomy.manage
    assert client.post(url, json=alias, headers=bi["h"]()).status_code == 200
    create = client.post(url, json={"level": "CHAPTER", "value": "Optics", "action": "CREATE"}, headers=bi["h"]())
    assert create.status_code == 409                       # the first resolution queued revalidation: resolve again after it
    work(db_factory, store)
    assert client.post(url, json={"level": "CHAPTER", "value": "Optics", "action": "CREATE"}, headers=bi["h"]()).status_code == 200
    work(db_factory, store)
    assert job_of(client, bi, job_id)["valid_rows"] == 3
    # the approved alias is reused by the next import without asking again
    again = upload(client, world, bi, xlsx([q(stem="A new question?", chapter="newton laws")])).json()["job_id"]
    work(db_factory, store)
    assert all_rows(client, bi, again)[0]["status"] == "VALID"


def test_a_mapping_for_one_import_does_not_create_an_alias(client, world, bi, db_factory, store):
    job_id = upload(client, world, bi, xlsx([q(chapter="Motion Laws")])).json()["job_id"]
    work(db_factory, store)
    r = client.post(f"{API}/bulk-imports/{job_id}/taxonomy-resolutions", headers=bi["h"](),
                    json={"level": "CHAPTER", "value": "Motion Laws", "action": "MAP", "node_id": str(bi["chapter"])})
    assert r.status_code == 200
    work(db_factory, store)
    assert all_rows(client, bi, job_id)[0]["status"] == "VALID"
    m = client.get(f"{API}/question-taxonomy/match?provider_id={world['p1']}&subject_id={bi['phy']}&level=CHAPTER&value=Motion%20Laws",
                   headers=bi["h"]()).json()
    assert m["match"] is None


# ------------------------------------------------------------------ authorisation, isolation, hostile files

def test_only_permitted_staff_see_and_act_on_an_import(client, world, bi, db_factory, store):
    job_id = upload(client, world, bi, xlsx([q()]), who="teacher").json()["job_id"]
    work(db_factory, store)
    for who, status in (("pa2", 404), ("teacher", 200), ("ba", 200), ("pa", 200)):
        assert client.get(f"{API}/bulk-imports/{job_id}", headers=bi["h"](who)).status_code == status
    other = upload(client, world, bi, xlsx([q(stem="Another one?")]), who="ba").json()["job_id"]
    assert client.get(f"{API}/bulk-imports/{other}", headers=bi["h"]("teacher")).status_code == 404     # teachers: own imports only
    listed = client.get(f"{API}/bulk-imports?branch_id={world['p1b1']}", headers=bi["h"]("teacher")).json()["items"]
    assert [j["job_id"] for j in listed] == [job_id]
    assert upload(client, world, bi, xlsx([q()]), who="pa2").status_code == 403                    # another provider's branch
    for path in ("rows", "taxonomy-issues", "report.csv"):
        assert client.get(f"{API}/bulk-imports/{job_id}/{path}", headers=bi["h"]("pa2")).status_code == 404
    assert client.post(f"{API}/bulk-imports/{job_id}/confirm", headers=bi["h"]("pa2")).status_code == 404


@pytest.mark.parametrize("name, data, code", [
    ("questions.csv", b"a,b", "UNSUPPORTED_FORMAT"),
    ("questions.doc", b"\xd0\xcf\x11\xe0", "UNSUPPORTED_FORMAT"),
    ("questions.xlsx", b"not a zip at all", "INVALID_FILE"),
])
def test_unsupported_or_fake_files_are_refused_at_upload(client, world, bi, name, data, code):
    r = upload(client, world, bi, data, name=name)
    assert r.status_code == 422 and r.json()["detail"]["code"] == code


def test_size_and_concurrency_limits(client, world, bi, monkeypatch):
    from institute_common.config import settings
    monkeypatch.setattr(settings, "bulk_import_max_bytes", 1000)
    assert upload(client, world, bi, xlsx([q()] * 20)).json()["detail"]["code"] == "FILE_TOO_LARGE"
    monkeypatch.setattr(settings, "bulk_import_max_bytes", 10 ** 8)
    monkeypatch.setattr(settings, "bulk_import_max_active_jobs", 1)
    assert upload(client, world, bi, xlsx([q()])).status_code == 202
    assert upload(client, world, bi, xlsx([q()])).status_code == 429


def _patched(data: bytes, replace: dict[str, bytes] | None = None, add: dict[str, bytes] | None = None) -> bytes:
    src, out = zipfile.ZipFile(io.BytesIO(data)), io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            dst.writestr(item.filename, (replace or {}).get(item.filename, src.read(item.filename)))
        for name, content in (add or {}).items():
            dst.writestr(name, content)
    return out.getvalue()


@pytest.mark.parametrize("mutate, message", [
    (lambda d: _patched(d, add={"xl/vbaProject.bin": b"macro"}), "macros"),
    (lambda d: _patched(d, add={"xl/sharedStrings.xml": b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><sst/>'}), "DOCTYPE"),
    (lambda d: _patched(d, add={"xl/sharedStrings.xml": b"<sst xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
                                + b"<si><t>" + b"a" * 40_000_000 + b"</t></si></sst>"}), "compression bomb"),
    (lambda d: xlsx([q()], headers=HEADERS + ["Marks"]), "Unknown column"),
    (lambda d: xlsx([q()], headers=[h for h in HEADERS if h != "Correct Option"]), "missing"),
    (lambda d: xlsx([q()], sheet="Sheet7") and _two_sheets(), "Questions"),
])
def test_hostile_or_malformed_files_fail_with_a_clear_reason(client, world, bi, db_factory, store, mutate, message):
    data = mutate(xlsx([q()]))
    job_id = upload(client, world, bi, data).json()["job_id"]
    work(db_factory, store)
    job = job_of(client, bi, job_id)
    assert job["status"] == "FAILED" and message in " ".join(job["file_errors"] or [job["last_error"] or ""])
    assert bank_count(db_factory) == 0


def _two_sheets() -> bytes:
    wb = Workbook()
    wb.active.title = "First"
    wb.create_sheet("Second")
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def test_a_csv_report_never_runs_formulas(client, world, bi, db_factory, store):
    import csv
    job_id = upload(client, world, bi, xlsx([q(stem="=HYPERLINK(\"http://evil\")", difficulty="@SUM(1)")])).json()["job_id"]
    work(db_factory, store)
    text = client.get(f"{API}/bulk-imports/{job_id}/report.csv", headers=bi["h"]()).text
    cells = [c for line in csv.reader(io.StringIO(text.lstrip("\ufeff"))) for c in line]
    assert any("formula" in c for c in cells) and any("@SUM(1)" in c for c in cells)
    assert not any(c[:1] in ("=", "+", "-", "@") for c in cells)


# ------------------------------------------------------------------ reliability

def test_one_bad_row_fails_alone_and_the_rest_imports(client, world, bi, db_factory, store, monkeypatch):
    job_id = upload(client, world, bi, xlsx([q(stem=f"Question number {i}?") for i in range(1, 6)])).json()["job_id"]
    work(db_factory, store)
    real = bulk_import._import_row

    def broken(db, job, row, store_, in_bank):
        if row.row_no == 4:
            raise RuntimeError("simulated database problem for one row")
        return real(db, job, row, store_, in_bank)
    monkeypatch.setattr(bulk_import, "_import_row", broken)
    client.post(f"{API}/bulk-imports/{job_id}/confirm", headers=bi["h"]())
    work(db_factory, store)
    job = job_of(client, bi, job_id)
    assert job["status"] == "PARTIAL" and job["imported_rows"] == 4 and job["failed_rows"] == 1
    failed = all_rows(client, bi, job_id, "FAILED")
    assert [r["row_no"] for r in failed] == [4] and "simulated" not in failed[0]["errors"][0]["message"]   # no internals leak


class WorkerDied(BaseException):
    """Stands for a killed worker process (not an Exception, so nothing inside the worker catches it)."""


def test_a_killed_worker_is_resumed_without_duplicates(client, world, bi, db_factory, store, monkeypatch):
    from institute_common.config import settings
    monkeypatch.setattr(settings, "bulk_import_batch_size", 2)
    job_id = upload(client, world, bi, xlsx([q(stem=f"Resumable question {i}?") for i in range(1, 8)])).json()["job_id"]
    work(db_factory, store)
    client.post(f"{API}/bulk-imports/{job_id}/confirm", headers=bi["h"]())
    real, calls = bulk_import.renew_lease, {"n": 0}

    def dies_after_two_batches(db, job, worker):
        real(db, job, worker)
        calls["n"] += 1
        if calls["n"] == 2:
            raise WorkerDied()
    monkeypatch.setattr(bulk_import, "renew_lease", dies_after_two_batches)
    with pytest.raises(WorkerDied):
        work(db_factory, store)
    monkeypatch.setattr(bulk_import, "renew_lease", real)
    assert job_of(client, bi, job_id)["status"] == "IMPORTING" and bank_count(db_factory) == 4
    work(db_factory, store)                                    # its lease is still running: nobody takes it over yet
    assert job_of(client, bi, job_id)["status"] == "IMPORTING"
    with db_factory() as db:
        db.get(BulkImportJob, UUID(job_id)).locked_until = utcnow() - timedelta(seconds=1)       # the lease ran out
        db.commit()
    work(db_factory, store)
    job = job_of(client, bi, job_id)
    assert job["status"] == "COMPLETED" and job["imported_rows"] == 7 and bank_count(db_factory) == 7


def test_reading_resumes_after_a_crash(client, world, bi, db_factory, store, monkeypatch):
    monkeypatch.setattr(bulk_import, "READ_BATCH", 2)
    job_id = upload(client, world, bi, xlsx([q(stem=f"Read me {i}?") for i in range(1, 7)])).json()["job_id"]
    real, calls = bulk_import.renew_lease, {"n": 0}

    def dies_once(db, job, worker):
        real(db, job, worker)
        calls["n"] += 1
        if calls["n"] == 1:
            raise WorkerDied()
    monkeypatch.setattr(bulk_import, "renew_lease", dies_once)
    with pytest.raises(WorkerDied):
        work(db_factory, store)
    monkeypatch.setattr(bulk_import, "renew_lease", real)
    with db_factory() as db:
        assert db.query(BulkImportRow).filter_by(job_id=UUID(job_id)).count() == 2
        db.get(BulkImportJob, UUID(job_id)).locked_until = utcnow() - timedelta(seconds=1)
        db.commit()
    work(db_factory, store)
    assert job_of(client, bi, job_id)["total_rows"] == 6 and job_of(client, bi, job_id)["status"] == "PREVIEW"


def test_a_job_that_keeps_failing_stops(client, world, bi, db_factory, store, monkeypatch):
    from institute_common.config import settings
    job_id = upload(client, world, bi, xlsx([q()])).json()["job_id"]
    monkeypatch.setattr(bulk_import, "read_file", lambda *a: (_ for _ in ()).throw(RuntimeError("disk full")))
    for _ in range(settings.bulk_import_max_attempts + 1):
        work(db_factory, store, rounds=1)                      # each failure releases the lease at once, so the next pass retries
    job = job_of(client, bi, job_id)
    assert job["status"] == "FAILED" and "disk full" not in (job["last_error"] or "")


def test_rechecks_and_corrections_never_use_up_the_attempts(client, world, bi, db_factory, store):
    """Each re-check is a worker claim; only claims of a stage that never finishes count, so a user may check again (or resolve
    taxonomy values) any number of times and still import."""
    from institute_common.config import settings
    job_id = upload(client, world, bi, xlsx([q(), q(stem="Which is a vector?")])).json()["job_id"]
    work(db_factory, store)
    for _ in range(settings.bulk_import_max_attempts + 2):
        assert client.post(f"{API}/bulk-imports/{job_id}/revalidate", headers=bi["h"]()).status_code == 200
        work(db_factory, store)
        assert job_of(client, bi, job_id)["status"] == "PREVIEW"
    assert client.post(f"{API}/bulk-imports/{job_id}/confirm", headers=bi["h"]()).status_code == 200
    work(db_factory, store)
    job = job_of(client, bi, job_id)
    assert job["status"] == "COMPLETED" and job["imported_rows"] == 2, job


def test_cancel_expiry_and_cleanup(client, world, bi, db_factory, store):
    keep = png("maroon")
    cancelled = upload(client, world, bi, xlsx([q()], [(2, "Question Image", keep)])).json()["job_id"]
    expired = upload(client, world, bi, xlsx([q(stem="Expire me?")], [(2, "Question Image", png("olive"))])).json()["job_id"]
    work(db_factory, store)
    r = client.post(f"{API}/bulk-imports/{cancelled}/cancel", headers=bi["h"]())
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    assert not store.exists(bulk_import.staging_key(cancelled, sha(keep)))
    assert client.post(f"{API}/bulk-imports/{cancelled}/confirm", headers=bi["h"]()).status_code == 409
    with db_factory() as db:
        report = bulk_import.housekeeping(db, store, now=utcnow() + timedelta(days=8))
    assert report["expired"] == 1 and job_of(client, bi, expired)["status"] == "EXPIRED"
    with db_factory() as db:
        assert db.get(BulkImportJob, UUID(expired)).cleaned_at is not None and not store.exists(db.get(BulkImportJob, UUID(expired)).source_key)
    assert bank_count(db_factory) == 0


def test_too_many_rows_stops_the_file(client, world, bi, db_factory, store, monkeypatch):
    from institute_common.config import settings
    monkeypatch.setattr(settings, "bulk_import_max_rows", 3)
    job_id = upload(client, world, bi, xlsx([q(stem=f"Row {i}?") for i in range(5)])).json()["job_id"]
    work(db_factory, store)
    assert "more than 3 questions" in " ".join(job_of(client, bi, job_id)["file_errors"])


# ------------------------------------------------------------------ scale and S3

def test_two_thousand_questions_with_shared_pictures(client, world, bi, db_factory, store):
    pictures = [png(c) for c in ("red", "blue", "green", "gold", "navy")]
    rows = [q(stem=f"Scale question {i}?", a=f"A{i}", b=f"B{i}", c=f"C{i}", d=f"D{i}") for i in range(2000)]
    placed = [(i + 2, "Question Image", pictures[(i // 40) % 5]) for i in range(0, 2000, 40)]
    job_id = upload(client, world, bi, xlsx(rows, placed)).json()["job_id"]
    work(db_factory, store, rounds=20)
    assert job_of(client, bi, job_id)["valid_rows"] == 2000
    client.post(f"{API}/bulk-imports/{job_id}/confirm", headers=bi["h"]())
    work(db_factory, store, rounds=20)
    job = job_of(client, bi, job_id)
    assert job["status"] == "COMPLETED" and job["imported_rows"] == 2000
    with db_factory() as db:
        assert db.query(QuestionImageAsset).count() == 5                       # each distinct picture stored once
        assert db.query(Question).count() == 2000


def test_the_same_import_works_on_s3(client, world, bi, db_factory, monkeypatch):
    from tests.test_object_storage import BUCKET  # noqa: F401
    moto = pytest.importorskip("moto")
    for name, value in (("AWS_ACCESS_KEY_ID", "testing"), ("AWS_SECRET_ACCESS_KEY", "testing"), ("AWS_DEFAULT_REGION", "ap-south-1")):
        monkeypatch.setenv(name, value)
    with moto.mock_aws():
        import boto3
        from institute_common.objectstore import S3ObjectStore
        boto3.client("s3", region_name="ap-south-1").create_bucket(Bucket="bulk", CreateBucketConfiguration={"LocationConstraint": "ap-south-1"})
        s3 = S3ObjectStore(bucket="bulk", region="ap-south-1", prefix="")
        set_object_store(s3)
        pic = png("indigo")
        job_id = upload(client, world, bi, xlsx([q()], [(2, "Question Image", pic)])).json()["job_id"]
        work(db_factory, s3)
        preview = client.get(f"{API}/bulk-imports/{job_id}/images/{sha(pic)}", headers=bi["h"](), follow_redirects=False)
        assert preview.status_code == 307 and "X-Amz-Signature" in preview.headers["location"]
        client.post(f"{API}/bulk-imports/{job_id}/confirm", headers=bi["h"]())
        work(db_factory, s3)
        with db_factory() as db:
            [asset] = db.query(QuestionImageAsset).all()
            assert asset.storage == "s3" and s3.get(asset.storage_key) == pic
        assert not s3.exists(bulk_import.staging_key(job_id, sha(pic)))


# ------------------------------------------------------------------ permissions re-checked by the worker

def _set_roles_inactive(db_factory, staff_id) -> None:
    from institute_common.models.staff import StaffRoleAssignment
    with db_factory() as db:
        for a in db.query(StaffRoleAssignment).filter_by(staff_id=staff_id):
            a.status = "INACTIVE"
        db.commit()


def _stopped_audit(db_factory, job_id) -> list:
    from institute_common.models.staff import AuditLog
    with db_factory() as db:
        return [a.details for a in db.query(AuditLog).filter_by(action="bulk_import.stopped", target_id=str(job_id))]


def test_a_confirmer_whose_role_is_revoked_before_the_worker_runs_imports_nothing(client, world, bi, db_factory, store):
    job_id = upload(client, world, bi, xlsx([q(), q(stem="Second question?")])).json()["job_id"]
    work(db_factory, store)
    assert client.post(f"{API}/bulk-imports/{job_id}/confirm", headers=bi["h"]()).status_code == 200
    _set_roles_inactive(db_factory, bi["pa_id"])            # revoked after confirmation, before the worker claims the import
    work(db_factory, store)
    with db_factory() as db:
        job = db.get(BulkImportJob, UUID(job_id))
        assert job.status == "FAILED" and "permission" in job.last_error and job.imported_rows == 0 and job.cleaned_at is not None
    assert bank_count(db_factory) == 0
    [details] = _stopped_audit(db_factory, job_id)
    assert details["who"] == "uploader" and details["stage"] == "IMPORTING"   # the provider admin uploaded and confirmed


def test_an_uploader_who_loses_the_subject_is_stopped_before_the_file_is_read(client, world, bi, db_factory, store):
    from institute_common.models.academics import TeacherSubjectAssignment
    job_id = upload(client, world, bi, xlsx([q()]), who="teacher").json()["job_id"]
    with db_factory() as db:                                # the teacher no longer teaches Physics in this branch
        for t in db.query(TeacherSubjectAssignment):
            t.status = "INACTIVE"
        db.commit()
    work(db_factory, store)
    with db_factory() as db:
        job = db.get(BulkImportJob, UUID(job_id))
        assert job.status == "FAILED" and job.total_rows == 0 and "permission" in job.last_error


@pytest.mark.parametrize("change, expected", [
    ("deactivate_account", "no longer active"),
    ("withdraw_subject", "no longer offered"),
    ("deactivate_branch", "branch is no longer active"),
])
def test_account_subject_and_branch_changes_stop_a_queued_import(client, world, bi, db_factory, store, change, expected):
    from institute_common.models.academics import ProviderSubject
    from institute_common.models.org import Branch
    from institute_common.models.staff import Staff
    job_id = upload(client, world, bi, xlsx([q()])).json()["job_id"]
    work(db_factory, store)
    client.post(f"{API}/bulk-imports/{job_id}/confirm", headers=bi["h"]())
    with db_factory() as db:
        if change == "deactivate_account":
            db.get(Staff, bi["pa_id"]).status = "INACTIVE"
        elif change == "withdraw_subject":
            db.query(ProviderSubject).filter_by(provider_id=world["p1"], subject_id=bi["phy"]).delete()
        else:
            db.get(Branch, world["p1b1"]).status = "INACTIVE"
        db.commit()
    work(db_factory, store)
    with db_factory() as db:
        job = db.get(BulkImportJob, UUID(job_id))
        assert job.status == "FAILED" and expected in job.last_error
    assert bank_count(db_factory) == 0


def test_a_teacher_can_never_be_the_confirmer_of_someone_elses_upload(client, world, bi, db_factory, store, make_staff):
    from institute_common.models.staff import Staff
    job_id = upload(client, world, bi, xlsx([q()]), who="ba").json()["job_id"]
    work(db_factory, store)
    with db_factory() as db:                                # a confirmation recorded for a teacher (not possible through the API)
        teacher = db.query(Staff).filter_by(staff_code=bi["teacher"]).one()
        job = db.get(BulkImportJob, UUID(job_id))
        job.status, job.confirmed_by, job.confirmed_at = "IMPORT_QUEUED", teacher.staff_id, utcnow()
        db.commit()
    work(db_factory, store)
    with db_factory() as db:
        job = db.get(BulkImportJob, UUID(job_id))
        assert job.status == "FAILED" and "own uploads" in job.last_error
    assert bank_count(db_factory) == 0


def test_a_revocation_during_an_import_stops_it_at_the_next_claim(client, world, bi, db_factory, store, monkeypatch):
    from institute_common.config import settings
    monkeypatch.setattr(settings, "bulk_import_batch_size", 2)
    job_id = upload(client, world, bi, xlsx([q(stem=f"Interrupted question {i}?") for i in range(6)])).json()["job_id"]
    work(db_factory, store)
    client.post(f"{API}/bulk-imports/{job_id}/confirm", headers=bi["h"]())
    real, calls = bulk_import.renew_lease, {"n": 0}

    def dies_after_one_batch(db, job, worker):
        real(db, job, worker)
        calls["n"] += 1
        if calls["n"] == 1:
            raise WorkerDied()
    monkeypatch.setattr(bulk_import, "renew_lease", dies_after_one_batch)
    with pytest.raises(WorkerDied):
        work(db_factory, store)
    monkeypatch.setattr(bulk_import, "renew_lease", real)
    _set_roles_inactive(db_factory, bi["pa_id"])
    with db_factory() as db:
        db.get(BulkImportJob, UUID(job_id)).locked_until = utcnow() - timedelta(seconds=1)
        db.commit()
    work(db_factory, store)
    with db_factory() as db:
        job = db.get(BulkImportJob, UUID(job_id))
        assert job.status == "FAILED" and job.imported_rows == 2                 # the first batch was imported while still allowed
    assert bank_count(db_factory) == 2
