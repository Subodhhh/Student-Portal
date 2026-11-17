"""Chemistry recognition: formulas, ions, isotopes, hydrates, state symbols and reaction equations typed in ordinary text.

It only changes notation (H₂O -> \\mathrm{H_{2}O}); it never balances, corrects, recases or completes anything. Element symbols are
read case-sensitively against the periodic table (Co is cobalt, CO is carbon + oxygen). A formula is recognised only with chemical
evidence: in a Chemistry paper, a subscript count, a charge, a state symbol or an isotope; in other subjects only notation that cannot
be anything else (a charge such as SO₄²⁻, or a two-letter element with a subscript such as CaCO₃). Everything else is left to the
mathematics recogniser or kept as typed.

Unclear typing is flagged, never guessed: ASCII charges (Cu2+, SO42-), a superscript without a sign (Fe³), a sign written before the
number (Mn⁺⁷), an unknown symbol (Xy₂), and a formula used as a variable in an equation in a Chemistry paper (P₂ = ...).
"""
import re
from dataclasses import dataclass

from .chem_data import ELEMENTS, STATES
from .symbols import SUBSCRIPTS, SUPERSCRIPTS

SUB_DIGITS = "₀₁₂₃₄₅₆₇₈₉"
SUP_DIGITS = "⁰¹²³⁴⁵⁶⁷⁸⁹"
SUP_SIGNS = "⁺⁻"
ARROWS = {"→": r"\rightarrow", "⟶": r"\rightarrow", "->": r"\rightarrow", "⇌": r"\rightleftharpoons", "⇄": r"\rightleftharpoons",
          "↔": r"\leftrightarrow"}
RELATION_WORDS = {"=", "<", ">", "≈", "≤", "≥", "≠", "∝"}
BONDS = {"-": "-", "–": "-", "=": "=", "≡": r"\equiv"}
_ASCII_ION = re.compile(r"^\(?((?:[A-Z][a-z]?\d*|\((?:[A-Z][a-z]?\d*)+\)\d*)+)(\d*)([+\-])\)?$")


@dataclass
class ChemSpan:
    start: int
    end: int
    original: str
    latex: str | None = None
    code: str | None = None          # set when flagged
    reason: str | None = None
    checked: str | None = None       # the source with plain counts written as subscripts (H2SO4 -> H₂SO₄), when it differs


class _Flag(Exception):
    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code, self.reason = code, reason


@dataclass
class _Species:
    latex: str
    scripts: bool          # a subscript count, charge, isotope, state or hydrate: real chemical notation
    charge: bool
    two_letter: bool       # contains a two-letter element symbol (Ca, Na, Cl ...)
    single_letters_only: bool
    sub_one: bool          # a subscript 1 (never written in formulas: V₁, P₁ are variables)
    subscripted: bool = False   # a subscript count (CaCO₃) - unlike a state symbol, evidence outside a Chemistry paper: Re(s) is Re of s


def _digits(s: str, i: int, alphabet: str) -> tuple[str, int]:
    j = i
    while j < len(s) and s[j] in alphabet:
        j += 1
    return s[i:j], j


def _plain(d: str) -> str:
    return "".join(SUBSCRIPTS.get(c) or SUPERSCRIPTS.get(c) or c for c in d)


