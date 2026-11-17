"""Chemistry recognition (Chemistry validation paper, 2026-11-13): concentration brackets, charges versus powers, reactions, kinetics,
electrochemistry, Raoult's law and units - every expression from the reported screenshots, the general patterns behind them, and the
look-alikes that stay flagged. A conversion is compared canonically on the independent validator's reading."""
import pytest

from inst_paper.services.math import recognise
from inst_paper.services.math.verify import _normal, read_latex, verify_text
from inst_paper.services.recognition import _field, asks_for_unit


def canon(latex: str):
    return _normal(read_latex(latex))


def one(text, option=False, unit_answer=False, subject="chemistry"):
    r = recognise(text, subject=subject, formula_field=option, unit_answer=unit_answer)
    assert r.outcome == "CONVERTED", (text, r.outcome, [(s.code, s.reason) for s in r.flags])
    assert r.original == text                                           # the source is kept for audit
    return [s.latex for s in r.spans if s.status == "converted"]


# Every field the screenshots show, exactly as in the file: (field, text, expected LaTeX of each converted span).
PAPER = [
    ("question", "For N₂(g)+3H₂(g) ⇌ 2NH₃(g), Kc is:",
     [r"\mathrm{N_{2}}\mathrm{(g)}+3\mathrm{H_{2}}\mathrm{(g)}\rightleftharpoons 2\mathrm{NH_{3}}\mathrm{(g)}"]),
    ("option", "[NH₃]²/([N₂][H₂]³)", [r"\frac{[\mathrm{NH_{3}}]^{2}}{[\mathrm{N_{2}}][\mathrm{H_{2}}]^{3}}"]),            # Q1 A
    ("option", "[N₂][H₂]³/[NH₃]²", [r"\frac{[\mathrm{N_{2}}][\mathrm{H_{2}}]^{3}}{[\mathrm{NH_{3}}]^{2}}"]),              # Q1 B
    ("option", "[NH₃]/([N₂][H₂])", [r"\frac{[\mathrm{NH_{3}}]}{[\mathrm{N_{2}}][\mathrm{H_{2}}]}"]),                      # Q1 C
    ("explanation", "Strong acid gives [H⁺]=10⁻³ M; pH=3.", [r"[\mathrm{H^{+}}]=10^{-3}\,\mathrm{M}", r"\mathrm{pH}=3"]),  # Q2
    ("explanation", "t½=ln2/k.", [r"t_{\frac{1}{2}}=\frac{\ln 2}{k}"]),                                                     # Q3
    ("option", "ln([A]₀/[A]t)=kt", [r"\ln(\frac{[A]_{0}}{[A]t})=kt"]),                                                     # Q4 A
    ("option", "[A]t=[A]₀−kt", [r"[A]t=[A]_{0}-kt"]),                                                                      # Q4 B
    ("option", "1/[A]t=1/[A]₀+kt", [r"\frac{1}{[A]t}=\frac{1}{[A]_{0}}+kt"]),                                              # Q4 C
    ("explanation", "Integrated first-order expression is ln([A]₀/[A]t)=kt.", [r"\ln(\frac{[A]_{0}}{[A]t})=kt"]),
    ("option", "MnO₄⁻+8H⁺+5e⁻→Mn²⁺+4H₂O",                                                                                  # Q7 A
     [r"\mathrm{MnO_{4}^{-}}+8\mathrm{H^{+}}+5\mathrm{e^{-}}\rightarrow\mathrm{Mn^{2+}}+4\mathrm{H_{2}O}"]),
    ("option", "MnO₄⁻+4H⁺+3e⁻→Mn²⁺+2H₂O",
     [r"\mathrm{MnO_{4}^{-}}+4\mathrm{H^{+}}+3\mathrm{e^{-}}\rightarrow\mathrm{Mn^{2+}}+2\mathrm{H_{2}O}"]),
    ("option", "MnO₄⁻+5e⁻→Mn²⁺", [r"\mathrm{MnO_{4}^{-}}+5\mathrm{e^{-}}\rightarrow\mathrm{Mn^{2+}}"]),
    ("option", "MnO₄⁻+8H⁺→Mn²⁺+4H₂O", [r"\mathrm{MnO_{4}^{-}}+8\mathrm{H^{+}}\rightarrow\mathrm{Mn^{2+}}+4\mathrm{H_{2}O}"]),
    ("explanation", "Standard relation is ΔG°=−nFE°cell.", [r"\Delta G^{\circ}=-nF{E^{\circ}}_{\mathrm{cell}}"]),          # Q8
    ("option", "nF/E°cell", [r"\frac{nF}{{E^{\circ}}_{\mathrm{cell}}}"]),
    ("option", "pA=xA pA°", [r"pA=xApA^{\circ}"]),                                                                         # Q26
    ("option", "pA=pA°/xA", [r"pA=\frac{pA^{\circ}}{xA}"]),
    ("option", "pA=xA/pA°", [r"pA=\frac{xA}{pA^{\circ}}"]),
    ("option", "[Al³⁺][OH⁻]³", [r"[\mathrm{Al^{3+}}][\mathrm{OH^{-}}]^{3}"]),                                             # Q30
    ("option", "[Al³⁺]³[OH⁻]", [r"[\mathrm{Al^{3+}}]^{3}[\mathrm{OH^{-}}]"]),
    ("option", "[Al³⁺][OH⁻]", [r"[\mathrm{Al^{3+}}][\mathrm{OH^{-}}]"]),
    ("option", "[Al³⁺]²[OH⁻]³", [r"[\mathrm{Al^{3+}}]^{2}[\mathrm{OH^{-}}]^{3}"]),
    ("explanation", "Al(OH)₃(s) ⇌ Al³⁺+3OH⁻.", [r"\mathrm{Al(OH)_{3}}\mathrm{(s)}\rightleftharpoons\mathrm{Al^{3+}}+3\mathrm{OH^{-}}"]),
]


