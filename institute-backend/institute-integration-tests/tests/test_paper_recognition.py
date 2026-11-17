"""Scientific-notation recognition in the real import workflow: preview -> review gate -> provider decisions -> import -> stored
record -> approval/publication -> student delivery. CSV, Excel (rich-text superscripts) and Word (formatting runs, native equations,
images) files; authorisation is the upload's own."""
import io
import json
import zipfile

import pytest

from institute_common.core.roles import Role
from inst_paper.services.math import recognise
from tests.test_document_formats import W_NS, docx
from tests.test_exams import FakePublisher, ctx, pa, plan_body  # noqa: F401  (fixtures)
from tests.test_papers import API, setup, tmp_store  # noqa: F401  (fixtures)

HEAD = "question,option_a,option_b,option_c,option_d,correct,marks,negative_marks,explanation,topic\n"
CLEAN = HEAD + ("Solve x² − 5x + 6 = 0.,2 and 3,1 and 6,−2 and −3,2 and −3,A,4,1,(x−2)(x−3)=0,Algebra\n"
                "The formula of water is:,H₂O,H₂O₂,HO,OH⁻,A,4,1,,Chemistry\n")
FLAGGED = HEAD + ("Evaluate ∫₀π sin x dx.,0,1,2,π,C,4,1,,Calculus\n"
                  "Simplify sin x/x at x = 1.,1,2,3,4,A,4,1,,Algebra\n")
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
M_NS = 'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'


def post(client, h, world, subject_id, data: bytes, name="paper.csv", ctype="text/csv", branch="p1b1", **form):
    fields = {"title": form.pop("title", "Recognition test"), **{k: (json.dumps(v) if isinstance(v, dict) else str(v).lower()
                                                                   if isinstance(v, bool) else v) for k, v in form.items()}}
    return client.post(f"{API}/branches/{world[branch]}/subjects/{subject_id}/papers/upload",
                       files={"file": (name, io.BytesIO(data), ctype)}, data=fields, headers=h)


@pytest.fixture()
def admin(make_staff, world, login):
    return login(make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])[1])


def _counts(db_factory):
    from institute_common.models.papers import PaperUpload, PaperVersion, Question, StoredFile
    with db_factory() as db:
        return tuple(db.query(m).count() for m in (PaperUpload, PaperVersion, Question, StoredFile))


# ------------------------------------------------------------------ preview and gate

def test_preview_shows_every_field_and_stores_nothing(client, world, setup, admin, db_factory):
    before = _counts(db_factory)
    r = post(client, admin, world, setup["chem"], CLEAN.encode(), preview=True)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["preview"] and j["domain"] == "chemistry" and j["engine_version"] and len(j["review_token"]) == 64
    q1, q2 = j["questions"]
    assert q1["fields"]["question"]["outcome"] == "CONVERTED"
    assert q1["fields"]["question"]["text"] == r"Solve \(x^{2} - 5x + 6 = 0\)."
    assert q1["fields"]["question"]["original"] == "Solve x² − 5x + 6 = 0."
    assert q2["fields"]["option_a"]["text"] == r"\(\mathrm{H_{2}O}\)"             # the Chemistry subject reads a formula
    assert q2["fields"]["question"]["outcome"] == "PLAIN_TEXT"
    assert j["summary"]["questions_needing_review"] == 0
    assert _counts(db_factory) == before                                           # nothing stored


def test_clean_paper_imports_converted_text_and_keeps_the_original(client, world, setup, admin, db_factory):
    r = post(client, admin, world, setup["chem"], CLEAN.encode())
    assert r.status_code == 201, r.text
    detail = client.get(f"{API}/paper-versions/{r.json()['paper_version_id']}", headers=admin).json()
    assert detail["questions"][0]["stem"] == r"Solve \(x^{2} - 5x + 6 = 0\)."
    assert detail["questions"][1]["option_d"] == r"\(\mathrm{OH^{-}}\)"
    rec = client.get(f"{API}/uploads/{r.json()['upload_id']}", headers=admin).json()["recognition"]
    f = rec["questions"][0]["fields"]["question"]
    assert rec["engine_version"] and rec["domain"] == "chemistry" and rec["subject"] == "Chemistry"
    assert f["source"] == "Solve x² − 5x + 6 = 0." and f["stored"] == detail["questions"][0]["stem"] and f["decision"] == "accepted"


def test_unresolved_ambiguity_blocks_the_import(client, world, setup, admin, db_factory):
    before = _counts(db_factory)
    r = post(client, admin, world, setup["phy"], FLAGGED.encode())
    assert r.status_code == 422
    d = r.json()["detail"]
    assert d["code"] == "REVIEW_REQUIRED" and d["review_token"]
    assert {(e["question"], e["field"]) for e in d["errors"]} == {("1", "question"), ("2", "question")}
    assert "BOUND_TYPED_ON_LINE" in d["errors"][0]["reasons"]
    uploads, versions, questions, _ = _counts(db_factory)
    assert (versions, questions) == before[1:3] and uploads == before[0] + 1         # a rejected upload is kept, nothing imported
    rejected = client.get(f"{API}/uploads/{d['upload_id']}", headers=admin).json()
    assert rejected["status"] == "REJECTED" and rejected["recognition"]["summary"]["questions_needing_review"] == 2


def test_provider_decisions_resolve_the_review(client, world, setup, admin):
    token = post(client, admin, world, setup["phy"], FLAGGED.encode(), preview=True).json()["review_token"]
    decisions = {"1:question": {"action": "edit", "text": "Evaluate ∫₀^π sin x dx."},
                 "2:question": {"action": "keep_original"}}
    r = post(client, admin, world, setup["phy"], FLAGGED.encode(), resolutions=decisions, review_token=token)
    assert r.status_code == 201, r.text
    stems = [q["stem"] for q in client.get(f"{API}/paper-versions/{r.json()['paper_version_id']}", headers=admin).json()["questions"]]
    assert stems == [r"Evaluate \(\int_{0}^{\pi} \sin x\,dx\).", "Simplify sin x/x at x = 1."]
    rec = client.get(f"{API}/uploads/{r.json()['upload_id']}", headers=admin).json()["recognition"]
    q1 = rec["questions"][0]["fields"]["question"]
    assert q1["source"] == "Evaluate ∫₀π sin x dx." and q1["outcome"] == "NEEDS_REVIEW" and q1["decision"] == "edit"
    assert q1["edited"]["original"] == "Evaluate ∫₀^π sin x dx."                   # the provider's original stays recoverable


def test_an_edit_that_is_still_ambiguous_is_refused(client, world, setup, admin):
    token = post(client, admin, world, setup["phy"], FLAGGED.encode(), preview=True).json()["review_token"]
    decisions = {"1:question": {"action": "edit", "text": "Evaluate ∫₀1 x dx."}, "2:question": {"action": "keep_original"}}
    r = post(client, admin, world, setup["phy"], FLAGGED.encode(), resolutions=decisions, review_token=token)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "REVIEW_REQUIRED"
    assert r.json()["detail"]["errors"][0]["message"].startswith("The corrected text still needs review")


