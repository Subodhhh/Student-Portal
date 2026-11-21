"""The bulk-import template (Phase E, one template for MCQ, MSQ and NAT) against the real importer: the committed Excel and Word files
have the first template's 16 columns followed by the MSQ / NAT columns, all in the parser's names, the Excel drop-downs offer only values the validator accepts, the blank templates upload cleanly, and
every example - text only, text with a picture, picture only, options as pictures - is read, validated and imported as shown, with each
picture attached to its own question or option field. The files are downloadable by staff who may add questions."""
import hashlib
import io
import os
import zipfile

import pytest
from openpyxl import load_workbook

from institute_common.models.papers import Question, QuestionImageAsset, QuestionVersion
from inst_paper.import_templates import build, content
from inst_paper.services.bulk_reader import TEMPLATE_COLUMNS, BulkFile, column_name
from inst_paper.services import bulk_validation
from tests.test_bulk_import import API, all_rows, bi, job_of, store, upload, work  # noqa: F401  (fixtures)

FORBIDDEN = ("subject", "marks", "negative", "numeric", "tolerance", "accepted")


def committed(kind: str) -> bytes:
    with open(build.XLSX_PATH if kind == "xlsx" else build.DOCX_PATH, "rb") as fh:
        return fh.read()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_one_template_holds_every_type_and_keeps_the_first_sixteen_columns():
    names = [column_name(t) for t in content.TITLES]
    assert names[:16] == list(TEMPLATE_COLUMNS[:6]) + [
        "question_image", "option_a_text", "option_a_image", "option_b_text", "option_b_image", "option_c_text", "option_c_image",
        "option_d_text", "option_d_image", "correct_option"]                                   # an MCQ file made before still fits
    assert set(names) == set(TEMPLATE_COLUMNS) and len(names) == len(set(names))
    assert set(content.WORD_TITLES) < set(content.TITLES)
    assert not any(word in t.lower() for t in content.TITLES for word in ("subject", "marks", "negative"))
    assert {e["Question Type"] for e in content.EXAMPLES} == {"MCQ", "MSQ", "NAT"}             # one sheet, every type


def test_every_drop_down_value_is_accepted_by_the_validator():
    for difficulty in content.DIFFICULTY_CHOICES:
        assert bulk_validation.normalised({"difficulty": difficulty})["difficulty"] in bulk_validation.DIFFICULTIES
    assert [bulk_validation.row_type({"question_type": t}) for t in content.QUESTION_TYPE_CHOICES] == ["MCQ_SINGLE", "MCQ_MULTI", "NUMERICAL"]
    assert set(bulk_validation.TOLERANCE_KINDS[t.upper().replace("-", "_")] for t in content.TOLERANCE_TYPE_CHOICES) == {"EXACT", "ABS", "RANGE"}
    assert {t.upper() for t in content.ANSWER_FORMAT_CHOICES} == {"INTEGER", "DECIMAL"}


def test_the_committed_excel_file(tmp_path):
    data = committed("xlsx")
    wb = load_workbook(io.BytesIO(data))
    assert wb.sheetnames == ["Questions", "Instructions", "Examples"]
    questions = wb["Questions"]
    assert [c.value for c in questions[1]] == content.TITLES and questions.max_row == 1          # header only: nothing to delete
    lists = {v.formula1 for v in questions.data_validations.dataValidation}
    assert lists == {'"Easy,Medium,Hard"', '"MCQ,MSQ,NAT"', '"Exact,Plus-minus,Range"', '"Integer,Decimal"'}
    assert [c.value for c in wb["Examples"][1]] == content.TITLES and len(wb["Examples"]._images) == 6
    guide = " ".join(str(c.value) for row in wb["Instructions"].iter_rows() for c in row if c.value)
    for title, *_ in content.COLUMNS:
        assert title in guide
    assert "Place in Cell" in guide and "never imported" in guide
    path = tmp_path / "t.xlsx"
    path.write_bytes(data)
    with BulkFile(str(path), "XLSX") as f:                          # the blank template is a valid upload with no questions
        assert list(f.rows(100)) == []


def test_the_committed_word_file_is_what_the_generator_writes(tmp_path):
    data = committed("docx")
    assert data == build.build_docx()                                # fixed dates: regenerated, the file is identical
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "word/document.xml" in names and sum(n.startswith("word/media/") for n in names) == 6
    text = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode()
    for title, *_ in content.COLUMNS:
        assert title in text
    assert "Insert &gt; Pictures" in text and "not imported" in text
    path = tmp_path / "t.docx"
    path.write_bytes(data)
    with BulkFile(str(path), "DOCX") as f:                           # its first table is empty: no questions, no errors
        assert list(f.rows(100)) == []


