"""Cycle 2.3, phase B1: the independent structural validator (math/verify.py).

Each case gives the provider's source, a CORRECT conversion (must verify) and deliberately CORRUPTED conversions (must be rejected).
Every corrupted string is valid LaTeX that passes the converter's allow-list and would render - proving that rendering or parsing is
never taken as evidence of meaning. The validator is also checked for independence: it must not import the recognition parser."""
import ast
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from inst_paper.services.math import verify
from inst_paper.services.math.parser import check_latex
from inst_paper.services.math.symbols import PROVIDER_COMMANDS

CASES = [
    # (category, source, correct, [corrupted ...], domain)
    ("italic vs upright", "x = 2 m", r"x = 2\,\mathrm{m}", [r"x = 2m", r"x = 2\,m"], "physics"),
    ("italic vs upright", "[M L² T⁻²]", r"[\mathrm{M}\,\mathrm{L}^{2}\,\mathrm{T}^{-2}]", [r"[ML^{2}T^{-2}]"], "physics"),
    ("italic vs upright", "F = m a", r"F = ma", [r"F = \mathrm{m}a"], "physics"),
    ("lost superscript", "x² + 1", r"x^{2} + 1", [r"x2 + 1", r"x + 1"], None),
    ("added superscript", "xy", r"xy", [r"x^{y}"], None),
    ("superscript became subscript", "x²", r"x^{2}", [r"x_{2}"], None),
    ("lost subscript", "a₁ + a₂", r"a_{1} + a_{2}", [r"a1 + a_{2}", r"a^{1} + a_{2}"], None),
    ("added subscript", "ab", r"ab", [r"a_{b}"], None),
    ("fraction boundary", "(a+b)/c", r"\frac{a + b}{c}", [r"a + \frac{b}{c}", r"(a + b)c"], None),
    ("fraction boundary", "a + b/c", r"a + \frac{b}{c}", [r"\frac{a + b}{c}", r"\frac{a}{c} + b"], None),
    ("fraction boundary", "(3 × 8)/2 L", r"\frac{3 \times 8}{2}\,\mathrm{L}", [r"\frac{3 \times 8}{2\,\mathrm{L}}"], "chemistry"),
    ("fraction boundary", "x − x²/2", r"x - \frac{x^{2}}{2}", [r"\frac{x - x^{2}}{2}"], None),
    ("grouping", "2(x + 1)", r"2(x + 1)", [r"2x + 1"], None),
    ("grouping", "√(x+1)", r"\sqrt{x + 1}", [r"\sqrt{x} + 1"], None),
    ("missing or changed symbol", "x − y", r"x - y", [r"x + y", r"x", r"x - z"], None),
    ("missing or changed symbol", "α + β", r"\alpha + \beta", [r"a + \beta", r"\alpha + \gamma"], None),
    ("unit vs variable", "1 h = 3", r"1\,\mathrm{h} = 3", [r"1h = 3"], "physics"),
    ("unit vs variable", "5 × 10⁻⁷ m", r"5 \times {10}^{-7}\,\mathrm{m}", [r"5 \times {10}^{-7}m"], "physics"),
    ("unit vs variable", "4 kΩ", r"4\,\mathrm{k}\mathrm{\Omega}", [r"4k\Omega"], "physics"),
    ("caret power", "e^(3t)", r"e^{3t}", [r"e^{3}t"], None),
    ("caret power", "x^-1 + 1", r"x^{-1} + 1", [r"x^{-1 + 1}", r"x - 1 + 1"], None),
    ("chemical charge", "SO₄²⁻", r"\mathrm{SO_{4}^{2-}}", [r"\mathrm{SO_{4}^{2+}}", r"\mathrm{SO_{4}2-}", r"\mathrm{SO_{4}^{-}}"], "chemistry"),
    ("chemical identity", "CO₂", r"\mathrm{CO_{2}}", [r"\mathrm{Co_{2}}", r"\mathrm{CO^{2}}"], "chemistry"),
    ("limits", "∫₀^π sin x dx", r"\int_{0}^{\pi} \sin x\,dx", [r"\int_{0} \pi\sin x\,dx", r"\int_{0}^{\pi} sin x\,dx"], None),
    ("limits", "limₓ→₀ sin(x)/x", r"\lim_{x \to 0} \frac{\sin(x)}{x}", [r"\lim_{x} \to 0\frac{\sin(x)}{x}"], None),
    ("accents", "3î − 2ĵ", r"3\hat{i} - 2\hat{j}", [r"3i - 2\hat{j}"], None),
]


@pytest.mark.parametrize("category, source, correct, corrupted, domain", CASES, ids=lambda x: x if isinstance(x, str) else None)
def test_correct_conversion_verifies(category, source, correct, corrupted, domain):
    ok, why = verify.verify_text(source, correct, domain)
    assert ok, f"{category}: {why}"


