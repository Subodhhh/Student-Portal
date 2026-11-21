"""Bulk import of MSQ and NAT questions (docs/institute-portal/QUESTION_TYPES_ARCHITECTURE.md, T4) with ONE template: MCQ, MSQ and
NAT rows mixed on one sheet (or one Word table), each naming its type in Question Type and its key in Correct Answer; typed rows
validated by the shared rulebook with messages naming the column; columns of another type refused, never ignored; the answer key in
the duplicate identity (D11: same content + same key = DUPLICATE, different key = KEY_CONFLICT for review); "Import anyway" (R1: a
separate question, the existing one untouched, audited); MSQ option pictures linked to their version; old MCQ-only files unchanged."""
import io
from uuid import UUID

from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.utils import get_column_letter

from institute_common.models.papers import Question, QuestionVersion, QuestionVersionAsset
from inst_paper.import_templates import content
from tests.test_bulk_import import API, all_rows, bi, docx, job_of, q, store, upload, work  # noqa: F401  (fixtures)
from tests.test_question_images import png


def mcq(stem="What is the SI unit of force?", **over) -> dict:
    row = {**q(stem=stem), "Correct Answer": "A"}
    row.pop("Correct Option")
    row.update(over)
    return row


def msq(stem="Which of these are vector quantities?", correct="A, C", **over) -> dict:
    row = {"Chapter": "Laws of Motion", "Topic": "Force", "Difficulty": "Medium", "Question Type": "MSQ", "Question Text": stem,
           "Option A Text": "Force", "Option B Text": "Mass", "Option C Text": "Velocity", "Option D Text": "Time", "Correct Answer": correct}
    row.update(over)
    return row


def nat(stem="What is g at the Earth's surface, in m/s²?", answer="9.8", **over) -> dict:
    row = {"Chapter": "Laws of Motion", "Topic": "Force", "Difficulty": "Easy", "Question Type": "NAT", "Question Text": stem,
           "Correct Answer": answer, "Tolerance Type": "Plus-minus", "Tolerance": "0.05", "Answer Format": "Decimal", "Decimal Places": "2",
           "Unit": "m/s²"}
    row.update(over)
    return row


