"""Grouping after '/', subscript labels and radicals (Physics validation paper, 2026-11-13).

Each reported expression is a standard formula that used to be flagged; the shared rules that now read it are tested in general,
together with the look-alikes that are still genuinely ambiguous and must stay flagged (never guessed)."""
import pytest

from inst_paper.services.math import recognise
from inst_paper.services.math.verify import _normal, read_latex


def canon(latex: str):
    return _normal(read_latex(latex))


def converted(text, subject="physics", option=False):
    r = recognise(text, subject=subject, formula_field=option)
    assert r.outcome == "CONVERTED", (text, r.outcome, [(s.code, s.reason) for s in r.flags])
    assert r.original == text                                           # the source is kept for audit
    return [s.latex for s in r.spans if s.status == "converted"]


# The fields reported from the Physics validation paper, exactly as in the file (field kind, text, expected LaTeX).
REPORTED = [
    ("option", "(1/4πε₀)q/r²", r"\frac{(\frac{1}{4\pi\varepsilon_{0}})q}{r^{2}}"),           # Q7 A
    ("option", "(1/4πε₀)q/r", r"\frac{(\frac{1}{4\pi\varepsilon_{0}})q}{r}"),                # Q7 B, Q25 A
    ("explanation", "|ε|=N|ΔΦ|/Δt=200×0.004/0.02=40 V.",
     r"|\varepsilon|=\frac{N|\Delta\Phi|}{\Delta t}=200\times\frac{0.004}{0.02}=40\,\mathrm{V}"),          # Q9 explanation
    ("explanation", "Kinetic theory gives v_rms=√(3RT/M).", r"v_{\mathrm{rms}}=\sqrt{\frac{3RT}{M}}"),      # Q16
    ("explanation", "η=1−Tc/Th=1−300/500=0.4.", r"\eta=1-\frac{Tc}{Th}=1-\frac{300}{500}=0.4"),           # Q18
    ("option", "√(GMr)", r"\sqrt{GMr}"),                                                                   # Q24 C
    ("explanation", "Vs/Vp=Ns/Np=0.2.", r"\frac{Vs}{Vp}=\frac{Ns}{Np}=0.2"),                               # transformer
]


@pytest.mark.parametrize("field, text, latex", REPORTED, ids=[c[1] for c in REPORTED])
def test_reported_physics_fields_convert(field, text, latex):
    got = converted(text, option=field == "option")
    assert len(got) == 1 and canon(got[0]) == canon(latex), got