def parse_species(w: str, chemistry: bool) -> _Species | None:
    """One formula, ion or isotope without spaces; None when the word is not chemical notation at all. Raises _Flag when it looks
    like chemistry but cannot be read in exactly one way."""
    if m := re.fullmatch(r"(\d*)e⁻", w):                   # e⁻, 5e⁻: the electron (with its coefficient in a half-reaction)
        return _Species(m.group(1) + r"\mathrm{e^{-}}", True, True, False, False, False)
    if w == "e-":
        return None
    i, n = 0, len(w)
    coeff, i = _digits(w, i, "0123456789")
    if coeff and (i >= n or not ("A" <= w[i] <= "Z" or w[i] in "([" or w[i] in SUP_DIGITS + SUB_DIGITS)):
        return None
    pre = ""                                  # isotope: mass number (superscript) and/or atomic number (subscript) before the symbol
    while i < n and w[i] in SUP_DIGITS + SUB_DIGITS:
        alpha = SUP_DIGITS if w[i] in SUP_DIGITS else SUB_DIGITS
        d, i = _digits(w, i, alpha)
        pre += ("^{" if alpha == SUP_DIGITS else "_{") + _plain(d) + "}"
    out: list[str] = []
    elements: list[str] = []
    scripts = bool(pre)
    charge = False
    sub_one = False
    subscripted = False
    stack: list[str] = []
    state = ""
    hydrate = False
    counted = any(ch in SUB_DIGITS for ch in w)                  # Xy₂ looks like a formula; "Avogadro" or "L⁻¹" does not
    while i < n:
        c = w[i]
        if "A" <= c <= "Z":
            sym = w[i:i + 2] if i + 1 < n and w[i + 1].islower() and w[i:i + 2] in ELEMENTS else w[i]
            if sym not in ELEMENTS:
                if not counted:
                    return None
                if i + 1 < n and w[i + 1].islower():
                    raise _Flag("UNKNOWN_ELEMENT_SYMBOL", f"'{w[i:i + 2]}' is not an element symbol; check the formula.")
                raise _Flag("UNKNOWN_ELEMENT_SYMBOL", f"'{c}' is not an element symbol; check the formula.")
            if i + len(sym) < n and w[i + len(sym)].islower():
                return None                                       # a word such as "Copper" or "Na" + "me": not a formula
            elements.append(sym)
            out.append(sym)
            i += len(sym)
        elif c in "([":
            if w[i:i + 4] in STATES or w[i:i + 3] in STATES:
                st = w[i:i + 4] if w[i:i + 4] in STATES else w[i:i + 3]
                if stack or i + len(st) != n:
                    return None
                state, i, scripts = st, i + len(st), True
                continue
            stack.append(c)
            out.append(c)
            i += 1
        elif c in ")]":
            if not stack or {"(": ")", "[": "]"}[stack.pop()] != c:
                return None
            out.append(c)
            i += 1
        elif c in SUB_DIGITS:
            d, i = _digits(w, i, SUB_DIGITS)
            if not out or out[-1] in "([":
                return None
            sub_one = sub_one or _plain(d) == "1"
            out.append("_{" + _plain(d) + "}")
            scripts = subscripted = True
        elif c in SUP_DIGITS + SUP_SIGNS:
            d, j = _digits(w, i, SUP_DIGITS + SUP_SIGNS)
            p = _plain(d)
            if not out:
                return None
            if not re.fullmatch(r"\d*[+-]", p):
                if re.fullmatch(r"-\d+", p) and not counted:
                    return None                                   # L⁻¹, s⁻² : a unit or a power, read by the mathematics recogniser
                if re.fullmatch(r"[+-]\d+", p):
                    raise _Flag("AMBIGUOUS_CHEMICAL_CHARGE", f"'{d}' puts the sign before the number: it could be a charge or an "
                                                             "oxidation state. Write charges as ²⁺ / ³⁻ (number, then sign).")
                raise _Flag("AMBIGUOUS_CHEMICAL_CHARGE", f"The superscript '{d}' has no sign, so it is unclear whether it is a charge.")
            rest = w[j:]
            if rest and rest not in STATES and not rest[0] in "·•." and not (stack and set(rest) <= set(")]")):
                raise _Flag("AMBIGUOUS_CHEMICAL_CHARGE", "A charge appears in the middle of the formula; check where it belongs.")
            out.append("^{" + p + "}")
            charge = scripts = True
            i = j
        elif c in "·•." and i + 1 < n and (w[i + 1].isdigit() or "A" <= w[i + 1] <= "Z") and out and not stack:
            out.append(r"\cdot " if c in "·•" else ".")        # hydrate: CuSO₄·5H₂O (a typed '.' stays a '.')
            i += 1
            d, i = _digits(w, i, "0123456789")
            out.append(d)
            hydrate = scripts = True
        elif c in BONDS and chemistry and out and i + 1 < n and ("A" <= w[i + 1] <= "Z" or w[i + 1] in "(["):
            out.append(BONDS[c] if c != "≡" else r"{\equiv}")   # CH₂=CH₂, HC≡CH: bonds in organic formulas (Chemistry papers only)
            i += 1
        else:
            return None
    if stack or not elements:
        return None
    body = "".join(out)
    latex = coeff + ("{}" + pre if pre else "") + r"\mathrm{" + body + "}" + (r"\mathrm{" + state + "}" if state else "")
    return _Species(latex, scripts or hydrate, charge, any(len(e) == 2 for e in elements), all(len(e) == 1 for e in elements),
                    sub_one, subscripted)