def test_decisions_are_bound_to_the_previewed_file(client, world, setup, admin):
    token = post(client, admin, world, setup["phy"], FLAGGED.encode(), preview=True).json()["review_token"]
    other = FLAGGED.replace("x = 1", "x = 2").encode()
    r = post(client, admin, world, setup["phy"], other, resolutions={"2:question": {"action": "keep_original"}}, review_token=token)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "REVIEW_STALE"
    bad = post(client, admin, world, setup["phy"], FLAGGED.encode(), resolutions={"9:nope": {"action": "edit"}}, review_token=token)
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "INVALID_RESOLUTIONS"


def test_preview_respects_upload_authorisation(client, world, setup, make_staff, login):
    other_branch = login(make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b2"])])[1])
    assert post(client, other_branch, world, setup["phy"], CLEAN.encode(), preview=True).status_code == 403
    foreign = login(make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])[1])
    assert post(client, foreign, world, setup["phy"], CLEAN.encode(), preview=True).status_code == 403


def test_extraction_errors_are_reported_by_the_preview(client, world, setup, admin):
    r = post(client, admin, world, setup["phy"], (HEAD + "Q?,a,a,b,c,A,4,1,,\n").encode(), preview=True)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "EXTRACTION_FAILED"


# ------------------------------------------------------------------ Word

def _run(text, align=None):
    rpr = f'<w:rPr><w:vertAlign w:val="{align}"/></w:rPr>' if align else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{text}</w:t></w:r>'


def _para(*parts):
    return f"<w:p>{''.join(parts)}</w:p>"


FRACTION = ('<m:oMath><m:f><m:num><m:r><m:t>a</m:t></m:r></m:num><m:den><m:r><m:t>b</m:t></m:r></m:den></m:f>'
            '<m:r><m:t>+</m:t></m:r><m:sSup><m:e><m:r><m:t>x</m:t></m:r></m:e><m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup></m:oMath>')
INTEGRAL = ('<m:oMath><m:nary><m:naryPr><m:chr m:val="∫"/></m:naryPr><m:sub><m:r><m:t>0</m:t></m:r></m:sub>'
            '<m:sup><m:r><m:t>π</m:t></m:r></m:sup><m:e><m:func><m:fName><m:r><m:rPr><m:sty m:val="p"/></m:rPr><m:t>sin</m:t></m:r>'
            '</m:fName><m:e><m:r><m:t>x</m:t></m:r></m:e></m:func><m:r><m:t>dx</m:t></m:r></m:e></m:nary></m:oMath>')
UNSUPPORTED = '<m:oMath><m:phant><m:e><m:r><m:t>q</m:t></m:r></m:e></m:phant></m:oMath>'


