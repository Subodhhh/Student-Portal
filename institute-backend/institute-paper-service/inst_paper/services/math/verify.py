"""Independent structural validator (Cycle 2.3, phase B1).

A conversion is accepted automatically only if this module, reading the ORIGINAL source and the GENERATED LaTeX separately, finds
the same structure in both. It never uses the recognition lexer, parser, their tree or their decisions; it shares only character
tables (data). Each side is reduced to a canonical list of atoms

    (symbol, style, path)

  symbol  the character as typed, canonicalised (− and - are "-", µ is μ, ∑ and Σ are "∑" ...);
  style   for Latin letters: "u" upright or "i" italic in the LaTeX; on the source side the EXPECTED style: "u", "i",
          "*" (either is correct) - or the source is refused when its intended style cannot be established;
  path    where the atom sits: inside a superscript / subscript, the numerator or denominator of a numbered fraction, the index or
          body of a root.

Grouping brackets that only delimit an operand (the numerator of (a+b)/c, the power of x^(n+1)) are not atoms; every other bracket
is. The source reader applies its own documented conventions and REFUSES (returns a reason) whenever the source does not settle the
structure: a power like e^3t, a denominator such as 1/2x, chained slashes a/b/c, a lone unit symbol with a power (s⁻¹), a unit
letter after an algebraic term (5t N). A refusal means the field goes to provider review; it is never a reason to accept.

The validator is deliberately conservative and incomplete: some correct conversions are refused (review), none is accepted
without both sides agreeing.
"""
import re
import unicodedata
from xml.etree import ElementTree as ET

from .symbols import ADD, ATOMS, BIG, GREEK, MUL, RELATIONS, ROOTS, SUBSCRIPTS, SUPERSCRIPTS, VULGAR

# ------------------------------------------------------------------------------------------------------------------- vocabulary
# The validator's OWN unit vocabulary (decision: SI prefixes and units plus min, h, L, eV, atm, °C), with the prefixes each unit
# really takes - not every prefix-unit combination.
_PREFIXED = {"m": "kcmμnd", "g": "kmμ", "s": "mμnp", "A": "kmμ", "N": "kmμMG", "J": "kMGmμ", "W": "kMGmμ", "C": "kμnmp",
             "V": "kmμM", "F": "μnpm", "Ω": "kMm", "T": "mμ", "Pa": "kMG", "Hz": "kMG", "L": "mμ", "eV": "kMG", "mol": "mμ",
             "H": "mμ"}
_PLAIN_UNITS = {"K", "cd", "Wb", "S", "rad", "sr", "lm", "lx", "Bq", "Gy", "Sv", "min", "h", "atm", "bar", "cal", "kcal", "mL", "ml",
                "°C", "Da"}
UNIT_WORDS = frozenset(_PLAIN_UNITS | set(_PREFIXED) | {p + u for u, ps in _PREFIXED.items() for p in ps})
UNIT_LETTERS = frozenset(u for u in UNIT_WORDS if len(u) == 1)
# Unit words that are units even without a number before them (never a product of variables in practice). Two-letter
# prefix+symbol pairs such as nC, mA, kN are NOT here: without a quantity they may be products of variables.
STANDALONE_UNITS = frozenset({"mol", "mmol", "kg", "cm", "mm", "km", "rad", "atm", "kcal", "cal", "Hz", "kHz", "MHz", "GHz", "Pa", "kPa",
                              "MPa", "eV", "keV", "MeV", "mL", "ml", "Wb", "°C"})
# cd, lm, lx (candela, lumen, lux) are units only after a number: on their own they are the products c·d, l·m, l·x of algebra
FUNCTION_WORDS = frozenset("sin cos tan cot sec csc cosec arcsin arccos arctan sinh cosh tanh coth log ln lg exp det lim max min "
                           "gcd deg arg tr Re Im erf erfc Ei sgn".split())
LABEL_UPRIGHT = frozenset({"pH", "pOH", "pKa", "pKb"})
DIMENSIONS = set("MLTINJ") | {"Θ"}
_INDEX_LETTERS = set("ijklmnpqrst")
_ELEMENT = re.compile(r"(?:[A-Z][a-z]?)")

_CANON = {"−": "-", "–": "-", "—": "-", "*": "·", "⋅": "·", "∙": "·", "µ": "μ", "Σ": "∑", "Π": "∏", "⟶": "→", "⇄": "⇌", "″": "''",
          "′": "'", "⩽": "≤", "⩾": "≥"}
_TWO = {"<=": "≤", ">=": "≥", "!=": "≠", "->": "→", "=>": "⇒"}
_RELATION_CHARS = {c for c in RELATIONS if len(c) == 1} | {"⇌", "→"}
_OPERATOR_CHARS = set(ADD) | set(MUL) | _RELATION_CHARS | {",", ";", "∈", "∉", "⊂", "⊆", "∪", "∩"}
_BIG = {**{k: _CANON.get(k, k) for k in BIG}, "Σ": "∑", "Π": "∏"}


class Refuse(Exception):
    pass


class _Ctx:
    """The request's settings, passed explicitly through every call - never global or per-thread state.
      domain   the paper's subject category, resolved from the authoritative backend subject_id (never from the file); None for an
               unknown or unconfigured subject, which enables no subject convention. Two conventions use it: in Mathematics a single
               capital letter with a power (A⁻¹, B²) is a matrix or set, never a unit; in Chemistry an uppercase M after a spaced
               number (10⁻³ M, 0.5 M) is the molar concentration unit and element formulas follow chemistry conventions.
      context  the whole field the span comes from: a letter the field DEFINES with '=' (A = [1 2; 3 4]) is a variable, so a power
               on it later in the field (A⁻¹) is not a unit. "1 m = 100 cm" defines nothing: the letter follows a number."""
    __slots__ = ("domain", "context", "unit_answer")

    def __init__(self, domain: str | None = None, context: str = "", unit_answer: bool = False):
        # unit_answer: the field is a whole answer option to a question that asks for a unit, so a lone s⁻¹ is the unit
        self.domain, self.context, self.unit_answer = domain, context, unit_answer


def _defines(w: str, ctx: _Ctx) -> bool:
    return re.search(rf"(?<![A-Za-z0-9.,⁰¹²³⁴⁵⁶⁷⁸⁹⁻])(?<![0-9⁰¹²³⁴⁵⁶⁷⁸⁹] ){re.escape(w)}\s*=(?!=)", ctx.context) is not None


# Font styles that carry meaning (bold vector F, the set ℝ): compared exactly on both sides, never treated as "either is fine".
FONT_STYLES = frozenset({"b", "bi", "bb", "scr", "frak", "sf", "tt"})
_UNICODE_STYLE = {"BOLD": "b", "BOLD ITALIC": "bi", "ITALIC": "i", "DOUBLE-STRUCK": "bb", "SCRIPT": "scr", "FRAKTUR": "frak",
                  "BLACK-LETTER": "frak", "SANS-SERIF": "sf", "MONOSPACE": "tt"}
_STYLED = re.compile(r"(?:MATHEMATICAL ([A-Z -]+?)|(DOUBLE-STRUCK|SCRIPT|BLACK-LETTER)) (CAPITAL|SMALL) ([A-Z])")   # 𝐅 / ℝ, never ˣ


def _math_italic(ch: str) -> str | None:
    """The plain character of a mathematical ITALIC character that _unicode_style does not compare by style: Greek (𝛼, 𝜋), 𝜕, 𝛻
    and ℎ (U+210E, italic h). Word for the web writes every equation letter in this form; italic is the maths default."""
    if ch.isascii() or not unicodedata.decomposition(ch).startswith("<font>"):
        return None
    name = unicodedata.name(ch, "")
    return unicodedata.normalize("NFKC", ch) if name == "PLANCK CONSTANT" or name.startswith("MATHEMATICAL ITALIC ") else None


def _unicode_style(ch: str):
    """(letter, style) of a mathematical styled letter (𝐅, ℝ, 𝒜, 𝑥), None for any other character; Refuse for a styled letter whose
    style is not compared (bold sans-serif ...)."""
    if ch.isascii():
        return None
    m = _STYLED.fullmatch(unicodedata.name(ch, ""))
    if not m:
        return None
    style = _UNICODE_STYLE.get(m.group(1) or m.group(2))
    if style is None:
        raise Refuse(f"the styled letter {ch!r} is not compared by the independent check")
    return (m.group(4) if m.group(3) == "CAPITAL" else m.group(4).lower()), style


# Element symbols: data for the chemistry convention (an element symbol reads the same upright or italic in a formula).
ELEMENTS = frozenset("""H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr
Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At
Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og""".split())


def _elements(word: str) -> int:
    """Number of element symbols the word is made of (CaCO -> 3), 0 if it is not made only of element symbols."""
    parts = _ELEMENT.findall(word)
    if "".join(parts) != word or not all(p in ELEMENTS for p in parts):
        return 0
    return len(parts)


def _c(ch: str) -> str:
    return _CANON.get(ch, ch)


