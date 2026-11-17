"""Word for the web writes an equation's letters as mathematical alphanumeric characters (𝑥 U+1D465, 𝐹, 𝛼, ℎ for italic h) instead
of plain letters with a style property, and puts U+2008 spaces between units. The run structures below are transcribed from a
genuine Word for the web download (Microsoft Word for the web 16.0000, 2026-11). Each must convert to the same meaning a desktop
Word equation has; a style with no faithful conversion is flagged, never dropped."""
import pytest
from xml.etree import ElementTree as ET

from inst_paper.services.math import recognise
from inst_paper.services.math import office
from inst_paper.services.math.verify import _normal, read_latex, verify_omml
from inst_paper.services.math.omml import omml_to_latex

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def r(t: str, pr: str = "") -> str:
    return f'<m:r>{pr}<m:t xml:space="preserve">{t}</m:t></m:r>'


def sup(e, s):
    return f"<m:sSup><m:sSupPr><m:ctrlPr/></m:sSupPr><m:e>{e}</m:e><m:sup>{s}</m:sup></m:sSup>"


def sub(e, s):
    return f"<m:sSub><m:sSubPr><m:ctrlPr/></m:sSubPr><m:e>{e}</m:e><m:sub>{s}</m:sub></m:sSub>"


def frac(n, d):
    return f"<m:f><m:fPr><m:ctrlPr/></m:fPr><m:num>{n}</m:num><m:den>{d}</m:den></m:f>"


def same(got: str, want: str) -> bool:
    """Same mathematical structure (the independent validator's canonical reading), not the same spelling: {x}^{2} is x^{2}."""
    return _normal(read_latex(got)) == _normal(read_latex(want))


def para(label: str, *equations: str) -> ET.Element:
    body = f'<w:r><w:t xml:space="preserve">{label}</w:t></w:r>' + "".join(f"<m:oMath>{e}</m:oMath>" for e in equations)
    return ET.fromstring(f'<w:p xmlns:w="{W_NS}" xmlns:m="{M_NS}">{body}</w:p>')


# (fixture item, equation XML as Word for the web wrote it, expected LaTeX of the equation)
GENUINE = [
    ("W6", sup(r("𝑥"), r("2")) + r("+1=5"), "x^{2}+1=5"),
    ("W10", frac(r("𝑎+𝑏"), r("𝑐")), r"\frac{a+b}{c}"),
    ("W11", sup(r("𝑥"), r("𝑛+1")), "x^{n+1}"),
    ("W12", sub(r("𝑎"), r("𝑛+1")), "a_{n+1}"),
    ("W13", f"<m:sSubSup><m:e>{r('𝑥')}</m:e><m:sub>{r('𝑖')}</m:sub><m:sup>{r('𝑛+1')}</m:sup></m:sSubSup>", "x_{i}^{n+1}"),
    ("W14", frac(r("𝑎+𝑏"), r("𝑐+") + frac(r("𝑑"), r("𝑒"))), r"\frac{a+b}{c+\frac{d}{e}}"),
    ("W18", r("𝑣") + r("=5\u2008") + r("𝑚") + r("\u2008") + sup(r("𝑠"), r("−1")), "v=5ms^{-1}"),   # U+2008 spaces, as Word wrote them
    ("W20", r("𝑥+𝑦"), "x+y"),
    ("h", r("𝐸=ℎ𝜈"), r"E=h\nu"),                         # Word writes italic h as U+210E (the hole in the italic alphabet)
    ("∂", frac(r("𝜕𝑓"), r("𝜕𝑥")), r"\frac{\partial f}{\partial x}"),
]


@pytest.mark.parametrize("item, xml, latex", GENUINE, ids=[g[0] for g in GENUINE])
def test_word_web_italic_letters_convert_like_desktop_word(item, xml, latex):
    el = ET.fromstring(f'<m:oMath xmlns:m="{M_NS}" xmlns:w="{W_NS}">{xml}</m:oMath>')
    got = omml_to_latex(el)
    assert same(got, latex), got
    ok, why = verify_omml(el, got)                       # the independent validator agrees with the conversion
    assert ok, why


@pytest.mark.parametrize("label, xml", [
    ("16. Acid equation: ", sub(r("𝐻"), r("2")) + r("𝑆") + sub(r("𝑂"), r("4")) + r("\u2008")),
    ("18. Speed equation: ", r("𝑣") + r("=5\u2008") + r("𝑚") + r("\u2008") + sup(r("𝑠"), r("−1"))),
], ids=["W16", "W18"])
def test_word_web_unit_spaces_are_spacing_not_symbols(label, xml):
    # mathematics: only the spacing is under test here (in Physics/Chemistry W18's italic 'm' after 5 is reviewed, see below)
    res = recognise(office.docx_paragraph_text(para(label, xml)), subject="mathematics")
    assert res.outcome == "CONVERTED", (res.outcome, res.reasons, res.text)


