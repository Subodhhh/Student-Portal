"""Question and option pictures: the upload template's image columns (.xlsx pictures, .zip packages), their validation, storage once per
provider, the authorised picture endpoint and the import into the question bank."""
import hashlib
import io
import zipfile
from datetime import timedelta

import pytest
from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from PIL import Image
from sqlalchemy import func, select

from institute_common.core.roles import Role
from institute_common.models.papers import Question, QuestionImageAsset, QuestionVersion
from inst_paper.services import extraction, image_assets
from tests.test_papers import API, setup, tmp_store  # noqa: F401  (shared fixtures)

HEADER = ["question", "question_image", "option_a", "option_a_image", "option_b", "option_b_image", "option_c", "option_c_image",
          "option_d", "option_d_image", "correct_answer", "positive_marks", "negative_marks", "chapter", "topic", "sub_topic",
          "explanation"]


def png(color: str, size=(48, 32)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def workbook(rows: list[list], pictures: list[tuple[str, bytes]]) -> bytes:
    """An .xlsx with the full template header, the given rows and pictures placed over the given cells (e.g. "B2")."""
    wb = Workbook()
    ws = wb.active
    ws.append(HEADER)
    for row in rows:
        ws.append(row)
    for cell, data in pictures:
        ws.add_image(XLImage(io.BytesIO(data)), cell)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def package(csv_text: str, files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("paper.csv", csv_text)
        for name, data in files.items():
            zf.writestr(name, data)
    return out.getvalue()


CELL_ROW = ["Identify the labelled organelle.", None, None, None, "Nucleus", None, "Ribosome", None, "Golgi body", None, "A", 4, 1,
            "Cell: the unit of life", "Cell organelles", "Plastids", "The picture shows a chloroplast."]
IMAGE_ONLY_ROW = [None, None, None, None, None, None, None, None, None, None, "C", 4, 0, "Graphs", None, None, None]


@pytest.fixture()
def admin(make_staff, world, login):
    return login(make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])[1])


def send(client, h, world, setup, data: bytes, name="paper.xlsx", preview=True, subject="phy"):
    ctype = "application/zip" if name.endswith(".zip") else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    form = {"title": "Pictures paper", **({"preview": "true"} if preview else {})}
    return client.post(f"{API}/branches/{world['p1b1']}/subjects/{setup[subject]}/papers/upload",
                       files={"file": (name, io.BytesIO(data), ctype)}, data=form, headers=h)


def mixed_workbook() -> bytes:
    """Question 1: text + question picture, option A a picture. Question 2: picture-only question with four picture options."""
    return workbook([CELL_ROW, IMAGE_ONLY_ROW], [("B2", png("red")), ("D2", png("blue")), ("B3", png("green")), ("D3", png("navy")),
                                                 ("F3", png("olive")), ("H3", png("teal")), ("J3", png("maroon"))])


# ------------------------------------------------------------------ preview, picture endpoint, import

def test_preview_shows_every_picture_and_import_links_them(client, world, setup, admin, db_factory):
    r = send(client, admin, world, setup, mixed_workbook())
    assert r.status_code == 200, r.text
    q1, q2 = r.json()["questions"]
    assert set(q1["images"]) == {"question", "A"} and set(q2["images"]) == {"question", "A", "B", "C", "D"}
    assert q1["chapter"] == "Cell: the unit of life" and q1["sub_topic"] == "Plastids"
    picture = client.get(f"{API}/question-images/{q1['images']['question']['asset_id']}", headers=admin)
    assert picture.status_code == 200 and picture.content == png("red")
    assert picture.headers["content-type"] == "image/png" and picture.headers["x-content-type-options"] == "nosniff"

    r = send(client, admin, world, setup, mixed_workbook(), preview=False)
    assert r.status_code == 201, r.text
    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(QuestionImageAsset)) == 7      # stored once: preview and import share them
        versions = db.scalars(select(QuestionVersion).order_by(QuestionVersion.created_at)).all()
        by_stem = {v.stem: v for v in versions}
        first, second = by_stem["Identify the labelled organelle."], by_stem[""]
        assert first.stem_image_id and first.option_a_image_id and first.option_a == "" and not first.option_b_image_id
        assert all(getattr(second, c) for c in ("stem_image_id", "option_a_image_id", "option_b_image_id", "option_c_image_id",
                                                 "option_d_image_id"))
        bank = db.get(Question, first.question_id)
        assert (bank.chapter, bank.topic, bank.subtopic) == ("Cell: the unit of life", "Cell organelles", "Plastids")