GENERAL = [
    # one denominator: a number with π and the vacuum constants
    ("physics", "h/2π", r"\frac{h}{2\pi}"),
    ("physics", "μ₀/4π", r"\frac{\mu_{0}}{4\pi}"),
    ("physics", "f=ω/2π=50 Hz", r"f=\frac{\omega}{2\pi}=50\,\mathrm{Hz}"),
    ("physics", "F=(1/4πε₀)q₁q₂/r²", r"F=\frac{(\frac{1}{4\pi\varepsilon_{0}})q_{1}q_{2}}{r^{2}}"),
    # one denominator: Δ glued to one symbol is the change in it
    ("physics", "NΔΦ/Δt", r"\frac{N\Delta\Phi}{\Delta t}"),
    ("physics", "a=Δv/Δt", r"a=\frac{\Delta v}{\Delta t}"),
    ("physics", "F=mΔv/Δt", r"F=\frac{m\Delta v}{\Delta t}"),
    # one denominator: labelled quantities (the same capital, different small labels on both sides)
    ("physics", "Vs/Vp=Ns/Np", r"\frac{Vs}{Vp}=\frac{Ns}{Np}"),
    ("physics", "η=1−Tc/Th", r"\eta=1-\frac{Tc}{Th}"),
    # the grouping is written out: brackets or an explicit operator settle it
    ("mathematics", "y=(a/b)c", r"y=(\frac{a}{b})c"),
    ("mathematics", "a/(bc)", r"\frac{a}{bc}"),
    ("mathematics", "a/b × c", r"\frac{a}{b}\times c"),
    ("mathematics", "x/(2y)", r"\frac{x}{2y}"),
    ("physics", "1/(2mv²)", r"\frac{1}{2mv^{2}}"),
    ("physics", "B/(qv)", r"\frac{B}{qv}"),
    ("chemistry", "ΔG°=−R/(KT)", r"\Delta G^{\circ}=-\frac{R}{KT}"),
    # Physics: a trigonometric function of a bare angle over a quantity (the argument must be dimensionless)
    ("physics", "u²sin²θ/(2g)", r"\frac{u^{2}\sin^{2}\theta}{2g}"),
    ("physics", "u²cos²θ/(2g)", r"\frac{u^{2}\cos^{2}\theta}{2g}"),
    ("physics", "u²sinθ/g", r"\frac{u^{2}\sin\theta}{g}"),
    # SI prefixes on units after a number (upright unit symbol)
    ("physics", "144 μJ", r"144\,\mu\mathrm{J}"),
    ("physics", "U=½CV²=144 μJ", r"U=\frac{1}{2}CV^{2}=144\,\mu\mathrm{J}"),
    ("physics", "4 μT", r"4\,\mu\mathrm{T}"),
    ("physics", "6 μN", r"6\,\mu\mathrm{N}"),
    ("physics", "5 μV", r"5\,\mu\mathrm{V}"),
    ("physics", "2 μW", r"2\,\mu\mathrm{W}"),
    ("physics", "2 μH", r"2\,\mu\mathrm{H}"),
    ("physics", "10 μF", r"10\,\mu\mathrm{F}"),
    ("physics", "3 μm", r"3\,\mu\mathrm{m}"),
    ("physics", "2 mΩ", r"2\,\mathrm{m\Omega}"),
    ("physics", "3 kΩ", r"3\,\mathrm{k\Omega}"),
    ("physics", "2 kJ", r"2\,\mathrm{kJ}"),
    ("physics", "3 mJ", r"3\,\mathrm{mJ}"),
    ("physics", "3 MW", r"3\,\mathrm{MW}"),
    ("physics", "3 MV", r"3\,\mathrm{MV}"),
    ("physics", "10 nm", r"10\,\mathrm{nm}"),
    ("physics", "1.6×10⁻¹⁹ C", r"1.6\times10^{-19}\,\mathrm{C}"),
    ("physics", "f=μN", r"f=\mu N"),                                             # no number: friction μ times N, never a unit
    # bracketed fractions (unchanged behaviour)
    ("physics", "(1/(4πε₀)) × (q/r²)", r"(\frac{1}{4\pi\varepsilon_{0}})\times(\frac{q}{r^{2}})"),
    ("physics", "q/(4πε₀r²)", r"\frac{q}{4\pi\varepsilon_{0}r^{2}}"),
    ("mathematics", "(a+b)/(c+d)", r"\frac{a+b}{c+d}"),
    ("mathematics", "x/(2y)", r"\frac{x}{2y}"),
    ("mathematics", "1/(x²+1)", r"\frac{1}{x^{2}+1}"),
    ("mathematics", "1/(1+1/x)", r"\frac{1}{1+\frac{1}{x}}"),                    # nested
    # subscripts and superscripts
    ("physics", "v_rms=√(3RT/M)", r"v_{\mathrm{rms}}=\sqrt{\frac{3RT}{M}}"),
    ("physics", "R_eq=R_1+R_2", r"R_{\mathrm{eq}}=R_{1}+R_{2}"),
    ("physics", "T_c/T_h", r"\frac{T_{c}}{T_{h}}"),
    ("physics", "V_s/V_p", r"\frac{V_{s}}{V_{p}}"),
    ("mathematics", "e^(−ax)", r"e^{-ax}"),
    ("mathematics", "x²", r"x^{2}"),
    ("physics", "10⁻³", r"10^{-3}"),
    ("physics", "E=hc/λ≈4.97×10⁻¹⁹ J", r"E=\frac{hc}{\lambda}\approx4.97\times10^{-19}\,\mathrm{J}"),    # scientific notation
    # radicals
    ("physics", "√(GMr)", r"\sqrt{GMr}"),
    ("physics", "√(GM/r)", r"\sqrt{\frac{GM}{r}}"),
    ("physics", "√(3RT/M)", r"\sqrt{\frac{3RT}{M}}"),
    ("mathematics", "√(x²+y²)", r"\sqrt{x^{2}+y^{2}}"),
    ("mathematics", "√((a+b)/(c+d))", r"\sqrt{\frac{a+b}{c+d}}"),
    ("physics", "ω₀=1/√(LC)", r"\omega_{0}=\frac{1}{\sqrt{LC}}"),
    # chained equations with units
    ("physics", "P=I²R=4×10=40 W", r"P=I^{2}R=4\times10=40\,\mathrm{W}"),
]