def word(body: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("word/document.xml", f'<?xml version="1.0" encoding="UTF-8"?><w:document {W_NS} {M_NS}><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


def _word_paper(first_question: list[str]) -> bytes:
    body = _para(*first_question) + "".join(_para(_run(t)) for t in (
        "A) one", "B) two", "C) three", "D) four", "Answer: B", "Marks: 4", "Negative: 1"))
    return word(body)


def test_word_native_equations_and_formatting_runs_are_converted(client, world, setup, admin):
    data = _word_paper([_run("1. If y = x"), _run("2", "superscript"), _run(" and z = "), FRACTION, _run(", evaluate "), INTEGRAL,
                        _run(".")])
    r = post(client, admin, world, setup["phy"], data, name="paper.docx", ctype=DOCX_MIME, preview=True)
    assert r.status_code == 200, r.text
    f = r.json()["questions"][0]["fields"]["question"]
    assert f["outcome"] == "CONVERTED"
    assert f["text"] == (r"If \(y = x^{2}\) and z = \(\frac{a}{b}+{x}^{2}\), evaluate \(\int_{0}^{\pi} \sin x\,dx\).")


def test_unsupported_word_equation_and_images_are_not_silently_dropped(client, world, setup, admin):
    image = '<w:r><w:drawing/></w:r>'
    data = _word_paper([_run("1. Use the circuit "), image, _run(" and "), UNSUPPORTED, _run(".")])
    j = post(client, admin, world, setup["phy"], data, name="paper.docx", ctype=DOCX_MIME, preview=True).json()
    f = j["questions"][0]["fields"]["question"]
    assert f["outcome"] == "EXTRACTION_ERROR" and set(f["reasons"]) == {"UNSUPPORTED_OBJECT", "UNSUPPORTED_WORD_EQUATION"}
    assert f["original"] == "Use the circuit [image] and q."
    r = post(client, admin, world, setup["phy"], data, name="paper.docx", ctype=DOCX_MIME)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "REVIEW_REQUIRED"


def test_word_table_with_template_style_headers_imports(client, world, setup, admin):
    cells = lambda *xs: "<w:tr>" + "".join(f"<w:tc>{_para(_run(x))}</w:tc>" for x in xs) + "</w:tr>"
    table = ("<w:tbl>" + cells("Question", "Option A", "Option B", "Option C", "Option D", "Correct", "Marks", "Negative")
             + cells("Formula of water?", "H₂O", "CO₂", "NaCl", "O₂", "A", "4", "1") + "</w:tbl>")
    r = post(client, admin, world, setup["chem"], docx(table), name="paper.docx", ctype=DOCX_MIME)
    assert r.status_code == 201, r.text                                            # was "The file contains no questions."
    q = client.get(f"{API}/paper-versions/{r.json()['paper_version_id']}", headers=admin).json()["questions"][0]
    assert q["option_a"] == r"\(\mathrm{H_{2}O}\)" and q["option_c"] == "NaCl"


# ------------------------------------------------------------------ Excel rich text

def xlsx_rich(rows: list[list]) -> bytes:
    """A one-sheet workbook with shared strings; a cell given as a list of (text, vertAlign) runs is rich text."""
    S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    strings, sheet_rows = [], []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, v in enumerate(row):
            ref = f"{chr(65 + c)}{r}"
            if isinstance(v, (int, float)):
                cells.append(f'<c r="{ref}"><v>{v}</v></c>')
                continue
            runs = v if isinstance(v, list) else [(v, None)]
            si = "".join(f'<r>{f"<rPr><vertAlign val=\"{a}\"/></rPr>" if a else ""}<t xml:space="preserve">{t}</t></r>' for t, a in runs)
            strings.append(f"<si>{si}</si>")
            cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
        sheet_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{S}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                                      '<sheets><sheet name="Questions" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                                 '<Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="ws"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{S}">{"".join(strings)}</sst>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{S}"><sheetData>{"".join(sheet_rows)}</sheetData></worksheet>')
    return buf.getvalue()


def test_excel_superscript_runs_keep_their_meaning(client, world, setup, admin):
    head = HEAD.strip().split(",")
    data = xlsx_rich([head, [[("Kinetic energy is ½mv", None), ("2", "superscript"), (".", None)], "true", "false", "maybe", "never",
                             "A", 4, 1, [("Speed in m s", None), ("−1", "superscript")], "Energy"]])
    r = post(client, admin, world, setup["phy"], data, name="paper.xlsx", ctype=XLSX_MIME)
    assert r.status_code == 201, r.text
    q = client.get(f"{API}/paper-versions/{r.json()['paper_version_id']}", headers=admin).json()["questions"][0]
    assert q["stem"] == r"Kinetic energy is \(\frac{1}{2}mv^{2}\)."                 # not "½mv2"
    assert q["explanation"] == r"Speed in \(\mathrm{m}\,\mathrm{s}^{-1}\)"           # units, not the product "ms"


# ------------------------------------------------------------------ to the student

def test_approved_content_reaches_the_student_delivery_unchanged(client, world, ctx, pa, login, db_factory):
    from inst_exam.services import outbox
    h = login(pa)
    paper = (HEAD + "Evaluate ∫₀^π sin x dx.,0,1,2,π,C,4,1,∫₀^π sin x dx = 2,Calculus\n"
                    "Which ion is SO₄²⁻?,sulfate,sulfite,sulfide,thiosulfate,A,4,1,,Ions\n"
                    "Rate of 2H₂ + O₂ → 2H₂O in mol L⁻¹ s⁻¹?,1,2,3,4,A,4,1,,Kinetics\n").encode()
    up = post(client, h, world, ctx["phy"], paper, title="Calculus and ions")
    assert up.status_code == 201, up.text
    pv = up.json()["paper_version_id"]
    for step in ("approve", "publish"):
        assert client.post(f"{API}/paper-versions/{pv}/{step}", json={}, headers=h).status_code == 200
    body = plan_body(ctx, slots=[{"subject_id": str(ctx["phy"]), "paper_version_id": pv}])
    plan = client.post(f"{API}/branches/{world['p1b1']}/exam-plans", json=body, headers=h).json()
    assert client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers=h).status_code in (200, 201)
    pub = FakePublisher()
    with db_factory() as db:
        assert outbox.deliver_due(db, pub)["sent"] == 1
    texts = [q["text"] for q in pub.sent[0]["questions"]]
    assert texts[0] == r"Evaluate \(\int_{0}^{\pi} \sin x\,dx\)."
    assert texts[1] == r"Which ion is \(\mathrm{SO_{4}^{2-}}\)?"                    # Physics paper: a charge is unmistakable
    assert pub.sent[0]["questions"][0]["options"]["D"] == r"\(\pi\)"


# ------------------------------------------------------------------ engine safety gates (permanent regressions)

@pytest.mark.parametrize("text, code", [
    ("∫₀π sin x dx", "BOUND_TYPED_ON_LINE"), ("∫₀1 x dx", "BOUND_TYPED_ON_LINE"), ("∫₀∞ e^(−x) dx", "BOUND_TYPED_ON_LINE"),
    ("∫ᵃb f(x) dx", "BOUND_TYPED_ON_LINE"), ("∑ᵢ₌₁n i", "BOUND_TYPED_ON_LINE"), ("∏ₖ₌₁n k", "BOUND_TYPED_ON_LINE"),
    ("∫₀ x dx", "BOUND_INCOMPLETE"), ("∫₀π/2 cos x dx", "BOUND_TYPED_ON_LINE"),
])
def test_known_silent_limit_errors_are_flagged(text, code):
    r = recognise(text)
    assert r.text == text and r.outcome == "NEEDS_REVIEW" and code in r.reasons


@pytest.mark.parametrize("text, latex", [
    ("∫₀² x dx", r"\int_{0}^{2} x\,dx"), ("∫₀^π sin x dx", r"\int_{0}^{\pi} \sin x\,dx"), ("∫_0^π sin x dx", r"\int_{0}^{\pi} \sin x\,dx"),
])
def test_well_typed_limits_convert(text, latex):
    assert recognise(text).text == rf"\({latex}\)"


@pytest.mark.parametrize("text, latex", [
    ("Co", None), ("CO", None), ("Hf", None), ("HF", None), ("Sn", None), ("Cs", None), ("CS₂", r"\mathrm{CS_{2}}"),
    ("Co₂(CO)₈", r"\mathrm{Co_{2}(CO)_{8}}"), ("HfO₂", r"\mathrm{HfO_{2}}"), ("SnCl₂", r"\mathrm{SnCl_{2}}"), ("CsF", None),
    ("NH₄⁺", r"\mathrm{NH_{4}^{+}}"), ("SO₄²⁻", r"\mathrm{SO_{4}^{2-}}"), ("Fe³⁺", r"\mathrm{Fe^{3+}}"),
    ("[Fe(CN)₆]³⁻", r"\mathrm{[Fe(CN)_{6}]^{3-}}"), ("N₂ + 3H₂ ⇌ 2NH₃", r"\mathrm{N_{2}} + 3\mathrm{H_{2}} \rightleftharpoons 2\mathrm{NH_{3}}"),
    ("CuSO₄·5H₂O", r"\mathrm{CuSO_{4}\cdot 5H_{2}O}"), ("²³⁵U", r"{}^{235}\mathrm{U}"), ("NaCl(aq)", r"\mathrm{NaCl}\mathrm{(aq)}"),
    ("Ca₂(PO₄)₃", r"\mathrm{Ca_{2}(PO_{4})_{3}}"),                                  # a deliberately wrong option is kept exactly
])
def test_chemistry_identity_and_case_are_preserved(text, latex):
    r = recognise(text, subject="chemistry")
    assert r.text == (rf"\({latex}\)" if latex else text) and r.outcome != "NEEDS_REVIEW"


@pytest.mark.parametrize("text, code", [
    ("Cu2+", "AMBIGUOUS_CHEMICAL_CHARGE"), ("SO42-", "AMBIGUOUS_CHEMICAL_CHARGE"), ("Fe³", "AMBIGUOUS_CHEMICAL_CHARGE"),
    ("Mn⁺⁷", "AMBIGUOUS_CHEMICAL_CHARGE"), ("Xy₂", "UNKNOWN_ELEMENT_SYMBOL"), ("P₂ = 2 atm", "AMBIGUOUS_DOMAIN"),
])
def test_unclear_chemistry_is_flagged_not_guessed(text, code):
    r = recognise(text, subject="chemistry")
    assert code in r.reasons and r.outcome == "NEEDS_REVIEW"


@pytest.mark.parametrize("text", [
    "Electron is negatively charged.", "Avogadro constant:", "IUPAC name of the compound:", "A catalyst changes the rate.",
    "Methane is a gas.", "The answer is A.",
])
def test_chemistry_prose_is_never_changed(text):
    r = recognise(text, subject="chemistry")
    assert r.text == text and r.outcome == "PLAIN_TEXT"


def test_a_formula_pasted_without_subscripts_is_rebuilt_and_the_prose_around_it_is_not():
    # policy change (source-independent recognition): in a Chemistry paper, H2O copied as plain text is H₂O - a number after an
    # element symbol is a count. Words, and formulas without counts (NaCl), stay exactly as typed.
    r = recognise("Water is H2O and salt is NaCl.", subject="chemistry")
    assert r.text == r"Water is \(\mathrm{H_{2}O}\) and salt is NaCl." and r.outcome == "CONVERTED"
    assert recognise("Water is H2O and salt is NaCl.", subject="mathematics").outcome != "CONVERTED"   # Chemistry papers only


def test_meaning_check_rejects_corrupted_conversions():
    from inst_paper.services.math.safety import preserved
    assert not preserved("x²", "x_{2}") and not preserved("∫₀^π sin x dx", r"\int_{0} \pi\sin x\,dx")
    assert not preserved("a−b", "a+b") and not preserved("α+1", "a+1") and not preserved("x⁻¹", "x^{1}")
    assert not preserved("[[1,2],[3,4]]", r"\begin{bmatrix}2 & 1 \\ 3 & 4\end{bmatrix}")
    assert preserved("SO₄²⁻", r"\mathrm{SO_{4}^{2-}}") and preserved("limₓ→₀ sin(x)/x", r"\lim_{x \to 0} \frac{\sin(x)}{x}")


def test_provider_latex_outside_the_allow_list_is_flagged():
    assert recognise(r"Find $\int_0^1 x\,dx$.").outcome == "CONVERTED"
    r = recognise(r"Find $\href{http://x}{y}$.")
    assert r.outcome == "NEEDS_REVIEW" and r.reasons == ["LATEX_VALIDATION_FAILED"]


@pytest.mark.parametrize("text, expected", [
    ("v=u+at=0+2×5=10 m s⁻¹.", r"\(v = u + at = 0 + 2 \times 5 = 10\,\mathrm{m}\,\mathrm{s}^{-1}\)."),   # was flagged by PoC-1
    ("PV=nRT", r"\(PV = nRT\)"), ("Q=It", r"\(Q = It\)"), ("For perpendicular motion, F=|q|vB.", r"For perpendicular motion, \(F = \lvert q \rvert vB\)."),
    ("Vᵣₘₛ=V₀/√2≈141.4 V.", r"\(V_{\mathrm{rms}} = \frac{V_{0}}{\sqrt{2}} \approx 141.4\,\mathrm{V}\)."),
])
def test_physics_letter_equations_typed_without_spaces(text, expected):
    assert recognise(text).text == expected


@pytest.mark.parametrize("text", ["x=at most 5", "Ans=Yes", "Set the value=true", "y=it is"])
def test_glued_words_in_sentences_stay_prose(text):
    assert recognise(text).text == text


# ------------------------------------------------------------------ correction cycle 2.1 (defects found by holdout C)

@pytest.mark.parametrize("text, expected", [
    ("1 h = 3 half-lives, so N/N₀ = (1/2)³ = 1/8.",
     r"\(1\,\mathrm{h} = 3\) half-lives, so \(\frac{N}{N_{0}} = (\frac{1}{2})^{3} = \frac{1}{8}\)."),          # h is the hour
    ("5 × 10⁻⁷ m", r"\(5 \times {10}^{-7}\,\mathrm{m}\)"),
    ("λ = c/ν = (3 × 10^8)/(6 × 10^14) = 5 × 10^-7 m = 500 nm.",
     r"\(\lambda = \frac{c}{\nu} = \frac{3 \times {10}^{8}}{6 \times {10}^{14}} = 5 \times {10}^{-7}\,\mathrm{m} = 500\,\mathrm{nm}\)."),
    ("k = 9 × 10⁹ N m² C⁻²", r"\(k = 9 \times {10}^{9}\,\mathrm{N}\,\mathrm{m}^{2}\,\mathrm{C}^{-2}\)"),
    ("N m² C⁻²", r"\(\mathrm{N}\,\mathrm{m}^{2}\,\mathrm{C}^{-2}\)"),
    ("2 m + 3 m = 5 m", r"\(2\,\mathrm{m} + 3\,\mathrm{m} = 5\,\mathrm{m}\)"),
    ("x = 2 m and y = 3 m", r"\(x = 2\,\mathrm{m}\) and \(y = 3\,\mathrm{m}\)"),
    ("density 1000 kg/m³", r"density \(1000\,\mathrm{kg}/\mathrm{m}^{3}\)"),
])
def test_units_are_not_read_as_variables(text, expected):
    r = recognise(text, subject="physics")
    assert r.text == expected and r.outcome == "CONVERTED"


@pytest.mark.parametrize("text, expected", [
    ("3m = 6", r"\(3m = 6\)"),                      # no space: the coefficient of a variable
    ("2 x = 6", r"\(2x = 6\)"),                     # x is never a unit symbol
    ("h = 3", r"\(h = 3\)"),                        # a variable on its own
    ("x/y²", r"\(\frac{x}{y^{2}}\)"),
    ("a b⁻¹", r"\(ab^{-1}\)"),
])
def test_letters_that_are_variables_stay_variables(text, expected):
    assert recognise(text).text == expected


@pytest.mark.parametrize("text", ["3 m = 6", "If 3 m = 6, find m."])
def test_unit_or_variable_that_cannot_be_decided_is_flagged(text):
    r = recognise(text)
    assert r.text == text and r.reasons == ["AMBIGUOUS_UNIT"]


@pytest.mark.parametrize("text, expected", [
    ("Φ = ∮ E · dA = q/ε₀, independent of the shape.", r"\(\Phi = \oint E \cdot dA = \frac{q}{\varepsilon_{0}}\), independent of the shape."),
    ("∭ ρ dV", r"\(\iiint \rho\,dV\)"),
    ("W = [x³] from 0 to 2 = 8 J.", r"\(W = [x^{3}]\) from 0 to 2 = 8 J."),       # never the false equation "2 = 8 J"
    ("from 0 to 2 = 8", "from 0 to 2 = 8"),
    ("P = nRT/V = (2 × 8.314 × 300)/0.0249 ≈ 2.0 × 10⁵ Pa.",
     r"\(P = \frac{nRT}{V} = \frac{2 \times 8.314 \times 300}{0.0249} \approx 2.0 \times {10}^{5}\,\mathrm{Pa}\)."),
])
def test_formula_boundaries_keep_the_whole_expression(text, expected):
    r = recognise(text)
    assert r.text == expected and r.outcome != "NEEDS_REVIEW"


@pytest.mark.parametrize("text, expected", [
    ("The dA term is small.", "The dA term is small."),                     # no integral: an ordinary word
    ("Pressure is force/area", "Pressure is force/area"),
    ("the ratio and/or the sum", "the ratio and/or the sum"),
    ("Use PV/nT here", "Use PV/nT here"),
    ("Hence 6 = 2 × 3.", r"Hence \(6 = 2 \times 3\)."),                      # a true equation after a word is still converted
    ("Show that 2 + 3 = 5.", r"Show that \(2 + 3 = 5\)."),
    ("Three half-lives pass; remaining fraction=(1/2)³=1/8.", r"Three half-lives pass; remaining fraction=\((\frac{1}{2})^{3} = \frac{1}{8}\)."),
])
def test_boundary_near_misses(text, expected):
    assert recognise(text).text == expected


@pytest.mark.parametrize("text, latex", [
    ("[M L² T⁻²]", r"[\mathrm{M}\,\mathrm{L}^{2}\,\mathrm{T}^{-2}]"), ("[ML⁻¹T⁻²]", r"[\mathrm{M}\,\mathrm{L}^{-1}\,\mathrm{T}^{-2}]"),
    ("[M⁰ L T⁻²]", r"[\mathrm{M}^{0}\,\mathrm{L}\,\mathrm{T}^{-2}]"), ("[M L T⁻² Θ⁻¹]", r"[\mathrm{M}\,\mathrm{L}\,\mathrm{T}^{-2}\,\Theta^{-1}]"),
    ("[M L² T⁻³ I⁻¹]", r"[\mathrm{M}\,\mathrm{L}^{2}\,\mathrm{T}^{-3}\,\mathrm{I}^{-1}]"), ("[N J⁻¹]", r"[\mathrm{N}\,\mathrm{J}^{-1}]"),
])
def test_bracketed_dimensional_formulas_are_upright(text, latex):
    assert recognise(text, subject="physics").text == rf"\({latex}\)"


@pytest.mark.parametrize("text, expected", [
    ("[MLT]", "[MLT]"), ("[M L T]", "[M L T]"), ("[N]", "[N]"),                 # no power written: not taken as dimensions
    ("MLT⁻²", r"\(MLT^{-2}\)"), ("ABC²", r"\(ABC^{2}\)"), ("[ABC²]", r"\([ABC^{2}]\)"),   # no brackets / not dimension symbols
    ("[x²]", r"\([x^{2}]\)"),
])
def test_dimension_near_misses_are_not_dimensions(text, expected):
    assert recognise(text, subject="physics").text == expected


@pytest.mark.parametrize("text", ["[M L T⁻² K⁻¹]", "[M M²]"])
def test_dimension_like_groups_with_a_unit_letter_are_flagged(text):
    # 2.2: K is the kelvin, not the dimension Θ, and a repeated symbol is not a dimensional formula; the trailing capital could be a
    # unit or a variable (S5 rule), so the group is kept as typed for review instead of an italic product (2.1 behaviour).
    r = recognise(text, subject="physics")
    assert r.text == text and r.outcome == "NEEDS_REVIEW" and r.reasons in (["AMBIGUOUS_UNIT"], ["NOT_INDEPENDENTLY_VERIFIED"])


# ================================================================== correction cycle 2.2 (independent Holdout D analysis; synthetic cases)
from xml.etree import ElementTree as ET  # noqa: E402

from inst_paper.services import extraction  # noqa: E402
from inst_paper.services.math import office, safety  # noqa: E402


def _txt(text, subject=None):
    return recognise(text, subject=subject)


# ---- S1 chained scripts: flagged, never one reading guessed
@pytest.mark.parametrize("text", ["Find 3^2^2 now.", "Take y = a^b^c here.", "Take y = x²^3 here.", "x^2²", "e^x^2", "a_i_j"])
def test_s1_chained_scripts_are_flagged(text):
    r = _txt(text)
    assert r.text == text and r.reasons == ["AMBIGUOUS_POWER"]


@pytest.mark.parametrize("text, expected", [
    ("3^(2^2)", r"\(3^{2^{2}}\)"), ("(3^2)^2", r"\((3^{2})^{2}\)"), ("x²³", r"\(x^{23}\)"), ("x^(n+1)", r"\(x^{n + 1}\)"),
    ("e^(x^2)", r"\(e^{x^{2}}\)"),
])
def test_s1_explicit_grouping_still_converts(text, expected):
    assert _txt(text).text == expected


# ---- S2 subscript + superscript on one base: one stacked node
@pytest.mark.parametrize("text, expected", [
    ("E = [t^2]_1^3 = 8", r"\(E = \left[t^{2}\right]_{1}^{3} = 8\)"), ("E = [t²]₁³ = 8", r"\(E = \left[t^{2}\right]_{1}^{3} = 8\)"),
    ("Here x_1^2 + 1 = 5", r"Here \(x_{1}^{2} + 1 = 5\)"), ("x^2_1", r"\(x^{2}_{1}\)"),
    ("(x_1)^2", r"\((x_{1})^{2}\)"), ("x_1 y^2", r"\(x_{1}y^{2}\)"),
])
def test_s2_sub_and_superscript_stack_on_one_base(text, expected):
    assert _txt(text).text == expected


# ---- S3 parenthesised numbers and spans starting with a binary operator
@pytest.mark.parametrize("text, expected", [
    ("(4)(5) + (6)(−2) = 8 is true.", r"\((4)(5) + (6)(-2) = 8\) is true."), ("So (4)(5) + (6)(−2) = 8.", r"So \((4)(5) + (6)(-2) = 8\)."),
    ("(2) Find x² + 1.", r"(2) Find \(x^{2} + 1\)."), ("(a) x² + 1", r"(a) \(x^{2} + 1\)"),
])
def test_s3_products_of_bracketed_numbers_are_not_labels(text, expected):
    assert _txt(text).text == expected


# ---- S4 caret and Unicode scripts are one token stream (parity)
@pytest.mark.parametrize("caret, unicode", [
    ("Express g in kg m s^-2 here", "Express g in kg m s⁻² here"), ("Give it in N m^-1 here", "Give it in N m⁻¹ here"),
    ("k = 9 × 10^9 N m^2 C^-2", "k = 9 × 10⁹ N m² C⁻²"), ("mol L^-1 s^-1", "mol L⁻¹ s⁻¹"), ("β = 2.0 × 10^-3 K^-1", "β = 2.0 × 10⁻³ K⁻¹"),
    ("A = 5 m^2", "A = 5 m²"), ("7 g cm^-3", "7 g cm⁻³"), ("[M L^2 T^-2]", "[M L² T⁻²]"), ("Find d^2y/dx^2 now", "Find d²y/dx² now"),
    ("x y^-1", "x y⁻¹"), ("e^-x", "e⁻ˣ"), ("x^-1 + 1", "x⁻¹ + 1"), ("2^-1", "2⁻¹"),
])
def test_s4_caret_and_unicode_read_identically(caret, unicode):
    a, b = _txt(caret, "physics"), _txt(unicode, "physics")
    assert a.outcome == b.outcome == "CONVERTED"
    assert [s.latex for s in a.spans] == [s.latex for s in b.spans]


def test_s4_caret_units_are_upright():
    assert _txt("A = 5 m^2").text == r"\(A = 5\,\mathrm{m}^{2}\)"
    # 2.3 (H4): a lone W opposite a side of units could be the watt or work - plain text does not settle it: review
    r = _txt("J s^-1 = W")
    assert r.outcome == "NEEDS_REVIEW" and r.text == "J s^-1 = W"


# ---- S5 units after a non-number coefficient
@pytest.mark.parametrize("text, expected", [
    ("ω = 40π rad s⁻¹", r"\(\omega = 40\pi\,\mathrm{rad}\,\mathrm{s}^{-1}\)"), ("ω = 40π rad s^-1", r"\(\omega = 40\pi\,\mathrm{rad}\,\mathrm{s}^{-1}\)"),
])
def test_s5_unit_word_carries_the_unit_group(text, expected):
    assert _txt(text).text == expected


@pytest.mark.parametrize("text", ["k = 5t N", "P = 2y² W acts", "P = 2y^2 W acts", "P = I V"])
def test_s5_unit_letter_after_an_algebraic_term_is_flagged(text):
    r = _txt(text)
    assert r.text == text and r.reasons == ["AMBIGUOUS_UNIT"]


@pytest.mark.parametrize("text, expected", [
    ("Let z = 2x y.", r"Let \(z = 2xy\)."), ("E = m c²", r"\(E = mc^{2}\)"), ("F = m a", r"\(F = ma\)"), ("v = u + a t", r"\(v = u + at\)"),
    ("x = 3m", r"\(x = 3m\)"),
])
def test_s5_products_of_variables_stay_variables(text, expected):
    assert _txt(text).text == expected


# ---- S6 glued unit letters
@pytest.mark.parametrize("text", ["v = 6 ms⁻¹", "6 ms^-1", "6 ms-1"])
def test_s6_ms_with_a_power_is_flagged_in_every_notation(text):
    r = _txt(text, "physics")
    assert r.text == text and r.outcome == "NEEDS_REVIEW" and r.reasons in (["AMBIGUOUS_UNIT"], ["INLINE_EXPONENT"])


@pytest.mark.parametrize("text, expected", [
    ("t = 6 ms", r"\(t = 6\,\mathrm{ms}\)"), ("Δt = 3 ms", r"\(\Delta t = 3\,\mathrm{ms}\)"),          # the millisecond, never m·s
    ("6 m s⁻¹", r"\(6\,\mathrm{m}\,\mathrm{s}^{-1}\)"), ("v = 6 m/s", r"\(v = 6\,\mathrm{m}/\mathrm{s}\)"),
])
def test_s6_separated_units_and_milliseconds(text, expected):
    assert _txt(text).text == expected


# ---- S7 division followed by a slash
@pytest.mark.parametrize("text", ["k = 12 ÷ 4/2", "a ÷ b/c"])
def test_s7_divide_then_slash_is_flagged(text):
    r = _txt(text)
    assert r.text == text and r.reasons == ["AMBIGUOUS_FRACTION"]


def test_s7_times_then_slash_keeps_its_value():
    assert _txt("k = 5 × 10^4/(2 × 10^2)").outcome == "CONVERTED"


# ---- validator (independent of the parser)
def test_validator_rejects_meaning_changing_structures():
    assert safety.tree_problems(("sup", ("sup", ("num", "3"), ("num", "2")), ("num", "2")))[0] == "STRUCTURE_NOT_PRESERVED"
    assert safety.tree_problems(("sup", ("sub", ("paren", "[", "]", [("var", "t")]), ("num", "1")), ("num", "3")))[0] == "STRUCTURE_NOT_PRESERVED"
    assert safety.tree_problems(("seq", [("unit", "rad"), ("sup", ("var", "s"), ("num", "-1"))]))[0] == "AMBIGUOUS_UNIT"
    assert safety.tree_problems(("op", ("num", "12"), r"\div", ("frac", ("num", "4"), ("num", "2"))))[0] == "AMBIGUOUS_FRACTION"
    assert safety.text_problems("+(6)(-2) = 8", "+(6)(-2) = 8", "(4)(5) ")[0] == "JOINED_TO_TEXT"
    assert safety.text_problems("5 m^-2", "5m^{-2}", "")[0] == "AMBIGUOUS_UNIT"


def test_validator_accepts_correct_structures():
    assert safety.tree_problems(("subsup", ("paren", "[", "]", [("var", "t")]), ("num", "1"), ("num", "3"), "_^")) is None
    assert safety.tree_problems(("quantity", None, [("unit", "rad"), ("sup", ("unit", "s"), ("num", "-1"))])) is None
    assert safety.text_problems("-5 + x", "-5 + x", "Solve ") is None
    assert safety.text_problems("5 m^-2", r"5\,\mathrm{m}^{-2}", "") is None


M = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def _omml(xml):
    return ET.fromstring(f'<m:oMath xmlns:m="{M}">{xml}</m:oMath>')


def test_word_equations_are_checked_against_their_own_symbols():
    sup = _omml("<m:sSup><m:e><m:r><m:t>x</m:t></m:r></m:e><m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup>")
    assert safety.omml_preserved(sup, "{x}^{2}")
    assert not safety.omml_preserved(sup, "{x}_{2}")                # subscript instead of superscript
    assert not safety.omml_preserved(sup, "x2")                     # flattened
    nary = _omml('<m:nary><m:naryPr><m:chr m:val="∫"/></m:naryPr><m:sub><m:r><m:t>0</m:t></m:r></m:sub><m:sup><m:r><m:t>π</m:t></m:r>'
                 '</m:sup><m:e><m:r><m:t>x</m:t></m:r></m:e></m:nary>')
    assert safety.omml_preserved(nary, r"\int_{0}^{\pi} x")
    assert not safety.omml_preserved(nary, r"\int_{0} \pi x")       # a lost upper limit


def test_a_word_equation_that_fails_the_check_is_not_imported_as_converted(monkeypatch):
    monkeypatch.setattr(office, "omml_to_latex", lambda el: "{x}_{2}")
    p = ET.fromstring(f'<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:m="{M}">'
                      '<m:oMath><m:sSup><m:e><m:r><m:t>x</m:t></m:r></m:e><m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup></m:oMath></w:p>')
    r = recognise(office.docx_paragraph_text(p))
    # 2.3 (W4): a conversion that fails verification is reported as such, not as an unsupported structure
    assert r.outcome == "EXTRACTION_ERROR" and r.reasons == ["WORD_EQUATION_NOT_VERIFIED"]


# ---- Office run boundaries
def test_excel_adjacent_superscript_runs_are_flagged_not_merged():
    S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    si = ET.fromstring(f'<si xmlns="{S}"><r><t>Find 5</t></r><r><rPr><vertAlign val="superscript"/></rPr><t>2</t></r>'
                       '<r><rPr><vertAlign val="superscript"/></rPr><t>3</t></r><r><t> now.</t></r></si>')
    r = recognise(office.xlsx_string_text(si))
    assert r.outcome == "NEEDS_REVIEW" and "AMBIGUOUS_POWER" in r.reasons and r.readable_original == "Find 5²³ now."
    one = ET.fromstring(f'<si xmlns="{S}"><r><t>Find 5</t></r><r><rPr><vertAlign val="superscript"/></rPr><t>23</t></r></si>')
    assert recognise(office.xlsx_string_text(one)).text == r"Find \(5^{23}\)"


def test_word_split_runs_with_identical_formatting_are_one_script():
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    sup = '<w:rPr><w:vertAlign w:val="superscript"/></w:rPr>'
    same = ET.fromstring(f'<w:p xmlns:w="{w}"><w:r><w:t>x</w:t></w:r><w:r>{sup}<w:t>1</w:t></w:r><w:r>{sup}<w:t>2</w:t></w:r></w:p>')
    assert office.docx_paragraph_text(same) == "x¹²"                 # Word split one exponent; it displays as one
    diff = ET.fromstring(f'<w:p xmlns:w="{w}"><w:r><w:t>x</w:t></w:r><w:r>{sup}<w:t>1</w:t></w:r>'
                         f'<w:r><w:rPr><w:vertAlign w:val="superscript"/><w:b/></w:rPr><w:t>2</w:t></w:r></w:p>')
    assert recognise(office.docx_paragraph_text(diff)).reasons == ["AMBIGUOUS_POWER"]


# ---- F1 detectors and their near-misses
@pytest.mark.parametrize("text, code", [
    ("6 ms-1", "INLINE_EXPONENT"), ("4 m s-2", "INLINE_EXPONENT"), ("9 kg m-3", "INLINE_EXPONENT"),
    ("It has MLT-2 dims", "DIMENSION_NOTATION"), ("ML2T-2", "DIMENSION_NOTATION"),
])
def test_f1_unmarked_powers_and_dimensions_are_flagged(text, code):
    r = _txt(text)
    assert r.text == text and r.reasons == [code]


@pytest.mark.parametrize("text", ["pages 10-12", "2019-20", "Class XI-A", "roll 23-45", "Q.5-7", "NH-48"])
def test_f1_ranges_and_labels_stay_plain(text):
    r = _txt(text, "physics")
    assert r.text == text and r.outcome == "PLAIN_TEXT"


# ---- coverage
@pytest.mark.parametrize("text, expected", [
    ("u⃗ = 3î − 2ĵ", r"\(\vec{u} = 3\hat{i} - 2\hat{j}\)"), ("î × ĵ = k̂", r"\(\hat{i} \times \hat{j} = \hat{k}\)"),
    ("ω⃗ = 2k̂ here", r"\(\vec{\omega} = 2\hat{k}\) here"), ("Find n(P ∪ Q) − n(P ∩ Q).", r"Find \(n(P \cup Q) - n(P \cap Q)\)."),
    ("a = 12 m / 4 s = 3 m s⁻²", r"\(a = \frac{12\,\mathrm{m}}{4\,\mathrm{s}} = 3\,\mathrm{m}\,\mathrm{s}^{-2}\)"),
    ("C_eq = 6 μF", r"\(C_{\mathrm{eq}} = 6\,\mu\mathrm{F}\)"), ("x_ij", r"\(x_{ij}\)"),
    ("Find d^2x/dt^2 now", r"Find \(\frac{d^{2}x}{dt^{2}}\) now"),
])
def test_coverage_additions(text, expected):
    assert _txt(text).text == expected


@pytest.mark.parametrize("text, code", [("a_n+1", "AMBIGUOUS_POWER"), ("d^2y/dx^3", "AMBIGUOUS_DERIVATIVE"), ("D = |1 -2; 3 4|", "AMBIGUOUS_MATRIX")])
def test_coverage_keeps_ambiguities_flagged(text, code):
    assert _txt(text).reasons == [code]


@pytest.mark.parametrize("text, subject, expected", [
    ("7 J", "physics", r"\(7\,\mathrm{J}\)"), ("4.5 eV", "physics", r"\(4.5\,\mathrm{eV}\)"), ("0.5 mol", "chemistry", r"\(0.5\,\mathrm{mol}\)"),
    ("7 J", "mathematics", "7 J"), ("100 marks", "physics", "100 marks"), ("5 a", "physics", "5 a"), ("The café and Hôpital rule.", None,
                                                                                                         "The café and Hôpital rule."),
])
def test_whole_field_quantities_and_near_misses(text, subject, expected):
    assert _txt(text, subject).text == expected


# ---- flag quality
@pytest.mark.parametrize("text, expected", [
    ("Then (1/2)(2/3) = 1/3 (by cancelling) holds.", r"Then \((\frac{1}{2})(\frac{2}{3}) = \frac{1}{3}\) (by cancelling) holds."),
    ("g(x) = 2x + 1 (x real) here", r"\(g(x) = 2x + 1\) (x real) here"),
    ("h = ½at² + 4", r"\(h = \frac{1}{2}at^{2} + 4\)"), ("W = Kq₁q₂/d", r"\(W = \frac{Kq_{1}q_{2}}{d}\)"),
    ("By D'Alembert's rule", "By D'Alembert's rule"), ("f'(x) = 2x", r"\(f'(x) = 2x\)"), ("y'' + y = 0", r"\(y'' + y = 0\)"),
    ("at 5 s", "at 5 s"),
])
def test_flag_quality(text, expected):
    r = _txt(text)
    assert r.text == expected and r.outcome != "NEEDS_REVIEW"


def test_index_digit_policy_is_unchanged():
    assert _txt("C1 = 5 F").reasons == ["AMBIGUOUS_NUMBER_ADJACENT"]          # documented conservative policy (U4)


# ---- chemistry importer: case-sensitive duplicate checks (one shared key)
def _rows(*rows):
    return [(n + 2, {"question": q, "option_a": a, "option_b": b, "option_c": c, "option_d": d, "correct": "A", "marks": "4",
                     "negative_marks": "1", "explanation": "", "topic": ""}) for n, (q, a, b, c, d) in enumerate(rows)]


@pytest.mark.parametrize("options", [("Hf", "HF", "H2O", "NaCl"), ("Co", "CO", "Cu", "C"), ("Sn", "SN", "Cs", "CS")])
def test_case_distinct_chemical_options_are_accepted(options):
    assert extraction.validate_paper(_rows(("Which is hafnium?", *options)), 10).ok


@pytest.mark.parametrize("options", [("Hf", "Hf", "H2O", "NaCl"), ("Hf", "Hf ", "H2O", "NaCl"), ("x + 1", "x  +  1", "y", "z"),
                                     ("é", "é", "a", "b")])
def test_true_duplicate_options_are_still_rejected(options):
    r = extraction.validate_paper(_rows(("Pick one", *options)), 10)
    assert not r.ok and any("identical" in e["message"] for e in r.errors)


def test_duplicate_questions_compare_case_sensitively():
    assert extraction.validate_paper(_rows(("Name the element Co.", "a", "b", "c", "d"), ("Name the element CO.", "a", "b", "c", "d")), 10).ok
    dup = extraction.validate_paper(_rows(("Name the element Co.", "a", "b", "c", "d"), ("Name  the element Co.", "a", "b", "c", "d")), 10)
    assert not dup.ok and "Duplicate of row 2." in [e["message"] for e in dup.errors]


@pytest.mark.parametrize("fmt", ["csv", "xlsx", "docx"])
def test_case_distinct_options_import_in_every_format(client, world, setup, admin, fmt):
    head = HEAD.strip().split(",")
    row = ["Which formula is hafnium?", "Hf", "HF", "H₂O", "NaCl", "A", 4, 1, "Hf is hafnium; HF is hydrogen fluoride.", "Elements"]
    if fmt == "csv":
        data, name, ctype = (HEAD + ",".join(str(x) for x in row) + "\n").encode(), "p.csv", "text/csv"
    elif fmt == "xlsx":
        data, name, ctype = xlsx_rich([head, row]), "p.xlsx", XLSX_MIME
    else:
        cells = lambda *xs: "<w:tr>" + "".join(f"<w:tc>{_para(_run(str(x)))}</w:tc>" for x in xs) + "</w:tr>"
        data, name, ctype = docx("<w:tbl>" + cells(*head) + cells(*row) + "</w:tbl>"), "p.docx", DOCX_MIME
    r = post(client, admin, world, setup["chem"], data, name=name, ctype=ctype)
    assert r.status_code == 201, r.text
    q = client.get(f"{API}/paper-versions/{r.json()['paper_version_id']}", headers=admin).json()["questions"][0]
    assert (q["option_a"], q["option_b"]) == ("Hf", "HF") and q["option_c"] == r"\(\mathrm{H_{2}O}\)"


def test_provider_revalidation_is_case_sensitive_too(client, world, setup, admin):
    paper = (HEAD + "Which is cobalt?,Co,CO,Cu,C,A,4,1,,Elements\n" + "Evaluate ∫₀π sin x dx.,0,1,2,π,C,4,1,,Calculus\n").encode()
    token = post(client, admin, world, setup["chem"], paper, preview=True).json()["review_token"]
    r = post(client, admin, world, setup["chem"], paper, resolutions={"2:question": {"action": "keep_original"}}, review_token=token)
    assert r.status_code == 201, r.text


# ---- cross-format parity: the same logical content in CSV (caret), Excel (rich-text runs) and Word (formatting runs)
PARITY = [  # (question text in CSV, the same as (text, align) runs)
    ("A force of 5 kg m s^-2 acts.", [("A force of 5 kg m s", None), ("-2", "superscript"), (" acts.", None)]),
    ("Find 3^2^2 here.", [("Find 3", None), ("2", "superscript"), ("2", "superscript"), (" here.", None)]),
    ("E = [t^2]_1^3 = 8 here.", [("E = [t", None), ("2", "superscript"), ("]", None), ("1", "subscript"), ("3", "superscript"),
                                 (" = 8 here.", None)]),
    ("v = 6 ms^-1 here.", [("v = 6 ms", None), ("-1", "superscript"), (" here.", None)]),
    ("Units [M L^2 T^-2] here.", [("Units [M L", None), ("2", "superscript"), (" T", None), ("-2", "superscript"), ("] here.", None)]),
]


def test_cross_format_parity(client, world, setup, admin):
    head = HEAD.strip().split(",")
    opts = ["one", "two", "three", "four"]
    csv_rows = [[q, *opts, "A", 4, 1, "", ""] for q, _ in PARITY]
    csv_data = (HEAD + "".join(",".join(f'"{x}"' if isinstance(x, str) else str(x) for x in r) + "\n" for r in csv_rows)).encode()
    xlsx_rows = [[runs, *opts, "A", 4, 1, "", ""] for _, runs in PARITY]
    xlsx_data = xlsx_rich([head] + xlsx_rows)
    paras = ""
    for n, (_, runs) in enumerate(PARITY, start=1):
        paras += _para(_run(f"{n}. "), *(  # a distinct formatting key per script run so Word keeps the boundaries the author typed
            f'<w:r><w:rPr><w:vertAlign w:val="{a}"/><w:sz w:val="{18 + k}"/></w:rPr><w:t xml:space="preserve">{t}</w:t></w:r>' if a
            else _run(t) for k, (t, a) in enumerate(runs)))
        paras += "".join(_para(_run(x)) for x in ("A) one", "B) two", "C) three", "D) four", "Answer: A", "Marks: 4", "Negative: 1"))
    docx_data = word(paras)
    results = {}
    for fmt, data, name, ctype in (("csv", csv_data, "p.csv", "text/csv"), ("xlsx", xlsx_data, "p.xlsx", XLSX_MIME),
                                   ("docx", docx_data, "p.docx", DOCX_MIME)):
        r = post(client, admin, world, setup["phy"], data, name=name, ctype=ctype, preview=True)
        assert r.status_code == 200, (fmt, r.text)
        results[fmt] = [(q["fields"]["question"]["outcome"], q["fields"]["question"]["reasons"],
                         [s["latex"] for s in q["fields"]["question"]["spans"]]) for q in r.json()["questions"]]
    assert results["csv"] == results["xlsx"] == results["docx"], results
    outcomes = [o for o, _, _ in results["csv"]]
    assert outcomes == ["CONVERTED", "NEEDS_REVIEW", "CONVERTED", "NEEDS_REVIEW", "CONVERTED"]



@pytest.mark.parametrize("text, subject", [("2 m + 3 = 7", "chemistry"), ("The pH of 10⁻³ M HCl", "physics")])
def test_23_unit_or_variable_after_a_number_goes_to_review(text, subject):
    # Cycle 2.3: upright/italic is meaningful. A unit letter after a spaced number that the context does not prove to be a unit
    # ('2 m + 3 = 7') - or 'M' outside a Chemistry paper, where it is not the molar unit - is no longer guessed: provider review.
    r = recognise(text, subject=subject)
    assert r.text == text and r.outcome == "NEEDS_REVIEW"