def test_pictures_are_private_to_their_provider(client, world, setup, admin, make_staff, login):
    asset = send(client, admin, world, setup, mixed_workbook()).json()["questions"][0]["images"]["question"]["asset_id"]
    outsider = login(make_staff([(Role.BRANCH_ADMIN, world["p2"], world["p2b1"])])[1])
    assert client.get(f"{API}/question-images/{asset}", headers=outsider).status_code == 404
    assert client.get(f"{API}/question-images/00000000-0000-0000-0000-000000000000", headers=admin).status_code == 404
    client.cookies.clear()
    assert client.get(f"{API}/question-images/{asset}").status_code == 401


def test_zip_package_imports_csv_with_its_pictures(client, world, setup, admin, db_factory):
    csv_text = (",".join(HEADER) + "\n"
                "Which graph shows y = x²?,graphs/q1.png,,graphs/a.png,,graphs/b.png,y = x,,y = 2x,,A,4,1,Functions,Parabola,,\n")
    data = package(csv_text, {"graphs/q1.png": png("red"), "graphs/a.png": png("blue"), "graphs/b.png": png("green")})
    r = send(client, admin, world, setup, data, name="paper.zip", preview=False)
    assert r.status_code == 201, r.text
    with db_factory() as db:
        v = db.scalar(select(QuestionVersion))
        assert v.stem_image_id and v.option_a_image_id and v.option_b_image_id and v.option_c == "\\(y = x\\)"   # recognised


@pytest.mark.parametrize("rows,pictures,expected", [
    # neither question text nor question picture
    ([[None, None, "a", None, "b", None, "c", None, "d", None, "A", 4, 1, None, None, None, None]], [],
     "Question text is empty and there is no question image"),
    # an option with both text and a picture
    ([["Q?", None, "a", None, "b", None, "c", None, "d", None, "A", 4, 1, None, None, None, None]], [("D2", png("red"))],
     "Option A has both text and an image"),
    # an option with neither
    ([["Q?", None, "a", None, "b", None, "c", None, None, None, "A", 4, 1, None, None, None, None]], [],
     "Option D is empty: give its text or its image"),
    # a picture outside the image columns
    ([["Q?", None, "a", None, "b", None, "c", None, "d", None, "A", 4, 1, None, None, None, None]], [("K2", png("red"))],
     "pictures belong only in"),
    # the same picture for two options
    ([["Q?", None, None, None, None, None, "c", None, "d", None, "A", 4, 1, None, None, None, None]],
     [("D2", png("red")), ("F2", png("red"))], "Two options are identical"),
    # text typed into an image column
    ([["Q?", "see figure", "a", None, "b", None, "c", None, "d", None, "A", 4, 1, None, None, None, None]], [],
     "holds text"),
    # a correct answer that is not an option
    ([["Q?", None, "a", None, "b", None, "c", None, "d", None, "E", 4, 1, None, None, None, None]], [],
     "Correct answer must be A, B, C or D"),
    # marks that are not numbers
    ([["Q?", None, "a", None, "b", None, "c", None, "d", None, "A", "four", 1, None, None, None, None]], [],
     "Marks must be a positive number"),
])
def test_invalid_rows_name_the_question_and_column(client, world, setup, admin, rows, pictures, expected):
    r = send(client, admin, world, setup, workbook(rows, pictures))
    assert r.status_code == 422
    errors = r.json()["detail"]["errors"]
    assert any(expected in e["message"] and e["row"] == 2 for e in errors), errors


