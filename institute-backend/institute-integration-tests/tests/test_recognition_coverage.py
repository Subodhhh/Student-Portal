"""Coverage of the final recognition assignment (2026-11-12): what a provider types in Mathematics, Physics and Chemistry, with the
expected meaning written by hand. A LaTeX expectation must be CONVERTED to exactly that structure (compared canonically by the
independent validator's reader); "REVIEW" must never be converted - an ambiguous text is flagged, never guessed."""
import pytest

from inst_paper.services.math import recognise
from inst_paper.services.math.verify import _normal, read_latex

CASES = [
    # powers and exponents
    ("mathematics", "x^2 + y^2 = r^2", "x^{2}+y^{2}=r^{2}"),
    ("mathematics", "a^n", "a^{n}"),
    ("chemistry", "10^-3 M", r"10^{-3}\,\mathrm{M}"),
    ("mathematics", "e^(x+1)", "e^{x+1}"),
    ("mathematics", "x^(-1/2)", r"x^{-\frac{1}{2}}"),
    ("mathematics", "2^(2^n)", "2^{2^{n}}"),
    ("mathematics", "x^(n+1) - x^n", "x^{n+1}-x^{n}"),
    ("mathematics", "e^3t", "REVIEW"),                                    # e^(3t) or (e^3)t: never guessed
    # subscripts
    ("mathematics", "x_1 + x_2 = 5", "x_{1}+x_{2}=5"),
    ("mathematics", "a_n = a_(n-1) + d", "a_{n}=a_{n-1}+d"),
    ("mathematics", "x_i^2", "x_{i}^{2}"),
    ("chemistry", "H₂O", r"\mathrm{H_{2}O}"),
    ("physics", "v_0 + a t", "v_{0}+at"),
    # fractions
    ("mathematics", "(a+b)/(c+d)", r"\frac{a+b}{c+d}"),
    ("mathematics", "1/(1 + 1/x)", r"\frac{1}{1+\frac{1}{x}}"),
    ("mathematics", "(x^2 - 1)/(x - 1)", r"\frac{x^{2}-1}{x-1}"),
    ("mathematics", "dy/dx = 2x", r"\frac{dy}{dx}=2x"),
    ("mathematics", "∂f/∂x", r"\frac{\partial f}{\partial x}"),
    ("mathematics", "sin x/x", "REVIEW"),                                 # sin(x/x) or (sin x)/x
    ("mathematics", "(sin x)/x", r"\frac{\sin x}{x}"),
    # roots
    ("mathematics", "√(x+1)", r"\sqrt{x+1}"),
    ("mathematics", "∛8 = 2", r"\sqrt[3]{8}=2"),
    ("mathematics", "√(1 + √x)", r"\sqrt{1+\sqrt{x}}"),
    ("mathematics", "√((a+b)/c)", r"\sqrt{\frac{a+b}{c}}"),
    # limits
    ("mathematics", "lim_(x→0) (sin x)/x = 1", r"\lim_{x\to 0}\frac{\sin x}{x}=1"),
    ("mathematics", "lim_(x→∞) 1/x = 0", r"\lim_{x\to\infty}\frac{1}{x}=0"),
    ("mathematics", "lim_(h→0) (f(x+h) − f(x))/h", r"\lim_{h\to 0}\frac{f(x+h)-f(x)}{h}"),
    # calculus
    ("mathematics", "∫₀^π sin x dx = 2", r"\int_{0}^{\pi}\sin x\,dx=2"),
    ("mathematics", "∫ x² dx", r"\int x^{2}\,dx"),
    ("mathematics", "∑_(i=1)^n i = n(n+1)/2", r"\sum_{i=1}^{n}i=\frac{n(n+1)}{2}"),
    ("mathematics", "∏_(k=1)^n k = n!", r"\prod_{k=1}^{n}k=n!"),
    ("mathematics", "d²y/dx² + y = 0", r"\frac{d^{2}y}{dx^{2}}+y=0"),
    ("physics", "∇ · E = ρ/ε₀", r"\nabla\cdot E=\frac{\rho}{\varepsilon_{0}}"),
    # notation
    ("mathematics", "α + β = γ", r"\alpha+\beta=\gamma"),
    ("mathematics", "|x − 2| < 3", r"\left|x-2\right|<3"),
    ("mathematics", "x ≤ y ≥ z", r"x\le y\ge z"),
    ("mathematics", "A ∪ B ⊆ C", r"A\cup B\subseteq C"),
    ("mathematics", "log₂ 8 = 3", r"\log_{2}8=3"),
    ("mathematics", "sin²θ + cos²θ = 1", r"\sin^{2}\theta+\cos^{2}\theta=1"),
    ("mathematics", "z = a + ib", "z=a+ib"),
    ("mathematics", "x ∈ ℝ", r"x\in\mathbb{R}"),
    ("mathematics", "C(n, r) = n!/(r!(n−r)!)", r"C(n,r)=\frac{n!}{r!(n-r)!}"),
    ("mathematics", "[1 2; 3 4]", r"\begin{bmatrix}1&2\\3&4\end{bmatrix}"),
    # unicode mathematical alphabets (Word for the web, pasted text)
    ("physics", "𝐅 = m𝐚", r"\mathbf{F}=m\mathbf{a}"),
    ("physics", "𝑭 = m𝒂", r"\boldsymbol{F}=m\boldsymbol{a}"),
    ("mathematics", "𝑓(𝑥) = 𝑥³", "f(x)=x^{3}"),
    ("physics", "ℎ𝜈 = E", r"h\nu=E"),
    # physics
    ("physics", "v = u + at", "v=u+at"),
    ("physics", "v = at.", "v=at"),
    ("physics", "By v = u + at, after t = 2 s", None),                 # the Review showed "+ at," as text before the comma fix
    ("physics", "s = ut + ½at²", r"s=ut+\frac{1}{2}at^{2}"),
    ("physics", "F = G m₁m₂/r²", r"F=\frac{Gm_{1}m_{2}}{r^{2}}"),       # G·m₁·m₂/r² (left to right), the same quantity
    ("physics", "E = mc²", "E=mc^{2}"),
    ("physics", "g = 9.8 m/s²", r"g=9.8\,\mathrm{m}/\mathrm{s}^{2}"),
    ("physics", "p = 5 kg m s⁻¹", r"p=5\,\mathrm{kg}\,\mathrm{m}\,\mathrm{s}^{-1}"),
    ("physics", "λ = h/p", r"\lambda=\frac{h}{p}"),
    ("physics", "ω = 2πf", r"\omega=2\pi f"),
    ("physics", "μ₀ = 4π × 10⁻⁷ T m A⁻¹", r"\mu_{0}=4\pi\times10^{-7}\,\mathrm{T}\,\mathrm{m}\,\mathrm{A}^{-1}"),
    ("physics", "[M L² T⁻²]", r"[\mathrm{M}\,\mathrm{L}^{2}\,\mathrm{T}^{-2}]"),
    ("physics", "W = 10 N·m", r"W=10\,\mathrm{N}\cdot\mathrm{m}"),          # supported since the final assignment (was: review)
    # chemistry
    ("chemistry", "Ca(OH)₂", r"\mathrm{Ca(OH)_{2}}"),
    ("chemistry", "NH₄⁺", r"\mathrm{NH_{4}^{+}}"),
    ("chemistry", "SO₄²⁻", r"\mathrm{SO_{4}^{2-}}"),
    ("chemistry", "Fe³⁺", r"\mathrm{Fe^{3+}}"),
    ("chemistry", "¹⁴C", r"{}^{14}\mathrm{C}"),
    ("chemistry", "CH₃COOH", r"\mathrm{CH_{3}COOH}"),
    ("chemistry", "2H₂ + O₂ → 2H₂O", r"2\mathrm{H_{2}}+\mathrm{O_{2}}\rightarrow 2\mathrm{H_{2}O}"),
    ("chemistry", "N₂ + 3H₂ ⇌ 2NH₃", r"\mathrm{N_{2}}+3\mathrm{H_{2}}\rightleftharpoons 2\mathrm{NH_{3}}"),
    ("chemistry", "1s² 2s² 2p⁶", r"\mathrm{1s}^{2}\,\mathrm{2s}^{2}\,\mathrm{2p}^{6}"),   # supported since the final assignment
    # OCR-like ambiguity: never silently a different equation
    ("mathematics", "x2 + 1 = 5", "REVIEW"),
    ("mathematics", "l0 = 1O", "REVIEW"),
    # prose stays prose
    ("physics", "The car moves at 5 m/s at noon.", None),
    ("mathematics", "If x + at most 3 apples", None),
]


