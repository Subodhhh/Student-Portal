"""Character tables for mathematical recognition: Unicode notation -> tokens / LaTeX. Pure data."""
import unicodedata

SUPERSCRIPTS = {
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
    "⁺": "+", "⁻": "-", "⁼": "=", "⁽": "(", "⁾": ")",
    "ᵃ": "a", "ᵇ": "b", "ᶜ": "c", "ᵈ": "d", "ᵉ": "e", "ᶠ": "f", "ᵍ": "g", "ʰ": "h", "ⁱ": "i", "ʲ": "j", "ᵏ": "k", "ˡ": "l",
    "ᵐ": "m", "ⁿ": "n", "ᵒ": "o", "ᵖ": "p", "ʳ": "r", "ˢ": "s", "ᵗ": "t", "ᵘ": "u", "ᵛ": "v", "ʷ": "w", "ˣ": "x", "ʸ": "y",
    "ᶻ": "z", "ᴬ": "A", "ᴮ": "B", "ᴰ": "D", "ᴱ": "E", "ᴳ": "G", "ᴴ": "H", "ᴵ": "I", "ᴶ": "J", "ᴷ": "K", "ᴸ": "L", "ᴹ": "M",
    "ᴺ": "N", "ᴼ": "O", "ᴾ": "P", "ᴿ": "R", "ᵀ": "T", "ᵁ": "U", "ᵂ": "W",
    "ᶿ": "θ", "ᵅ": "α", "ᵝ": "β", "ᵞ": "γ", "ᵟ": "δ", "ᵠ": "φ", "ᵡ": "χ",
}
SUBSCRIPTS = {
    "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4", "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
    "₊": "+", "₋": "-", "₌": "=", "₍": "(", "₎": ")",
    "ₐ": "a", "ₑ": "e", "ₕ": "h", "ᵢ": "i", "ⱼ": "j", "ₖ": "k", "ₗ": "l", "ₘ": "m", "ₙ": "n", "ₒ": "o", "ₚ": "p", "ᵣ": "r",
    "ₛ": "s", "ₜ": "t", "ᵤ": "u", "ᵥ": "v", "ₓ": "x", "ᵦ": "β", "ᵧ": "γ", "ᵨ": "ρ", "ᵩ": "φ", "ᵪ": "χ",
}
# Reverse maps, used when Word/Excel superscript or subscript formatting is turned back into characters (office.py).
TO_SUPERSCRIPT = {v: k for k, v in SUPERSCRIPTS.items()} | {"−": "⁻"}
TO_SUBSCRIPT = {v: k for k, v in SUBSCRIPTS.items()} | {"−": "₋"}

GREEK = {
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta", "ε": r"\varepsilon", "ϵ": r"\epsilon", "ζ": r"\zeta",
    "η": r"\eta", "θ": r"\theta", "ϑ": r"\vartheta", "ι": r"\iota", "κ": r"\kappa", "λ": r"\lambda", "μ": r"\mu", "µ": r"\mu",
    "ν": r"\nu", "ξ": r"\xi", "π": r"\pi", "ϖ": r"\varpi", "ρ": r"\rho", "ϱ": r"\varrho", "σ": r"\sigma", "ς": r"\varsigma",
    "τ": r"\tau", "υ": r"\upsilon", "φ": r"\varphi", "ϕ": r"\phi", "χ": r"\chi", "ψ": r"\psi", "ω": r"\omega",
    "Γ": r"\Gamma", "Δ": r"\Delta", "Θ": r"\Theta", "Λ": r"\Lambda", "Ξ": r"\Xi", "Π": r"\Pi", "Σ": r"\Sigma",
    "Υ": r"\Upsilon", "Φ": r"\Phi", "Ψ": r"\Psi", "Ω": r"\Omega",
}
VULGAR = {"½": (1, 2), "⅓": (1, 3), "⅔": (2, 3), "¼": (1, 4), "¾": (3, 4), "⅕": (1, 5), "⅖": (2, 5), "⅗": (3, 5), "⅘": (4, 5),
          "⅙": (1, 6), "⅚": (5, 6), "⅐": (1, 7), "⅛": (1, 8), "⅜": (3, 8), "⅝": (5, 8), "⅞": (7, 8), "⅑": (1, 9), "⅒": (1, 10)}