@pytest.mark.parametrize("field, text, latex", PAPER, ids=[p[1] for p in PAPER])
def test_chemistry_paper_fields(field, text, latex):
    got = one(text, option=field == "option")
    assert [canon(x) for x in got] == [canon(x) for x in latex], got


GENERAL = [
    # formulas (ASCII counts are explicit subscripts in a Chemistry paper)
    ("H2SO4", r"\mathrm{H_{2}SO_{4}}"), ("Ca(OH)2", r"\mathrm{Ca(OH)_{2}}"), ("Al(OH)3", r"\mathrm{Al(OH)_{3}}"),
    ("KMnO4", r"\mathrm{KMnO_{4}}"), ("K2Cr2O7", r"\mathrm{K_{2}Cr_{2}O_{7}}"), ("NH4Cl", r"\mathrm{NH_{4}Cl}"),
    ("CuSO₄·5H₂O", r"\mathrm{CuSO_{4}\cdot 5H_{2}O}"),
    # ions and polyatomic ions
    ("H⁺", r"\mathrm{H^{+}}"), ("OH⁻", r"\mathrm{OH^{-}}"), ("Ca²⁺", r"\mathrm{Ca^{2+}}"), ("Al³⁺", r"\mathrm{Al^{3+}}"),
    ("Fe²⁺", r"\mathrm{Fe^{2+}}"), ("Fe³⁺", r"\mathrm{Fe^{3+}}"), ("SO₄²⁻", r"\mathrm{SO_{4}^{2-}}"), ("CO₃²⁻", r"\mathrm{CO_{3}^{2-}}"),
    ("Cr₂O₇²⁻", r"\mathrm{Cr_{2}O_{7}^{2-}}"), ("[Fe(CN)₆]⁴⁻", r"\mathrm{[Fe(CN)_{6}]^{4-}}"),
    ("[Co(NH₃)₆]Cl₃", r"\mathrm{[Co(NH_{3})_{6}]Cl_{3}}"),                     # a complex keeps its brackets as part of the formula
    # concentrations: the species is upright, a power outside the bracket is a power
    ("[H+]", r"[\mathrm{H^{+}}]"), ("[OH−]", r"[\mathrm{OH^{-}}]"), ("[H3O+]", r"[\mathrm{H_{3}O^{+}}]"), ("[Na+]", r"[\mathrm{Na^{+}}]"),
    ("[Cl−]", r"[\mathrm{Cl^{-}}]"), ("[SO4^2−]", r"[\mathrm{SO_{4}^{2-}}]"),
    ("Kc = [NH3]^2 / ([N2][H2]^3)", r"\frac{[\mathrm{NH_{3}}]^{2}}{[\mathrm{N_{2}}][\mathrm{H_{2}}]^{3}}"),
    ("Kc=[NH₃]²/([N₂][H₂]³)", r"Kc=\frac{[\mathrm{NH_{3}}]^{2}}{[\mathrm{N_{2}}][\mathrm{H_{2}}]^{3}}"),
    ("Ksp=[Ag⁺][Cl⁻]", r"Ksp=[\mathrm{Ag^{+}}][\mathrm{Cl^{-}}]"),
    ("Ksp=[Ca²⁺][F⁻]²", r"Ksp=[\mathrm{Ca^{2+}}][\mathrm{F^{-}}]^{2}"),
    ("Ksp=[Ca²⁺]³[PO₄³⁻]²", r"Ksp=[\mathrm{Ca^{2+}}]^{3}[\mathrm{PO_{4}^{3-}}]^{2}"),        # different charges and powers together
    ("Kw=[H⁺][OH⁻]=10⁻¹⁴", r"Kw=[\mathrm{H^{+}}][\mathrm{OH^{-}}]=10^{-14}"),
    ("pH=−log[H⁺]", r"\mathrm{pH}=-\log[\mathrm{H^{+}}]"),
    ("rate=k[A]²[B]", r"k[A]^{2}[B]"),                                          # [B] in a rate law is a reactant, not boron
    ("Kp=(pNH₃)²/(pN₂·pH₂³)", r"Kp=\frac{(pNH_{3})^{2}}{pN_{2}\cdot pH_{2}^{3}}"),   # pH₂: p times H₂, never the pH label
    # reactions: ionic, redox, half-reactions, ASCII charges written explicitly with ^ or a plain sign
    ("MnO4^- + 8H+ + 5e^- → Mn^2+ + 4H2O",
     r"\mathrm{MnO_{4}^{-}}+8\mathrm{H^{+}}+5\mathrm{e^{-}}\rightarrow\mathrm{Mn^{2+}}+4\mathrm{H_{2}O}"),
    ("Fe²⁺ → Fe³⁺ + e⁻", r"\mathrm{Fe^{2+}}\rightarrow\mathrm{Fe^{3+}}+\mathrm{e^{-}}"),
    ("Fe^2+ → Fe^3+ + e^-", r"\mathrm{Fe^{2+}}\rightarrow\mathrm{Fe^{3+}}+\mathrm{e^{-}}"),
    ("Cr₂O₇²⁻ + 14H⁺ + 6e⁻ → 2Cr³⁺ + 7H₂O",
     r"\mathrm{Cr_{2}O_{7}^{2-}}+14\mathrm{H^{+}}+6\mathrm{e^{-}}\rightarrow 2\mathrm{Cr^{3+}}+7\mathrm{H_{2}O}"),
    ("2H2 + O2 → 2H2O", r"2\mathrm{H_{2}}+\mathrm{O_{2}}\rightarrow 2\mathrm{H_{2}O}"),
    ("N2 + 3H2 ⇌ 2NH3", r"\mathrm{N_{2}}+3\mathrm{H_{2}}\rightleftharpoons 2\mathrm{NH_{3}}"),
    ("H+ + OH− → H2O", r"\mathrm{H^{+}}+\mathrm{OH^{-}}\rightarrow\mathrm{H_{2}O}"),
    ("CO3^2− + 2H+ → CO2 + H2O", r"\mathrm{CO_{3}^{2-}}+2\mathrm{H^{+}}\rightarrow\mathrm{CO_{2}}+\mathrm{H_{2}O}"),
    # kinetics, logarithms, electrochemistry
    ("t½ = 0.693/k", r"t_{\frac{1}{2}}=\frac{0.693}{k}"), ("t½ = ln(2)/k", r"t_{\frac{1}{2}}=\frac{\ln(2)}{k}"),
    ("k = 2.303/t × log(a/(a−x))", r"k=\frac{2.303}{t}\times\log(\frac{a}{a-x})"),
    ("E=E°−(0.0591/n)log Q", r"E=E^{\circ}-(\frac{0.0591}{n})\log Q"),
    ("Ecell=E°cathode−E°anode", r"E_{\mathrm{cell}}={E^{\circ}}_{\mathrm{cathode}}-{E^{\circ}}_{\mathrm{anode}}"),
    ("ΔG°=−RT ln K", r"\Delta G^{\circ}=-RT\ln K"),
    # units: upright, with their powers and spacing
    ("L mol⁻¹ s⁻¹", r"\mathrm{L}\,\mathrm{mol}^{-1}\,\mathrm{s}^{-1}"), ("L mol^-1 s^-1", r"\mathrm{L}\,\mathrm{mol}^{-1}\,\mathrm{s}^{-1}"),
    ("mol L^-1", r"\mathrm{mol}\,\mathrm{L}^{-1}"), ("mol dm^-3", r"\mathrm{mol}\,\mathrm{dm}^{-3}"),
    ("mol^-1 L s^-1", r"\mathrm{mol}^{-1}\,\mathrm{L}\,\mathrm{s}^{-1}"), ("0.1 M", r"0.1\,\mathrm{M}"),
]