def canon(latex: str):
    return _normal(read_latex(latex))


@pytest.mark.parametrize("subject, text, want", CASES, ids=[c[1] for c in CASES])
def test_recognition_coverage(subject, text, want):
    r = recognise(text, subject=subject)
    converted = [s.latex for s in r.spans if s.status == "converted"]
    if want == "REVIEW":
        assert r.outcome != "CONVERTED", (r.outcome, converted)
    elif want is None and text.startswith("By v"):                      # the whole formula, "+ at" included, converts
        assert any(canon(x) == canon("v=u+at") for x in converted), converted
    elif want is None:                                                   # prose: no formula invented from English words
        assert not any(" at" in (x or "") or x in ("at", "a t") for x in converted), converted
    else:
        assert r.outcome == "CONVERTED" and len(converted) == 1, (r.outcome, converted, [s.code for s in r.spans])
        assert canon(converted[0]) == canon(want), converted[0]


# ------------------------------------------------------------------ where limit conditions and bounds are shown (also inside a sentence)
from xml.etree import ElementTree as ET  # noqa: E402

from inst_paper.services.math.omml import omml_to_latex  # noqa: E402

M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"


@pytest.mark.parametrize("text, shown", [
    ("lim_(x→0) (sin x)/x = 1", r"\lim\limits_{"),          # the condition under "lim"
    ("∑_(i=1)^n i = n(n+1)/2", r"\sum\limits_{"),             # bounds above and below the sum
    ("∏_(k=1)^n k = n!", r"\prod\limits_{"),
    ("∫₀^π sin x dx = 2", r"\int_{"),                         # an integral keeps its bounds at the side
], ids=["lim", "sum", "prod", "int"])
def test_typed_limits_and_bounds_are_placed_as_in_print(text, shown):
    latex = [s.latex for s in recognise(text, subject="mathematics").spans if s.status == "converted"][0]
    assert latex.replace(" ", "").startswith(shown), latex