@pytest.mark.parametrize("subject, text, latex", GENERAL, ids=[c[1] for c in GENERAL])
def test_general_grouping(subject, text, latex):
    got = converted(text, subject)
    assert len(got) == 1 and canon(got[0]) == canon(latex), got


@pytest.mark.parametrize("text", ["Here η=1−Tc/Th, so η=0.4.", "The emf is |ε|=N|ΔΦ|/Δt here.", "Using η=1−T_c/T_h we get 0.4."])
def test_equation_inside_a_sentence(text):
    assert len(converted(text)) >= 1


def test_chemistry_formulas_unchanged():
    assert canon(converted("H₂SO₄", "chemistry")[0]) == canon(r"\mathrm{H_{2}SO_{4}}")
    assert canon(converted("Ca(OH)₂", "chemistry")[0]) == canon(r"\mathrm{Ca(OH)_{2}}")


AMBIGUOUS = [
    ("1/2x", "AMBIGUOUS_DENOMINATOR"),             # ½x or 1/(2x)
    ("x/2y", "AMBIGUOUS_DENOMINATOR"),
    ("1/2mv²", "AMBIGUOUS_DENOMINATOR"),           # ½mv² is meant as often as 1/(2mv²)
    ("1/2πr", "AMBIGUOUS_DENOMINATOR"),            # a variable after the constant group
    ("a/2πε₀r", "AMBIGUOUS_DENOMINATOR"),
    ("1/4π²", "AMBIGUOUS_DENOMINATOR"),            # (4π)² or 4π²: not the plain constant group
    ("x/2π(a+b)", "AMBIGUOUS_DENOMINATOR"),
    ("a/Δxy", "AMBIGUOUS_FRACTION"),               # Δ takes one symbol, not a word
    ("a/b c", "AMBIGUOUS_FRACTION"),               # a space ends the typed word
    ("a_nx+1", "AMBIGUOUS_POWER"),                 # a two-letter label before '+' is not settled
    ("sin x/x", "AMBIGUOUS_FRACTION"),
    ("a/b/c", "AMBIGUOUS_FRACTION"),
    ("a/bc", "AMBIGUOUS_FRACTION"),                # a/(bc) or (a/b)c: nothing in the source settles it
    ("a/bc²", "AMBIGUOUS_FRACTION"),
    ("y=a/bc", "AMBIGUOUS_FRACTION"),
    ("B/qv", "AMBIGUOUS_FRACTION"),
    ("y=R/KT", "AMBIGUOUS_FRACTION"),              # an all-capital word is no different
    ("x/AB", "AMBIGUOUS_FRACTION"),
    ("y=Ta/bc", "AMBIGUOUS_FRACTION"),             # labels must share the capital on both sides
    ("sin θ/2", "AMBIGUOUS_FRACTION"),             # half angle sin(θ/2) or (sin θ)/2
    ("sinθ/θ", "AMBIGUOUS_FRACTION"),
]


@pytest.mark.parametrize("text, code", AMBIGUOUS, ids=[c[0] for c in AMBIGUOUS])
def test_genuinely_ambiguous_stays_flagged(text, code):
    for subject in ("physics", "mathematics"):
        r = recognise(text, subject=subject, formula_field=True)               # a whole option: always a formula candidate
        assert r.outcome == "NEEDS_REVIEW" and code in r.reasons and r.text == text, (subject, r.outcome, r.reasons)
        r = recognise(f"Here {text} is used.", subject=subject)             # in prose: never converted (flagged or left as text)
        assert r.outcome != "CONVERTED" and text in r.text, (subject, r.outcome, r.text)


