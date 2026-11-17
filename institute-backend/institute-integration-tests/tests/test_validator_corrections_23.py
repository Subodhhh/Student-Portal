"""Cycle 2.3 final corrections: the five validator weaknesses (A1-A5), explicit subject context (B), false positives (C) and the
explicit subject-category mapping (D). Every 'corrupted' LaTeX string is valid, allow-listed and renderable: the validator must reject
it on meaning alone."""
import ast
import threading
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree as ET

import pytest

from inst_paper.services.math import domain_of_subject_id, office, recognise, verify
from inst_paper.services.math.parser import Unreadable, check_latex
from inst_paper.services.math.symbols import PROVIDER_COMMANDS
from tests.test_paper_recognition import HEAD, admin, post  # noqa: F401  (fixture)
from tests.test_papers import setup, tmp_store  # noqa: F401  (fixtures)

M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def accepts(source, latex, domain=None):
    return verify.verify_text(source, latex, domain)[0]


def rejects_renderable(source, latex, domain=None):
    check_latex(latex, PROVIDER_COMMANDS)                    # it would parse and render
    return not verify.verify_text(source, latex, domain)[0]


# ---------------------------------------------------------------- A1 matrix shape
@pytest.mark.parametrize("source, latex", [
    ("[1 2; 3 4]", r"\begin{bmatrix}1 & 2 \\ 3 & 4\end{bmatrix}"), ("[[1,2],[3,4]]", r"\begin{bmatrix}1 & 2 \\ 3 & 4\end{bmatrix}"),
    ("(1, 2; 3, 4)", r"\begin{pmatrix}1 & 2 \\ 3 & 4\end{pmatrix}"), ("|a b; c d|", r"\begin{vmatrix}a & b \\ c & d\end{vmatrix}"),
    ("[1 2 3; 4 5 6]", r"\begin{bmatrix}1 & 2 & 3 \\ 4 & 5 & 6\end{bmatrix}"), ("[x+1 2; 3 4]", r"\begin{bmatrix}x + 1 & 2 \\ 3 & 4\end{bmatrix}"),
])
def test_a1_matrices_of_the_same_shape_verify(source, latex):
    assert accepts(source, latex)


@pytest.mark.parametrize("source, latex", [
    ("[1 2; 3 4]", r"\begin{bmatrix}1 & 2 & 3 & 4\end{bmatrix}"),            # 2×2 as 1×4
    ("[1 2; 3 4]", r"\begin{bmatrix}1 \\ 2 \\ 3 \\ 4\end{bmatrix}"),          # 2×2 as 4×1
    ("[1 2; 3 4]", r"\begin{bmatrix}1 & 3 \\ 2 & 4\end{bmatrix}"),           # transposed
    ("[1 2 3; 4 5 6]", r"\begin{bmatrix}1 & 2 \\ 3 & 4 \\ 5 & 6\end{bmatrix}"),  # 2×3 as 3×2
    ("[1 2; 3 4]", r"\begin{vmatrix}1 & 2 \\ 3 & 4\end{vmatrix}"),           # matrix as determinant
    ("A = [1 2; 3 4], (1, 2)", r"A = \begin{bmatrix}1 & 2 \\ 3 & 4\end{bmatrix}, (12)"),   # a comma lost next to a matrix
])
def test_a1_a_matrix_of_another_shape_is_rejected(source, latex):
    assert rejects_renderable(source, latex)


@pytest.mark.parametrize("source", ["[1 2; 3]", "|1 -2; 3 4|", "[1 2; 3 4"])
def test_a1_unsettled_matrices_are_refused(source):
    assert not accepts(source, r"\begin{bmatrix}1 & 2 \\ 3 & 4\end{bmatrix}")


def test_a1_tuples_and_intervals_are_not_matrices():
    assert accepts("(1, 2)", "(1, 2)") and accepts("[0, 1]", "[0, 1]") and not accepts("(1, 2)", r"\begin{pmatrix}1 & 2\end{pmatrix}")