@pytest.mark.parametrize("text, latex", GENERAL, ids=[g[0] for g in GENERAL])
def test_general_chemistry(text, latex):
    got = one(text, option=True)
    assert len(got) == 1 and canon(got[0]) == canon(latex), got


@pytest.mark.parametrize("text", ["Strong acid gives [H⁺]=10⁻³ M; pH=3.", "At equilibrium Ksp=[Ag⁺][Cl⁻] holds.",
                                  "The reaction 2H₂+O₂→2H₂O is exothermic.", "Here ΔG°=−nFE°cell, so the cell is spontaneous."])
def test_formula_inside_a_sentence(text):
    r = recognise(text, subject="chemistry")
    assert r.outcome == "CONVERTED", (r.outcome, r.reasons)
    assert all(w in r.text for w in text.split() if w.isalpha() and w.islower())      # the English words stay prose


def test_unit_answer_needs_the_question():
    # s⁻¹ alone could be a unit or 1/s (s for solubility): the question asking for a unit settles it, nothing else does
    assert asks_for_unit("Unit of rate constant for second-order reaction is:") and not asks_for_unit("Rate constant is:")
    assert one("s⁻¹", option=True, unit_answer=True) == [r"\mathrm{s}^{-1}"]
    assert canon(one("M s^-1", option=True, unit_answer=True)[0]) == canon(r"\mathrm{M}\,\mathrm{s}^{-1}")
    for text in ("s⁻¹", "M s^-1"):
        assert recognise(text, subject="chemistry", formula_field=True).outcome == "NEEDS_REVIEW"
    assert recognise("s⁻¹", subject="mathematics", formula_field=True, unit_answer=True).outcome == "NEEDS_REVIEW"
    assert one("x⁻¹", option=True, unit_answer=True) == ["x^{-1}"]                 # a variable is never made a unit
    assert _field("s⁻¹", "chemistry", "option_b", True)["outcome"] == "CONVERTED"   # what the import preview does for Q25 B