def test_glued_word_followed_by_a_word_stays_unconverted():
    # pre-existing safeguard ("x=at most 5"): a letter group straight before a lowercase word may be prose; flagged, never guessed
    assert recognise("Using η=1−Tc/Th we get 0.4.", subject="physics").outcome == "NEEDS_REVIEW"


@pytest.mark.parametrize("text", ["Take √(area) of the square.", "√(mass)"])
def test_english_word_under_a_root_is_not_split(text):
    r = recognise(text, subject="mathematics", formula_field=True)
    assert r.outcome == "NEEDS_REVIEW" and not any(s.status == "converted" for s in r.spans), r.text


def test_angle_rule_is_physics_only():
    # outside Physics an angle symbol over a letter is not known to be dimensionless: sin(θ/g) stays a possible reading
    for subject in ("mathematics", None):
        r = recognise("u²sinθ/g", subject=subject, formula_field=True)
        assert r.outcome == "NEEDS_REVIEW" and "AMBIGUOUS_FRACTION" in r.reasons, (subject, r.reasons)
    r = recognise("sin x/g", subject="physics", formula_field=True)                   # x is not an angle symbol
    assert r.outcome == "NEEDS_REVIEW" and "AMBIGUOUS_FRACTION" in r.reasons, r.reasons


def test_unit_after_algebra_is_settled_only_by_the_field():
    r = recognise("For x(t)=3t²−2t³ m, find acceleration at t=2 s.", subject="physics")      # Physics Q1
    assert r.outcome == "CONVERTED" and r"3t^{2} - 2t^{3}\,\mathrm{m}" in r.text, (r.text, r.reasons)
    for text, subject in [
        ("x(t)=3t²−2t³ m", "physics"),                                   # no quantity elsewhere in the field
        ("For x(t)=3t²−2t³ m, find acceleration at t=2 s.", "mathematics"),  # no unit convention outside Physics/Chemistry
        ("For x=3t² m, where m is the mass, at t=2 s.", "physics"),      # m is also used as a symbol in the field
        ("k = 5t N", "physics"),
    ]:
        r = recognise(text, subject=subject)
        assert r.outcome == "NEEDS_REVIEW" and "AMBIGUOUS_UNIT" in r.reasons, (text, subject, r.reasons)


@pytest.mark.parametrize("source, latex, domain, context, ok", [
    ("a/bc", r"\frac{a}{bc}", "mathematics", "", False),                 # the validator alone refuses the guessed a/(bc)
    ("a/bc", r"\frac{a}{b}c", "mathematics", "", False),                 # ... and the guessed (a/b)c
    ("B/qv", r"\frac{B}{qv}", "physics", "", False),
    ("Tc/Th", r"\frac{Tc}{Th}", "physics", "", True),
    ("x=3t² m", r"x=3t^{2}\,\mathrm{m}", "physics", "x=3t² m", False),
    ("x=3t² m", r"x=3t^{2}\,\mathrm{m}", "physics", "x=3t² m at t=2 s", True),
    ("144 μJ", r"144\,\mu\mathrm{J}", "physics", "", True),
    ("NΔΦ/Δt", r"\frac{N\Delta\Phi}{\Delta t}", "physics", "", True),
    ("1/2x", r"\frac{1}{2x}", "mathematics", "", False),
])
def test_independent_validator_agrees(source, latex, domain, context, ok):
    from inst_paper.services.math.verify import verify_text
    assert verify_text(source, latex, domain, context or source)[0] is ok


@pytest.mark.parametrize("text", ["u²sin2θ/g", "R=u²sin2θ/g"])
def test_a_number_glued_to_a_function_name_offers_every_reading(text):
    """sin2θ copied from a rendered page may be sin²θ or sin 2θ: the engine no longer decides (it used to read sin 2θ in Physics).
    Both readings are offered, checked, and the list is complete."""
    from inst_paper.services.recognition import _field
    rec = _field(text, "physics")
    assert rec["outcome"] == "NEEDS_REVIEW" and rec["reasons"] == ["FLATTENED_FUNCTION_POWER"]
    shown = {s["text"].replace(" ", "") for s in rec["suggestions"]}
    assert any(r"\sin2\theta" in t for t in shown) and any(r"\sin^{2}\theta" in t for t in shown)
    assert rec["candidate_sets"][0]["complete"]