# ------------------------------------------------------------------------------------------------------------- source tokens
class _T:
    __slots__ = ("kind", "value", "space", "marks", "style")

    def __init__(self, kind, value, space, marks=()):
        self.kind, self.value, self.space, self.marks, self.style = kind, value, space, list(marks), None


def _tokens(text: str) -> list[_T]:
    out: list[_T] = []
    i, n, space = 0, len(text), False
    while i < n:
        c = text[i]
        if c in "":
            raise Refuse("the text contains extraction markers")
        if c.isspace():
            space, i = True, i + 1
            continue
        if text[i:i + 2] in _TWO:
            out.append(_T("OP", _TWO[text[i:i + 2]], space))
            i += 2
        elif c.isascii() and c.isalpha():
            j = i
            while j < n and text[j].isascii() and text[j].isalpha():
                j += 1
            out.append(_T("W", text[i:j], space))
            i = j
        elif (styled := _unicode_style(c)) is not None:
            out.append(_T("SL", styled[0], space))                       # 𝐅, ℝ: a letter whose font is part of its meaning
            out[-1].style = styled[1]
            i += 1
        elif c.isdigit() and c.isascii():
            j = i
            while j < n and (text[j].isdigit() and text[j].isascii() or (text[j] == "." and j + 1 < n and text[j + 1].isdigit())):
                j += 1
            out.append(_T("N", text[i:j], space))
            i = j
        elif c in SUPERSCRIPTS or c in SUBSCRIPTS:
            table, kind = (SUPERSCRIPTS, "SUP") if c in SUPERSCRIPTS else (SUBSCRIPTS, "SUB")
            j = i
            while j < n and text[j] in table:
                j += 1
            out.append(_T(kind, "".join(table[x] for x in text[i:j]), space))
            i = j
        elif c in "⃗̂":
            if not out or out[-1].kind not in ("W", "G"):
                raise Refuse("an accent without a letter")
            out[-1].marks.append("vec" if c == "⃗" else "hat")
            i += 1
        elif c in VULGAR:
            out.append(_T("VF", VULGAR[c], space))
            i += 1
        elif c in ROOTS:
            out.append(_T("ROOT", ROOTS[c] or "", space))
            i += 1
        elif c in _BIG and (c in BIG or (i + 1 < n and (text[i + 1] in SUPERSCRIPTS or text[i + 1] in SUBSCRIPTS or text[i + 1] in "_^"))):
            out.append(_T("BIG", _BIG[c], space))
            i += 1
        elif c in GREEK or c == "µ":
            out.append(_T("G", _c(c), space))
            i += 1
        elif not c.isascii() and len(d := unicodedata.normalize("NFD", c)) == 2 and d[1] == "̂" and d[0].isalpha():
            out.append(_T("W", d[0], space, ["hat"]))
            i += 1
        else:
            kind = {"^": "CARET", "_": "USC", "(": "OPEN", "[": "OPEN", "{": "OPEN", ")": "CLOSE", "]": "CLOSE", "}": "CLOSE",
                    "⌊": "OPEN", "⌈": "OPEN", "⌋": "CLOSE", "⌉": "CLOSE",
                    "|": "BAR", "/": "SLASH"}.get(c)
            if kind:
                out.append(_T(kind, c, space))
            elif c in ATOMS or c in "∂":
                out.append(_T("A", c, space))
            elif c in "°'′″!":
                out.append(_T("POST", _c(c), space))
            else:
                out.append(_T("OP", _c(c), space))
            i += 1
        space = False
    return out


# ------------------------------------------------------------------------------------------------------- expected styles
def _quantity_before(toks: list[_T], k: int) -> bool:
    """A number (or n × 10ᵏ, possibly followed by its powers) ends right before token k, separated by a space."""
    j = k - 1
    while j >= 0 and toks[j].kind in ("SUP", "SUB") and not toks[j].space:
        j -= 1
    if j >= 0 and toks[j].kind == "CARET":
        return False
    return j >= 0 and toks[j].kind == "N" and toks[k].space


def _unit_word(t: _T) -> bool:
    return t.kind in ("W", "G") and t.value in UNIT_WORDS or (t.kind == "W" and t.value in UNIT_WORDS)


def _glued_chunks(toks: list[_T]):
    """(start, end) of runs of tokens typed without spaces between them."""
    k = 0
    while k < len(toks):
        j = k + 1
        while j < len(toks) and not toks[j].space:
            j += 1
        yield k, j
        k = j


def _species_chunks(toks: list[_T], ctx: _Ctx):
    """Glued runs; in Chemistry each run is also cut at '+' and reaction arrows, so Al³⁺+3OH⁻ or MnO₄⁻+8H⁺+5e⁻ is read species by
    species (the sign between two species is not part of either)."""
    for a, b in _glued_chunks(toks):
        if ctx.domain != "chemistry":
            yield toks[a:b]
            continue
        cur: list[_T] = []
        for t in toks[a:b]:
            if t.kind == "OP" and t.value in ("+", "→", "⇌", "↔") and cur:
                yield cur
                cur = []
            else:
                cur.append(t)
        if cur:
            yield cur


def _chemistry(words: list, subs: list, charge: bool, isotope: bool, states: bool, reaction: bool, ctx: _Ctx) -> bool:
    """Whether element-shaped letters are a chemical formula (either style reads correctly). Outside Chemistry only unmistakable
    structure counts - two or more elements with a subscript count (H₂O, CO₂), a charge (Na⁺) or a mass number (²³⁵U) - so V₁, T²,
    O₂ in a Physics paper are variables whose style is checked. In Chemistry a single element with a count of 2 or more (O₂, Cl₂),
    a state symbol or a reaction arrow also counts; a count of 1 never does (P₁, V₁, K₁ are variables: real formulas omit 1)."""
    n = sum(_elements(w) for w in words)
    if not words or any(_elements(w) == 0 for w in words):
        return False
    counts = [int(s) for s in subs if s.isdigit()]
    if any(c == 1 for c in counts):
        return False                                  # P₁V₁, K₁: real formulas never write a count of 1
    if charge or isotope or n >= 2 and counts:
        return True
    return ctx.domain == "chemistry" and (any(c >= 2 for c in counts) or states or reaction)


_NOBLE = {"He", "Ne", "Ar", "Kr", "Xe", "Rn"}


def _orbital_labels(toks: list[_T]) -> None:
    """Chemistry convention, read independently of the converter: in an electron configuration the subshell letter after the shell
    number (1s², 2p⁶, 3d¹⁰) and a hybridisation (sp³, dsp², sp³d²) are upright labels; [Ne] is the neon core. Counts are powers."""
    for a, b in _glued_chunks(toks):
        chunk = toks[a:b]
        kinds = [t.kind for t in chunk]
        if kinds[:2] == ["N", "W"] and len(chunk[0].value) == 1 and chunk[0].value in "1234567" and chunk[1].value in ("s", "p", "d", "f") \
                and all(k == "SUP" for k in kinds[2:]) and len(chunk) <= 3:
            chunk[1].style = "u"
        elif all(k in ("W", "SUP") for k in kinds) and re.fullmatch(r"(d\d?)?sp\d?(d\d?)?",
                                                                    "".join(t.value for t in chunk)) and "sp" in [t.value[-2:] for t in chunk]:
            for t in chunk:
                if t.kind == "W":
                    t.style = "u"
        elif kinds == ["OPEN", "W", "CLOSE"] and chunk[0].value == "[" and chunk[1].value in _NOBLE:
            chunk[1].style = "u"