AMBIGUOUS = [
    ("[NH₃]²/[N₂][H₂]", "AMBIGUOUS_FRACTION"),          # Q1 D: ([NH₃]²/[N₂])[H₂] or [NH₃]²/([N₂][H₂]) - the a/bc doubt
    ("E=E°−n/0.0591 log Q", "AMBIGUOUS_FRACTION"),       # Q9 D: (n/0.0591) log Q or n/(0.0591 log Q)
    ("E = E° − 0.0591/n log Q", "AMBIGUOUS_FRACTION"),
    ("[Ca2+]", "AMBIGUOUS_CHEMICAL_CHARGE"),             # Ca²⁺ or Ca₂⁺: a digit before a typed sign
    ("Cu2+", "AMBIGUOUS_CHEMICAL_CHARGE"), ("NH4+", "AMBIGUOUS_CHEMICAL_CHARGE"),
    ("Fe2+ → Fe3+ + e−", "AMBIGUOUS_CHEMICAL_CHARGE"),
    ("y=1/[A]t", "AMBIGUOUS_FRACTION"),                  # no [A] with a subscript label in the formula: t may be a factor
    ("pA=pA°/xB", "AMBIGUOUS_FRACTION"),                 # B labels nothing else: x times B is possible
    ("y=a/xA", "AMBIGUOUS_FRACTION"),
    ("log 3/2", "AMBIGUOUS_FRACTION"), ("ln 2/3", "AMBIGUOUS_FRACTION"),   # log(3/2) or (log 3)/2: a number after '/'
    ("Ksp=[Ag⁺]/[Cl⁻]2", "AMBIGUOUS_NUMBER_ADJACENT"),   # a power or a factor 2
]


