"""Cycle 2.3, phase B2: permanent regressions for the Holdout E root causes (RC1-RC10), all with synthetic inputs.

Each case states the expected recognition outcome and, for conversions, that the independent validator (math/verify.py) agrees -
the conversion is accepted only because both readings match."""
from xml.etree import ElementTree as ET

import pytest

from inst_paper.services.math import office, recognise
from inst_paper.services.math.verify import verify_omml, verify_text


def converted(text, subject, expected):
    r = recognise(text, subject=subject)
    assert r.outcome == "CONVERTED" and r.text == expected, (r.text, r.reasons)
    for s in r.spans:
        if s.status == "converted":
            assert verify_text(s.original, s.latex, subject, text)[0]          # independently verified


def reviewed(text, subject, code=None):
    r = recognise(text, subject=subject)
    assert r.outcome == "NEEDS_REVIEW" and r.text == text, (r.text, r.reasons)
    if code:
        assert code in r.reasons, r.reasons


# ---- RC1 unit vocabulary (case-sensitive, context-aware)
@pytest.mark.parametrize("text, expected", [
    ("R = 4 kΩ", r"\(R = 4\,\mathrm{k}\mathrm{\Omega}\)"), ("2.2 MΩ", r"\(2.2\,\mathrm{M}\mathrm{\Omega}\)"),
    ("5 × 2.4 MJ = 12 MJ", r"\(5 \times 2.4\,\mathrm{MJ} = 12\,\mathrm{MJ}\)"), ("1.5 GJ", r"\(1.5\,\mathrm{GJ}\)"),
    ("3 mJ", r"\(3\,\mathrm{mJ}\)"), ("t = 5 min", r"\(t = 5\,\mathrm{min}\)"), ("t = 2 h", r"\(t = 2\,\mathrm{h}\)"),
    ("V = 2 L", r"\(V = 2\,\mathrm{L}\)"), ("E = 3 eV", r"\(E = 3\,\mathrm{eV}\)"), ("p = 1 atm", r"\(p = 1\,\mathrm{atm}\)"),
    ("T = 25 °C", r"\(T = 25\,{}^{\circ}\mathrm{C}\)"),
])
def test_rc1_units_are_recognised_upright(text, expected):
    converted(text, "physics", expected)


@pytest.mark.parametrize("text, expected", [
    ("min(a, b) = a", r"\(\min(a, b) = a\)"),            # the function, not the minute
    ("x = k Ω", r"\(x = k\Omega\)"),                     # spaced: a product of variables
    ("Let MJ be a line.", "Let MJ be a line."),          # letters in prose are not units
])
def test_rc1_near_misses(text, expected):
    r = recognise(text, subject="physics")
    assert r.text == expected and r.outcome != "NEEDS_REVIEW"


# ---- RC2 lone powered units
@pytest.mark.parametrize("text", ["s^-1", "m^-2", "s⁻¹"])
def test_rc2_lone_powered_unit_symbol_goes_to_review(text):
    reviewed(text, "physics", "NOT_INDEPENDENTLY_VERIFIED")


@pytest.mark.parametrize("text, subject, expected", [
    ("k = 0.5 s⁻¹", "chemistry", r"\(k = 0.5\,\mathrm{s}^{-1}\)"), ("5 s^-1", "physics", r"\(5\,\mathrm{s}^{-1}\)"),
    ("mol^-1", "chemistry", r"\(\mathrm{mol}^{-1}\)"), ("x^-1 + 1", None, r"\(x^{-1} + 1\)"), ("A⁻¹", "mathematics", r"\(A^{-1}\)"),
    ("A = [1 2; 3 4], find A⁻¹", None, r"\(A = \begin{bmatrix}1 & 2 \\ 3 & 4\end{bmatrix}\), find \(A^{-1}\)"),
])
def test_rc2_units_and_variables_with_powers(text, subject, expected):
    converted(text, subject, expected)


# ---- RC3 / RC9 Word equations: styling and adjacent runs
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
PLAIN = '<m:rPr><m:sty m:val="p"/></m:rPr>'


def _eq(xml):
    return ET.fromstring(f'<w:p xmlns:w="{W}" xmlns:m="{M}"><m:oMath>{xml}</m:oMath></w:p>')


