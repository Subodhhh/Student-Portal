"""Multi-interpretation preview: an expression whose formatting was lost in copying is never decided by the engine. Every reading the
declared grammar allows is generated, checked by full recognition of the whole field, deduplicated by what would be stored, and
reported with whether the list is complete. Nothing is ranked by how common it is."""
import pytest

from inst_paper.services.math import NEEDS_REVIEW, recognise
from inst_paper.services.math.suggest import MAX_CANDIDATES
from inst_paper.services.recognition import _field


def readings(text: str, subject: str | None = None) -> tuple[set[str], dict]:
    rec = _field(text, subject)
    assert rec["outcome"] == NEEDS_REVIEW, (text, rec["outcome"])
    assert len(rec["candidate_sets"]) == 1
    return {s["text"].replace(" ", "") for s in rec["suggestions"]}, rec["candidate_sets"][0]


# the brief's examples: (text, readings that must all be offered, exact number of distinct readings)
EXAMPLES = [
    ("x2 + 2x + 1", {r"\(x^{2}+2x+1\)", r"\(x_{2}+2x+1\)", r"\(x\cdot2+2x+1\)"}, 3),
    ("sin3 x cos2x", {r"\(\sin^{3}x\cos^{2}x\)", r"\(\sin3x\cos2x\)", r"\(\sin^{3}x\cos2x\)", r"\(\sin3x\cos^{2}x\)"}, 4),
    ("a2+b2=c2", {r"\(a^{2}+b^{2}=c^{2}\)", r"\(a_{2}+b_{2}=c_{2}\)", r"\(a^{2}+b^{2}=c_{2}\)"}, 27),
    ("e2x", {r"\(e^{2}x\)", r"\(e^{2x}\)", r"\(e_{2}x\)", r"\(e\cdot2x\)"}, 4),
    ("d2y/dx2", {r"\(\frac{d^{2}y}{dx^{2}}\)", r"\(\frac{d_{2}y}{dx_{2}}\)"}, 9),
    ("∫ 3ax/(b2+c2x2) dx", {r"\(\int\frac{3ax}{b^{2}+c^{2}x^{2}}\,dx\)", r"\(\int\frac{3ax}{b_{2}+c_{2}x_{2}}\,dx\)"}, 27),
    ("limx→0 sinx/x", {r"\(\lim\limits_{x\to0}\frac{\sinx}{x}\)", r"\(\lim\limits_{x\to0}\sin(\frac{x}{x})\)"}, 2),
    ("v2=u2+2as", {r"\(v^{2}=u^{2}+2as\)", r"\(v_{2}=u_{2}+2as\)", r"\(v^{2}=u_{2}+2as\)"}, 9),
    ("log2x", {r"\(\log_{2}x\)", r"\(\log^{2}x\)", r"\(\log2x\)"}, 3),
    ("∫ sin3 x cos2x dx", {r"\(\int\sin^{3}x\cos^{2}x\,dx\)", r"\(\int\sin3x\cos2x\,dx\)"}, 4),
]


@pytest.mark.parametrize("text,required,count", EXAMPLES)
def test_every_structurally_plausible_reading_is_offered(text, required, count):
    shown, info = readings(text)
    assert required <= shown, (text, required - shown)
    assert len(shown) == count == info["shown"]
    assert info["complete"] and info["checked"] == info["combinations"]


@pytest.mark.parametrize("text", ["sin3 x", "log2x", "∫ sin3 x cos2x dx", "R=u²sin2θ/g", "2 sin2x cos x"])
def test_a_number_glued_to_a_function_name_is_never_converted_silently(text):
    assert recognise(text).outcome == NEEDS_REVIEW


@pytest.mark.parametrize("text", ["sin 2x", "sin30°", "log 2x", "sin x + cos x", "x² + 2x + 1"])
def test_unambiguous_notation_still_converts(text):
    assert recognise(text).outcome == "CONVERTED", text


def test_a_function_of_a_bare_number_is_not_a_flattened_power():
    # ln2/k: nothing follows the 2, so it is ln 2 (in Chemistry, (ln 2)/k is the half-life convention)
    assert recognise("ln2/k", subject="chemistry").outcome == "CONVERTED"


@pytest.mark.parametrize("text", ["Q2 + Q3 marks", "Class 10 + 2 students", "Room B12"])
def test_labels_and_prose_are_not_formulas(text):
    assert recognise(text).outcome == "PLAIN_TEXT"


@pytest.mark.parametrize("text,count", [
    # combinations never written in any example: the same generic rules produce every reading
    ("tan2x sec3y", 4),            # two functions: each number a power or part of the angle
    ("cot4θ", 2),
    ("p2 = q2 - r2", 27),          # three flattened scripts
    ("lg3x", 3),                   # a logarithm with a base
])
def test_the_rules_generalise_to_unseen_combinations(text, count):
    shown, info = readings(text)
    assert len(shown) == count and info["complete"]


def test_more_readings_than_the_limit_are_disclosed_as_incomplete():
    # five flattened scripts: 3^5 = 243 readings, more than the declared limit
    shown, info = readings("a2+b2+c2+f2 = g2")
    assert info["combinations"] == 243 and info["checked"] == MAX_CANDIDATES
    assert not info["complete"] and len(shown) == info["shown"] <= MAX_CANDIDATES
    # the coherent readings are never the ones cut: they are checked first
    assert r"\(a^{2}+b^{2}+c^{2}+f^{2}=g^{2}\)" in shown and r"\(a_{2}+b_{2}+c_{2}+f_{2}=g_{2}\)" in shown


def test_readings_that_store_the_same_thing_are_one_reading():
    shown, info = readings("sin3 x cos2x")
    assert len(shown) == info["shown"]                     # set == list: no duplicates


def test_no_reading_is_invented_when_none_is_valid():
    rec = _field("x/2y", None)                             # a doubt with readings the grammar may refuse: never a made-up one
    for s in rec["suggestions"]:
        assert recognise(s["text"]).outcome == "CONVERTED"


def test_chemistry_formulas_and_charges_follow_the_subject():
    assert recognise("H2SO4", subject="chemistry").outcome == "CONVERTED"
    assert recognise("Fe3+", subject="chemistry").outcome == NEEDS_REVIEW                  # Fe³⁺ or Fe₃⁺: the teacher decides
    shown, _ = readings("Fe3+", "chemistry")
    assert {"Fe³⁺", "Fe₃⁺"} <= {t.replace(r"\(", "").replace(r"\)", "") for t in shown} or len(shown) == 2