def test_damaged_and_missing_package_pictures_are_reported(client, world, setup, admin):
    csv_text = ",".join(HEADER) + "\nQ?,q.png,a,,b,,c,,d,,A,4,1,,,,\nR?,missing.png,a,,b,,c,,d,,A,4,1,,,,\n"
    r = send(client, admin, world, setup, package(csv_text, {"q.png": b"\x89PNG\r\n\x1a\n not really"}), name="paper.zip")
    messages = [(e["row"], e["field"], e["message"]) for e in r.json()["detail"]["errors"]]
    assert (2, "question_image", "The image q.png is damaged or cannot be read.") in messages
    assert any(row == 3 and "'missing.png' is not in the package" in m for row, _, m in messages)


def test_a_disguised_file_is_not_accepted_as_a_picture():
    data = package(",".join(HEADER) + "\nQ?,evil.png,a,,b,,c,,d,,A,4,1,,,,\n", {"evil.png": b"<svg onload=alert(1)>"})
    result = extraction.extract_zip_package(data, 500)
    assert not result.ok and "is not a PNG, JPEG, GIF or WebP image" in result.errors[0]["message"]


def test_a_text_only_paper_still_uses_the_old_template(client, world, setup, admin):
    r = client.post(f"{API}/branches/{world['p1b1']}/subjects/{setup['phy']}/papers/upload", headers=admin, data={"title": "Old"},
                    files={"file": ("p.csv", io.BytesIO(b"question,option_a,option_b,option_c,option_d,correct,marks,negative_marks\n"
                                                         b"Q?,a,b,c,d,A,4,1\n"), "text/csv")})
    assert r.status_code == 201, r.text


def test_unused_preview_pictures_are_swept_only_after_retention(client, world, setup, admin, db_factory):
    send(client, admin, world, setup, mixed_workbook())
    with db_factory() as db:
        assert image_assets.sweep_unreferenced(db, dry_run=False)["deleted"] == 0                  # too young
        later = image_assets.utcnow() + image_assets.UNREFERENCED_RETENTION + timedelta(minutes=1)
        assert image_assets.sweep_unreferenced(db, now=later)["unused_pictures"] == 7              # a dry run lists them
        assert db.scalar(select(func.count()).select_from(QuestionImageAsset)) == 7
        assert image_assets.sweep_unreferenced(db, dry_run=False, now=later)["deleted"] == 7
    send(client, admin, world, setup, mixed_workbook(), preview=False)                              # imported pictures are kept
    with db_factory() as db:
        later = image_assets.utcnow() + image_assets.UNREFERENCED_RETENTION + timedelta(minutes=1)
        assert image_assets.sweep_unreferenced(db, dry_run=False, now=later)["deleted"] == 0


def in_cell_picture_workbook(picture: bytes) -> bytes:
    """An .xlsx whose option_a_image cell (D2) holds a picture placed IN the cell (Excel 365 "Place in Cell"): the cell carries a
    value-metadata index that leads through xl/metadata.xml and xl/richData/* to xl/media/image1.png, as Excel writes it."""
    base = workbook([["Pick the benzene ring.", None, None, None, "b", None, "c", None, "d", None, "A", 4, 1, None, None, None, None]], [])
    src, out = zipfile.ZipFile(io.BytesIO(base)), io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                data = data.replace(b'<c r="E2"', b'<c r="D2" t="e" vm="1"><v>#VALUE!</v></c><c r="E2"', 1)
            dst.writestr(item, data)
        dst.writestr("xl/metadata.xml",
                     '<metadata xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                     'xmlns:xlrd="http://schemas.microsoft.com/office/spreadsheetml/2017/richdata">'
                     '<metadataTypes count="1"><metadataType name="XLRICHVALUE"/></metadataTypes>'
                     '<futureMetadata name="XLRICHVALUE" count="1"><bk><extLst><ext uri="{3e2802c4-a4d2-4d8b-9148-e3be6c30e623}">'
                     '<xlrd:rvb i="0"/></ext></extLst></bk></futureMetadata>'
                     '<valueMetadata count="1"><bk><rc t="1" v="0"/></bk></valueMetadata></metadata>')
        dst.writestr("xl/richData/rdrichvalue.xml",
                     '<rvData xmlns="http://schemas.microsoft.com/office/spreadsheetml/2017/richdata" count="1">'
                     '<rv s="0"><v>0</v><v>5</v></rv></rvData>')
        dst.writestr("xl/richData/richValueRel.xml",
                     '<richValueRels xmlns="http://schemas.microsoft.com/office/spreadsheetml/2022/richvaluerel" '
                     'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><rel r:id="rId1"/></richValueRels>')
        dst.writestr("xl/richData/_rels/richValueRel.xml.rels",
                     '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                     '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                     'Target="../media/image1.png"/></Relationships>')
        dst.writestr("xl/media/image1.png", picture)
    return out.getvalue()