def _nary(ch: str, loc: str | None) -> ET.Element:
    pr = f'<m:naryPr><m:chr m:val="{ch}"/>' + (f'<m:limLoc m:val="{loc}"/>' if loc else "") + "</m:naryPr>"
    return ET.fromstring(f'<m:oMath xmlns:m="{M_NS}"><m:nary>{pr}<m:sub><m:r><m:t>i=1</m:t></m:r></m:sub>'
                         f'<m:sup><m:r><m:t>n</m:t></m:r></m:sup><m:e><m:r><m:t>i</m:t></m:r></m:e></m:nary></m:oMath>')


@pytest.mark.parametrize("ch, loc, limits", [("∑", None, True), ("∑", "undOvr", True), ("∑", "subSup", False), ("∫", None, False),
                                              ("∫", "undOvr", True)], ids=["sum-default", "sum-under", "sum-side", "int-default", "int-under"])
def test_word_equation_bounds_follow_the_files_own_placement(ch, loc, limits):
    assert (r"\limits" in omml_to_latex(_nary(ch, loc))) is limits


# ------------------------------------------------------------------ final assignment: floor/ceiling, dotted units, orbitals, options
FINAL = [
    ("mathematics", "⌊x⌋ + ⌈y⌉", r"\lfloor x\rfloor+\lceil y\rceil"),
    ("mathematics", "⌊2.7⌋ = 2", r"\lfloor 2.7\rfloor=2"),
    ("mathematics", "⌈x/2⌉ + ⌊⌊x⌋/3⌋", r"\lceil\frac{x}{2}\rceil+\lfloor\frac{\lfloor x\rfloor}{3}\rfloor"),   # nested
    ("mathematics", "Find ⌊√10⌋.", r"\lfloor\sqrt{10}\rfloor"),
    ("mathematics", "⌊x⌉ = 1", "REVIEW"),                                # mismatched brackets: never guessed
    ("physics", "p = 6 kg·m/s", r"p=6\,\mathrm{kg}\cdot\mathrm{m}/\mathrm{s}"),   # was converted as kg·(m/s) with VARIABLES m, s
    ("physics", "The unit kg·m/s² is the newton.", r"\mathrm{kg}\cdot\mathrm{m}/\mathrm{s}^{2}"),
    ("physics", "5 J·s", r"5\,\mathrm{J}\cdot\mathrm{s}"),
    ("physics", "x·y = 3", r"x\cdot y=3"),                               # variables stay variables
    ("physics", "Here m·g = 10.", r"m\cdot g=10"),
    ("chemistry", "The configuration of Na is 1s² 2s² 2p⁶ 3s¹.", r"\mathrm{1s}^{2}\,\mathrm{2s}^{2}\,\mathrm{2p}^{6}\,\mathrm{3s}^{1}"),
    ("chemistry", "[Ar] 3d¹⁰ 4s²", r"[\mathrm{Ar}]\,\mathrm{3d}^{10}\,\mathrm{4s}^{2}"),
    ("chemistry", "sp³", r"\mathrm{sp}^{3}"),                             # was converted as the product s·p³
    ("chemistry", "dsp²", r"\mathrm{dsp}^{2}"),
    ("chemistry", "sp³d²", r"\mathrm{sp}^{3}\mathrm{d}^{2}"),
    ("chemistry", "d²sp³", r"\mathrm{d}^{2}\mathrm{sp}^{3}"),
    ("chemistry", "3d orbitals", r"\mathrm{3d}"),
    ("chemistry", "Here 2s = 4.", "REVIEW"),                             # orbital 2s or 2·s: the two readings disagree
    ("mathematics", "sp³", "sp^{3}"),                                     # outside Chemistry: ordinary variables
]


@pytest.mark.parametrize("subject, text, want", FINAL, ids=[c[1] for c in FINAL])
def test_final_assignment_notation(subject, text, want):
    test_recognition_coverage(subject, text, want)


OPTIONS = [("h/p", r"\frac{h}{p}"), ("qB/v", r"\frac{qB}{v}"), ("1/r", r"\frac{1}{r}"), ("2GM/R", r"\frac{2GM}{R}"), ("F/4", r"\frac{F}{4}"),
           ("2n", "2n"), ("kq/r", r"\frac{kq}{r}"), ("1/T", r"\frac{1}{T}"), ("3x² + 2", "3x^{2}+2"),
           ("and/or", None), ("N/A", None), ("A/B", None), ("None of these", None), ("a/b/c", "REVIEW")]


@pytest.mark.parametrize("text, want", OPTIONS, ids=[o[0] for o in OPTIONS])
def test_an_answer_option_that_is_exactly_one_formula(text, want):
    r = recognise(text, subject="physics", formula_field=True)
    converted = [s.latex for s in r.spans if s.status == "converted"]
    if want is None:
        assert r.outcome == "PLAIN_TEXT" and r.text == text, (r.outcome, r.text)        # words and N/A stay as typed
    elif want == "REVIEW":
        assert r.outcome == "NEEDS_REVIEW"
    else:
        assert r.outcome == "CONVERTED" and len(converted) == 1 and canon(converted[0]) == canon(want), (r.outcome, converted)
    if want not in (None, "REVIEW") and "²" not in text:                   # weak forms (h/p, 2n): a question stem stays unchanged
        assert recognise(text, subject="physics").outcome == "PLAIN_TEXT"