RELATIONS = {"=": "=", "≠": r"\neq", "<": "<", ">": ">", "≤": r"\leq", "≥": r"\geq", "⩽": r"\leq", "⩾": r"\geq",
             "≈": r"\approx", "≡": r"\equiv", "∝": r"\propto", "→": r"\to", "⇒": r"\Rightarrow", "⇔": r"\Leftrightarrow",
             "↔": r"\leftrightarrow", "∈": r"\in", "∉": r"\notin", "⊂": r"\subset", "⊆": r"\subseteq", "⊃": r"\supset",
             "⊇": r"\supseteq", "∥": r"\parallel", "⊥": r"\perp", "≅": r"\cong", "∼": r"\sim", "⇌": r"\rightleftharpoons",
             "<=": r"\leq", ">=": r"\geq", "!=": r"\neq", "->": r"\to", "=>": r"\Rightarrow"}
ADD = {"+": "+", "-": "-", "−": "-", "–": "-", "±": r"\pm", "∓": r"\mp", "∪": r"\cup", "∩": r"\cap"}
MUL = {"×": r"\times", "·": r"\cdot", "⋅": r"\cdot", "∙": r"\cdot", "*": r"\cdot", "÷": r"\div"}
ATOMS = {"∞": r"\infty", "∅": r"\emptyset", "∇": r"\nabla"}
PREFIX = {"∠": r"\angle", "△": r"\triangle"}
POSTFIX = {"°": r"^{\circ}", "′": "'", "″": "''", "!": "!"}
BIG = {"∫": r"\int", "∬": r"\iint", "∭": r"\iiint", "∮": r"\oint", "∑": r"\sum", "∏": r"\prod"}
BIG_GREEK = {"Σ": r"\sum", "Π": r"\prod"}      # only when written with limits (Σ_(r=0)^n); otherwise the Greek letter
ROOTS = {"√": None, "∛": "3", "∜": "4"}
COMBINING_ARROW = "⃗"

FUNCTIONS = {name: "\\" + name for name in (
    "sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh", "coth",
    "log", "ln", "lg", "exp", "det", "lim", "max", "min", "gcd", "deg")}
FUNCTIONS["cosec"] = r"\operatorname{cosec}"
UNITS = {"cm", "mm", "km", "kg", "mg", "ml", "dm", "mL", "mol", "mmol", "Pa", "kPa", "MPa", "GPa", "Hz", "kHz", "MHz", "eV", "keV",
         "MeV", "kJ", "kW", "MW", "kN", "kV", "mV", "mA", "nm", "atm", "Wb", "cal", "kcal", "rad",
         "pH", "pOH", "pKa", "pKb", "GHz", "GeV"}
# Cycle 2.3 (approved vocabulary: SI prefixes with the units that really take them). These are units ONLY directly after a number
# ("2.4 MJ", "3 nC"): elsewhere the same letters are variables or points (nCr = nC(r-1), the line MJ).
CONTEXT_UNITS = {"MJ", "GJ", "mJ", "GW", "mW", "mN", "nC", "mC", "pF", "nF", "mF", "mT", "mH", "kA", "MV", "kN", "MN", "GN"}

# Two-letter English words that are prose, never a product of two variables.
PROSE_2 = {"am", "an", "as", "at", "be", "by", "do", "go", "he", "hi", "if", "in", "is", "it", "me", "my", "no", "of", "oh", "ok",
           "on", "or", "so", "to", "up", "us", "we", "eg", "ie", "vs"}

# Every LaTeX command the converter may emit: the output check refuses anything else.
ALLOWED_COMMANDS = ({c[1:] for c in GREEK.values()} | {c[1:] for c in RELATIONS.values() if c.startswith("\\")}
                    | {c[1:] for c in ADD.values() if c.startswith("\\")} | {c[1:] for c in MUL.values()}
                    | {c[1:] for c in ATOMS.values()} | {c[1:] for c in PREFIX.values()} | {c[1:] for c in BIG.values()}
                    | {n for n in FUNCTIONS} | {"operatorname", "frac", "sqrt", "circ", "partial", "vec", "hat", "begin", "end", "lvert", "rvert",
                                              "mathrm", "left", "right", "rightarrow", "{", "}", ",", "\\",
                                              "mathbf", "boldsymbol", "mathbb", "mathcal",    # styled letters 𝐅 𝒂 ℝ 𝒜 (lexer)
                                              "limits", "lfloor", "rfloor", "lceil", "rceil"})                                      # lim / ∑ conditions under the operator