def _mark_conventions(toks: list[_T], ctx: _Ctx) -> None:
    """Fixed, documented conventions applied before the token-by-token rules (they only ever ESTABLISH a style):
    chemistry chunks, bracketed dimensional formulas, unit groups without a number, prefixed units."""
    reaction = any(t.kind == "OP" and t.value in ("→", "⇌") for t in toks)
    state_words = {"aq", "s", "l", "g"}
    if ctx.domain == "chemistry":
        _orbital_labels(toks)
    for chunk in _species_chunks(toks, ctx):
        allowed = all(t.kind in ("W", "SUB", "SUP", "OPEN", "CLOSE") or t.kind == "N" and (n == 0 or chunk[n - 1].kind == "OP")
                      or t.kind == "OP" and t.value in ("·", ".", "=", "≡", "-") and 0 < n < len(chunk) - 1
                      for n, t in enumerate(chunk))
        states = [t for n, t in enumerate(chunk) if t.kind == "W" and t.value in state_words and n and chunk[n - 1].kind == "OPEN"]
        words = [t for t in chunk if t.kind == "W" and t not in states and not (t.value == "e" and sum(x.kind == "W" for x in chunk) == 1)]
        electron = [t for t in chunk if t.kind == "W" and t.value == "e" and t not in words]
        if not words and not electron:
            continue
        charge = any(t.kind == "SUP" and re.fullmatch(r"\d*[+-]", t.value) for t in chunk)
        isotope = chunk[0].kind == "SUP" and chunk[0].value.isdigit()
        subs = [t.value for t in chunk if t.kind == "SUB"]
        if allowed and (electron and charge or _chemistry([t.value for t in words], subs, charge, isotope, bool(states), reaction, ctx)):
            for t in words + states + electron:
                t.style = "*"
    # [M L² T⁻²]: only dimension symbols, each at most once, at least one power written
    k = 0
    while k < len(toks):
        if toks[k].kind == "OPEN" and toks[k].value == "[":
            j = k + 1
            while j < len(toks) and toks[j].kind != "CLOSE":
                j += 1
            inner = toks[k + 1:j]
            letters = "".join(t.value for t in inner if t.kind in ("W", "G"))
            caret_ok = all(t.kind in ("W", "G", "SUP", "CARET") or t.kind == "N" and n and inner[n - 1].kind in ("CARET", "OP")
                           or t.kind == "OP" and t.value == "-" and n and inner[n - 1].kind == "CARET" for n, t in enumerate(inner))
            powered = any(t.kind == "SUP" and re.fullmatch(r"-?\d+", t.value) or t.kind == "CARET" for t in inner)
            if (inner and j < len(toks) and toks[j].value == "]" and caret_ok and letters and set(letters) <= DIMENSIONS and powered
                    and len(set(letters)) != len(letters)):
                raise Refuse("a bracket of dimension symbols with a repeated symbol (like [M M²]) is not a dimensional formula")
            if (inner and j < len(toks) and toks[j].value == "]" and caret_ok
                    and set(letters) <= DIMENSIONS and len(set(letters)) == len(letters) and powered):
                for t in inner:
                    if t.kind == "W":
                        t.style = "u"
            k = j
        k += 1
    # mol L⁻¹ s⁻¹, m s⁻¹, N m² C⁻²: two or more spaced unit symbols with a unit word or a negative power, not all single capitals
    k = 0
    while k < len(toks):
        group, j = [], k
        while j < len(toks) and toks[j].kind == "W" and toks[j].value in UNIT_WORDS and (j == k or toks[j].space):
            group.append(toks[j])
            j += 1
            while j < len(toks) and not toks[j].space and (toks[j].kind == "SUP" or toks[j].kind == "CARET"
                                                          or toks[j].kind == "OP" and toks[j].value in "+-" and toks[j - 1].kind == "CARET"
                                                          or toks[j].kind == "N" and toks[j - 1].kind in ("CARET", "OP")):
                j += 1
        negative = any(toks[x].kind == "SUP" and toks[x].value.startswith("-") or toks[x].kind == "OP" and toks[x].value == "-"
                       and x and toks[x - 1].kind == "CARET" for x in range(k, j))
        if (len(group) >= 2 and (negative or any(len(w.value) > 1 for w in group))
                and not all(len(w.value) == 1 and w.value.isupper() for w in group)
                and (j >= len(toks) or toks[j].kind in ("OP", "SLASH", "CLOSE"))):
            for w in group:
                w.style = "u"
            k = j
        else:
            k += 1


_FIELD_QUANTITY = re.compile(r"(?<![\w.])\d+(?:\.\d+)?\s+(?:" + "|".join(sorted(map(re.escape, UNIT_WORDS), key=len, reverse=True))
                             + r")(?![A-Za-z])")


def _field_unit(w: str, ctx: _Ctx) -> bool:
    """A unit letter ending an algebraic term (3t²−2t³ m) is the unit only when its field proves it: a Physics or Chemistry paper,
    a number with a spaced unit elsewhere in the field (t=2 s), and the letter appearing nowhere else in the field as a symbol."""
    return (ctx.domain in ("physics", "chemistry") and bool(_FIELD_QUANTITY.search(ctx.context))
            and len(re.findall(rf"(?<![A-Za-z]){re.escape(w)}(?![A-Za-z])", ctx.context)) == 1)


def _classify(toks: list[_T], ctx: _Ctx) -> None:
    """Expected style of every Latin letter group. Raises Refuse when the source does not settle unit vs variable."""
    _mark_conventions(toks, ctx)
    if ctx.unit_answer and ctx.domain in ("physics", "chemistry") and _only_units(toks, ctx):
        for t in toks:
            if t.kind == "W":
                t.style = "u"                                               # s⁻¹, M s⁻¹ answering "Unit of rate constant ...?"
        return
    prev_unit = False
    for k, t in enumerate(toks):
        if t.kind == "W" and t.style is not None:
            prev_unit = t.style == "u"
            continue
        if t.kind == "W" and k and not t.space and (toks[k - 1].kind == "G" and toks[k - 1].value + t.value in UNIT_WORDS
                                                     and _quantity_before(toks, k - 1)
                                                     or toks[k - 1].kind == "POST" and toks[k - 1].value + t.value in UNIT_WORDS):
            t.style, prev_unit = "u", True                                  # 10 μF, 25 °C
            continue
        if t.kind == "W" and k + 1 < len(toks) and not toks[k + 1].space and toks[k + 1].kind == "G" \
                and t.value + toks[k + 1].value in UNIT_WORDS and _quantity_before(toks, k):
            t.style, prev_unit = "u", True                                  # 4 kΩ
            continue
        if t.kind == "G" and t.value == "Ω":
            prev_unit = _quantity_before(toks, k) or prev_unit and t.space
            continue
        if t.kind != "W":
            if t.kind not in ("SUP", "SUB", "SLASH", "CARET") and not (t.kind == "N" and not t.space and k and toks[k - 1].kind == "CARET") \
                    and not (t.kind == "OP" and t.value == "·" and not t.space):        # N·m: a glued dot keeps the unit context
                prev_unit = False if t.kind not in ("SUP", "SUB") else prev_unit
            continue
        w = t.value
        nxt = toks[k + 1] if k + 1 < len(toks) else None
        if k and not t.space and toks[k - 1].kind == "N" and w not in STANDALONE_UNITS and (
                len(w) >= 2 and w in UNIT_WORDS or nxt is not None and not nxt.space and nxt.kind == "G" and w + nxt.value in UNIT_WORDS):
            # 4kΩ, 2.4MJ, 3nC: a prefixed unit glued to its number could be the unit or a product of variables
            raise Refuse(f"'{w}' glued to a number could be a unit or variables; put a space before a unit")
        if w == "M" and _quantity_before(toks, k) and ctx.domain != "chemistry":
            raise Refuse("'M' after a number could be the molar unit or a variable; only a Chemistry paper settles it")
        if w == "M" and ctx.domain == "chemistry":
            # molar concentration: only uppercase M, only in a Chemistry paper, only right after a spaced number (10⁻³ M, 0.5 M).
            # 0.1M (glued) and a lone M⁻¹ could be the molar unit or the molar-mass variable M: never guessed.
            prev = toks[k - 1] if k else None
            if _quantity_before(toks, k):
                t.style, prev_unit = "u", True
                continue
            if prev is not None and not t.space and prev.kind in ("N", "SUP"):
                raise Refuse("'M' glued to a number could be the molar unit or a variable; put a space before a unit")
            if _lone_powered(toks, k):
                raise Refuse("'M' with a power on its own could be the molar unit or a variable")
        if w in FUNCTION_WORDS and nxt is not None and nxt.kind not in ("OP",):
            t.style, prev_unit = "u", False
        elif w in LABEL_UPRIGHT and not (nxt is not None and nxt.kind == "SUB" and not nxt.space):
            t.style, prev_unit = "u", False                                 # pH; but pH₂ is p times H₂ (a partial pressure)
        elif w in UNIT_WORDS and (_quantity_before(toks, k) or (prev_unit and t.space)
                                   or (k >= 2 and toks[k - 1].kind == "SLASH" and prev_unit)
                                   or (k >= 2 and toks[k - 1].kind == "OP" and toks[k - 1].value == "·" and not t.space and prev_unit)):
            t.style, prev_unit = "u", True
        elif w in STANDALONE_UNITS:
            t.style, prev_unit = "u", True                                  # mol, kg: an unmistakable unit word on its own
        elif _chem_word(toks, k, ctx):
            t.style, prev_unit = "*", False
        elif len(w) == 1 and w in UNIT_LETTERS and t.space and k and toks[k - 1].kind in ("W", "N", "G", "CLOSE", "SUP", "SUB") \
                and not _quantity_before(toks, k):
            if prev_unit:
                t.style, prev_unit = "u", True                              # mol L: a unit after a unit
            elif toks[k - 1].kind == "W" and toks[k - 1].value in FUNCTION_WORDS:
                t.style, prev_unit = "i", False                             # sin A: the function's argument
            elif _ends_side(toks, k) and _field_unit(w, ctx):
                t.style, prev_unit = "u", True                              # x(t)=3t²−2t³ m, ... at t=2 s: the field settles it
            elif _ends_side(toks, k):
                raise Refuse(f"'{w}' after an algebraic term could be a unit or a variable")
            else:
                t.style, prev_unit = "i", False
        elif len(w) == 1 and w in UNIT_LETTERS and _lone_powered(toks, k) and not (ctx.domain == "mathematics" and w.isupper()) \
                and not _defines(w, ctx):
            raise Refuse(f"'{w}' with a power on its own could be a unit or a variable")
        else:
            t.style, prev_unit = "i", False
    _relation_units(toks)