@pytest.mark.parametrize("text, code", AMBIGUOUS, ids=[a[0] for a in AMBIGUOUS])
def test_genuinely_ambiguous_stays_flagged(text, code):
    r = recognise(text, subject="chemistry", formula_field=True)
    assert r.outcome == "NEEDS_REVIEW" and code in r.reasons, (r.outcome, r.reasons)
    assert r.original == text


def test_subject_conventions_stay_in_their_subject():
    # t½ as a half-life subscript and ln2/k are Physics/Chemistry conventions; Mathematics keeps its own reading
    r = recognise("ln2/k", subject="mathematics", formula_field=True)
    assert r.outcome == "NEEDS_REVIEW" and "AMBIGUOUS_FRACTION" in r.reasons
    assert one("t½ = ln2/k", option=True, subject="physics") == [r"t_{\frac{1}{2}} = \frac{\ln 2}{k}"]


@pytest.mark.parametrize("source, latex, context, ok", [
    ("[NH₃]²/[N₂][H₂]", r"\frac{[\mathrm{NH_{3}}]^{2}}{[\mathrm{N_{2}}][\mathrm{H_{2}}]}", "", False),   # the guess is refused
    ("1/[A]t", r"\frac{1}{[A]t}", "", False),
    ("1/[A]t=1/[A]₀+kt", r"\frac{1}{[A]t}=\frac{1}{[A]_{0}}+kt", "", True),
    ("pA=pA°/xA", r"pA=\frac{pA^{\circ}}{xA}", "", True),
    ("pA=pA°/xB", r"pA=\frac{pA^{\circ}}{xB}", "", False),
    ("Mn²⁺+4H₂O", r"\mathrm{Mn^{2+}}+4\mathrm{H_{2}O}", "", True),
    ("t½=ln2/k", r"t_{\frac{1}{2}}=\frac{\ln 2}{k}", "", True),
    ("E°cell", r"{E^{\circ}}_{\mathrm{cell}}", "", True),
    ("s⁻¹", r"\mathrm{s}^{-1}", "", False),
    ("pH₂", r"\mathrm{pH}_{2}", "", False),                                     # pH₂ is not the pH label
])
def test_independent_validator_agrees(source, latex, context, ok):
    assert verify_text(source, latex, "chemistry", context or source)[0] is ok
