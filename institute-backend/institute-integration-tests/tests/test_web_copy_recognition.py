"""Formulas copied from a web page that renders maths (KaTeX): found in the first real Mathematics paper (2026-11-15).

A copy pastes each formula three times run together - MathML text, the LaTeX source, the visible characters in the page's order -
so the field 'πln⁡28\\dfrac{\\pi\\ln2}{8}8πln2​' holds one formula. Before the fix the whole field was wrapped as ONE formula and
marked converted (a silent wrong reading, the dangerous error)."""
import pytest

from inst_paper.services import recognition
from inst_paper.services.math import CONVERTED, NEEDS_REVIEW, recognise
from inst_paper.services.math.katex_paste import flatten, islands


def paste(mathml: str, source: str, page: str) -> str:
    return mathml + source + page


FRACTION = paste("πln⁡28", r"\dfrac{\pi\ln2}{8}", "8πln2​")


@pytest.mark.parametrize("text,stored", [
    (FRACTION, r"\(\dfrac{\pi\ln2}{8}\)"),
    (paste("−352125+64125i", r"-\dfrac{352}{125}+\dfrac{64}{125}i", "−125352​+12564​i"), r"\(-\dfrac{352}{125}+\dfrac{64}{125}i\)"),
    ("If " + paste("x3+y3=3xy", "x^3+y^3=3xy", "x3+y3=3xy") + ", then " + paste("dydx", r"\dfrac{dy}{dx}", "dxdy​") + " equals:",
     r"If \(x^3+y^3=3xy\), then \(\dfrac{dy}{dx}\) equals:"),
])
def test_a_proven_web_copy_is_read_as_its_own_latex_source(text, stored):
    r = recognise(text, subject="mathematics")
    assert (r.outcome, r.text) == (CONVERTED, stored)


def test_copies_that_do_not_prove_each_other_are_never_recovered():
    # the page copy differs from the source (8 vs 4): no proof, so nothing is read as a formula
    r = recognise(paste("πln⁡28", r"\dfrac{\pi\ln2}{8}", "4πln2​"), subject="mathematics")
    assert r.outcome == NEEDS_REVIEW and "\\(" not in r.text
    assert islands("x^2") == []                                          # a typed formula alone is not a paste


def test_rendered_text_is_never_wrapped_as_one_formula():
    """The dangerous error: invisible rendering characters + LaTeX commands, nothing provable."""
    r = recognise("πln⁡28\\dfrac{\\pi\\ln2}{8}", subject="mathematics")
    assert r.outcome == NEEDS_REVIEW and not r.text.startswith("\\(")


def test_a_web_copy_without_its_source_is_flagged_not_read():
    # x³/(eˣ−1) copied without its source: the page shows the denominator first; reading it would give the wrong formula
    r = recognise("∫0∞​ex−1x3​dx equals:", subject="mathematics")
    assert r.outcome == NEEDS_REVIEW and r.text == "∫0∞​ex−1x3​dx equals:"
    assert [s.code for s in r.spans if s.status == "flagged"] == ["WEB_COPY_WITHOUT_SOURCE"]


def test_flattening_follows_the_mathml_text():
    assert flatten(r"e^{\sin(x^2)}\left[\dfrac{3x^2}{1+x^3}+2x\cos(x^2)\ln(1+x^3)\right]") == "esin(x2)[3x21+x3+2xcos(x2)ln(1+x3)]"
    assert flatten(r"\sqrt[3]{27}") == "327" and flatten(r"\unknowncommand{x}") is None


@pytest.mark.parametrize("text,flagged", [("A. 2x + 1", True), ("(B) 4", True), ("C) x", True), ("A.2", False), ("Apple", False),
                                          ("2 A. m", False)])
def test_an_option_starting_with_its_letter_is_left_for_the_teacher(text, flagged):
    f = recognition.field_result(text, "mathematics", "option_a")
    codes = [s["code"] for s in f["spans"] if s["status"] == "flagged"]
    assert ("OPTION_LETTER_IN_TEXT" in codes) is flagged
    assert f["text"].startswith(text.split()[0])                         # never removed by the engine