# ------------------------------------------------------------------ lines, split scripts, the reviewer's readings
def test_a_line_break_ends_every_formula():
    # was: "x ≥ 0" + line break + "f(x)" read as x ≥ 0·f(x) - a silent change of meaning
    r = recognise("f(x) = x² for x ≥ 0\nf(x) = −x for x < 0", subject="mathematics")
    assert r.outcome == "CONVERTED" and r.text.split("\n")[0].endswith(r"\(x \geq 0\)"), r.text
    assert [canon(s.latex) for s in r.spans if s.status == "converted"] == [canon(x) for x in ("f(x)=x^{2}", r"x\geq 0", "f(x)=-x", "x<0")]
    r = recognise("Solve:\nx + y = 5\n2x − y = 1", subject="mathematics")
    assert r.text == "Solve:\n\\(x + y = 5\\)\n\\(2x - y = 1\\)", r.text                   # one system, each equation on its line


def test_a_split_script_is_explained_as_such():
    from inst_paper.services.math import markers
    r = recognise("A force of 5 kg m s⁻" + markers.SPLIT_TEXT + "² acts.", subject="physics")
    assert r.outcome == "NEEDS_REVIEW" and "separately formatted" in r.spans[0].reason, r.spans[0].reason


@pytest.mark.parametrize("text, field, units, variables", [
    ("m² s", "option_c", r"\mathrm{m}^{2}\,\mathrm{s}", "m^{2} s"),
    ("N m²", "option_a", r"\mathrm{N}\,\mathrm{m}^{2}", "N m^{2}"),
])
def test_a_unit_or_variable_expression_offers_both_checked_readings(text, field, units, variables):
    from inst_paper.services.recognition import _field
    f = _field(text, "physics", field)
    assert f["outcome"] == "NEEDS_REVIEW" and "AMBIGUOUS_UNIT" in f["reasons"]
    assert [s["text"] for s in f["suggestions"]] == [f"\\({units}\\)", f"\\({variables}\\)"]
    for s in f["suggestions"]:                                          # each reading is itself accepted when chosen
        assert _field(s["text"], "physics", field)["outcome"] == "CONVERTED", s


def test_a_power_in_doubt_offers_its_readings_but_the_choice_stays_the_reviewers():
    # the policy changed (source-independent recognition): plausible readings are shown typeset, never applied - the field still
    # needs a decision, and the chosen reading is checked again like any correction
    from inst_paper.services.recognition import _field
    f = _field("Compute e^3t when t = 0.", "mathematics", "question")
    assert f["outcome"] == "NEEDS_REVIEW" and f["text"] == r"Compute e^3t when \(t = 0\)."          # the doubt left as typed
    assert [s["text"] for s in f["suggestions"]] == [r"Compute \(e^{3}t\) when \(t = 0\).", r"Compute \(e^{3t}\) when \(t = 0\)."]
    for s in f["suggestions"]:
        assert _field(s["text"], "mathematics", "question")["outcome"] == "CONVERTED"


@pytest.mark.parametrize("text, subject, want", [
    # reported 2026-11-13: the readings moved the fraction out of the root (√(RT)/(3M)) - a change of structure, never a reading
    ("v = √(RT/3M)", "physics", [r"\(v = \sqrt{\frac{RT}{3M}}\)", r"\(v = \sqrt{(\frac{RT}{3})M}\)"]),
    ("y = f(x/2y) + 1", "mathematics", [r"\(y = f(\frac{x}{2y}) + 1\)", r"\(y = f((\frac{x}{2})y) + 1\)"]),
])
def test_a_denominator_in_doubt_inside_a_bracket_keeps_the_bracket(text, subject, want):
    from inst_paper.services.recognition import _field
    f = _field(text, subject, "question")
    assert f["outcome"] == "NEEDS_REVIEW" and [s["text"] for s in f["suggestions"]] == want, f["suggestions"]


@pytest.mark.parametrize("src", ["√(RT/3M)", "f(x/2y) + 1", "[a + x/2y]", "(1/2x)", "√(2gh/3m) = v", "a + (b/2c − d)"])
def test_a_reading_only_rewrites_inside_the_innermost_bracket_around_the_slash(src):
    from inst_paper.services.math.suggest import alternatives
    opener = max(src.rfind("(", 0, src.index("/")), src.rfind("[", 0, src.index("/")))
    closer = src.index(")" if src[opener] == "(" else "]", src.index("/"))
    readings = alternatives(src, 0, len(src), "AMBIGUOUS_DENOMINATOR", lambda t: t)
    assert readings
    for _, candidate in readings:                                       # the text before and after the group is untouched
        assert candidate.startswith(src[:opener + 1]) and candidate.endswith(src[closer:]), candidate


@pytest.mark.parametrize("text, flagged", [
    ("Differentiate e13x with respect to x.", "e13x"), ("e13x", "e13x"), ("Find e2x.", "e2x"),   # e¹³x or e¹³ˣ copied flat
    ("See page e12", None), ("Vitamin B12 is needed.", None),                                   # labels: no letter after the number
])
def test_e_with_a_number_and_a_letter_is_flagged_never_left_as_plain_text(text, flagged):
    r = recognise(text, subject="mathematics")
    assert [s.original for s in r.flags] == ([flagged] if flagged else []), (r.outcome, r.spans)


