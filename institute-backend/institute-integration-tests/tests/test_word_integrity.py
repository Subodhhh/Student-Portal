"""Workstream A (Cycle 2.3): Word extraction integrity. Text in a Word paper reaches the question bank exactly as typed; a field can
never be imported short; "keep the text exactly as in the file" stores the file's text; Word equations that cannot be used are
reported truthfully with a readable linear form. All fixtures are synthetic."""
import re
from xml.etree import ElementTree as ET

import pytest

from inst_paper.services import documents
from inst_paper.services.extraction import ExtractionResult
from inst_paper.services.math import office, recognise
from tests.test_document_formats import docx
from tests.test_paper_recognition import (DOCX_MIME, M_NS, _para, _run, admin, post, word)  # noqa: F401  (fixture)
from tests.test_papers import API, setup, tmp_store  # noqa: F401  (fixtures)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"

BRACKETED = {
    "question": "(Hint) Find the value of x (in cm).",
    "option_a": "(a) 2", "option_b": "3 (approx)", "option_c": "(4)", "option_d": ")",
    "explanation": "(Using the identity) x = 3 (cm).",
    "topic": "(Geometry)",
}


def _blocks(fields=BRACKETED, answer="(B)", extra_lines=()):
    lines = [f"1. {fields['question']}", f"A) {fields['option_a']}", f"B) {fields['option_b']}", f"C) {fields['option_c']}",
             f"D) {fields['option_d']}", f"Answer: {answer}", "Marks: (4)", "Negative: 1", f"Explanation: {fields['explanation']}",
             *extra_lines, f"Topic: {fields['topic']}"]
    return docx("".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{x}</w:t></w:r></w:p>" for x in lines))


def _table(fields=BRACKETED, answer="B"):
    head = ["question", "option_a", "option_b", "option_c", "option_d", "correct", "marks", "negative_marks", "explanation", "topic"]
    row = [fields["question"], fields["option_a"], fields["option_b"], fields["option_c"], fields["option_d"], answer, "4", "1",
           fields["explanation"], fields["topic"]]
    cells = lambda xs: "<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t xml:space=\"preserve\">{x}</w:t></w:r></w:p></w:tc>" for x in xs) + "</w:tr>"
    return docx("<w:tbl>" + cells(head) + cells(row) + "</w:tbl>")


def _fields(res):
    q = res.questions[0]
    return {"question": q.stem, "option_a": q.options["A"], "option_b": q.options["B"], "option_c": q.options["C"],
            "option_d": q.options["D"], "explanation": q.explanation, "topic": q.topic}


# ---- W1: brackets are text, except around a one-code answer/marks value
def test_paragraph_layout_keeps_every_bracket():
    res = documents.extract_docx(_blocks(), 10)
    assert res.ok, res.errors
    assert _fields(res) == BRACKETED                              # was: "Hint) Find ... (in cm", "Using the identity) x = 3 (cm" ...
    assert res.questions[0].correct == "B" and str(res.questions[0].marks) == "4"


def test_table_and_paragraph_layouts_extract_the_same_text():
    assert _fields(documents.extract_docx(_blocks(), 10)) == _fields(documents.extract_docx(_table(), 10))


@pytest.mark.parametrize("answer", ["(B)", "B", "B)", "(b)", " ( B ) "])
def test_answer_codes_with_brackets(answer):
    res = documents.extract_docx(_blocks(answer=answer), 10)
    assert res.ok and res.questions[0].correct == "B"


def test_multi_line_explanation_keeps_brackets_on_every_line():
    res = documents.extract_docx(_blocks(extra_lines=("(continued) and (more).",)), 10)
    assert res.questions[0].explanation == "(Using the identity) x = 3 (cm). (continued) and (more)."


def test_pdf_text_path_shares_the_fixed_parser():
    res = ExtractionResult()
    rows = documents.parse_question_blocks("1. (a) What is 2 + 2?\nA) (4)\nB) 5\nC) 6\nD) 7\nAnswer: (A)\nMarks: 4\n"
                                           "Negative: 1\nExplanation: (trivial)\nTopic: (Arithmetic)", res)
    assert not res.errors
    row = rows[0][1]
    assert (row["question"], row["option_a"], row["explanation"], row["topic"], row["correct"]) == (
        "(a) What is 2 + 2?", "(4)", "(trivial)", "(Arithmetic)", "A")


# ---- W2: any unaccounted character rejects the paper (TEXT_LOST), never a short import
def test_lost_text_is_detected_and_nothing_is_imported(monkeypatch):
    # simulate a future regression: the option reader drops the first character of every option
    monkeypatch.setattr(documents, "_OPT", re.compile(r"^\s*\(?([A-Da-d])\s*[).:]\s*.(.*)$"))
    res = documents.extract_docx(_blocks(), 10)
    assert not res.ok and res.questions == []
    assert any(e.get("code") == "TEXT_LOST" for e in res.errors)


def test_text_account_does_not_reject_correct_papers():
    for answer in ("(B)", "B"):
        res = ExtractionResult()
        documents.parse_question_blocks("1. Q (x)\nA) (1)\nB) 2)\nC) (3\nD) 4\nAnswer: " + answer + "\nMarks: (4)\nNegative: (1)", res)
        assert not res.errors, res.errors