def test_a1_word_matrix_shape_is_compared():
    m = ET.fromstring(f'<m:oMath xmlns:m="{M}"><m:m>' + "".join(
        "<m:mr>" + "".join(f"<m:e><m:r><m:t>{x}</m:t></m:r></m:e>" for x in row) + "</m:mr>" for row in (("1", "2"), ("3", "4")))
        + "</m:m></m:oMath>")
    assert verify.verify_omml(m, r"\begin{matrix}1 & 2 \\ 3 & 4\end{matrix}")[0]
    assert not verify.verify_omml(m, r"\begin{matrix}1 & 2 & 3 & 4\end{matrix}")[0]


def test_a1_pipeline_matrix_converts():
    assert recognise("A = [1 2; 3 4], find A⁻¹").text == r"\(A = \begin{bmatrix}1 & 2 \\ 3 & 4\end{bmatrix}\), find \(A^{-1}\)"


# ---------------------------------------------------------------- A2 spaced numbers and mixed numbers
@pytest.mark.parametrize("source, latex", [
    ("x = 1 1/2", r"x = \frac{11}{2}"), ("x = 1 1/2", r"x = 1\frac{1}{2}"), ("3 1/4", r"\frac{31}{4}"), ("x = 1 2", "x = 12"),
    ("x = 12", r"x = 1\,2"), ("x = 12", r"x = 1\;2"), ("x = 12", r"x = 1\,{2}"), ("x = 10 000", "x = 10000"),
])
def test_a2_number_spacing_changes_are_rejected(source, latex):
    assert rejects_renderable(source, latex)


@pytest.mark.parametrize("source, latex", [
    ("1½", r"1\frac{1}{2}"), ("2 × 1/2", r"2 \times \frac{1}{2}"), ("x = 1/2", r"x = \frac{1}{2}"), ("x = 12", "x = 12"),
    ("1, 1/2", r"1, \frac{1}{2}"), ("2 m", r"2\,\mathrm{m}"), ("10⁻³ x", r"{10}^{-3}x"),
])
def test_a2_near_misses_verify(source, latex):
    assert accepts(source, latex, "physics")


@pytest.mark.parametrize("text", ["x = 1 1/2", "Take 3 1/4 cups.", "x = 1 2"])
def test_a2_mixed_and_spaced_numbers_are_never_converted_silently(text):
    r = recognise(text)
    assert r.text == text and "frac{11}" not in r.text


# ---------------------------------------------------------------- A3 capital letter plus number
@pytest.mark.parametrize("source, good, bad, domain", [
    ("T²", "T^{2}", r"\mathrm{T}^{2}", "physics"), ("T²", "T^{2}", r"\mathrm{T}^{2}", "chemistry"),
    ("T²", "T^{2}", r"\mathrm{T}^{2}", "mathematics"), ("V₁ = 2 V", r"V_{1} = 2\,\mathrm{V}", r"\mathrm{V}_{1} = 2\,\mathrm{V}", "physics"),
    ("V₂", "V_{2}", r"\mathrm{V}_{2}", "physics"), ("O₂", "O_{2}", r"\mathrm{O}_{2}", "physics"), ("A₁ ∩ A₂", r"A_{1} \cap A_{2}",
    r"\mathrm{A}_{1} \cap A_{2}", "mathematics"), ("P₁V₁", "P_{1}V_{1}", r"\mathrm{P}_{1}\mathrm{V}_{1}", "chemistry"),
    ("K₁ = 5", "K_{1} = 5", r"\mathrm{K}_{1} = 5", "chemistry"), ("B₂", "B_{2}", r"\mathrm{B}_{2}", "mathematics"),
])
def test_a3_variables_with_numbers_keep_their_style_in_every_subject(source, good, bad, domain):
    assert accepts(source, good, domain) and rejects_renderable(source, bad, domain)