def test_a_word_web_equation_paragraph_is_converted_not_refused():
    res = recognise(office.docx_paragraph_text(para("Greek equation: ", r("Δ", '<m:rPr><m:sty m:val="p"/></m:rPr>'),
                                                    r("𝑥") + r("=") + r("𝜋"), sup(r("𝑟"), r("2")))), subject="physics")
    assert res.outcome == "CONVERTED", (res.outcome, res.reasons, res.text)
    assert res.text.replace(" ", "") == r"Greekequation:\(\Delta\)\(x=\pi\)\({r}^{2}\)", res.text


@pytest.mark.parametrize("xml, latex", [
    (r("𝐅") + r("=𝑚𝒂"), None),                            # 𝒂 bold italic letter: \boldsymbol
    (r("𝐅=𝑚𝑎"), r"\mathbf{F}=ma"),                         # bold upright letter: \mathbf
    (r("𝑭=𝑚𝑎"), r"\boldsymbol{F}=ma"),                     # bold italic: a bold vector
    (r("𝑥∈ℝ"), r"x\in\mathbb{R}"),
], ids=["mixed", "bold", "bold-italic", "double-struck"])
def test_styled_letters_keep_their_font(xml, latex):
    el = ET.fromstring(f'<m:oMath xmlns:m="{M_NS}" xmlns:w="{W_NS}">{xml}</m:oMath>')
    got = omml_to_latex(el)
    if latex is not None:
        assert same(got, latex), got
    assert r"\boldsymbol" in got or r"\mathbf" in got or r"\mathbb" in got
    ok, why = verify_omml(el, got)
    assert ok, why


@pytest.mark.parametrize("letter", ["𝜶", "𝛂", "𝔉", "𝖥", "𝙵", "𝟏", "𝕩"], ids=["bold-italic-alpha", "bold-alpha", "fraktur", "sans", "mono",
                                                                         "bold-digit", "lowercase-double-struck"])
def test_a_style_without_a_faithful_conversion_is_flagged_never_dropped(letter):
    res = recognise(office.docx_paragraph_text(para("Bold Greek equation: ", r(letter) + r("+1"))), subject="physics")
    assert res.outcome in ("NEEDS_REVIEW", "EXTRACTION_ERROR"), (res.outcome, res.text)


# ------------------------------------------------------------------ a bold letter in ordinary text (a vector), Word and Excel

def wp(*runs: tuple[str, str]) -> ET.Element:
    """A Word paragraph of text runs (text, rPr children) - as in the genuine file: <w:b w:val="1"/> on ' F'."""
    body = "".join(f'<w:r>{f"<w:rPr>{pr}</w:rPr>" if pr else ""}<w:t xml:space="preserve">{t}</w:t></w:r>' for t, pr in runs)
    return ET.fromstring(f'<w:p xmlns:w="{W_NS}">{body}</w:p>')


B = '<w:b w:val="1"/><w:bCs w:val="1"/>'


def test_w23_a_bold_letter_in_text_stays_bold():
    p = wp(("The vector", ""), (" F", B), (" acts on m.", ""))           # W23, run for run
    assert office.docx_paragraph_text(p) == "The vector 𝐅 acts on m."
    res = recognise(office.docx_paragraph_text(p), subject="physics")
    assert res.outcome == "CONVERTED" and r"\mathbf{F}" in res.text, (res.outcome, res.text)


def test_bold_italic_letter_is_a_bold_italic_vector():
    p = wp(("Here ", ""), ("a", B + "<w:i/>"), (" is the acceleration.", ""))
    assert office.docx_paragraph_text(p) == "Here 𝒂 is the acceleration."


@pytest.mark.parametrize("runs, text", [
    ((("The ", ""), ("resultant", B), (" force", "")), "The resultant force"),          # W22: a bold WORD is emphasis, kept as text
    ((("F", B), ("orce acts.", "")), "Force acts."),                                     # a bold letter inside a word
    ((("A", B), (") one", "")), "A) one"),                                               # an option label
    ((("Answer: ", ""), ("B", B)), "Answer: B"),                                         # the answer line
    ((("Marks: ", ""), ("4", B)), "Marks: 4"),
], ids=["word", "inside-word", "option-label", "answer", "marks"])
def test_bold_never_changes_structure_or_ordinary_words(runs, text):
    assert office.docx_paragraph_text(wp(*runs)) == text


def xsi(xml: str) -> ET.Element:
    return ET.fromstring(f'<si xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">{xml}</si>')


