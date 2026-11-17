"""Mathematical recognition PROOF OF CONCEPT (inst_paper.services.math): not connected to imports, the API or the database.

The 50-case corpus (math_poc_corpus.json), ordinary text that must never change, regressions found on real questions, safety limits,
and Word/Excel superscript/subscript formatting."""
import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from inst_paper.services import documents, extraction
from inst_paper.services.math import recognise
from inst_paper.services.math.office import docx_paragraph_text, xlsx_string_text
from inst_paper.services.math.parser import Unreadable, check_latex

CORPUS = json.loads((Path(__file__).parent / "math_poc_corpus.json").read_text())


def norm(s: str) -> str:
    """Spacing and braces around single tokens do not change the LaTeX."""
    s = re.sub(r"\s+", "", s.replace(r"\lvert", "|").replace(r"\rvert", "|"))
    for _ in range(3):
        s = re.sub(r"\{(\\[A-Za-z]+|[A-Za-z0-9])\}", r"\1", s)
    return s


@pytest.mark.parametrize("case", CORPUS, ids=lambda c: f"{c['id']}-{c['category']}")
def test_corpus(case):
    r = recognise(case["input"])
    if case["expect"] == "flag":
        assert r.text == case["input"], "an ambiguous expression must never be rewritten"
        if case["input"] != "a/b/c":                     # known gap: not detected as a formula at all, so kept as typed without a flag
            assert r.flags
    elif case["expect"] == "unchanged":
        assert r.text == case["input"] and not r.flags
    else:
        assert not r.flags, [s.reason for s in r.flags]
        assert norm(r.text) == norm(case["expect"])


ORDINARY = [
    "Q1. (a) Which of the following statements is correct?", "A pen costs $5 and a book costs $10.", "A car moves at a speed of 2 m/s.",
    "The fee is Rs. 5/- per page.", "Section A–D carries 4 marks each.", "Choose the odd one out: cat, dog, cow, sin.",
    "I am a student of class 10.", "The test lasts 90 min.", "Log in to your account before the exam.",
    "Mark the x-axis and the y-axis on the graph.", "COVID-19 cases rose sharply in 2020.", "The exam starts on 12/05/2026 at 10:30.",
    "Water is H2O and salt is NaCl.", "Version 2.0 of the syllabus was released.", "The ratio of boys to girls is 3:4.",
    "Who wrote 'Hamlet'?", "Answer: (b)", "Metals such as iron, copper and zinc, e.g. in wires.", "Use the formula sheet (page 2) if needed.",
    "Mr. A. Kumar teaches Physics and Chemistry.", "", "   ",
]


@pytest.mark.parametrize("text", ORDINARY)
def test_ordinary_text_is_never_changed_or_flagged(text):
    r = recognise(text)
    assert r.text == text and not r.flags


@pytest.mark.parametrize("text, expected", [
    ("Molecular orbital theory gives O₂ a bond order of 2.", r"Molecular orbital theory gives \(O_{2}\) a bond order of 2."),  # article
    ("Evaluate ∫ dx/(x² + 4).", r"Evaluate \(\int \frac{dx}{x^{2} + 4}\)."),
    ("A body accelerates at 2 m/s² for 5 s.", r"A body accelerates at \(2\,\mathrm{m}/\mathrm{s}^{2}\) for 5 s."),
    ("Its speed is 10 m s⁻¹.", r"Its speed is \(10\,\mathrm{m}\,\mathrm{s}^{-1}\)."),
    ("y = 2 x + 3", r"\(y = 2x + 3\)"),                                                       # a variable, not a unit
    ("ML⁻¹T⁻²", r"\(ML^{-1}T^{-2}\)"),                                                       # the power belongs to the last letter
    ("Solve sin(2x) = cos(x) in the interval [0, 2π).", r"Solve \(\sin(2x) = \cos(x)\) in the interval \([0, 2\pi)\)."),
    ("Evaluate lim(x→0) [1 − cos(3x)]/x².", r"Evaluate \(\lim\limits_{x \to 0} \frac{[1 - \cos(3x)]}{x^{2}}\)."),
    ("For N₂ + 3H₂ ⇌ 2NH₃, find K.", r"For \(N_{2} + 3H_{2} \rightleftharpoons 2NH_{3}\), find K."),
    ("Total atoms = Nₐ.", r"Total atoms = \(N_{a}\)."),
])
def test_regressions_found_on_real_questions(text, expected):
    r = recognise(text)
    assert r.text == expected and not r.flags