@pytest.mark.parametrize("source, latex, domain", [
    ("O₂", r"\mathrm{O}_{2}", "chemistry"), ("O₂", "O_{2}", "chemistry"), ("CO₂", r"\mathrm{CO_{2}}", "physics"),
    ("H₂O", "H_{2}O", "mathematics"), ("²³⁵U", r"{}^{235}\mathrm{U}", "physics"), ("Na⁺", r"\mathrm{Na^{+}}", "physics"),
    ("Zn + Cu²⁺ → Zn²⁺ + Cu", r"\mathrm{Zn} + \mathrm{Cu^{2+}} \rightarrow \mathrm{Zn^{2+}} + \mathrm{Cu}", "chemistry"),
])
def test_a3_real_formulas_keep_the_chemistry_convention(source, latex, domain):
    assert accepts(source, latex, domain)


@pytest.mark.parametrize("text, subject, expected", [
    ("V₁ = 2 V", "physics", r"\(V_{1} = 2\,\mathrm{V}\)"), ("T² ∝ r³", "physics", r"\(T^{2} \propto r^{3}\)"),
    ("K₁ = 5", "chemistry", r"\(K_{1} = 5\)"), ("N₂ + 3H₂ → 2NH₃", "chemistry", r"\(\mathrm{N_{2}} + 3\mathrm{H_{2}} \rightarrow 2\mathrm{NH_{3}}\)"),
])
def test_a3_pipeline(text, subject, expected):
    r = recognise(text, subject=subject)
    assert r.outcome == "CONVERTED" and r.text == expected


# ---------------------------------------------------------------- A4 upright F (and W)
@pytest.mark.parametrize("source, latex", [
    ("F = kg m s⁻²", r"\mathrm{F} = \mathrm{kg}\,\mathrm{m}\,\mathrm{s}^{-2}"), ("F = kg m s⁻²", r"F = \mathrm{kg}\,\mathrm{m}\,\mathrm{s}^{-2}"),
    ("J s⁻¹ = W", r"\mathrm{J}\,\mathrm{s}^{-1} = \mathrm{W}"), ("J s⁻¹ = W", r"\mathrm{J}\,\mathrm{s}^{-1} = W"),
])
def test_a4_a_lone_unit_letter_opposite_units_is_not_settled_by_plain_text(source, latex):
    assert not accepts(source, latex, "physics")


@pytest.mark.parametrize("source, good, bad", [
    ("1 F = 1 C V⁻¹", r"1\,\mathrm{F} = 1\,\mathrm{C}\,\mathrm{V}^{-1}", r"1F = 1\,\mathrm{C}\,\mathrm{V}^{-1}"),   # the number settles it
    ("C = 2 F", r"C = 2\,\mathrm{F}", r"C = 2F"), ("F = 2 C", r"F = 2\,\mathrm{C}", r"\mathrm{F} = 2\,\mathrm{C}"),     # F the variable
])
def test_a4_context_that_settles_f(source, good, bad):
    assert accepts(source, good, "physics") and rejects_renderable(source, bad, "physics")


def test_a4_word_formatting_settles_the_style():
    # in a Word equation the provider's formatting states it: a plain-style W is the watt, an italic W the work
    plain = ET.fromstring(f'<m:oMath xmlns:m="{M}"><m:r><m:t>P=</m:t></m:r><m:r><m:rPr><m:sty m:val="p"/></m:rPr><m:t>W</m:t></m:r></m:oMath>')
    assert verify.verify_omml(plain, r"P=\mathrm{W}")[0] and not verify.verify_omml(plain, "P=W")[0]


@pytest.mark.parametrize("text", ["J s⁻¹ = W", "F = kg m s⁻²", "J s^-1 = W"])
def test_a4_pipeline_sends_it_to_review(text):
    r = recognise(text, subject="physics")
    assert r.outcome == "NEEDS_REVIEW" and r.text == text