def test_x8_excel_bold_letter_in_a_cell_stays_bold():
    # X8 as Excel for the web wrote it: <b/> on F, <i/> on m (italic is how a maths letter looks anyway)
    si = xsi('<r><rPr></rPr><t xml:space="preserve">The force </t></r><r><rPr><b/></rPr><t>F</t></r>'
             '<r><rPr></rPr><t xml:space="preserve"> acts on mass </t></r><r><rPr><i/></rPr><t>m</t></r><r><rPr></rPr><t>.</t></r>')
    assert office.xlsx_string_text(si) == "The force 𝐅 acts on mass m."
    res = recognise(office.xlsx_string_text(si), subject="physics")
    assert res.outcome == "CONVERTED" and r"\mathbf{F}" in res.text, (res.outcome, res.text)


def test_an_excel_cell_holding_only_a_bold_letter_is_left_alone():
    assert office.xlsx_string_text(xsi("<r><rPr><b/></rPr><t>A</t></r>")) == "A"          # the correct-answer column


# ------------------------------------------------------------------ a unit Word for the web could only write in italic (as a variable)

@pytest.mark.parametrize("label, xml, subject, outcome", [
    ("18. Speed equation: ", r("𝑣") + r("=5 ") + r("𝑚") + r(" ") + sup(r("𝑠"), r("−1")), "physics", "NEEDS_REVIEW"),
    ("7. Force equation: ", r("𝐹") + r("=5") + r("𝑁"), "physics", "NEEDS_REVIEW"),
    ("7. Force equation: ", r("𝐹") + r("=5") + r("𝑁"), "chemistry", "NEEDS_REVIEW"),
    ("7. Force equation: ", r("𝐹") + r("=5") + r("𝑁"), "mathematics", "CONVERTED"),          # mathematics: a variable N
    ("8. Vector equation: ", r("𝐹") + r("=") + r("𝑚𝑎"), "physics", "CONVERTED"),             # no number before a unit letter
    ("16. Acid equation: ", sub(r("𝐻"), r("2")) + r("𝑆") + sub(r("𝑂"), r("4")), "chemistry", "CONVERTED"),
    ("Speed (upright unit): ", r("𝑣") + r("=5 ") + r("m s", '<m:rPr><m:sty m:val="p"/></m:rPr>'), "physics", "CONVERTED"),
], ids=["W18-physics", "W7-physics", "W7-chemistry", "W7-mathematics", "W8", "W16", "upright-unit"])
def test_an_italic_unit_after_a_number_in_a_word_equation_is_reviewed(label, xml, subject, outcome):
    # Word marks a variable with italic and a unit with upright (Normal Text); Word for the web cannot make letters upright, so an
    # italic m/s/N right after a number may be a unit or a variable: in Physics and Chemistry it goes to review, never guessed
    res = recognise(office.docx_paragraph_text(para(label, xml)), subject=subject)
    assert res.outcome == outcome, (res.outcome, res.reasons, res.text)
    if outcome == "NEEDS_REVIEW":
        assert "AMBIGUOUS_UNIT" in res.reasons


def test_an_italic_unit_in_a_word_equation_offers_the_upright_reading():
    from inst_paper.services.recognition import _field
    p = para("18. Speed equation: ", r("𝑣") + r("=5 ") + r("𝑚") + r(" ") + sup(r("𝑠"), r("−1")))
    f = _field(office.docx_paragraph_text(p), "physics", "question")
    assert [s["text"] for s in f["suggestions"]] == ["18. Speed equation: \\(v=5\\,\\mathrm{m}\\,\\mathrm{s}^{-1}\\)",
                                                     "18. Speed equation: \\(v=5 m {s}^{-1}\\)"]
    assert _field(f["suggestions"][0]["text"], "physics", "question")["outcome"] == "CONVERTED"


def test_a_limit_condition_under_a_word_function_name_is_ordinary_maths():
    # desktop Word: lim (upright, in m:fName) with n→∞ underneath; the condition's n is the variable n, never upright text
    x = ('<m:func><m:fName><m:limLow><m:e><m:r><m:rPr><m:sty m:val="p"/></m:rPr><m:t>lim</m:t></m:r></m:e><m:lim>' + r("n→∞") +
         '</m:lim></m:limLow></m:fName><m:e>' + sup('<m:d><m:dPr><m:begChr m:val="("/><m:endChr m:val=")"/></m:dPr><m:e>' + r("1+") +
         frac(r("1"), r("n")) + "</m:e></m:d>", r("n")) + "</m:e></m:func>")
    el = ET.fromstring(f'<m:oMath xmlns:m="{M_NS}" xmlns:w="{W_NS}">{x}</m:oMath>')
    got = omml_to_latex(el)
    assert r"\mathrm{n}" not in got and r"\lim\limits_{n" in got.replace(" ", ""), got
    ok, why = verify_omml(el, got)
    assert ok, why