@pytest.mark.parametrize("kind", ["xlsx", "docx"])
def test_every_example_imports_exactly_as_the_template_shows(kind, client, world, bi, db_factory, store):
    data = build.build_xlsx(examples_on_questions_sheet=True) if kind == "xlsx" else build.build_docx(examples_in_first_table=True)
    job_id = upload(client, world, bi, data, name=f"examples.{kind}").json()["job_id"]
    work(db_factory, store)
    every = {r["row_no"]: r for r in all_rows(client, bi, job_id)}
    assert [r["status"] for r in every.values()] == ["VALID"] * 8, [r["errors"] for r in every.values()]
    # rows of every type in one sheet / table, in the file's order and with the file's row numbers
    assert [r["question_type"] for r in every.values()] == ["MCQ_SINGLE"] * 4 + ["MCQ_MULTI"] * 2 + ["NUMERICAL"] * 2
    assert [r["label"] for r in every.values()] == [str(n) for n in range(2, 10)]
    rows = {n: r for n, r in every.items() if r["question_type"] == "MCQ_SINGLE"}
    pictures = {name: sha(build.picture(name)) for name in ("incline", "whole_question", "graph_flat", "graph_rising", "graph_falling",
                                                             "graph_curve")}
    text_only, with_picture, picture_only, picture_options = (rows[n] for n in sorted(rows))
    assert text_only["content_format"] == "TEXT" and text_only["images"] == {}
    assert with_picture["content_format"] == "TEXT_WITH_IMAGE"
    assert {c: i["sha256"] for c, i in with_picture["images"].items()} == {"question_image": pictures["incline"]}
    assert picture_only["content_format"] == "IMAGE" and picture_only["data"]["question_text"] == ""
    assert {c: i["sha256"] for c, i in picture_only["images"].items()} == {"question_image": pictures["whole_question"]}
    assert {c: i["sha256"] for c, i in picture_options["images"].items()} == {
        "option_a_image": pictures["graph_flat"], "option_b_image": pictures["graph_rising"], "option_c_image": pictures["graph_falling"],
        "option_d_image": pictures["graph_curve"]}

    assert client.post(f"{API}/bulk-imports/{job_id}/confirm", headers=bi["h"]()).status_code == 200
    work(db_factory, store)
    assert job_of(client, bi, job_id)["status"] == "COMPLETED"
    with db_factory() as db:
        assert db.query(Question).count() == 8 and db.query(QuestionImageAsset).count() == 6      # every picture stored once
        assert {q.chapter for q in db.query(Question)} == {"Laws of Motion"}
        versions = db.query(QuestionVersion).all()
        assert sorted(v.correct_option for v in versions if v.question_type == "MCQ_SINGLE") == ["A", "A", "A", "C"]
        assert sorted(v.answer_key["correct"] for v in versions if v.question_type == "MCQ_MULTI") == [["A", "C"], ["A", "C"]]
        assert sorted((v.answer_key["value"], v.answer_key["tolerance"]["kind"]) for v in versions if v.question_type == "NUMERICAL") == [
            ("5", "EXACT"), ("9.8", "ABS")]
        assert all(v.correct_option is None and v.option_a == "" for v in versions if v.question_type != "MCQ_SINGLE")


def test_staff_who_may_add_questions_can_download_the_templates(client, world, bi):
    for kind in ("xlsx", "docx"):
        r = client.get(f"{API}/bulk-import-templates/{kind}", headers=bi["h"]("teacher"))
        assert r.status_code == 200 and r.content == committed(kind)
        assert r.headers["content-disposition"].startswith("attachment") and kind in r.headers["content-disposition"]
    assert client.get(f"{API}/bulk-import-templates/pdf", headers=bi["h"]()).status_code == 422
    assert client.get(f"{API}/bulk-import-templates/..%2F..%2Fmain.py", headers=bi["h"]()).status_code in (404, 422)
    client.cookies.clear()
    assert client.get(f"{API}/bulk-import-templates/xlsx").status_code in (401, 403)


def test_the_template_files_ship_with_the_service():
    assert os.path.getsize(build.XLSX_PATH) < 200_000 and os.path.getsize(build.DOCX_PATH) < 200_000