# Reported acceptance cases (2026-11-13): every doubt offers its readings typeset, each passing the full engine when chosen.
READINGS = [
    ("e13x", "mathematics", "option_a", [r"\(e^{13}x\)", r"\(e_{13}x\)", r"\(e \cdot 13x\)", r"\(e^{13x}\)"]),
    ("∫₀π sin x dx", "mathematics", "question", [r"\(\int_{0}^{\pi} \sin x\,dx\)"]),
    ("∫ᵃb f(x) dx", "mathematics", "question", [r"\(\int_{b}^{a} f(x)\,dx\)"]),
    ("limₓ→∞x", "mathematics", "question", [r"\(\lim\limits_{x \to \infty} x\)"]),
    ("A = |1 -2; 3 4|", "mathematics", "question", [r"\(A = \begin{vmatrix}1 & -2 \\ 3 & 4\end{vmatrix}\)"]),
    ("Fe3+", "chemistry", "option_a", [r"\(\mathrm{Fe^{3+}}\)", r"\(\mathrm{Fe_{3}^{+}}\)"]),
    ("SO42-", "chemistry", "option_b", [r"\(\mathrm{SO_{4}^{2-}}\)", r"\(\mathrm{SO_{42}^{-}}\)"]),
    ("Cr2O72- ion", "chemistry", "question", [r"\(\mathrm{Cr_{2}O_{7}^{2-}}\) ion", r"\(\mathrm{Cr_{2}O_{72}^{-}}\) ion"]),
    ("a = 9.8 ms-2", "physics", "question", [r"\(a = 9.8\,\mathrm{m}\,\mathrm{s}^{-2}\)"]),
]


@pytest.mark.parametrize("text, subject, field, want", READINGS, ids=[c[0] for c in READINGS])
def test_each_reported_doubt_offers_its_typeset_readings(text, subject, field, want):
    from inst_paper.services.recognition import _field
    f = _field(text, subject, field)
    assert f["outcome"] == "NEEDS_REVIEW" and [s["text"] for s in f["suggestions"]] == want, f["suggestions"]
    for s in f["suggestions"]:                                          # the choice is checked again like any correction
        assert _field(s["text"], subject, field)["outcome"] == "CONVERTED", s


@pytest.mark.parametrize("text, subject, field", [(c[0], c[1], c[2]) for c in READINGS] + [("y = e13x", "mathematics", "question"),
                         ("√(RT/3M)", "physics", "option_b")])
def test_a_flag_says_where_it_is_and_explains_itself_without_codes(text, subject, field):
    from inst_paper.services.recognition import EXPLANATION_DEFAULT, _field
    f = _field(text, subject, field)
    flags = [s for s in f["spans"] if s["status"] in ("flagged", "error")]
    assert flags
    for s in flags:
        assert f["original"][s["start"]:s["end"]] == s["original"]       # the position of the doubt in the text shown
        assert s["explanation"] != EXPLANATION_DEFAULT and "_" not in s["explanation"] and s["code"] not in s["explanation"]
    assert all(s["explanation"] is None for s in f["spans"] if s["status"] not in ("flagged", "error"))


def test_a_span_position_counts_extraction_markers_as_shown():
    from inst_paper.services.math import markers
    from inst_paper.services.recognition import _field
    f = _field("See " + markers.make(markers.OBJECT, "image") + " then e13x.", "mathematics", "question")
    for s in f["spans"]:
        assert f["original"][s["start"]:s["end"]] == s["original"], s