# ---- W3: keeping the original stores the file's text; refused when there is no faithful text
FLAGGED_DOCX_LINES = ["1. (Note) Evaluate ∫₀π sin x dx (exactly).", "A) (0)", "B) 1", "C) 2", "D) π", "Answer: (C)", "Marks: 4",
                      "Negative: 1", "Explanation: (Area under one arch.)"]


def _paper(lines):
    return docx("".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{x}</w:t></w:r></w:p>" for x in lines))


def test_keep_original_stores_the_exact_text_of_the_file(client, world, setup, admin):
    data = _paper(FLAGGED_DOCX_LINES)
    token = post(client, admin, world, setup["phy"], data, name="p.docx", ctype=DOCX_MIME, preview=True).json()["review_token"]
    r = post(client, admin, world, setup["phy"], data, name="p.docx", ctype=DOCX_MIME,
             resolutions={"1:question": {"action": "keep_original"}}, review_token=token)
    assert r.status_code == 201, r.text
    q = client.get(f"{API}/paper-versions/{r.json()['paper_version_id']}", headers=admin).json()["questions"][0]
    assert q["stem"] == "(Note) Evaluate ∫₀π sin x dx (exactly)."
    assert q["option_a"] == "(0)" and q["explanation"] == "(Area under one arch.)"


def test_keep_original_is_refused_for_content_that_could_not_be_extracted(client, world, setup, admin):
    data = word(_para(_run("1. Use the circuit "), "<w:r><w:drawing/></w:r>", _run(" to find R.")) +
                "".join(_para(_run(x)) for x in ("A) 1", "B) 2", "C) 3", "D) 4", "Answer: B", "Marks: 4", "Negative: 1")))
    token = post(client, admin, world, setup["phy"], data, name="p.docx", ctype=DOCX_MIME, preview=True).json()["review_token"]
    r = post(client, admin, world, setup["phy"], data, name="p.docx", ctype=DOCX_MIME,
             resolutions={"1:question": {"action": "keep_original"}}, review_token=token)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "REVIEW_REQUIRED"
    assert "cannot be extracted as text" in r.json()["detail"]["errors"][0]["message"] or \
        "could not be extracted as text" in r.json()["detail"]["errors"][0]["message"]
    ok = post(client, admin, world, setup["phy"], data, name="p.docx", ctype=DOCX_MIME,
              resolutions={"1:question": {"action": "edit", "text": "Use the circuit with R = 4 Ω to find the current."}}, review_token=token)
    assert ok.status_code == 201, ok.text


# ---- W4: Word equations - no glued commands, truthful reasons, readable fallback
def _omml_paragraph(xml):
    return ET.fromstring(f'<w:p xmlns:w="{W}" xmlns:m="{M}"><m:oMath>{xml}</m:oMath></w:p>')


def test_a_command_run_followed_by_a_letter_run_is_not_glued():
    p = _omml_paragraph("<m:sSup><m:e><m:r><m:t>y</m:t></m:r></m:e><m:sup><m:r><m:t>Δ</m:t></m:r><m:r><m:t>k</m:t></m:r></m:sup></m:sSup>")
    r = recognise(office.docx_paragraph_text(p))
    assert r.outcome == "CONVERTED" and r.text == r"\({y}^{\Delta k}\)"     # was \Deltak -> refused as "unsupported"
    star = _omml_paragraph("<m:r><m:t>a*b</m:t></m:r>")
    assert recognise(office.docx_paragraph_text(star)).text == r"\(a\cdot b\)"


def test_unsupported_equation_says_what_is_unsupported_and_shows_a_linear_form():
    p = _omml_paragraph("<m:sSup><m:e><m:r><m:t>y</m:t></m:r></m:e><m:sup><m:phant><m:e><m:r><m:t>k</m:t></m:r></m:e></m:phant></m:sup></m:sSup>")
    r = recognise(office.docx_paragraph_text(p))
    assert r.outcome == "EXTRACTION_ERROR" and r.reasons == ["UNSUPPORTED_WORD_EQUATION"]
    assert "Not supported: the equation part phant" in r.flags[0].reason
    assert r.readable_original == "y^(k)"                                   # the power stays visible (was flattened to "yk")


def test_an_equation_that_fails_verification_is_reported_as_such(monkeypatch):
    monkeypatch.setattr(office, "omml_to_latex", lambda el: "{y}_{2}")
    p = _omml_paragraph("<m:sSup><m:e><m:r><m:t>y</m:t></m:r></m:e><m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup>")
    r = recognise(office.docx_paragraph_text(p))
    assert r.outcome == "EXTRACTION_ERROR" and r.reasons == ["WORD_EQUATION_NOT_VERIFIED"]
    assert "did not match" in r.flags[0].reason and r.readable_original == "y^(2)"


def test_linear_form_keeps_fractions_roots_and_brackets():
    el = ET.fromstring(f'<m:oMath xmlns:m="{M}"><m:f><m:num><m:r><m:t>a+1</m:t></m:r></m:num><m:den><m:rad><m:radPr><m:degHide m:val="1"/>'
                       '</m:radPr><m:deg/><m:e><m:r><m:t>b</m:t></m:r></m:e></m:rad></m:den></m:f><m:d><m:e><m:r><m:t>c</m:t></m:r></m:e></m:d></m:oMath>')
    from inst_paper.services.math.omml import omml_text
    assert omml_text(el) == "(a+1)/(√(b))(c)"