# ---------------------------------------------------------------- A5 bold and blackboard styles
@pytest.mark.parametrize("source, good, bad", [
    ("x ∈ ℝ", r"x \in \mathbb{R}", r"x \in R"), ("x ∈ ℝ", r"x \in \mathbb{R}", r"x \in \mathrm{R}"), ("n ∈ ℕ", r"n \in \mathbb{N}", r"n \in N"),
    ("𝐅 = m𝐚", r"\mathbf{F} = m\mathbf{a}", "F = ma"), ("𝐅 = m𝐚", r"\mathbf{F} = m\mathbf{a}", r"\mathbf{F} = ma"),
    ("𝑭 = m𝒂", r"\boldsymbol{F} = m\boldsymbol{a}", r"\mathbf{F} = m\mathbf{a}"), ("𝒜", r"\mathcal{A}", "A"),
    ("z ∈ ℂ", r"z \in \mathbb{C}", r"z \in \mathcal{C}"),
])
def test_a5_fonts_are_compared_exactly(source, good, bad):
    assert accepts(source, good) and rejects_renderable(source, bad)


def test_a5_plain_letters_never_gain_a_font():
    assert rejects_renderable("F = ma", r"\mathbf{F} = ma") and rejects_renderable("x ∈ R", r"x \in \mathbb{R}")


def _eq(xml):
    return ET.fromstring(f'<w:p xmlns:w="{W}" xmlns:m="{M}"><m:oMath>{xml}</m:oMath></w:p>')


def _run(text, pr=""):
    return f"<m:r>{pr}<m:t>{text}</m:t></m:r>"


@pytest.mark.parametrize("xml, expected", [
    (_run("F", '<m:rPr><m:sty m:val="b"/></m:rPr>') + _run("=ma"), r"\(\mathbf{F}=ma\)"),
    (_run("x∈") + _run("R", '<m:rPr><m:scr m:val="double-struck"/></m:rPr>'), r"\(x\in \mathbb{R}\)"),
    (_run("x∈ℝ"), r"\(x\in \mathbb{R}\)"),
    (_run("L", '<m:rPr><m:scr m:val="script"/></m:rPr>') + _run("=2"), r"\(\mathcal{L}=2\)"),
    ('<m:r><m:rPr><m:nor/></m:rPr><w:rPr><w:b/></w:rPr><m:t>v</m:t></m:r>' + _run("=2"), r"\(\mathbf{v}=2\)"),
    (_run("x∈R"), r"\(x\in R\)"),                                                       # near miss: an ordinary italic R
])
def test_a5_word_equation_fonts_are_preserved_and_verified(xml, expected):
    r = recognise(office.docx_paragraph_text(_eq(xml)))
    assert r.outcome == "CONVERTED" and r.text == expected


@pytest.mark.parametrize("pr", ['<m:rPr><m:scr m:val="fraktur"/></m:rPr>',
                                '<m:rPr><m:scr m:val="sans-serif"/></m:rPr>', '<m:rPr><m:scr m:val="double-struck"/></m:rPr>'])
def test_a5_a_style_the_converter_cannot_show_is_flagged_never_dropped(pr):
    letter = "k" if "double-struck" in pr else "F"                                     # lowercase double-struck: not shown
    r = recognise(office.docx_paragraph_text(_eq(_run(letter, pr) + _run("=1"))))
    assert r.outcome == "EXTRACTION_ERROR" and "\\(" not in r.text


def test_a5_a_dropped_font_is_never_accepted(monkeypatch):
    monkeypatch.setattr(office, "omml_to_latex", lambda el: "x\\in R")                  # the converter loses the double-struck R
    r = recognise(office.docx_paragraph_text(_eq(_run("x∈") + _run("R", '<m:rPr><m:scr m:val="double-struck"/></m:rPr>'))))
    assert r.outcome == "EXTRACTION_ERROR" and r.reasons == ["WORD_EQUATION_NOT_VERIFIED"]


# ---------------------------------------------------------------- B explicit subject context
def test_b_the_validator_keeps_no_shared_or_thread_state():
    tree = ast.parse(Path(verify.__file__).read_text())
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert "threading" not in imported and "contextvars" not in imported
    assert not any(isinstance(n, ast.Global) for n in ast.walk(tree))
    # no module-level mutable holder of per-call settings (the old _DOMAIN = [None] pattern); lookup tables are read-only
    assert not [n for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign)) and isinstance(n.value, ast.List)]