def _ends_side(toks, k) -> bool:
    j = k + 1
    while j < len(toks) and toks[j].kind in ("SUP", "SUB") and not toks[j].space:
        j += 1
    return j >= len(toks) or toks[j].kind == "OP" and toks[j].value in _RELATION_CHARS | {","}


def _only_units(toks: list[_T], ctx: _Ctx) -> bool:
    """The whole text is unit symbols (M only in Chemistry) with integer powers, spaced or joined by '·' or '/': s⁻¹, M s⁻¹, L mol⁻¹ s⁻¹."""
    allowed = UNIT_WORDS | ({"M"} if ctx.domain == "chemistry" else set())
    for n, t in enumerate(toks):
        if t.kind == "W" and t.value in allowed and (n == 0 or t.space or toks[n - 1].kind == "SLASH" or toks[n - 1].value == "·"):
            continue
        if t.kind == "SUP" and re.fullmatch(r"-?\d+", t.value) and n and not t.space:
            continue
        if t.kind == "CARET" and n + 1 < len(toks) and not t.space:
            continue
        if (t.kind == "OP" and t.value in "-−" or t.kind == "N") and n and toks[n - 1].kind in ("CARET", "OP") and not t.space:
            continue
        if t.kind == "SLASH" or t.kind == "OP" and t.value == "·":
            continue
        return False
    return any(t.kind == "W" for t in toks)


def _lone_powered(toks, k) -> bool:
    nxt = toks[k + 1] if k + 1 < len(toks) else None
    powered = nxt is not None and not nxt.space and (nxt.kind == "SUP" and nxt.value.startswith("-")
                                                     or nxt.kind == "CARET" and k + 2 < len(toks) and toks[k + 2].value in "-−")
    prev = toks[k - 1] if k else None
    return powered and (prev is None or prev.kind == "OP" or prev.space and prev.kind not in ("N",))


def _chem_word(toks, k, ctx: _Ctx) -> bool:
    """H₂O, SO₄²⁻, CaCO₃: element symbols followed by subscript counts or a charge - shown upright or italic, both readable."""
    w = toks[k].value
    nxt = toks[k + 1] if k + 1 < len(toks) else None
    if nxt is None or nxt.space:
        return False
    sub = [nxt.value] if nxt.kind == "SUB" and nxt.value.isdigit() else []
    charge = nxt.kind == "SUP" and re.fullmatch(r"\d*[+-]", nxt.value) is not None
    return bool((sub or charge) and _chemistry([w], sub, charge, False, False, False, ctx))


def _relation_units(toks) -> None:
    """J s⁻¹ = W, F = kg m s⁻²: a lone unit letter opposite a side made only of units could be the unit (watt, farad) or a quantity
    symbol (W for work, F for force). Plain text does not settle it: refused. (A Word equation states the style itself.)"""
    sides, cur = [], []
    for t in toks:
        if t.kind == "OP" and t.value in _RELATION_CHARS:
            sides.append(cur)
            cur = []
        else:
            cur.append(t)
    sides.append(cur)
    if len(sides) < 2:
        return
    unit_side = any(s and all(x.kind in ("SUP", "SUB", "CARET") or x.kind == "W" and x.style == "u"
                              or x.kind in ("N", "OP") and n and s[n - 1].kind in ("CARET", "OP") for n, x in enumerate(s)) for s in sides)
    for s in sides:
        words = [x for x in s if x.kind == "W"]
        if unit_side and len(s) == 1 and len(words) == 1 and words[0].value in UNIT_LETTERS:
            raise Refuse(f"'{words[0].value}' opposite a side of units could be a unit or a quantity symbol")