def _strip(word: str, start: int) -> tuple[str, int]:
    """Trailing sentence punctuation and unbalanced outer brackets/quotes are prose, not part of the formula."""
    a, b = 0, len(word)
    while b > a and word[b - 1] in ".,;:?!\"'”’":
        b -= 1
    while a < b and word[a] in "\"'“‘":
        a += 1
    core = word[a:b]
    if core.startswith("(") and core.count("(") > core.count(")"):
        a += 1
    if core.endswith(")") and core.count(")") > core.count("(") and not core.endswith(STATES):
        b -= 1
    core = word[a:b]
    if core[:1] == "(" and core[-1:] == ")" and not core.endswith(STATES) and _closing(core) == len(core) - 1:
        a, b = a + 1, b - 1                                       # "(C₂H₂)" in a sentence: the brackets are punctuation
    return word[a:b], start + a


def _closing(s: str) -> int:
    depth = 0
    for k, ch in enumerate(s):
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if depth == 0:
            return k
    return -1


_SUPS = "⁰¹²³⁴⁵⁶⁷⁸⁹"
_ORB_TERM = re.compile(rf"([1-7])([spdf])([{_SUPS}]*)")                       # 1s², 2p⁶, 3d¹⁰ (count optional: "3d orbitals")
_ORB_CORE = re.compile(r"\[(He|Ne|Ar|Kr|Xe|Rn)\]")                            # [Ne] 3s¹: a noble-gas core
_HYBRID = re.compile(rf"(?:(d)([{_SUPS}]?))?(sp)([{_SUPS}]?)(?:(d)([{_SUPS}]?))?")   # sp, sp², sp³, dsp², sp³d, sp³d², d²sp³
_ORB_WORDS = {"orbital", "orbitals", "subshell", "subshells", "shell", "electron", "electrons", "hybrid", "hybridised", "hybridized",
              "hybridisation", "hybridization", "configuration"}


def _sup(s: str) -> str:
    return "^{" + "".join(str(_SUPS.index(c)) for c in s) + "}" if s else ""


def _orbital(word: str) -> tuple[str, bool] | None:
    """LaTeX of one orbital word (shell+subshell+count, a noble-gas core, a hybridisation) and whether it carries a count; else None.
    Orbital letters are labels, upright - never variables s·p."""
    if m := _ORB_TERM.fullmatch(word):
        return r"\mathrm{" + m.group(1) + m.group(2) + "}" + _sup(m.group(3)), bool(m.group(3))
    if m := _ORB_CORE.fullmatch(word):
        return r"[\mathrm{" + m.group(1) + "}]", True
    if m := _HYBRID.fullmatch(word):
        d1, n1, sp, n2, d2, n3 = m.groups()
        latex = (r"\mathrm{d}" + _sup(n1) if d1 and n1 else "") + r"\mathrm{" + ("d" if d1 and not n1 else "") + "sp}" + _sup(n2)
        latex += (r"\mathrm{d}" + _sup(n3)) if d2 else ""
        return latex, True
    return None