# tr(A), the trace: added after ALLOWED_COMMANDS so a typed \tr (not a KaTeX command) is never allowed
FUNCTIONS["tr"] = r"\operatorname{tr}"
# special functions and operators of non-elementary calculus and complex numbers, upright like sin: Re(s), Im(z), erf(x), Ei(x), sgn(x)
# - only when a bracket is glued after them (Re(s)), so the words of a sentence (Re-examine, Im ...) stay prose
BRACKET_FUNCTIONS = frozenset({"Re", "Im", "erf", "erfc", "Ei", "sgn"})
for _name in BRACKET_FUNCTIONS:
    FUNCTIONS[_name] = r"\operatorname{" + _name + "}"

# Commands accepted in LaTeX a provider typed or Word equations produced: a subset of what KaTeX renders in strict mode, verified
# against the installed KaTeX by the frontend tests. Anything else is flagged (LATEX_VALIDATION_FAILED), never passed to students.
PROVIDER_COMMANDS = frozenset(ALLOWED_COMMANDS | {
    "dfrac", "tfrac", "binom", "cdots", "ldots", "dots", "vdots", "ddots", "quad", "qquad", ";", ":", "!", " ", "|", "%", "#", "&", "_", "$",
    "hat", "bar", "overline", "underline", "tilde", "dot", "ddot", "overrightarrow", "overbrace", "underbrace", "boxed", "underset",
    "overset", "xrightarrow", "xleftarrow", "rightarrow", "leftarrow", "Leftarrow", "longrightarrow", "mapsto", "uparrow", "downarrow",
    "text", "textrm", "mathbf", "boldsymbol", "mathit", "mathbb", "mathcal", "mathrm", "operatorname", "displaystyle", "limits", "big", "Big", "bigl", "bigr",
    "Bigl", "Bigr", "langle", "rangle", "lfloor", "rfloor", "lceil", "rceil", "lbrace", "rbrace", "vert", "Vert", "mid", "prime", "le",
    "ge", "ne", "infty", "forall", "exists", "neg", "land", "lor", "wedge", "vee", "setminus", "emptyset", "varnothing", "therefore",
    "because", "ell", "hbar", "Re", "Im", "deg", "arg", "sinh", "cosh", "tanh", "csc", "sec", "cot", "arcsin", "arccos", "arctan",
    "bigcup", "bigcap", "oint", "iint", "iiint", "circ", "bullet", "star", "ast", "cdot", "times", "div", "pm", "mp", "leq", "geq",
    "ll", "gg", "subset", "supset", "nabla", "partial", "degree", "angle", "measuredangle", "triangle", "square", "parallel", "perp",
    "cong", "simeq", "sim", "approx", "equiv", "propto", "Rightarrow", "Leftrightarrow", "iff", "implies", "leftrightarrow",
    "rightleftharpoons", "longleftrightarrow", "matrix", "pmatrix", "bmatrix", "vmatrix", "Vmatrix", "cases", "aligned", "gathered",
    "array", "hline", "pi", "theta", "lim", "sum", "prod", "int", "sqrt", "frac", "left", "right", "begin", "end", "vec",
})


# the style words of a mathematical alphanumeric character's Unicode name, longest first ("BOLD ITALIC" before "BOLD")
_ALNUM_STYLES = (("DOUBLE-STRUCK ITALIC", None), ("SANS-SERIF BOLD ITALIC", None), ("SANS-SERIF BOLD", None), ("SANS-SERIF ITALIC", None),
                 ("BOLD SCRIPT", None), ("BOLD FRAKTUR", None), ("BOLD ITALIC", "boldsymbol"), ("DOUBLE-STRUCK", "mathbb"),
                 ("SANS-SERIF", None), ("MONOSPACE", None), ("FRAKTUR", None), ("BLACK-LETTER", None), ("SCRIPT", "mathcal"),
                 ("BOLD", "mathbf"), ("ITALIC", ""))


def math_alphanumeric(ch: str) -> tuple[str, str | None] | None:
    """(base character, font command) of a mathematical alphanumeric character - Word for the web writes an equation's letters as
    these (𝑥 U+1D465, 𝐹, 𝛼, ℎ U+210E for italic h, 𝜕) instead of plain letters with a style property. "" is the maths default
    (italic letter), None a style no allowed command preserves (fraktur, sans-serif, monospace ...). None for any other character."""
    if ch.isascii() or not unicodedata.decomposition(ch).startswith("<font>"):
        return None
    base = unicodedata.normalize("NFKC", ch)
    name = unicodedata.name(ch, "")
    if name == "PLANCK CONSTANT":
        return base, ""
    rest = name.removeprefix("MATHEMATICAL ")
    for words, cmd in _ALNUM_STYLES:
        if rest.startswith(words + " "):
            return base, cmd
    return base, None