def test_b_concurrent_requests_never_exchange_the_subject():
    results, barrier = {}, threading.Barrier(12)

    def run(n):
        subject = ("chemistry", "physics", None)[n % 3]
        barrier.wait()
        results[n] = {recognise("c = 0.5 M", subject=subject).outcome for _ in range(100)}
    threads = [threading.Thread(target=run, args=(n,)) for n in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(results[n] == ({"CONVERTED"} if n % 3 == 0 else {"NEEDS_REVIEW"}) for n in range(12))


@pytest.mark.parametrize("subject", [None, "physics", "mathematics"])
def test_b_unknown_or_other_subjects_send_molar_m_to_review(subject):
    for text in ("c = 0.5 M", "The pH of 10⁻³ M HCl", "k = 5 M⁻¹ s⁻¹"):
        r = recognise(text, subject=subject)
        assert r.outcome == "NEEDS_REVIEW" and "mathrm{M}" not in r.text


def test_b_the_parser_needs_chemistry_explicitly_for_molar_m():
    from inst_paper.services.math.lexer import classify, lex
    from inst_paper.services.math.parser import parse_tokens, to_latex
    toks = lambda: classify(lex("c = 0.5 M"))
    assert "mathrm{M}" in to_latex(parse_tokens(toks(), molar=True))
    assert "mathrm{M}" not in to_latex(parse_tokens(toks()))


# ---------------------------------------------------------------- C false positives
@pytest.mark.parametrize("text", ["3/4th of the class passed.", "Only 2/3rd remain.", "3/4ths of it", "the 5th question",
                                  "the 21st and 22nd items"])
def test_c_ordinal_prose_is_plain_text(text):
    r = recognise(text)
    assert r.outcome == "PLAIN_TEXT" and r.text == text


@pytest.mark.parametrize("text, code", [("Find x/4th", "AMBIGUOUS_DENOMINATOR"), ("Find x/2y now", "AMBIGUOUS_DENOMINATOR"),
                                        ("y = 3/4t h", "AMBIGUOUS_DENOMINATOR")])
def test_c_ordinal_near_misses_are_still_detected(text, code):
    r = recognise(text)
    assert r.outcome == "NEEDS_REVIEW" and code in r.reasons


def test_c_unit_digit_reason_is_accurate():
    r = recognise("area 6 m2", subject="physics")
    assert r.reasons == ["UNIT_DIGIT_POWER"] and "hyphen" not in r.flags[0].reason and "m2" in r.flags[0].reason
    r = recognise("v = 5 ms-1", subject="physics")
    assert r.reasons == ["INLINE_EXPONENT"] and "hyphen" in r.flags[0].reason


# ---------------------------------------------------------------- D explicit subject-category mapping
def test_d_mapping_is_by_subject_id_only():
    chem, bio, pe, other = str(uuid4()), str(uuid4()), str(uuid4()), str(uuid4())
    categories = {chem: "CHEMISTRY", pe: "MATHEMATICS", other: "OTHER"}
    assert domain_of_subject_id(chem, categories) == "chemistry"
    assert domain_of_subject_id(other, categories) is None                 # OTHER: an explicit "no scientific domain"
    assert domain_of_subject_id(bio, categories) is None                   # not configured: no inferred domain
    assert domain_of_subject_id(uuid4(), categories) is None


def test_d_the_setting_is_validated():
    from pydantic import ValidationError
    from institute_common.config import Settings
    ok = Settings(database_url="sqlite://", recognition_subject_categories={str(uuid4()).upper(): "chemistry"})
    assert list(ok.recognition_subject_categories.values()) == ["CHEMISTRY"]
    assert Settings(database_url="sqlite://", recognition_subject_categories={str(uuid4()): "other"}).recognition_subject_categories
    for bad in ({"Chemistry": "CHEMISTRY"}, {str(uuid4()): "BIOLOGY"}):
        with pytest.raises(ValidationError):
            Settings(database_url="sqlite://", recognition_subject_categories=bad)


def _subject(db_factory, world, name):
    from institute_common.models.academics import ProviderSubject, Subject
    with db_factory() as db:
        s = Subject(code=name.upper().replace(" ", "")[:20], name=name)
        db.add(s)
        db.flush()
        db.add(ProviderSubject(provider_id=world["p1"], subject_id=s.subject_id))
        db.commit()
        return s.subject_id


def _question(client, admin, world, subject_id, topic="Chemistry"):
    csv = (HEAD + f'"Chemistry: the pH of 10⁻³ M HCl is",1,2,3,4,C,4,1,,{topic}\n').encode()
    r = post(client, admin, world, subject_id, csv, preview=True)
    assert r.status_code == 200, r.text
    return r.json()["questions"][0]["fields"]["question"]


@pytest.mark.parametrize("name", ["Biochemistry", "Physical Education", "Chemistry"])
def test_d_unconfigured_subjects_get_no_domain_whatever_their_name(client, world, admin, setup, db_factory, name):
    sid = _subject(db_factory, world, name)                                  # not in the configured mapping
    field = _question(client, admin, world, sid)
    assert field["outcome"] == "NEEDS_REVIEW" and "mathrm{M}" not in field["text"]


def test_d_a_configured_subdiscipline_is_chemistry(client, world, admin, setup, db_factory, monkeypatch):
    from institute_common.config import settings
    sid = _subject(db_factory, world, "Biochemistry")
    monkeypatch.setattr(settings, "recognition_subject_categories", {**settings.recognition_subject_categories, str(sid): "CHEMISTRY"})
    field = _question(client, admin, world, sid)
    assert field["outcome"] == "CONVERTED" and r"{10}^{-3}\,\mathrm{M}" in field["text"]


def test_d_physical_education_is_never_physics(client, world, admin, setup, db_factory):
    sid = _subject(db_factory, world, "Physical Education")
    csv = (HEAD + '"Find 2 m + 3 = 7 here",1,2,3,4,C,4,1,,Physics\n').encode()
    r = post(client, admin, world, sid, csv, preview=True)
    assert r.status_code == 200 and r.json()["questions"][0]["fields"]["question"]["outcome"] == "NEEDS_REVIEW"


@pytest.mark.parametrize("topic", ["Chemistry", "Mathematics"])
def test_d_topic_text_never_overrides_the_subject_id(client, world, admin, setup, topic):
    phy = _question(client, admin, world, setup["phy"], topic)                # configured PHYSICS, topic and text say otherwise
    chem = _question(client, admin, world, setup["chem"], "Physics")
    assert phy["outcome"] == "NEEDS_REVIEW" and chem["outcome"] == "CONVERTED"


# ---------------------------------------------------------------- approval action 1: \boldsymbol for Word bold-italic letters
BI = '<m:rPr><m:sty m:val="bi"/></m:rPr>'
B = '<m:rPr><m:sty m:val="b"/></m:rPr>'
WB = '<w:rPr><w:b/></w:rPr>'


def _wrun(text, mpr="", wpr=""):
    return f"<m:r>{mpr}{wpr}<m:t>{text}</m:t></m:r>"


@pytest.mark.parametrize("xml, expected", [
    (_wrun("F", BI) + _wrun("=m") + _wrun("a", BI), r"\(\boldsymbol{F}=m\boldsymbol{a}\)"),       # m:sty bi: a bold vector
    (_wrun("v", wpr=WB) + _wrun("=2"), r"\(\boldsymbol{v}=2\)"),                                 # italic run + w:b
    (_wrun("AB", BI), r"\(\boldsymbol{AB}\)"),
    (_wrun("F", BI) + _wrun("=2"), r"\(\boldsymbol{F}=2\)"),
])
def test_boldsymbol_word_bold_italic_is_preserved(xml, expected):
    r = recognise(office.docx_paragraph_text(_eq(xml)))
    assert r.outcome == "CONVERTED" and r.text == expected, (r.text, r.reasons)


@pytest.mark.parametrize("xml, expected", [
    (_wrun("F", B) + _wrun("=ma"), r"\(\mathbf{F}=ma\)"),                                       # bold UPRIGHT stays \mathbf
    ('<m:r><m:rPr><m:nor/></m:rPr><w:rPr><w:b/></w:rPr><m:t>v</m:t></m:r>' + _wrun("=2"), r"\(\mathbf{v}=2\)"),
    (_wrun("F=ma"), r"\(F=ma\)"),                                                              # not bold at all
    ('<m:r><w:rPr><w:b w:val="0"/></w:rPr><m:t>v</m:t></m:r>' + _wrun("=2"), r"\(v=2\)"),         # bold explicitly off
])
def test_boldsymbol_near_misses(xml, expected):
    r = recognise(office.docx_paragraph_text(_eq(xml)))
    assert r.outcome == "CONVERTED" and r.text == expected, (r.text, r.reasons)


@pytest.mark.parametrize("letter, pr", [("α", BI), ("Ω", B), ("θ", BI)])
def test_bold_greek_is_flagged_never_shown_without_bold(letter, pr):
    r = recognise(office.docx_paragraph_text(_eq(_wrun(letter, pr) + _wrun("=1"))))
    assert r.outcome == "EXTRACTION_ERROR" and "\\(" not in r.text


def _omath(xml):
    return ET.fromstring(f'<m:oMath xmlns:m="{M}" xmlns:w="{W}">{xml}</m:oMath>')


@pytest.mark.parametrize("xml, good, bads", [
    (_wrun("F", BI), r"\boldsymbol{F}", [r"\mathbf{F}", "F", r"\mathrm{F}"]),                    # bold italic: only \boldsymbol
    (_wrun("F", B), r"\mathbf{F}", [r"\boldsymbol{F}", "F", r"\mathrm{F}"]),                     # bold upright: only \mathbf
    (_wrun("v", wpr=WB), r"\boldsymbol{v}", [r"\mathbf{v}", "v"]),
    (_wrun("F"), "F", [r"\boldsymbol{F}", r"\mathbf{F}"]),                                       # plain italic: never bold
    (_wrun("α", BI), r"\boldsymbol{\alpha}", [r"\alpha", r"\mathbf{\alpha}"]),                    # Greek keeps its font too
])
def test_boldsymbol_the_validator_checks_weight_and_slant_from_the_word_run(xml, good, bads):
    el = _omath(xml)
    assert verify.verify_omml(el, good)[0]
    for bad in bads:
        check_latex(bad, PROVIDER_COMMANDS)                                                         # renderable, allow-listed
        assert not verify.verify_omml(el, bad)[0], bad


def test_boldsymbol_a_converter_that_loses_the_slant_is_blocked(monkeypatch):
    monkeypatch.setattr(office, "omml_to_latex", lambda el: r"\mathbf{F}=ma")                      # bold kept, italic lost
    r = recognise(office.docx_paragraph_text(_eq(_wrun("F", BI) + _wrun("=ma"))))
    assert r.outcome == "EXTRACTION_ERROR" and r.reasons == ["WORD_EQUATION_NOT_VERIFIED"]


def test_boldsymbol_text_unicode_bold_italic():
    assert accepts("𝑭 = m𝒂", r"\boldsymbol{F} = m\boldsymbol{a}")
    assert rejects_renderable("𝑭 = m𝒂", r"\mathbf{F} = m\mathbf{a}") and rejects_renderable("F = ma", r"\boldsymbol{F} = ma")


def test_boldsymbol_is_the_only_new_command():
    check_latex(r"\boldsymbol{F}", PROVIDER_COMMANDS)
    for cmd in ("bm", "pmb", "href", "url", "htmlClass", "includegraphics"):
        with pytest.raises(Unreadable):
            check_latex("\\" + cmd + "{F}", PROVIDER_COMMANDS)
    # Changed in the final recognition assignment (2026-11-12): the text converter now also emits the four font commands, for a
    # styled letter typed or pasted as text (𝐅 𝑭 ℝ 𝒜) or a bold letter run in Word/Excel text; nothing else was added
    for cmd in ("mathbf", "boldsymbol", "mathbb", "mathcal"):
        check_latex("\\" + cmd + "{F}")
    for cmd in ("bm", "pmb", "href", "url", "htmlClass", "includegraphics", "mathit", "text"):
        with pytest.raises(Unreadable):
            check_latex("\\" + cmd + "{F}")