# ------------------------------------------------------------------------------------------------------------- structure
class _Reader:
    """Turns the classified tokens into canonical atoms with paths."""

    def __init__(self, toks: list[_T], counter: list | None, ctx: _Ctx):
        self.t, self.i, self.ctx = toks, 0, ctx
        self.counter = counter if counter is not None else [0]

    def new_fraction(self) -> int:
        self.counter[0] += 1
        return self.counter[0]

    def peek(self, k=0):
        j = self.i + k
        return self.t[j] if j < len(self.t) else None

    # operand = base + glued scripts; returned as atom list (relative paths)
    def operand(self) -> list:
        t = self.t[self.i]
        if t.kind == "OPEN":
            j = self._closing(self.i)
            matrix = self.matrix(t.value, self.t[self.i + 1:j], self.t[j].value)
            if matrix is not None:
                self.i = j + 1
                return matrix
            inner, close = self.group()
            return [("(" if t.value == "(" else t.value, None, ())] + inner + [(close, None, ())]
        if t.kind == "BAR":
            j = self.i + 1
            while j < len(self.t) and self.t[j].kind != "BAR":
                j += 1
            if j < len(self.t):
                matrix = self.matrix("|", self.t[self.i + 1:j], "|")
                if matrix is not None:
                    self.i = j + 1
                    return matrix
            inner = self.until_bar()
            return [("|", None, ())] + inner + [("|", None, ())]
        self.i += 1
        if t.kind == "SL":
            return [(t.value, t.style, ())] + self.scripts()
        if t.kind == "W":
            if t.style == "u" or t.style == "*":
                atoms = [(ch, t.style, (), tuple(t.marks) if n == len(t.value) - 1 else ()) for n, ch in enumerate(t.value)]
            else:
                atoms = [(ch, "i", (), tuple(t.marks) if n == len(t.value) - 1 else ()) for n, ch in enumerate(t.value)]
        elif t.kind == "N":
            atoms = [(ch, None, ()) for ch in t.value]
        elif t.kind == "G":
            atoms = [(t.value, None, (), tuple(t.marks))]
        elif t.kind == "VF":
            f = self.new_fraction()
            atoms = [(ch, None, (("f", f, "n"),)) for ch in str(t.value[0])] + [(ch, None, (("f", f, "d"),)) for ch in str(t.value[1])]
        elif t.kind in ("A", "BIG"):
            atoms = [(t.value, None, ())]
        elif t.kind == "ROOT":
            atoms = [("√", None, ())] + [(ch, None, (("r", "i"),)) for ch in t.value]
            nxt = self.peek()
            if nxt is None or nxt.space:
                raise Refuse("a root sign without an operand")
            body = self.script_operand()
            atoms += [(a[0], a[1], (("r", "b"),) + a[2], *a[3:]) for a in body]
            return atoms + self.scripts()
        else:
            raise Refuse(f"unexpected '{t.value}'")
        return atoms + self.scripts(base=t.value if t.kind in ("W", "A") else "")

    def scripts(self, first: bool = False, base: str = "") -> list:
        out = []
        seen: set = set()
        while (t := self.peek()) is not None and (not t.space or first):
            first = False
            if t.kind in ("SUP", "SUB", "CARET", "USC"):
                lvl = "^" if t.kind in ("SUP", "CARET") else "_"
                if lvl in seen:
                    raise Refuse("a power on a power (like 3^2^2) or a subscript on a subscript can be read in more than one way")
                seen.add(lvl)
            if t.kind in ("SUP", "SUB"):
                self.i += 1
                out += self.sub_script(t.value, lvl)
            elif t.kind in ("CARET", "USC"):
                self.i += 1
                body = self.script_operand(caret=base not in ("d", "∂"), label=lvl == "_")
                out += [(a[0], a[1], ((lvl,),) + a[2], *a[3:]) for a in body]
            elif t.kind == "POST":
                self.i += 1
                out += [(ch, None, ()) for ch in t.value]
            else:
                break
        return out

    def sub_script(self, content: str, lvl: str) -> list:
        if content.isascii() and content.isalpha() and len(content) >= 2:
            style = "*" if set(content) <= _INDEX_LETTERS and len(content) == 2 else "u"
            return [(ch, style, ((lvl,),)) for ch in content]
        inner = read_source(content, self.counter, self.ctx)
        return [(a[0], a[1], ((lvl,),) + a[2], *a[3:]) for a in inner]

    def script_operand(self, caret: bool = False, label: bool = False) -> list:
        t = self.peek()
        if t is None or t.space:
            raise Refuse("a power or subscript without an operand")
        if t.kind == "OPEN":
            inner, _ = self.group()
            return inner                                                    # grouping brackets: not atoms
        if t.kind == "OP" and t.value in "+-±":
            self.i += 1
            return [(t.value, None, ())] + self.script_operand(caret, label)
        if t.kind == "N":
            self.i += 1
            nxt = self.peek()
            if caret and nxt is not None and not nxt.space and nxt.kind in ("W", "G", "OPEN", "ROOT"):
                raise Refuse(f"it is unclear what is in the power after '^{t.value}' (like e^3t)")
            return [(ch, None, ()) for ch in t.value]
        if t.kind == "W":
            self.i += 1
            if label and len(t.value) >= 2:
                style = "*" if set(t.value) <= _INDEX_LETTERS and len(t.value) == 2 else "u"
                return [(ch, style, ()) for ch in t.value]
            if len(t.value) > 1:
                raise Refuse(f"it is unclear how much of '{t.value}' is in the power")
            nxt, after = self.peek(), self.peek(1)
            # e^x+C, xe^x−∫e^x dx: an integral or sum, or the constant of integration C ending the expression, is never in a power
            ends = nxt is not None and nxt.kind == "OP" and nxt.value in "+-" and after is not None and not after.space and (
                after.kind == "BIG" or after.kind == "W" and after.value == "C" and self.peek(2) is None)
            if caret and not ends and nxt is not None and not nxt.space and nxt.kind in ("OP", "W", "N") \
                    and nxt.value not in _RELATION_CHARS | {",", ")", "/"}:
                raise Refuse("it is unclear what is in the power (like e^x+1)")
            return [(t.value, "i", ())]
        if t.kind in ("G", "A"):
            self.i += 1
            return [(t.value, None, ())]
        if t.kind == "ROOT" or t.kind == "VF":
            return self.operand()
        raise Refuse(f"unexpected '{t.value}' in a power")

    def _closing(self, i: int) -> int:
        depth, j = 0, i
        while j < len(self.t):
            depth += {"OPEN": 1, "CLOSE": -1}.get(self.t[j].kind, 0)
            if depth == 0:
                return j
            j += 1
        raise Refuse("a bracket is not closed")

    @staticmethod
    def _top_split(toks: list, sep) -> list:
        """Split at top-level tokens for which sep(token) is true (brackets nest)."""
        parts, cur, depth = [], [], 0
        for t in toks:
            if depth == 0 and sep(t):
                parts.append(cur)
                cur = []
                continue
            depth += {"OPEN": 1, "CLOSE": -1}.get(t.kind, 0)
            cur.append(t)
        parts.append(cur)
        return parts

    def matrix(self, open_: str, inner: list, close: str):
        """[1 2; 3 4], (a, b; c, d), |a b; c d|, [[1,2],[3,4]]: rows and cells, each cell's atoms at path ("m", row, column), so a
        matrix of another shape (2×2 written as 1×4) never matches. None when the bracket is not a matrix."""
        is_semi = lambda t: t.kind == "OP" and t.value == ";"
        is_comma = lambda t: t.kind == "OP" and t.value == ","
        if open_ == "[" and inner and inner[0].kind == "OPEN" and inner[0].value == "[":
            rows = []
            for part in self._top_split(inner, is_comma):
                if len(part) < 2 or part[0].value != "[" or part[-1].value != "]":
                    raise Refuse("a matrix row is not in [ ]")
                rows.append(self._top_split(part[1:-1], is_comma))
        elif len(self._top_split(inner, is_semi)) > 1:
            rows = []
            for row in self._top_split(inner, is_semi):
                if len(self._top_split(row, is_comma)) > 1:
                    rows.append(self._top_split(row, is_comma))
                    continue
                cells, cur, depth = [], [], 0
                for t in row:                                       # entries separated by spaces
                    if depth == 0 and t.space and cur:
                        cells.append(cur)
                        cur = []
                    depth += {"OPEN": 1, "CLOSE": -1}.get(t.kind, 0)
                    cur.append(t)
                cells.append(cur)
                for c in cells:
                    if c and c[0].kind == "OP" and (len(c) == 1 or c[0].value in "+-±"):
                        raise Refuse("a matrix entry separated only by spaces around a sign (like |1 -2; 3 4|); use commas")
                rows.append(cells)
        else:
            return None
        if any(not c for r in rows for c in r) or len({len(r) for r in rows}) != 1:
            raise Refuse("the rows of the matrix have different numbers of entries")
        out = [(open_, None, ())]
        for r, row in enumerate(rows, 1):
            for c, cell in enumerate(row, 1):
                out += [(a[0], a[1], (("m", r, c),) + a[2], *a[3:]) for a in _Reader(cell, self.counter, self.ctx).read()]
        return out + [(close, None, ())]

    def group(self):
        open_ = self.t[self.i]
        depth, j = 0, self.i
        while j < len(self.t):
            if self.t[j].kind == "OPEN":
                depth += 1
            elif self.t[j].kind == "CLOSE":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if j >= len(self.t):
            raise Refuse("a bracket is not closed")
        inner = self.t[self.i + 1:j]
        close = self.t[j].value
        self.i = j + 1
        del open_
        return self.sub(inner), close

    def until_bar(self):
        j = self.i + 1
        while j < len(self.t) and self.t[j].kind != "BAR":
            j += 1
        if j >= len(self.t):
            raise Refuse("a '|' is not closed")
        inner = self.t[self.i + 1:j]
        self.i = j + 1
        return self.sub(inner)

    def sub(self, toks):
        return _Reader(toks, self.counter, self.ctx).read()

    def read(self) -> list:
        """Items: operands and operators; fractions bind the operand run before '/' and one operand after it."""
        items: list = []                       # ("op", atoms, first_token) | ("x", atoms)
        while self.i < len(self.t):
            t = self.t[self.i]
            if t.kind == "OP" or t.kind == "POST":
                self.i += 1
                items.append(("x", [(t.value, None, ())], t))
                continue
            if t.kind == "SLASH":
                self.i += 1
                items.append(("/", t))
                continue
            if t.kind in ("SUP", "SUB", "CARET", "USC"):
                items.append(("o", self.scripts(first=True), t))          # a script with no base (isotopes ²³⁵U, ⁿCᵣ)
                continue
            if t.kind == "W" and t.value == "lim":
                items.append(("x", self.limit(), t))                       # an operator on what follows, never a numerator
                continue
            if t.kind == "BIG":
                start = self.i
                items.append(("x", self.operand(), self.t[start]))       # ∫, ∑ with their limits: same
                continue
            if t.kind == "N" and t.space and self.i and self.t[self.i - 1].kind == "N":
                raise Refuse("two numbers separated only by a space (like 1 1/2 or 10 000) can be read in more than one way")
            start = self.i
            atoms = self.operand()
            if self.i == start:
                raise Refuse(f"unexpected '{t.value}'")                  # never loop: no progress means not understood
            items.append(("o", atoms, self.t[start]))
        return self.fractions(items)

    def limit(self) -> list:
        """lim with its limit set as a subscript: limₓ→₀, lim_(x→0), lim(x→0), lim x→0 all mean the same."""
        t = self.t[self.i]
        self.i += 1
        atoms = [(ch, "u", ()) for ch in t.value]
        sub: list = []
        nxt = self.peek()
        j = self.i
        toks = []
        if nxt is not None and nxt.kind == "SUB" and not nxt.space:
            toks.append(nxt)
            j += 1
            if j < len(self.t) and self.t[j].kind == "OP" and self.t[j].value == "→" and not self.t[j].space:
                toks.append(self.t[j])
                j += 1
                if j < len(self.t) and not self.t[j].space and self.t[j].kind in ("SUB", "A", "N", "OP"):
                    if self.t[j].kind == "OP" and self.t[j].value in "+-" and j + 1 < len(self.t):
                        toks.append(self.t[j])
                        j += 1
                    toks.append(self.t[j])
                    j += 1
            self.i = j
            for x in toks:
                if x.kind == "SUB":
                    sub += read_source(x.value, self.counter, self.ctx)
                else:
                    sub.append((x.value, None, ()))
        elif nxt is not None and nxt.kind in ("OPEN", "USC"):
            if nxt.kind == "USC":
                self.i += 1
            inner, _ = self.group()
            sub = inner
        elif nxt is not None and nxt.space and nxt.kind == "W" and len(nxt.value) == 1:
            arrow = self.peek(1)
            if arrow is not None and arrow.kind == "OP" and arrow.value == "→":
                self.i += 2
                sub = [(nxt.value, "i", ()), ("→", None, ())] + self.operand()
        return atoms + [(a[0], a[1], (("_",),) + a[2], *a[3:]) for a in sub]

    def fractions(self, items) -> list:
        k = 0
        pending: list = []                     # operand run since the last operator
        starts: list = []                      # the first token of each pending operand (where it starts, whether a space precedes it)
        result: list = []

        def flush():
            nonlocal pending, starts
            for it in pending:
                result.extend(it)
            pending, starts = [], []

        while k < len(items):
            it = items[k]
            if it[0] == "x":
                flush()
                result.extend(it[1])
            elif it[0] == "o":
                pending.append(it[1])
                starts.append(it[2])
            else:                              # "/"
                if not pending:
                    raise Refuse("a '/' without a numerator")
                if k + 1 >= len(items) or items[k + 1][0] != "o":
                    raise Refuse("a '/' without a denominator")
                den_item = items[k + 1]
                num = [a for x in pending for a in x]
                num_tokens_units = any(a[1] == "u" for a in num)
                den = list(den_item[1])
                j = k + 2
                # x/ln x, 1/sin θ: a function name in the denominator takes its argument with it, as in print
                first_tok = den_item[2]
                if first_tok.kind == "W" and first_tok.value in FUNCTION_WORDS and first_tok.value != "lim" and first_tok.style == "u" \
                        and j < len(items) and items[j][0] == "o":
                    den += items[j][1]
                    j += 1
                # 1/(x−1)²: a power glued to a bracketed denominator is the bracket's own power
                while j < len(items) and items[j][0] == "o" and items[j][2].kind in ("SUP", "SUB", "CARET", "USC") and not items[j][2].space:
                    den += items[j][1]
                    j += 1
                # 2 m/s: unit over unit keeps the slash as typed
                if num and num[-1][1] == "u" and all(a[1] in ("u", None) for a in den if a[0].isalpha()) and any(
                        a[0].isalpha() and a[1] == "u" for a in den):
                    flush_atoms = num + [("/", None, ())] + den
                    pending = []
                    result.extend(flush_atoms)
                    k = j
                    continue
                # derivative: d/dx, dy/dx, ∂f/∂x - the denominator is d (or ∂) with its variable
                derivative = first_tok.kind == "W" and first_tok.value.startswith("d") or first_tok.kind == "A" and first_tok.value == "∂"
                # d/dx(x²), ∂/∂x(…): the operator alone over its differential applies to the bracket after it, outside the fraction
                operator = derivative and len(starts) == 1 and (starts[0].kind == "W" and starts[0].value == "d"
                                                                 or starts[0].kind == "A" and starts[0].value == "∂")
                # Leibniz notation: in "3 dy/dx" the quotient is dy/dx - its numerator starts at the d (or ∂) that begins a new word;
                # the operands before it are a coefficient outside the fraction (3·dy/dx), as in print
                lead = [n for n, s in enumerate(starts) if n and s.space and (s.kind == "W" and s.value.startswith("d")
                                                                               or s.kind == "A" and s.value == "∂")]
                if derivative and lead and all(not s.space for s in starts[lead[-1] + 1:]):
                    for x in pending[:lead[-1]]:
                        result.extend(x)
                    pending, starts = pending[lead[-1]:], starts[lead[-1]:]
                    num = [a for x in pending for a in x]
                if derivative:
                    while j < len(items) and items[j][0] == "o" and not items[j][2].space and first_tok.value in ("d", "∂") \
                            and not (operator and items[j][2].kind == "OPEN"):
                        den += items[j][1]
                        j += 1
                # 12 m / 4 s: a quantity over a quantity (only when the numerator is itself a quantity)
                elif num_tokens_units and first_tok.kind == "N":
                    while j < len(items) and items[j][0] == "o" and items[j][2].kind == "W" and items[j][2].style == "u":
                        den += items[j][1]
                        j += 1
                # NΔΦ/Δt: Δ glued to one symbol is the change in that quantity - one operand, never Δ times t
                elif (first_tok.kind == "G" and first_tok.value == "Δ" and len(den) == 1 and j < len(items) and items[j][0] == "o"
                      and not items[j][2].space and (items[j][2].kind == "G" or items[j][2].kind == "W" and len(items[j][2].value) == 1)):
                    den += items[j][1]
                    j += 1
                # h/2π, 1/4πε₀: a number glued to π, then optionally ε₀ or μ₀, is one constant factor (1/2πr, 1/2x are not)
                elif first_tok.kind == "N" and len(den) == 1 and j < len(items) and items[j][0] == "o" and not items[j][2].space \
                        and [a[0] for a in items[j][1]] == ["π"]:
                    run = [items[j][1]]
                    n = j + 1
                    while n < len(items) and items[n][0] == "o" and not items[n][2].space and [(a[0], a[2]) for a in items[n][1]] in (
                            [("ε", ()), ("0", (("_",),))], [("μ", ()), ("0", (("_",),))]):
                        run.append(items[n][1])
                        n += 1
                    if not (n < len(items) and items[n][0] == "o" and not items[n][2].space):
                        den += [a for x in run for a in x]
                        j = n
                # 1/[A]t, [A]₀/[A]t: the same bracket carries a subscript label elsewhere in the field, so t is its label too
                if (first_tok.kind == "OPEN" and first_tok.value == "[" and j < len(items) and items[j][0] == "o" and not items[j][2].space
                        and items[j][2].kind == "W" and len(items[j][2].value) == 1 and items[j][2].value.islower()
                        and self.ctx.domain in ("physics", "chemistry")
                        and re.search(re.escape("".join(a[0] for a in den)) + "[\u2080-\u209c]", self.ctx.context)):
                    den += items[j][1]
                    j += 1
                if j < len(items) and items[j][0] == "o" and not items[j][2].space and not (operator and items[j][2].kind == "OPEN"):
                    raise Refuse("it is unclear how much of the text after '/' is in the denominator (like 1/2x)")
                # a/bc, R/KT: a word of letters is a/(bc) or (a/b)c - one operand only for labelled quantities, the same capital with
                # different small labels on both sides (Tc/Th, Vs/Vp)
                if first_tok.kind == "W" and len(first_tok.value) > 1 and first_tok.style == "i" and not derivative \
                        and not (starts and starts[-1].kind == "W" and _labelled_pair(starts[-1].value, first_tok.value)) \
                        and not _shared_label(first_tok.value, self.ctx):
                    raise Refuse("it is unclear whether the letters after '/' are all in the denominator (like a/bc)")
                if j < len(items) and items[j][0] == "/":
                    raise Refuse("several '/' in a row (like a/b/c)")
                f = self.new_fraction()
                frac = [(a[0], a[1], (("f", f, "n"),) + a[2], *a[3:]) for a in _ungroup(num)]
                frac += [(a[0], a[1], (("f", f, "d"),) + a[2], *a[3:]) for a in _ungroup(den)]
                pending = [frac]
                k = j
                continue
            k += 1
        flush()
        return result