# ------------------------------------------------------------------ realistic combinations (final assignment, Part 9)
COMBINATIONS = [
    ("mathematics", "(x²+1)/(1 + 1/x²)", r"\frac{x^{2}+1}{1+\frac{1}{x^{2}}}"),                 # nested fraction with exponents
    ("mathematics", "√((x+1)/(x−1))", r"\sqrt{\frac{x+1}{x-1}}"),                                # root containing a fraction
    ("mathematics", "[1 2; 3 4]/2", r"\frac{\begin{bmatrix}1&2\\3&4\end{bmatrix}}{2}"),        # fraction containing a matrix
    ("mathematics", "lim_(x→0) (1 − cos x)/x²", r"\lim\limits_{x\to 0}\frac{1-\cos x}{x^{2}}"),  # limit, fraction, trigonometry
    ("mathematics", "∫₁^e (ln x)/x dx", r"\int_{1}^{e}\frac{\ln x}{x}\,dx"),                     # integral with both bounds
    ("mathematics", "∑_(k=0)^(n−1) 2^k = 2^n − 1", r"\sum\limits_{k=0}^{n-1}2^{k}=2^{n}-1"),      # sum with expression bounds
    ("physics", "ω₀² = k/m", r"\omega_{0}^{2}=\frac{k}{m}"),                                     # Greek with sub- and superscript
    ("physics", "𝐅₁ + 𝐅₂ = 0", r"\mathbf{F}_{1}+\mathbf{F}_{2}=0"),                              # bold vectors with subscripts
    ("chemistry", "Fe²⁺ → Fe³⁺ + e⁻", r"\mathrm{Fe^{2+}}\rightarrow\mathrm{Fe^{3+}}+\mathrm{e^{-}}"),   # ionic charges, reaction
    ("chemistry", "Cr₂O₇²⁻ + 14H⁺", r"\mathrm{Cr_{2}O_{7}^{2-}}+14\mathrm{H^{+}}"),
    ("chemistry", "CuSO₄·5H₂O", r"\mathrm{CuSO_{4}\cdot 5H_{2}O}"),
    ("chemistry", "CaCO₃(s) → CaO(s) + CO₂(g)", r"\mathrm{CaCO_{3}}\mathrm{(s)}\rightarrow\mathrm{CaO}\mathrm{(s)}+\mathrm{CO_{2}}\mathrm{(g)}"),
    ("mathematics", "−(−3) + (−(2 − 5)) = 6", "-(-3)+(-(2-5))=6"),                               # negatives, nested brackets
    ("mathematics", "[(2 + 3) × (4 − 1)] ÷ 5", r"[(2+3)\times(4-1)]\div 5"),
    ("physics", "c = 3.0 × 10⁸ m/s", r"c=3.0\times10^{8}\,\mathrm{m}/\mathrm{s}"),                # decimals, scientific notation
    ("physics", "1.6 × 10⁻¹⁹ C", r"1.6\times10^{-19}\,\mathrm{C}"),
    ("mathematics", "x ∈ (−∞, 2] ∪ [5, ∞)", r"x\in(-\infty,2]\cup[5,\infty)"),                   # intervals and sets
    ("mathematics", "d²y/dx² − 3 dy/dx + 2y = 0", r"\frac{d^{2}y}{dx^{2}}-3\frac{dy}{dx}+2y=0"),  # differential equation
    ("mathematics", "∂²u/∂x² = ∂u/∂t", r"\frac{\partial^{2}u}{\partial x^{2}}=\frac{\partial u}{\partial t}"),
    ("physics", "E = ½ m v²", r"E=\frac{1}{2}mv^{2}"),
    ("physics", "F = q(𝐯 × 𝐁)", r"F=q(\mathbf{v}\times\mathbf{B})"),
    ("mathematics", "∮ 𝐁 · d𝐥 = μ₀ I", r"\oint\mathbf{B}\cdot d\mathbf{l}=\mu_{0}I"),
    # OCR look-alikes and malformed input: never a different equation
    ("mathematics", "x = 0O", "REVIEW"), ("mathematics", "1l + 2 = 3", "REVIEW"), ("mathematics", "3 x 4 = 12", "REVIEW"),
    ("mathematics", "(x + 1", None), ("mathematics", "", None), ("chemistry", "pH = −log[H⁺]", r"\mathrm{pH}=-\log[\mathrm{H^{+}}]"),   # 2026-11-13: [H⁺] is a concentration
]


@pytest.mark.parametrize("subject, text, want", COMBINATIONS, ids=[c[1] or "empty" for c in COMBINATIONS])
def test_realistic_combinations(subject, text, want):
    r = recognise(text, subject=subject)
    if want is None:
        assert r.outcome == "PLAIN_TEXT" and r.text == text                   # kept exactly as typed, nothing invented
    else:
        test_recognition_coverage(subject, text, want)


def test_repeated_expression_with_different_formatting_reads_the_same():
    a = [s.latex for s in recognise("x² and x^2", subject="mathematics").spans if s.status == "converted"]
    assert len(a) == 2 and canon(a[0]) == canon(a[1])                                   # Unicode and caret: one meaning


def test_no_reading_is_offered_when_another_doubt_would_remain():
    # W3 of the genuine Excel workbook: a unit doubt AND a charge doubt; a unit reading alone could not resolve the field
    from inst_paper.services.math import markers
    from inst_paper.services.recognition import _field
    f = _field("Units [M L² T⁻" + markers.SPLIT_TEXT + "²] here.", "chemistry", "question")     # as the workbook stores it
    assert f["outcome"] == "NEEDS_REVIEW" and f["suggestions"] == [], f["suggestions"]


@pytest.mark.parametrize("subject, text, option, want", [
    ("mathematics", "ab − cd", True, "ab-cd"),                                  # cd: the product, not the candela (no number)
    ("physics", "By v² = u² + 2as, v equals", False, "v^{2}=u^{2}+2as"),          # 2as: the product a·s, not the word "as"
    ("physics", "mv/(qB)", True, r"\frac{mv}{qB}"),                              # qB: the product q·B, not a word
    ("physics", "E = kT", False, "E=kT"),
    ("physics", "I = 5 mA", False, r"I=5\,\mathrm{mA}"),                          # mA stays the milliampere
], ids=["cd", "2as", "qB", "kT", "mA"])
def test_products_that_look_like_words_or_units(subject, text, option, want):
    r = recognise(text, subject=subject, formula_field=option)
    converted = [s.latex for s in r.spans if s.status == "converted"]
    assert r.outcome == "CONVERTED" and canon(converted[0]) == canon(want), (r.outcome, converted)


@pytest.mark.parametrize("text", ["He sat at 5 am.", "The pH is 7.", "In this case x = 2."])
def test_prose_with_short_words_stays_prose(text):
    r = recognise(text, subject="physics")
    assert not any("am" in (s.latex or "") or "In" in (s.latex or "") for s in r.spans), r.text


@pytest.mark.parametrize("text, option, want", [
    ("x/ln x+C", True, r"\(\frac{x}{\ln x} + C\)"),                       # a function in a denominator takes its argument
    ("x/sin x", True, r"\(\frac{x}{\sin x}\)"),
    ("xe^x+C", True, r"\(xe^{x} + C\)"),                                   # the constant of integration is never in the power
    ("By parts: xe^x−∫e^x dx.", False, r"By parts: \(xe^{x} - \int e^{x}\,dx\)."),   # nor is an integral
    (r"\int \frac{\ln x}{x}\,dx", False, r"\(\int \frac{\ln x}{x}\,dx\)"),  # a whole field of LaTeX typed without delimiters
    (r"\frac{1}{2}(\ln x)^2+C", True, r"\(\frac{1}{2}(\ln x)^2+C\)"),
])
def test_integration_answers_are_typeset(text, option, want):
    r = recognise(text, subject="mathematics", formula_field=option)
    assert r.outcome == "CONVERTED" and r.text == want, (r.outcome, r.text, r.reasons)