def test_a_picture_placed_in_the_cell_is_read():
    result = extraction.extract_xlsx(in_cell_picture_workbook(png("purple")), 500)
    assert result.ok, result.errors
    assert result.questions[0].images["A"].sha256 == hashlib.sha256(png("purple")).hexdigest()
    assert result.questions[0].options["A"] == ""


# ------------------------------------------------------------------ pictures survive corrections and new versions

def _imported_picture_paper(client, world, setup, admin, db_factory):
    r = send(client, admin, world, setup, mixed_workbook(), preview=False)
    assert r.status_code == 201, r.text
    with db_factory() as db:
        first = db.scalar(select(QuestionVersion).where(QuestionVersion.stem == "Identify the labelled organelle."))
        return r.json()["paper_version_id"], first


def test_a_paper_correction_keeps_the_pictures(client, world, setup, admin, db_factory):
    pv, before = _imported_picture_paper(client, world, setup, admin, db_factory)
    r = client.post(f"{API}/paper-versions/{pv}/revisions", headers=admin,
                    json={"edits": [{"position": 1, "explanation": "A chloroplast has a double membrane."}], "reason": "Better explanation"})
    assert r.status_code == 201, r.text
    detail = client.get(f"{API}/paper-versions/{r.json()['paper_version_id']}", headers=admin).json()
    first = next(q for q in detail["questions"] if q["stem"] == "Identify the labelled organelle.")
    assert first["stem_image_id"] == str(before.stem_image_id) and first["option_a_image_id"] == str(before.option_a_image_id)
    second = next(q for q in detail["questions"] if q["stem"] == "")
    assert all(second[c] for c in ("stem_image_id", "option_a_image_id", "option_b_image_id", "option_c_image_id", "option_d_image_id"))


def test_a_correction_cannot_give_a_picture_option_text_too(client, world, setup, admin, db_factory):
    pv, _ = _imported_picture_paper(client, world, setup, admin, db_factory)
    r = client.post(f"{API}/paper-versions/{pv}/revisions", headers=admin,
                    json={"edits": [{"position": 1, "option_a": "Chloroplast"}], "reason": "Typed the option"})
    assert r.status_code == 422 and "Option A has both text and an image" in r.text


def test_a_new_question_version_keeps_its_pictures(client, world, setup, admin, db_factory):
    _, before = _imported_picture_paper(client, world, setup, admin, db_factory)
    body = {"stem": "Name the labelled organelle.", "option_a": "", "option_b": "Nucleus", "option_c": "Ribosome", "option_d": "Golgi body",
            "correct_option": "A", "marks_positive": "4", "marks_negative": "1"}
    r = client.post(f"{API}/questions/{before.question_id}/versions", json=body, headers=admin)
    assert r.status_code == 201, r.text
    assert r.json()["stem_image_id"] == str(before.stem_image_id) and r.json()["option_a_image_id"] == str(before.option_a_image_id)
    both = client.post(f"{API}/questions/{before.question_id}/versions", json={**body, "option_a": "Chloroplast"}, headers=admin)
    assert both.status_code == 422 and both.json()["detail"]["code"] == "OPTION_TEXT_AND_PICTURE"