def workbook(rows, pictures=()) -> bytes:
    """The template's shape: one Questions sheet, every type mixed; pictures = (row number, header, png) placed over that cell."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Questions"
    ws.append(content.TITLES)
    for r in rows:
        ws.append([r.get(t, "") or None for t in content.TITLES])
    for row_no, header, data in pictures:
        img = XLImage(io.BytesIO(data))
        img.anchor = f"{get_column_letter(content.TITLES.index(header) + 1)}{row_no}"
        ws.add_image(img)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def preview(client, world, bi, db_factory, store, data, name="questions.xlsx") -> tuple[str, dict]:
    job_id = upload(client, world, bi, data, name=name).json()["job_id"]
    work(db_factory, store)
    return job_id, {r["row_no"]: r for r in all_rows(client, bi, job_id)}


def confirm(client, bi, db_factory, store, job_id) -> dict:
    assert client.post(f"{API}/bulk-imports/{job_id}/confirm", headers=bi["h"]()).status_code == 200
    work(db_factory, store)
    return job_of(client, bi, job_id)


def messages(row) -> str:
    return " ".join(e["message"] for e in row["errors"])


def test_mixed_rows_are_checked_by_the_rulebook_with_messages_naming_the_column(client, world, bi, db_factory, store):
    data = workbook([
        mcq(),                                                             # row 2: MCQ, valid
        msq(),                                                             # 3: MSQ, valid
        nat(),                                                             # 4: NAT, valid
        msq(stem="Correct option not offered?", correct="A, E"),          # 5
        msq(stem="A gap in the options?", **{"Option C Text": ""}),        # 6: D given, C empty
        nat(stem="Thousands separator?", answer="1,000"),                  # 7
        nat(stem="Exponent?", answer="1e3"),                               # 8
        nat(stem="Range without maximum?", **{"Tolerance Type": "Range", "Tolerance": "", "Minimum": "9"}),   # 9
        nat(stem="Exact with a tolerance?", **{"Tolerance Type": "Exact"}),   # 10
        nat(stem="Integer with decimals?", answer="2.5", **{"Answer Format": "Integer", "Decimal Places": "", "Tolerance Type": "Exact",
                                                           "Tolerance": ""}),   # 11
        nat(stem="Thirteen digits?", answer="1234567890123", **{"Tolerance Type": "Exact", "Tolerance": ""}),   # 12
        nat(stem="A NAT question with options?", **{"Option A Text": "x"}),   # 13: a column of another type
        mcq(stem="An MCQ with a tolerance?", **{"Tolerance": "1"}),           # 14
        mcq(stem="An MCQ with a fifth option?", **{"Option E Text": "e"}),    # 15
        mcq(stem="Unknown type?", **{"Question Type": "TF"}),                 # 16
    ])
    job_id, rows = preview(client, world, bi, db_factory, store, data)
    assert [rows[n]["question_type"] for n in (2, 3, 4)] == ["MCQ_SINGLE", "MCQ_MULTI", "NUMERICAL"]
    assert [rows[n]["status"] for n in (2, 3, 4)] == ["VALID"] * 3, [rows[n]["errors"] for n in (2, 3, 4)]
    expected = {5: "Correct option E is not one of the options", 6: "Option C is empty", 7: "plain number", 8: "plain number",
                9: "Maximum is required", 10: "Leave Tolerance empty", 11: "whole number", 12: "significant digits",
                13: "not used by a NAT question", 14: "not used by an MCQ", 15: "not used by an MCQ", 16: "Unknown question type"}
    for n, text in expected.items():
        assert rows[n]["status"] == "INVALID" and text in messages(rows[n]), (n, rows[n]["errors"])
    assert rows[5]["errors"][0]["field"] == "correct_option" and rows[7]["errors"][0]["field"] == "correct_option"   # Correct Answer
    report = client.get(f"{API}/bulk-imports/{job_id}/report.csv", headers=bi["h"]()).text
    assert "5,INVALID,correct_option" in report and "9,INVALID,maximum" in report

    # a correction validates at once; changing the Question Type re-reads the row as that type
    r = client.patch(f"{API}/bulk-imports/{job_id}/rows/7", headers=bi["h"](), json={"correct_option": "1000"})
    assert r.status_code == 200 and r.json()["status"] == "VALID"
    r = client.patch(f"{API}/bulk-imports/{job_id}/rows/16", headers=bi["h"](), json={"question_type": "MSQ", "correct_option": "A, B"})
    assert r.status_code == 200 and r.json()["status"] == "VALID" and r.json()["question_type"] == "MCQ_MULTI"

    job = confirm(client, bi, db_factory, store, job_id)
    assert job["imported_rows"] == 5
    with db_factory() as db:
        typed = {v.stem: v for v in db.query(QuestionVersion) if v.question_type != "MCQ_SINGLE"}
        assert typed["Which of these are vector quantities?"].answer_key == {"correct": ["A", "C"]}
        assert typed["Unknown type?"].answer_key == {"correct": ["A", "B"]}
        assert typed["Thousands separator?"].answer_key == {"value": "1000", "tolerance": {"kind": "ABS", "amount": "0.05"}}
        assert typed["Thousands separator?"].content == {"format": "DECIMAL", "decimals": 2, "unit": "m/s²"}
        assert {q.question_type for q in db.query(Question)} == {"MCQ_SINGLE", "MCQ_MULTI", "NUMERICAL"}


def test_a_word_table_holds_every_type_too(client, world, bi, db_factory, store):
    import tests.test_bulk_import as base
    titles = content.WORD_TITLES
    old, base.HEADERS = base.HEADERS, titles               # the helper writes the table with the template's Word columns
    try:
        data = docx([mcq(), msq(), nat()])
    finally:
        base.HEADERS = old
    _, rows = preview(client, world, bi, db_factory, store, data, name="questions.docx")
    assert [r["question_type"] for r in rows.values()] == ["MCQ_SINGLE", "MCQ_MULTI", "NUMERICAL"]
    assert [r["status"] for r in rows.values()] == ["VALID"] * 3, [r["errors"] for r in rows.values()]


def test_a_different_key_is_a_conflict_and_import_anyway_adds_a_separate_question(client, world, bi, db_factory, store):
    job_id, _ = preview(client, world, bi, db_factory, store, workbook([mcq(), msq(), nat()]))
    assert confirm(client, bi, db_factory, store, job_id)["imported_rows"] == 3
    with db_factory() as db:
        before = {(v.question_id, v.version_no, str(v.answer_key), v.correct_option) for v in db.query(QuestionVersion)}

    data = workbook([mcq(), mcq(**{"Correct Answer": "B"}), msq(), msq(correct="A, B"), nat(), nat(answer="9.81")])
    job_id, rows = preview(client, world, bi, db_factory, store, data)
    for same in (2, 4, 6):                                                  # same content, same key
        assert rows[same]["status"] == "DUPLICATE", rows[same]
    for other in (3, 5, 7):                                                 # same content, another key: for review
        assert rows[other]["status"] == "INVALID" and rows[other]["errors"][0]["code"] == "KEY_CONFLICT", rows[other]
    assert "A, C" in messages(rows[5]) and "9.8 ± 0.05" in messages(rows[7])

    # only a KEY_CONFLICT row can be imported anyway
    dup = client.post(f"{API}/bulk-imports/{job_id}/rows/2/import-anyway", headers=bi["h"]())
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "NOT_A_KEY_CONFLICT"
    r = client.post(f"{API}/bulk-imports/{job_id}/rows/5/import-anyway", headers=bi["h"]())
    assert r.status_code == 200 and r.json()["status"] == "VALID" and r.json()["import_anyway"] is True
    from institute_common.models.staff import AuditLog
    with db_factory() as db:
        decided = db.query(AuditLog).filter_by(action="bulk_import.key_conflict_import_anyway").one()
        assert decided.actor_staff_id == bi["pa_id"] and decided.details["row"] == "5"
    # a later full re-check keeps the decision
    assert client.post(f"{API}/bulk-imports/{job_id}/revalidate", headers=bi["h"]()).status_code in (200, 202)
    work(db_factory, store)
    rows = {r["row_no"]: r for r in all_rows(client, bi, job_id)}
    assert rows[5]["status"] == "VALID" and rows[7]["status"] == "INVALID"

    assert confirm(client, bi, db_factory, store, job_id)["imported_rows"] == 1
    with db_factory() as db:
        after = {(v.question_id, v.version_no, str(v.answer_key), v.correct_option) for v in db.query(QuestionVersion)}
        assert before < after and len(after - before) == 1                  # the existing questions and keys are untouched
        [(new_id, *_)] = after - before
        keys = sorted(v.answer_key["correct"] for v in db.query(QuestionVersion).filter_by(question_type="MCQ_MULTI"))
        assert keys == [["A", "B"], ["A", "C"]]
        created = db.query(AuditLog).filter_by(action="bulk_import.key_conflict_imported").one()
        assert created.target_id == str(new_id) and created.actor_staff_id == bi["pa_id"]


def test_msq_option_pictures_are_linked_to_their_version(client, world, bi, db_factory, store):
    row = msq(stem="Which graph shows constant velocity or rest?", **{"Option A Text": "", "Option B Text": "", "Option C Text": "",
                                                                      "Option D Text": "", "Correct Answer": "A, B"})
    data = workbook([row], pictures=[(2, f"Option {x} Image", png(colour)) for x, colour in
                                     (("A", "navy"), ("B", "lime"), ("C", "red"), ("D", "teal"))])
    job_id, rows = preview(client, world, bi, db_factory, store, data)
    assert rows[2]["status"] == "VALID", rows[2]["errors"]
    assert set(rows[2]["images"]) == {"option_a_image", "option_b_image", "option_c_image", "option_d_image"}
    assert confirm(client, bi, db_factory, store, job_id)["imported_rows"] == 1
    with db_factory() as db:
        v = db.query(QuestionVersion).one()
        links = {a.role: a.asset_id for a in db.query(QuestionVersionAsset)}
        assert links == {f"option_{o['id'].lower()}": UUID(o["image"]) for o in v.content["options"]} and len(links) == 4
        assert v.option_a_image_id is None and all(o["text"] == "" for o in v.content["options"])
    # the history shows the stored pictures once the staged copies are gone
    shown = {r["row_no"]: r for r in all_rows(client, bi, job_id)}[2]["images"]
    assert all(i["url"].startswith("/api/v1/question-images/") for i in shown.values())