@pytest.mark.parametrize("category, source, correct, corrupted, domain", CASES, ids=lambda x: x if isinstance(x, str) else None)
def test_corrupted_conversions_are_rejected_although_they_render(category, source, correct, corrupted, domain):
    for bad in corrupted:
        check_latex(bad, PROVIDER_COMMANDS)                  # valid, allow-listed LaTeX: it would parse and render
        ok, why = verify.verify_text(source, bad, domain)
        assert not ok, f"{category}: corrupted {bad!r} was accepted"
        assert why


@pytest.mark.parametrize("source", ["e^3t", "x^2y", "10^2n", "3^2^2", "a/b/c", "1/2x", "s⁻¹", "m^-2", "k = 5t N"])
def test_sources_whose_structure_is_not_settled_are_refused(source):
    # whatever the converter produced, the validator refuses: the source itself does not establish one reading
    for latex in (r"e^{3}t", r"e^{3t}", r"x^{2}y", r"{3^{2}}^{2}", r"\frac{a}{bc}", r"\frac{1}{2x}", r"s^{-1}", r"\mathrm{s}^{-1}", r"k = 5tN"):
        assert not verify.verify_text(source, latex, "physics")[0]


@pytest.mark.parametrize("source, latex, domain", [
    ("sin A = 5/13", r"\sin A = \frac{5}{13}", None), ("x_ij", r"x_{ij}", None), ("C_eq", r"C_{\mathrm{eq}}", None),
    ("Vᵣₘₛ", r"V_{\mathrm{rms}}", None), ("2 m/s", r"2\,\mathrm{m}/\mathrm{s}", "physics"), ("dy/dx", r"\frac{dy}{dx}", None),
    ("d²y/dx²", r"\frac{d^{2}y}{dx^{2}}", None), ("½mv²", r"\frac{1}{2}mv^{2}", None), ("[MLT]", r"[MLT]", None),
    ("mol L⁻¹ s⁻¹", r"\mathrm{mol}\,\mathrm{L}^{-1}\,\mathrm{s}^{-1}", "chemistry"), ("A⁻¹", r"A^{-1}", "mathematics"),
    ("lim x→2 (x² − 4)/(x − 2)", r"\lim_{x \to 2} \frac{x^{2} - 4}{x - 2}", None), ("[Fe(CN)₆]³⁻", r"\mathrm{[Fe(CN)_{6}]^{3-}}", "chemistry"),
    ("2H₂ + O₂ → 2H₂O", r"2\mathrm{H_{2}} + \mathrm{O_{2}} \rightarrow 2\mathrm{H_{2}O}", "chemistry"), ("H₂O", r"H_{2}O", None),
    ("²³⁵U", r"{}^{235}\mathrm{U}", "chemistry"), ("|a b; c d|", r"\begin{vmatrix}a & b \\ c & d\end{vmatrix}", None),
])
def test_near_misses_and_conventions_verify(source, latex, domain):
    ok, why = verify.verify_text(source, latex, domain)
    assert ok, why


M = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def _omml(xml):
    return ET.fromstring(f'<m:oMath xmlns:m="{M}">{xml}</m:oMath>')


PLAIN = '<m:rPr><m:sty m:val="p"/></m:rPr>'


def test_word_equation_styles_and_structure_are_compared():
    unit = _omml(f'<m:r><m:t>5</m:t></m:r><m:r>{PLAIN}<m:t> J</m:t></m:r>')
    assert verify.verify_omml(unit, r"5\mathrm{J}")[0]
    assert not verify.verify_omml(unit, r"5 J")[0]                           # the upright unit became an italic variable
    frac = _omml("<m:f><m:num><m:r><m:t>a</m:t></m:r></m:num><m:den><m:r><m:t>b</m:t></m:r></m:den></m:f>")
    assert verify.verify_omml(frac, r"\frac{a}{b}")[0]
    assert not verify.verify_omml(frac, r"\frac{b}{a}")[0]
    sup = _omml("<m:sSup><m:e><m:r><m:t>x</m:t></m:r></m:e><m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup>")
    assert verify.verify_omml(sup, r"{x}^{2}")[0] and not verify.verify_omml(sup, r"{x}_{2}")[0]
    fn = _omml(f'<m:func><m:fName><m:r>{PLAIN}<m:t>sin</m:t></m:r></m:fName><m:e><m:r><m:t>x</m:t></m:r></m:e></m:func>')
    assert verify.verify_omml(fn, r"\sin x")[0] and not verify.verify_omml(fn, r"sin x")[0]


def test_the_validator_does_not_use_the_recognition_parser():
    src = (Path(verify.__file__)).read_text()
    project = {n.module for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom) and n.level > 0}
    assert project == {"symbols"}, project                                   # character tables only: no lexer, parser, chem, safety