@pytest.mark.parametrize("xml, expected", [
    (f"<m:r><m:t>F=5</m:t></m:r><m:r>{PLAIN}<m:t> N</m:t></m:r>", r"\(F=5 \mathrm{N}\)"),                       # leading space
    (f"<m:r><m:t>E=</m:t></m:r><m:r>{PLAIN}<m:t>5 kg</m:t></m:r>", r"\(E=5 \mathrm{kg}\)"),                       # number + unit run
    (f"<m:r><m:t>t=3</m:t></m:r><m:r>{PLAIN}<m:t>min</m:t></m:r>", r"\(t=3\mathrm{min}\)"),                       # never \min
    ('<m:r><m:rPr><m:nor/></m:rPr><m:t>J</m:t></m:r>', r"\(\mathrm{J}\)"),                                        # m:nor
    (f"<m:func><m:fName><m:r>{PLAIN}<m:t>sin</m:t></m:r></m:fName><m:e><m:r><m:t>x</m:t></m:r></m:e></m:func>", r"\(\sin x\)"),
    ("<m:r><m:t>a</m:t></m:r><m:r><m:t>+</m:t></m:r><m:r><m:t>b</m:t></m:r>", r"\(a+b\)"),                     # adjacent runs
    ("<m:sSup><m:e><m:r><m:t>y</m:t></m:r></m:e><m:sup><m:r><m:t>Δ</m:t></m:r><m:r><m:t>k</m:t></m:r></m:sup></m:sSup>",
     r"\({y}^{\Delta k}\)"),
])
def test_rc3_rc9_word_equation_style_and_runs_are_preserved(xml, expected):
    p = _eq(xml)
    r = recognise(office.docx_paragraph_text(p))
    assert r.outcome == "CONVERTED" and r.text == expected, (r.text, r.reasons)
    assert verify_omml(p.find(f"{{{M}}}oMath"), expected[2:-2])[0]


def test_rc3_italic_word_letters_stay_italic():
    p = _eq("<m:r><m:t>m</m:t></m:r><m:r><m:t>a</m:t></m:r>")
    assert recognise(office.docx_paragraph_text(p)).text == r"\(ma\)"


def test_rc3_a_style_change_is_never_accepted(monkeypatch):
    monkeypatch.setattr(office, "omml_to_latex", lambda el: "F=5 N")          # the upright unit converted as an italic N
    p = _eq(f"<m:r><m:t>F=5</m:t></m:r><m:r>{PLAIN}<m:t> N</m:t></m:r>")
    r = recognise(office.docx_paragraph_text(p))
    assert r.outcome == "EXTRACTION_ERROR" and r.reasons == ["WORD_EQUATION_NOT_VERIFIED"]


# ---- RC5 fraction denominators and units
@pytest.mark.parametrize("text", ["(3 × 8)/2 L", "x/2 m = 3"])
def test_rc5_number_and_unit_after_a_slash_goes_to_review(text):
    reviewed(text, "chemistry")


@pytest.mark.parametrize("text, subject, expected", [
    ("a = 12 m / 4 s = 3 m s⁻²", "physics", r"\(a = \frac{12\,\mathrm{m}}{4\,\mathrm{s}} = 3\,\mathrm{m}\,\mathrm{s}^{-2}\)"),
    ("v = 6 m/s", "physics", r"\(v = 6\,\mathrm{m}/\mathrm{s}\)"), ("ρ = 1000 kg/m³", "physics", r"\(\rho = 1000\,\mathrm{kg}/\mathrm{m}^{3}\)"),
    ("x = (a + b)/c", None, r"\(x = \frac{a + b}{c}\)"), ("y = 6/(1−1/3)", None, r"\(y = \frac{6}{1 - \frac{1}{3}}\)"),
])
def test_rc5_valid_fractions_and_quantities_convert(text, subject, expected):
    converted(text, subject, expected)


# ---- RC6 caret powers
@pytest.mark.parametrize("text", ["y = e^3t", "x^2y", "10^2n", "z = e^2(x)"])
def test_rc6_caret_number_glued_to_letters_goes_to_review(text):
    reviewed(text, None, "AMBIGUOUS_POWER")


@pytest.mark.parametrize("text, expected", [
    ("y = e^(3t)", r"\(y = e^{3t}\)"), ("x^2 y", r"\(x^{2}y\)"), ("10^2", r"\({10}^{2}\)"), ("Find d^2y/dx^2 now", r"Find \(\frac{d^{2}y}{dx^{2}}\) now"),
])
def test_rc6_explicit_powers_convert(text, expected):
    converted(text, None, expected)