def _shared_label(word: str, ctx: _Ctx) -> bool:
    """xA in pA = pA°/xA (Physics, Chemistry): one small letter and a capital, where the same capital also follows a different small
    letter in the field - the capital is a component's label (p_A, x_A), so the word is one quantity."""
    if ctx.domain not in ("physics", "chemistry") or not re.fullmatch(r"[a-z][A-Z]", word):
        return False
    return bool(set(re.findall(rf"(?<![A-Za-z])([a-z]){word[1]}(?![A-Za-z])", ctx.context)) - {word[0]})


def _labelled_pair(a: str, b: str) -> bool:
    return all(re.fullmatch(r"[A-Z][a-z]{1,2}", w) for w in (a, b)) and a[0] == b[0] and a != b


def _ungroup(atoms: list) -> list:
    """(a + b) as a whole numerator or denominator: the brackets only group."""
    if len(atoms) >= 2 and atoms[0][0] == "(" and atoms[-1][0] == ")" and atoms[0][2] == () and atoms[-1][2] == ():
        depth = 0
        for n, a in enumerate(atoms):
            if a[2] == ():
                depth += {"(": 1, ")": -1}.get(a[0], 0)
                if depth == 0 and n < len(atoms) - 1:
                    return atoms
        return atoms[1:-1]
    return atoms


def read_source(text: str, counter: list | None = None, ctx: _Ctx | None = None) -> list:
    ctx = ctx if ctx is not None else _Ctx(context=text)
    toks = _subject_notation(_tokens(text), ctx)
    _classify(toks, ctx)
    return _Reader(toks, counter, ctx).read()


_LABELS = {"cell", "red", "ox", "rxn", "sys", "surr"}


def _subject_notation(toks: list[_T], ctx: _Ctx) -> list[_T]:
    """Labels written on the line, read as the validator's own conventions:
      E°cell, H°f: lowercase letters glued after a degree sign are a subscript label (the degree mark ends the symbol);
      Ecell: a capital glued to an electrochemical label word;
      t½ in Physics and Chemistry: a half glued to one letter is the half-life subscript."""
    out: list[_T] = []
    for t in toks:
        prev = out[-1] if out else None
        if (t.kind == "W" and not t.space and prev is not None and prev.kind == "POST" and prev.value == "°" and not prev.space
                and t.value.islower() and len(t.value) <= 8):
            out.append(_T("SUB", t.value, False))
        elif t.kind == "W" and len(t.value) > 1 and t.value[0].isupper() and t.value[1:] in _LABELS:
            out += [_T("W", t.value[0], t.space, list(t.marks)), _T("SUB", t.value[1:], False)]
        elif (t.kind == "VF" and not t.space and ctx.domain in ("physics", "chemistry") and prev is not None and prev.kind == "W"
              and len(prev.value) == 1):
            out.append(_T("SUB", f"{t.value[0]}/{t.value[1]}", False))
        else:
            out.append(t)
    return out


# ---------------------------------------------------------------------------------------------------------------- LaTeX side
_LATEX_SYMBOLS: dict[str, str] = {}
for _table in (GREEK, RELATIONS, ADD, MUL, ATOMS, BIG):
    for _ch, _cmd in _table.items():
        if _cmd.startswith("\\") and len(_ch) == 1:
            _LATEX_SYMBOLS.setdefault(_cmd[1:], _c(_ch))