def _orbital_spans(text: str, words: list[tuple[str, int]], offset: int) -> tuple[list[ChemSpan], set[int]]:
    """Electron configurations (1s² 2s² 2p⁶, [Ne] 3s¹) and hybridisations (sp³, dsp²) in a Chemistry paper: consecutive orbital
    words are one span. A term without a count (2p) only next to other orbital words, before 'orbital'/'electrons' ..., or alone."""
    spans, used, k = [], set(), 0
    stripped = [(w.rstrip(".,;:!?)"), s) for w, s in words]
    while k < len(stripped):
        run = []
        while k < len(stripped) and (o := _orbital(stripped[k][0])) is not None:
            run.append((stripped[k], o))
            k += 1
        if run:
            nxt = words[k][0].lower().strip(".,;:!?") if k < len(words) else ""
            alone = len(words) == len(run)
            if len(run) > 1 or run[0][1][1] or nxt in _ORB_WORDS or alone:
                (w0, s0), (wl, sl) = run[0][0], run[-1][0]
                spans.append(ChemSpan(offset + s0, offset + sl + len(wl), text[s0:sl + len(wl)], r"\,".join(o[0] for _, o in run)))
                used.update(s for (_, s), _ in run)
        else:
            k += 1
    return spans, used


def find_chem(text: str, domain: str | None, offset: int = 0) -> list[ChemSpan]:
    """Chemistry spans in a piece of ordinary text (positions are absolute: `offset` is added)."""
    chemistry = domain == "chemistry"
    words = [(m.group(), m.start()) for m in re.finditer(r"\S+", text)]
    orbital_spans, orbital_words = _orbital_spans(text, words, offset) if chemistry else ([], set())
    words = [(w, s) for w, s in words if s not in orbital_words]
    items: list[tuple[str, int, int, object]] = []        # (kind, start, end, payload): kind species | op | flag | other
    split: list[tuple[str, int]] = []
    for w, s in words:                                    # 2H₂+O₂→2H₂O, Al³⁺+3OH⁻ typed without spaces: split at arrows and plus signs
        if chemistry and _concentration_expression(w):
            split.append((w, s))                          # [NH₃]²/([N₂][H₂]³), [H⁺]=10⁻³: mathematics over concentrations
        elif chemistry and (any(a in w for a in ARROWS) or "+" in w.strip("+")):
            for m in re.finditer(r"->|[→⟶⇌⇄↔+]|(?:(?!->)[^→⟶⇌⇄↔+])+", w):
                split.append((m.group(), s + m.start()))
        else:
            split.append((w, s))
    for w, s in split:
        core, cs = _strip(w, s)
        if chemistry and _concentration_expression(core):
            items.append(("other", s, s + len(w), None))
            continue
        if core in ARROWS or core == "+":
            items.append(("op", cs, cs + len(core), core))
            continue
        if chemistry and core.isascii() and _ASCII_ION.match(core) and re.search(r"\d[+\-]\)?$", core) and _all_elements(core):
            items.append(("flag", cs, cs + len(core), _Flag("AMBIGUOUS_CHEMICAL_CHARGE",
                          f"'{core}' writes the charge with ordinary characters: it is unclear whether the digit is a count or the "
                          "charge (Cu2+ could be Cu²⁺). Type the charge as a superscript (Cu²⁺).")))
            continue
        if chemistry and re.search(r"[\^−-]|[A-Za-z)]\+$", core):
            try:
                ascii_charged = unicode_species(core)
            except _Flag as f:
                items.append(("flag", cs, cs + len(core), f))
                continue
            if ascii_charged is not None and ascii_charged != core:      # MnO4^-, 8H+, e^-, OH−: the charge is explicit
                sp = parse_species(ascii_charged, chemistry)
                if sp is not None:
                    items.append(("species", cs, cs + len(core), sp))
                    continue
        try:
            explicit = explicit_counts(core) if chemistry and core.isascii() else core
            sp = parse_species(explicit, chemistry) if core else None
            if sp is None and explicit != core:
                sp = parse_species(core, chemistry)              # not a formula with counts: read as typed
        except _Flag as f:                                # chemistry-shaped but unclear: flagged in Chemistry papers only
            items.append(("flag", cs, cs + len(core), f) if chemistry else ("other", s, s + len(w), None))
            continue
        items.append(("species", cs, cs + len(core), sp) if sp else ("other", s, s + len(w), None))

    spans: list[ChemSpan] = []
    k = 0
    while k < len(items):
        kind, s, e, payload = items[k]
        if kind == "flag":
            spans.append(ChemSpan(offset + s, offset + e, text[s:e], code=payload.code, reason=payload.reason))
            k += 1
            continue
        if kind != "species":
            k += 1
            continue
        # a chain: species (op species)*
        j, members, ops = k, [items[k]], []
        while j + 2 < len(items) and items[j + 1][0] == "op" and items[j + 2][0] == "species":
            ops.append(items[j + 1])
            members.append(items[j + 2])
            j += 2
        arrow = any(o[3] in ARROWS for o in ops)
        neighbours = {text[items[x][1]:items[x][2]] for x in (k - 1, j + 1) if 0 <= x < len(items)}
        species = [m[3] for m in members]

        def qualifies(sp: _Species) -> bool:
            if chemistry:
                return sp.scripts and not sp.sub_one
            return sp.charge or (sp.two_letter and sp.subscripted and not sp.sub_one)

        if (chemistry and neighbours & RELATION_WORDS and not any(sp.charge for sp in species)
                and all(sp.single_letters_only for sp in species)):
            # P₂ = 2 atm, O₂ = 32 g: a formula or a variable. P₁ (subscript 1) is always a variable: left to mathematics.
            if any(sp.scripts for sp in species) and not any(sp.sub_one for sp in species):
                s0, e0 = members[0][1], members[-1][2]
                spans.append(ChemSpan(offset + s0, offset + e0, text[s0:e0], code="AMBIGUOUS_DOMAIN",
                                      reason="This could be a chemical formula or a variable in an equation; check how it should "
                                             "be shown."))
            k = j + 1
            continue
        chain = len(members) > 1 and (all(qualifies(sp) for sp in species) or (
            chemistry and arrow and any(qualifies(sp) for sp in species) and not any(sp.sub_one for sp in species)))
        if chain:
            s0, e0 = members[0][1], members[-1][2]
            parts = [species[0].latex]
            for o, m in zip(ops, species[1:], strict=False):
                parts += [ARROWS.get(o[3], "+"), m.latex]
            src = text[s0:e0]
            spans.append(ChemSpan(offset + s0, offset + e0, src, " ".join(parts), checked=_explicit_chain(src) if chemistry else None))
            k = j + 1
            continue
        for _kind, s1, e1, sp in members:                 # no chain: each formula on its own
            if qualifies(sp):
                src = text[s1:e1]
                spans.append(ChemSpan(offset + s1, offset + e1, src, sp.latex, checked=_explicit_chain(src) if chemistry else None))
        k = j + 1
    return sorted(spans + orbital_spans, key=lambda x: x.start)