@pytest.mark.parametrize("text, code", [
    ("e^x+1", "AMBIGUOUS_POWER"), ("e^x+C+1", "AMBIGUOUS_POWER"), ("e^x+Cx", ""),   # still never guessed
    (r"Set u = \ln x", "LATEX_WITHOUT_DELIMITERS"),                       # prose among commands is not wrapped
    (r"\href{http://a}{x}", "LATEX_WITHOUT_DELIMITERS"), (r"\frac{1}{", "LATEX_WITHOUT_DELIMITERS"),
])
def test_integration_doubts_stay_flagged(text, code):
    r = recognise(text, subject="mathematics", formula_field=True)
    assert r.outcome == "NEEDS_REVIEW" and (not code or code in r.reasons) and r.text == text, (r.outcome, r.text, r.reasons)


DERIVATIVES = [
    ("d(5x⁴−3x²+7)/dx", r"\frac{d(5x^{4} - 3x^{2} + 7)}{dx}"),          # the derivative of a bracketed expression
    ("d(f(x))/dx", r"\frac{d(f(x))}{dx}"),
    ("d[sin(3x)]/dx", r"\frac{d[\sin(3x)]}{dx}"),
    ("d(e^x)/dx", r"\frac{d(e^{x})}{dx}"),
    ("d[e^(2x)]/dx", r"\frac{d[e^{2x}]}{dx}"),
    ("d[(x²+1)/(x−1)]/dx", r"\frac{d[\frac{x^{2} + 1}{x - 1}]}{dx}"),   # a fraction inside the bracket stays inside it
    ("d[tan⁻¹(2x)]/dx", r"\frac{d[\tan^{-1}(2x)]}{dx}"),
    ("d²(x⁴)/dx²", r"\frac{d^{2}(x^{4})}{dx^{2}}"),
    ("dy/dx", r"\frac{dy}{dx}"),
    ("d²y/dx²", r"\frac{d^{2}y}{dx^{2}}"),
    ("d³y/dx³", r"\frac{d^{3}y}{dx^{3}}"),
    ("∂f/∂x", r"\frac{\partial f}{\partial x}"),
    ("∂²f/∂x∂y", r"\frac{\partial^{2} f}{\partial x \partial y}"),     # a mixed partial: order 2 over two differentials
    ("d/dx(x²+1)", r"\frac{d}{dx}(x^{2} + 1)"),                         # the operator applies to the bracket after it
    ("d²/dx²(x⁴)", r"\frac{d^{2}}{dx^{2}}(x^{4})"),
    ("∂/∂x(x²y)", r"\frac{\partial}{\partial x}(x^{2}y)"),
    ("(x²−2x−1)/(x−1)²", r"\frac{x^{2} - 2x - 1}{(x - 1)^{2}}"),        # a power on a bracketed denominator is its own
    ("cos x/sin x", r"\frac{\cos x}{\sin x}"),                          # a function over a function
    ("x/ln x", r"\frac{x}{\ln x}"), ("1/sin θ", r"\frac{1}{\sin \theta}"),
    ("(x²+1)/(x−1)", r"\frac{x^{2} + 1}{x - 1}"), ("a/b+c/d", r"\frac{a}{b} + \frac{c}{d}"),
]


@pytest.mark.parametrize("text, latex", DERIVATIVES, ids=[d[0] for d in DERIVATIVES])
@pytest.mark.parametrize("field", ["question", "option", "explanation"])
def test_derivative_notation_is_typeset_in_every_field(text, latex, field):
    whole = {"question": f"Find {text}.", "option": text, "explanation": f"Here {text} is used."}[field]
    r = recognise(whole, subject="mathematics", formula_field=field == "option")
    assert r.outcome == "CONVERTED" and "\\(" + latex + "\\)" in r.text, (r.outcome, r.text, [s.reason for s in r.flags])


@pytest.mark.parametrize("text, code", [
    ("sin x/x", "AMBIGUOUS_FRACTION"), ("ln x/x", "AMBIGUOUS_FRACTION"), ("tan x/x", "AMBIGUOUS_FRACTION"),   # sin(x/x) or (sin x)/x
    ("1/2x", "AMBIGUOUS_DENOMINATOR"), ("(a+b)/c/d", "AMBIGUOUS_FRACTION"), ("∫ln x dx/x", "AMBIGUOUS_FRACTION"),
    ("d(x)/dy²", "AMBIGUOUS_DERIVATIVE"), ("d²(x)/dx", "AMBIGUOUS_DERIVATIVE"), ("d²y/dx", "AMBIGUOUS_DERIVATIVE"),
    ("∂³f/∂x∂y", "AMBIGUOUS_DERIVATIVE"),                                      # order 3 over two differentials
    ("dy/dx(x²)", "NOT_INDEPENDENTLY_VERIFIED"),                               # a product or an evaluation: never guessed
])
def test_genuinely_ambiguous_fractions_and_derivatives_stay_flagged(text, code):
    for option in (True, False):
        r = recognise(text, subject="mathematics", formula_field=option)
        assert r.outcome == "NEEDS_REVIEW" and code in r.reasons and r.text == text, (r.outcome, r.text, r.reasons)