_LATEX_SYMBOLS.update({"mu": "μ", "sum": "∑", "prod": "∏", "Sigma": "∑", "Pi": "∏", "to": "→", "rightarrow": "→",
                       "rightleftharpoons": "⇌", "leftrightarrow": "↔", "lvert": "|", "rvert": "|", "vert": "|", "mid": "|",
                       "partial": "∂", "infty": "∞", "cdot": "·", "times": "×", "div": "÷", "pm": "±", "mp": "∓", "le": "≤",
                       "ge": "≥", "ne": "≠", "neq": "≠", "leq": "≤", "geq": "≥", "equiv": "≡", "approx": "≈", "propto": "∝",
                       "prime": "'", "ldots": "…", "cdots": "…", "{": "{", "}": "}", "%": "%", "cup": "∪", "cap": "∩",
                       "in": "∈", "notin": "∉", "subset": "⊂", "subseteq": "⊆", "angle": "∠", "triangle": "△", "nabla": "∇",
                       "Rightarrow": "⇒", "Leftrightarrow": "⇔", "parallel": "∥", "perp": "⊥", "circ": "°",
                       "lfloor": "⌊", "rfloor": "⌋", "lceil": "⌈", "rceil": "⌉"})
_LATEX_SKIP = {",", ";", ":", "!", " ", "quad", "qquad", "displaystyle", "limits", "left", "right"}
_FONT_CMDS = {"mathrm": "u", "operatorname": "u", "text": "u", "textrm": "u", "mathit": "i", "mathbf": "b", "boldsymbol": "bi",
              "bm": "bi", "mathbb": "bb", "mathcal": "scr", "mathscr": "scr", "mathfrak": "frak", "mathsf": "sf", "mathtt": "tt"}
_SPACING = {",", ";", ":", "!", " ", "quad", "qquad"}
_ENV_DELIMS = {"matrix": ("", ""), "pmatrix": ("(", ")"), "bmatrix": ("[", "]"), "Bmatrix": ("{", "}"), "vmatrix": ("|", "|"),
               "Vmatrix": ("‖", "‖")}
_MARK_CMDS = {"vec": "vec", "hat": "hat", "overrightarrow": "vec"}


def read_latex(latex: str) -> list:
    toks = re.findall(r"\\[A-Za-z]+|\\.|[{}^_\[\]]|[^\\{}^_\[\]]", latex)
    counter = [0]
    return _latex_run(toks, (), None, counter)


def _latex_group(toks, i):
    """(content tokens, next index) of the {...} group or single token at i."""
    while i < len(toks) and toks[i] == " ":
        i += 1
    if i >= len(toks):
        raise Refuse("the LaTeX ends too early")
    if toks[i] != "{":
        return [toks[i]], i + 1
    depth, j = 0, i
    while j < len(toks):
        depth += {"{": 1, "}": -1}.get(toks[j], 0)
        if depth == 0:
            return toks[i + 1:j], j + 1
        j += 1
    raise Refuse("unbalanced braces in the LaTeX")


def _latex_env(toks, i, env, path, style, counter):
    """\\begin{env} ... \\end{env} starting after the name: (atoms, next index). Rows split at \\\\, cells at &, each cell at path
    ("m", row, column) - the same shape as the source reading."""
    if env not in _ENV_DELIMS:
        raise Refuse(f"the LaTeX environment {env} is not compared by the independent check")
    depth, braces, j = 1, 0, i
    rows, row, cell = [], [], []
    while j < len(toks):
        t = toks[j]
        if t == "\\begin":
            depth += 1
        elif t == "\\end":
            depth -= 1
            if depth == 0:
                break
        braces += {"{": 1, "}": -1}.get(t, 0)
        if depth == 1 and braces == 0 and t == "&":
            row.append(cell)
            cell = []
        elif depth == 1 and braces == 0 and t == "\\\\":
            row.append(cell)
            rows.append(row)
            row, cell = [], []
        else:
            cell.append(t)
        j += 1
    if j >= len(toks):
        raise Refuse("a LaTeX environment is not closed")
    row.append(cell)
    if any(x.strip() for x in cell) or len(row) > 1:
        rows.append(row)
    name, nxt = _latex_group(toks, j + 1)
    if "".join(name) != env:
        raise Refuse("mismatched LaTeX environment")
    open_, close = _ENV_DELIMS[env]
    out = [(open_, None, path)] if open_ else []
    for r, cells in enumerate(rows, 1):
        for c, body in enumerate(cells, 1):
            atoms = _latex_run(body, path + (("m", r, c),), style, counter)
            if not atoms:
                raise Refuse("an empty matrix entry")
            out += atoms
    return out + ([(close, None, path)] if close else []), nxt


def _latex_run(toks, path, style, counter, marks=()) -> list:
    out = []
    i = 0
    gap = False                           # a spacing command since the last atom: 1\,2 is not the number 12
    while i < len(toks):
        t = toks[i]
        i += 1
        was_gap, gap = gap, False
        if t in (" ", "{", "}"):                                  # transparent: 1\,{2} is still a gap inside a number
            gap = was_gap
            continue
        if t.startswith("\\") and t[1:] in _SPACING:
            gap = True
            continue
        if t.isdigit() and was_gap and out and out[-1][0].isdigit() and out[-1][2] == path:
            raise Refuse("a space inside the digits of a number (like 1\\,2)")
        if t in ("^", "_"):
            body, i = _latex_group(toks, i)
            if t == "^" and body == ["\\circ"]:
                out.append(("°", None, path))
            elif t == "^" and body and all(x in ("'", "\\prime") for x in body):
                out += [("'", None, path)] * len(body)
            else:
                out += _latex_run(body, path + ((t,),), style, counter)
        elif t in ("\\frac", "\\dfrac", "\\tfrac"):
            counter[0] += 1
            f = counter[0]
            num, i = _latex_group(toks, i)
            den, i = _latex_group(toks, i)
            out += _latex_run(num, path + (("f", f, "n"),), style, counter)
            out += _latex_run(den, path + (("f", f, "d"),), style, counter)
        elif t == "\\sqrt":
            out.append(("√", None, path))
            if i < len(toks) and toks[i] == "[":
                j = toks.index("]", i)
                out += _latex_run(toks[i + 1:j], path + (("r", "i"),), style, counter)
                i = j + 1
            body, i = _latex_group(toks, i)
            out += _latex_run(body, path + (("r", "b"),), style, counter)
        elif t == "\\begin":
            env, i = _latex_group(toks, i)
            atoms, i = _latex_env(toks, i, "".join(env), path, style, counter)
            out += atoms
        elif t == "\\end":
            raise Refuse("an unmatched \\end in the LaTeX")
        elif t[1:] in _FONT_CMDS and t.startswith("\\"):
            if i < len(toks) and toks[i] == "*":
                i += 1
            body, i = _latex_group(toks, i)
            out += _latex_run(body, path, _FONT_CMDS[t[1:]], counter)
        elif t.startswith("\\") and t[1:] in _MARK_CMDS:
            body, i = _latex_group(toks, i)
            inner = _latex_run(body, path, style, counter)
            if inner:
                last = inner[-1]
                inner[-1] = (last[0], last[1], last[2], tuple(last[3]) + (_MARK_CMDS[t[1:]],) if len(last) > 3 else (_MARK_CMDS[t[1:]],))
            out += inner
        elif t.startswith("\\"):
            name = t[1:]
            if name in ("left", "right"):
                if i < len(toks) and toks[i] == ".":
                    i += 1
                continue
            if name in _LATEX_SKIP:
                continue
            if name in _LATEX_SYMBOLS:
                sym = _LATEX_SYMBOLS[name]
                out.append((sym, style if style in FONT_STYLES and sym.isalpha() else None, path))
            elif name.isalpha() and name in FUNCTION_WORDS | {"lim", "max", "min", "det"}:
                out += [(ch, "u", path) for ch in name]
            elif name in ("&",):
                continue
            elif name == "\\":
                continue
            else:
                raise Refuse(f"the LaTeX command \\{name} is not understood by the independent check")
        elif t in ("{", "}"):
            continue
        elif t == "&" or t == " ":
            continue
        elif t.isascii() and t.isalpha():
            out.append((t, style or "i", path))
        elif (styled := _unicode_style(t)) is not None:
            out.append((styled[0], styled[1], path))
        else:
            out.append((_c(t), None, path))
    return out