# ---- RC10 candidate detection
@pytest.mark.parametrize("text, code", [("Find x/2y now", "AMBIGUOUS_DENOMINATOR"), ("area 6 m2", "UNIT_DIGIT_POWER")])
def test_rc10_ambiguous_typings_are_no_longer_silently_plain(text, code):
    reviewed(text, "physics", code)


@pytest.mark.parametrize("text", ["pages 2-3", "Room 6 B2", "See Q.5-7 below.", "and/or", "2019-20"])
def test_rc10_ordinary_text_is_not_detected(text):
    r = recognise(text, subject="physics")
    assert r.text == text and r.outcome != "NEEDS_REVIEW"


# ---- Review fixes: chemistry molar M (decided by the backend subject only), definitions, per-thread subject
@pytest.mark.parametrize("text, expected", [
    ("The pH of 10⁻³ M HCl at 25°C is:", r"The pH of \({10}^{-3}\,\mathrm{M}\) HCl at 25°C is:"), ("c = 0.5 M", r"\(c = 0.5\,\mathrm{M}\)"),
    ("k = 5 M⁻¹ s⁻¹", r"\(k = 5\,\mathrm{M}^{-1}\,\mathrm{s}^{-1}\)"), ("1.5 × 10⁻² M", r"\(1.5 \times {10}^{-2}\,\mathrm{M}\)"),
])
def test_molar_m_after_a_number_in_chemistry(text, expected):
    converted(text, "chemistry", expected)


@pytest.mark.parametrize("subject", ["physics", "mathematics", None])
@pytest.mark.parametrize("text", ["The pH of 10⁻³ M HCl", "c = 0.5 M", "k = 5 M⁻¹ s⁻¹"])
def test_molar_m_is_never_a_unit_outside_chemistry(text, subject):
    assert "mathrm{M}" not in recognise(text, subject=subject).text


@pytest.mark.parametrize("text", ["c = 0.1M", "M⁻¹", "x = 2 M + 3"])
def test_ambiguous_m_in_chemistry_goes_to_review(text):
    reviewed(text, "chemistry")


@pytest.mark.parametrize("text, expected", [
    ("M = 58.5 g mol⁻¹", r"\(M = 58.5\,\mathrm{g}\,\mathrm{mol}^{-1}\)"),       # molar mass: a variable
    ("n = m/M", r"\(n = \frac{m}{M}\)"),
    ("c = 0.5 m", r"\(c = 0.5\,\mathrm{m}\)"),                                   # lowercase m is never molar
])
def test_molar_m_near_misses(text, expected):
    converted(text, "chemistry", expected)


def test_a_number_before_a_letter_is_not_a_definition():
    # "1 m = 100 cm" does not define a variable m, so the lone m⁻¹ later is not settled: review, never an italic variable
    r = recognise("1 m = 100 cm, so m⁻¹ = 0.01 cm⁻¹", subject="physics")
    assert r.outcome == "NEEDS_REVIEW" and "m^{-1}" not in r.text


def test_the_subject_is_kept_per_thread():
    import threading
    results, barrier = {}, threading.Barrier(8)

    def run(n):
        subject = "chemistry" if n % 2 else "physics"
        barrier.wait()
        results[n] = [verify_text("c = 0.5 M", r"c = 0.5\,\mathrm{M}", subject)[0] for _ in range(300)]
    threads = [threading.Thread(target=run, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(all(results[n]) if n % 2 else not any(results[n]) for n in range(8))


@pytest.mark.parametrize("text", ["R = 4kΩ", "E = 2.4MJ", "q = 3nC"])
def test_a_prefixed_unit_glued_to_its_number_goes_to_review(text):
    # 2.2.0 converted 4kΩ and 2.4MJ with italic letters (4k\Omega: k as a variable); now never guessed
    reviewed(text, "physics")


@pytest.mark.parametrize("text, subject, expected", [
    ("m = 5kg", "physics", r"\(m = 5\,\mathrm{kg}\)"), ("L = 10cm", "physics", r"\(L = 10\,\mathrm{cm}\)"),
    ("n = 2mol", "chemistry", r"\(n = 2\,\mathrm{mol}\)"), ("x = 2ab", None, r"\(x = 2ab\)"),
])
def test_unmistakable_glued_units_and_products_still_convert(text, subject, expected):
    converted(text, subject, expected)