MATRICES = [
    ("[[1,2],[3,4]]", r"\begin{bmatrix}1 & 2 \\ 3 & 4\end{bmatrix}"),                 # 2×2 integers: no operator inside
    ("[[1,0],[0,2]]", r"\begin{bmatrix}1 & 0 \\ 0 & 2\end{bmatrix}"),
    ("[[0,-1],[2,0]]", r"\begin{bmatrix}0 & -1 \\ 2 & 0\end{bmatrix}"),                # zero and negative values
    ("[[0,−1],[2,0]]", r"\begin{bmatrix}0 & -1 \\ 2 & 0\end{bmatrix}"),
    ("[[1,0],[0,1/2]]", r"\begin{bmatrix}1 & 0 \\ 0 & \frac{1}{2}\end{bmatrix}"),      # a fraction inside
    ("[[a,b],[c,d]]", r"\begin{bmatrix}a & b \\ c & d\end{bmatrix}"),                 # variables
    ("[[x, y], [z, w]]", r"\begin{bmatrix}x & y \\ z & w\end{bmatrix}"),
    ("[[1,2,3],[4,5,6],[7,8,9]]", r"\begin{bmatrix}1 & 2 & 3 \\ 4 & 5 & 6 \\ 7 & 8 & 9\end{bmatrix}"),
    ("[[1,2,3]]", r"\begin{bmatrix}1 & 2 & 3\end{bmatrix}"),                          # row vector
    ("[[1],[2],[3]]", r"\begin{bmatrix}1 \\ 2 \\ 3\end{bmatrix}"),                    # column vector
    ("[1;2;3]", r"\begin{bmatrix}1 \\ 2 \\ 3\end{bmatrix}"),
    ("[1 2; 3 4]", r"\begin{bmatrix}1 & 2 \\ 3 & 4\end{bmatrix}"),
    ("|a b; c d|", r"\begin{vmatrix}a & b \\ c & d\end{vmatrix}"),                    # determinant
    ("det([[2,1],[3,4]])", r"\det(\begin{bmatrix}2 & 1 \\ 3 & 4\end{bmatrix})"),
]


@pytest.mark.parametrize("text, latex", MATRICES, ids=[m[0] for m in MATRICES])
def test_a_matrix_is_typeset_identically_in_every_field(text, latex):
    """The same matrix source gives the same LaTeX as a whole option, inside a question sentence and inside an explanation."""
    contexts = {"option": (text, True), "question": (f"Find the inverse of {text}.", False),
                "explanation": (f"Here A = {text} is used.", False), "sentence": (f"If A = {text}, find its trace.", False)}
    found = {}
    for name, (whole, option) in contexts.items():
        r = recognise(whole, subject="mathematics", formula_field=option)
        assert r.outcome == "CONVERTED", (name, r.outcome, r.text, r.reasons)
        found[name] = [s.latex for s in r.spans if s.status == "converted"]
    assert found["option"] == [latex] and found["question"] == [latex], found
    assert found["explanation"] == ["A = " + latex] and found["sentence"] == ["A = " + latex], found
    assert recognise(f"Find the inverse of {text}.", subject="mathematics").text == rf"Find the inverse of \({latex}\)."


def test_sums_of_matrices_and_the_trace_operator():
    r = recognise("[[1,2],[3,4]] + [[1,0],[0,1]]", subject="mathematics", formula_field=True)
    assert r.text == r"\(\begin{bmatrix}1 & 2 \\ 3 & 4\end{bmatrix} + \begin{bmatrix}1 & 0 \\ 0 & 1\end{bmatrix}\)", r.text
    r = recognise("λ²−tr(A)λ+det(A)", subject="mathematics", formula_field=True)
    assert r.text == r"\(\lambda^{2} - \operatorname{tr}(A)\lambda + \det(A)\)", r.text
    assert recognise(r"\(\tr A\)", subject="mathematics").reasons == ["LATEX_VALIDATION_FAILED"]      # \tr is not a KaTeX command


@pytest.mark.parametrize("text, code", [
    ("[[1,2],[3]]", "AMBIGUOUS_MATRIX"),                  # rows of different lengths: never guessed
    ("[[1,2],[3,4]", "UNSUPPORTED_NOTATION"),             # a bracket not closed: flagged, not left as silent text
])
@pytest.mark.parametrize("context", ["{}", "Find the inverse of {}.", "Here A = {} is used."])
def test_malformed_matrices_are_flagged_for_review(text, code, context):
    r = recognise(context.format(text), subject="mathematics", formula_field=context == "{}")
    assert r.outcome == "NEEDS_REVIEW" and code in r.reasons, (r.outcome, r.text, r.reasons)


@pytest.mark.parametrize("text", ["[x]", "[1, 2]", "[a, b]", "[0, 1]", "(1, 2)", "[[x]]", "[[1]]", "See [1], [2] and [3].",
                                  "Options [A] and [B]."])
def test_ordinary_brackets_and_lists_are_not_matrices(text):
    for option in (True, False):
        r = recognise(text, subject="mathematics", formula_field=option)
        assert "matrix" not in r.text and r.outcome == "PLAIN_TEXT", (r.outcome, r.text)