# -------------------------------------------------------------------------------------------------------------- Word equations
_M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def read_omml(el: ET.Element) -> list:
    """The structure of a native Word equation, with the style Word stores: plain runs (m:sty p, m:nor) and function names are
    upright, ordinary math letters italic."""
    counter = [0]

    def val(e, tag, default=None):
        x = e.find(f"{_M}{tag}") if e is not None else None
        return default if x is None else x.get(f"{_M}val", default)

    def kids(e, path, fname=False):
        out = []
        for c in (e if e is not None else []):
            out += node(c, path, fname)
        return out

    def part(e, tag, path, fname=False):
        return kids(e.find(f"{_M}{tag}"), path, fname)

    def node(e, path, fname=False):
        name = e.tag[len(_M):] if e.tag.startswith(_M) else None
        if name is None or name.endswith("Pr"):
            return []
        if name == "r":
            pr = e.find(f"{_M}rPr")
            nor = pr is not None and pr.find(f"{_M}nor") is not None
            wb = e.find(f"{_W}rPr/{_W}b")
            bold = wb is not None and wb.get(f"{_W}val", "true") not in ("0", "false", "off")
            scr = val(pr, "scr")
            sty = val(pr, "sty")
            if scr and scr != "roman":
                style = {"double-struck": "bb", "script": "scr", "fraktur": "frak", "sans-serif": "sf", "monospace": "tt"}.get(scr)
                if style is None:
                    raise Refuse(f"the equation font {scr} is not compared by the independent check")
            elif fname:
                style = "u"
            else:
                # Word's own two properties, read separately: weight (m:sty b/bi, w:b) and slant (upright: m:sty p/b, m:nor)
                heavy = sty in ("b", "bi") or bold
                upright = nor or sty in ("p", "b")
                style = ("b" if upright else "bi") if heavy else ("u" if upright else "i")
            out = []
            for t in e.findall(f"{_M}t"):
                for ch in t.text or "":
                    if ch.isspace() or ch in "⁡⁢⁣​":
                        continue
                    if ch in SUPERSCRIPTS:
                        out.append((SUPERSCRIPTS[ch], None, path + (("^",),)))
                    elif ch in SUBSCRIPTS:
                        out.append((SUBSCRIPTS[ch], None, path + (("_",),)))
                    elif ch.isascii() and ch.isalpha():
                        out.append((ch, style, path))
                    elif (styled := _unicode_style(ch)) is not None:
                        out.append((styled[0], styled[1], path))
                    elif (base := _math_italic(ch)) is not None:
                        out.append((base, "i", path) if base.isascii() else (_c(base), None, path))
                    else:                            # a Greek letter keeps the run's font too: bold α is not α
                        out.append((_c(ch), style if style in FONT_STYLES and ch.isalpha() else None, path))
            return out
        if name in ("e", "num", "den", "sub", "sup", "deg", "lim", "oMath"):
            return kids(e, path, fname)
        if name == "fName":
            return kids(e, path, True)
        if name == "f":
            counter[0] += 1
            f = counter[0]
            return part(e, "num", path + (("f", f, "n"),)) + part(e, "den", path + (("f", f, "d"),))
        if name == "sSup":
            return part(e, "e", path) + part(e, "sup", path + (("^",),))
        if name == "sSub":
            return part(e, "e", path) + part(e, "sub", path + (("_",),))
        if name == "sSubSup":
            return part(e, "e", path) + part(e, "sub", path + (("_",),)) + part(e, "sup", path + (("^",),))
        if name == "sPre":
            return part(e, "sub", path + (("_",),)) + part(e, "sup", path + (("^",),)) + part(e, "e", path)
        if name == "rad":
            hidden = val(e.find(f"{_M}radPr"), "degHide", "0") in ("1", "on", "true")
            return [("√", None, path)] + ([] if hidden else part(e, "deg", path + (("r", "i"),))) + part(e, "e", path + (("r", "b"),))
        if name == "nary":
            pr = e.find(f"{_M}naryPr")
            out = [(_c(val(pr, "chr", "∫") or "∫"), None, path)]
            if val(pr, "subHide", "0") not in ("1", "on", "true"):
                out += part(e, "sub", path + (("_",),))
            if val(pr, "supHide", "0") not in ("1", "on", "true"):
                out += part(e, "sup", path + (("^",),))
            return out + part(e, "e", path)
        if name == "d":
            pr = e.find(f"{_M}dPr")
            beg, end, sep = val(pr, "begChr", "("), val(pr, "endChr", ")"), val(pr, "sepChr", "|")
            out = [(_c(beg), None, path)] if beg else []
            for n, x in enumerate(e.findall(f"{_M}e")):
                if n and sep:
                    out.append((_c(sep), None, path))
                out += kids(x, path)
            return out + ([(_c(end), None, path)] if end else [])
        if name == "func":
            return part(e, "fName", path, True) + part(e, "e", path)
        if name in ("limLow", "limUpp"):
            return part(e, "e", path, True) + part(e, "lim", path + (("_" if name == "limLow" else "^",),))
        if name in ("acc", "bar"):
            inner = part(e, "e", path)
            mark = {"⃗": "vec", "̂": "hat", "^": "hat"}.get(val(e.find(f"{_M}accPr"), "chr", "̂") or "", None) \
                if name == "acc" else None
            if mark and inner:
                last = inner[-1]
                inner[-1] = (last[0], last[1], last[2], (mark,))
            elif name == "bar" or mark is None:
                raise Refuse("an accent or bar the independent check does not compare")
            return inner
        if name == "m":
            return [a for r, row in enumerate(e.findall(f"{_M}mr"), 1) for c, x in enumerate(row.findall(f"{_M}e"), 1)
                    for a in kids(x, path + (("m", r, c),))]
        if name == "eqArr":
            return [a for x in e.findall(f"{_M}e") for a in kids(x, path)]
        if name in ("box", "borderBox"):
            return part(e, "e", path)
        if name == "oMathPara":
            return [a for x in e.findall(f"{_M}oMath") for a in kids(x, path)]
        raise Refuse(f"the equation part {name} is not compared by the independent check")

    return node(el, ())


# ------------------------------------------------------------------------------------------------------------------ compare
def _normal(atoms: list) -> list:
    out = []
    for a in atoms:
        marks = tuple(sorted(a[3])) if len(a) > 3 and a[3] else ()
        out.append((a[0], a[1], a[2], marks))
    return _flat_lim(out)


def _flat_lim(atoms: list) -> list:
    """Fractions are numbered in order of first appearance on both sides, so the numbering never depends on reading order."""
    order: dict = {}
    for a in atoms:
        for p in a[2]:
            if p[0] == "f" and p[1] not in order:
                order[p[1]] = len(order) + 1
    return [(a[0], a[1], tuple(("f", order[p[1]], p[2]) if p[0] == "f" else p for p in a[2]), a[3]) for a in atoms]


def _match(src, ltx) -> str:
    if len(src) != len(ltx):
        return f"{len(src)} symbols in the text, {len(ltx)} in the conversion"
    for n, (a, b) in enumerate(zip(src, ltx, strict=True)):
        if a[0] != b[0]:
            return f"symbol {n + 1}: '{a[0]}' typed, '{b[0]}' converted"
        if a[2] != b[2]:
            return f"'{a[0]}' is {_where(a[2])} in the text but {_where(b[2])} in the conversion"
        if a[3] != b[3]:
            return f"'{a[0]}' has different accents"
        if a[0].isalpha() and (a[1] in FONT_STYLES or b[1] in FONT_STYLES) and a[1] != b[1]:
            return f"'{a[0]}' changes font (bold, double-struck or script letters carry meaning)"
        if a[1] in ("u", "i") and b[1] in ("u", "i") and a[1] != b[1]:
            return f"'{a[0]}' should be {'upright' if a[1] == 'u' else 'italic'} but is {'upright' if b[1] == 'u' else 'italic'}"
        if a[1] in ("u", "i") and b[1] is None or a[1] is None and b[1] in ("u",) and a[0].isalpha():
            return f"'{a[0]}' changes style"
    return ""


def _where(path) -> str:
    if not path:
        return "on the line"
    names = {"^": "in a superscript", "_": "in a subscript"}
    last = path[-1]
    if last[0] == "f":
        return f"in the {'numerator' if last[2] == 'n' else 'denominator'} of fraction {last[1]}"
    if last[0] == "r":
        return "under a root"
    return names.get(last[0], str(last))


def verify_text(original: str, latex: str, domain: str | None = None, context: str = "", unit_answer: bool = False) -> tuple[bool, str]:
    """(True, "") when the plain-text original and the LaTeX have the same independent structure; otherwise (False, reason)."""
    try:
        src = read_source(original, None, _Ctx(domain, context or original, unit_answer))
        ltx = read_latex(latex)
    except Refuse as exc:
        return False, str(exc)
    except (RecursionError, ValueError, IndexError) as exc:
        return False, f"the independent check could not read it ({type(exc).__name__})"
    why = _match(_normal(src), _normal(ltx))
    return (not why), why


def verify_omml(el: ET.Element, latex: str) -> tuple[bool, str]:
    try:
        src = read_omml(el)
        ltx = read_latex(latex)
    except Refuse as exc:
        return False, str(exc)
    except (RecursionError, ValueError, IndexError) as exc:
        return False, f"the independent check could not read it ({type(exc).__name__})"
    why = _match(_normal(src), _normal(ltx))
    return (not why), why