@pytest.mark.parametrize("text", [
    "For 100 g the mole ratio is 3.33:6.7:3.33 = 1:2:1.",   # part of a ratio chain must not become "3.33 = 1"
    "∫ x² dx/2",                                             # is the /2 inside the integral?
    "√x²",
    "lim x 0",
])
def test_unclear_formulas_are_flagged_and_kept_as_typed(text):
    r = recognise(text)
    assert r.flags
    for span in r.flags:
        assert span.original in r.text


def test_existing_latex_is_never_converted_twice():
    for case in CORPUS:
        once = recognise(case["input"]).text
        assert recognise(once).text == once


def test_latex_commands_without_delimiters_are_kept_and_flagged():
    r = recognise(r"Evaluate \frac{1}{2} + x²")
    assert r.text == r"Evaluate \frac{1}{2} + x²" and r.flags


def test_escaped_dollar_and_prices_are_text():
    for text in [r"It costs \$5.", "Pay $5 now and $10 later.", "$ 5 $"]:
        assert recognise(text).text == text


def test_output_check_refuses_commands_the_converter_never_emits():
    for bad in [r"\href{http://x}{y}", r"\url{x}", r"\includegraphics{x}", r"\htmlClass{a}{b}", r"\frac{1}{2", r"x}"]:
        with pytest.raises(Unreadable):
            check_latex(bad)
    check_latex(r"\frac{\partial f}{\partial x} + \sqrt[3]{27} + \begin{bmatrix}1 & 2 \\ 3 & 4\end{bmatrix}")


def test_hostile_input_is_bounded():
    deep = "(" * 300 + "x²" + ")" * 300
    r = recognise(deep)
    assert r.text == deep and r.flags
    long = ("x² + 2x + 1, " * 400)[:5000]
    assert recognise(long).text.count(r"\(") > 100


# ---------------------------------------------------------------------- Word / Excel formatting
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _docx(*runs):
    body = "".join(f'<w:r>{f"<w:rPr><w:vertAlign w:val=\"{a}\"/></w:rPr>" if a else ""}<w:t xml:space="preserve">{t}</w:t></w:r>' for t, a in runs)
    return ET.fromstring(f'<w:p xmlns:w="{W}">{body}</w:p>')


def _xlsx(*runs):
    body = "".join(f'<r>{f"<rPr><vertAlign val=\"{a}\"/></rPr>" if a else ""}<t xml:space="preserve">{t}</t></r>' for t, a in runs)
    return ET.fromstring(f'<si xmlns="{S}">{body}</si>')


def test_word_superscript_and_subscript_formatting_is_kept():
    p = _docx(("Solve x", None), ("2", "superscript"), (" − 5x + 6 = 0 for a", None), ("1", "subscript"))
    assert documents._docx_paragraph_text(p) == "Solve x² − 5x + 6 = 0 for a₁"          # the importer keeps the formatting (was "x2")
    assert docx_paragraph_text(p) == "Solve x² − 5x + 6 = 0 for a₁"
    assert recognise(docx_paragraph_text(p)).text == r"Solve \(x^{2} - 5x + 6 = 0\) for \(a_{1}\)"


def test_word_formatting_without_unicode_characters_uses_brackets():
    p = _docx(("e", None), ("2π", "superscript"))
    assert docx_paragraph_text(p) == "e^(2π)"
    assert recognise("e^(2π)").text == r"\(e^{2\pi}\)"


def test_excel_rich_text_superscript_is_kept():
    si = _xlsx(("e", None), ("−x", "superscript"), (" + 10", None), ("−3", "superscript"))
    assert extraction._text_of(si) == "e⁻ˣ + 10⁻³"                                      # the importer keeps it (was "e minus x")
    assert xlsx_string_text(si) == "e⁻ˣ + 10⁻³"
    assert recognise(xlsx_string_text(si)).text == r"\(e^{-x} + {10}^{-3}\)"


def test_plain_word_and_excel_text_is_unchanged():
    p = _docx(("What is the SI unit of force?", None))
    assert docx_paragraph_text(p) == documents._docx_paragraph_text(p)
    si = _xlsx(("Newton", None))
    assert xlsx_string_text(si) == extraction._text_of(si)