_SUB = str.maketrans("0123456789", SUB_DIGITS)
_SUPER = str.maketrans("0123456789+-−", SUP_DIGITS + "⁺⁻⁻")
_PLAIN_BRACKET = re.compile(r"\[([^\[\]()\s]+)\]")


def _concentration_expression(w: str) -> bool:
    """[NH₃]²/([N₂][H₂]³), [H⁺]=10⁻³, [Al³⁺][OH⁻]³, [A]₀: a square bracket around a plain species (no ligand brackets, unlike the
    complex [Co(NH₃)₆]Cl₃) with more mathematics after it - a power, a label, another bracket, '/' or '='. A bracket alone ([H⁺]) or
    with a signed charge after it stays chemistry."""
    for m in _PLAIN_BRACKET.finditer(w):
        if m.group(1) in ("He", "Ne", "Ar", "Kr", "Xe", "Rn"):
            continue                                      # [Ne]3s¹: a noble-gas core of an electron configuration
        rest = w[m.end():].rstrip(".,;:?!")
        if w[:m.start()].strip("(") or (rest and not re.fullmatch(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]*[⁺⁻]", rest)):
            return True
    return False


def unicode_species(content: str) -> str | None:
    """A species typed with ordinary characters in its explicit form: H3O+ -> H₃O⁺, SO4^2− -> SO₄²⁻, OH− -> OH⁻, MnO4^- -> MnO₄⁻,
    e^- -> e⁻ (Unicode input is returned as typed). None when it is not one species of real elements. A charge typed as a digit and
    a sign (Ca2+: Ca²⁺ or Ca₂⁺?) raises _Flag: never guessed."""
    if m := re.fullmatch(r"(\d*)e(?:\^?[-−]|⁻)", content):
        return m.group(1) + "e⁻"
    m = re.fullmatch(r"(.*?)(?:\^(\d*[+\-−])|(?<![0-9])([+\-−]))", content)
    if m:
        body, charge = m.group(1), (m.group(2) or m.group(3))
    else:
        if re.fullmatch(r".*[A-Za-z)]\d+[+\-−]", content):
            raise _Flag("AMBIGUOUS_CHEMICAL_CHARGE", f"'{content}' writes the charge with ordinary characters: it is unclear whether "
                        "the digit is a count or the charge (Ca2+ could be Ca²⁺). Type the charge as a superscript (Ca²⁺).")
        body, charge = content, ""
    if not body or not re.fullmatch(r"[A-Za-z0-9₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+", body):
        return None
    word = explicit_counts(body) + charge.translate(_SUPER)            # SO4²⁻ (from SO4^2−): the 4 is still a count
    sp = parse_species(word, True)
    return word if sp is not None and r"\cdot" not in sp.latex else None


def bracket_species(content: str) -> str | None:
    """The LaTeX of the species inside a concentration bracket ([NH₃], [H⁺], [Al³⁺], [H3O+], [SO4^2−]), or None. Raises _Flag for
    [Ca2+]."""
    word = unicode_species(content)
    if word is None or word[:1].isdigit() or re.fullmatch(r"[A-Z]", word):
        return None                       # [A], [B]: a reactant named by a letter in a rate law - B is not boron without a count or charge
    return parse_species(word, True).latex


def explicit_brackets(text: str) -> str:
    """The text with each concentration bracket's species in its explicit form ([H3O+] -> [H₃O⁺]): what the checks compare."""
    def one(m):
        try:
            word = unicode_species(m.group(1))
        except _Flag:
            word = None
        return "[" + word + "]" if word and not word[:1].isdigit() else m.group()
    return _PLAIN_BRACKET.sub(one, text)


def _explicit_chain(src: str) -> str | None:
    """The explicit form of a span (each ASCII word with its counts as subscripts), or None when nothing changes."""
    def one(w: str) -> str:
        try:
            u = unicode_species(w) if re.search(r"[\^−-]|[A-Za-z)]\+$", w) else None
        except _Flag:
            u = None
        return u if u else explicit_counts(w) if w.isascii() else w
    out = re.sub(r"\S+", lambda m: one(m.group()), src)
    return out if out != src else None


def explicit_counts(word: str) -> str:
    """H2SO4 -> H₂SO₄, Ca(OH)2 -> Ca(OH)₂, 2H2O -> 2H₂O: a formula pasted as plain text has lost its subscripts; in a Chemistry paper a
    number right after an element symbol or a closing bracket is a count. A leading coefficient (2H₂O), a hydrate's count (·5H₂O) and
    anything that is not then a formula of real elements are left as typed (the caller parses the result like any typed formula)."""
    return re.sub(r"(?<=[A-Za-z)\]])\d+", lambda m: m.group().translate(_SUB), word)


def _all_elements(core: str) -> bool:
    letters = re.findall(r"[A-Z][a-z]?", core)
    return bool(letters) and all(x in ELEMENTS for x in letters) and "".join(letters) == re.sub(r"[^A-Za-z]", "", core)
