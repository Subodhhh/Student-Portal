"""Meaning-preservation check, independent of the parser: a conversion is accepted only if the LaTeX, read back, contains exactly the
same symbols, in the same order, at the same script level (normal / superscript / subscript, nested), as the provider's text.

It catches a dropped or added symbol, a changed sign, a superscript turned into a subscript, a Greek letter turned into a Latin one,
a lost integral limit and reordered matrix entries. Brackets, commas and spacing are ignored (LaTeX draws a fraction or a matrix
without them), so grouping itself is guaranteed by the grammar, which refuses every reading it cannot prove (parser.Ambiguous).
"""
import re
import unicodedata

from .symbols import ADD, ATOMS, BIG, GREEK, MUL, PREFIX, RELATIONS, ROOTS, SUBSCRIPTS, SUPERSCRIPTS, VULGAR

IGNORED = set("()[]{},;& \t\n ")
_CANON = {"−": "-", "–": "-", "—": "-", "‐": "-", "*": "·", "⋅": "·", "∙": "·", "µ": "μ", "Σ": "∑", "Π": "∏", "⟶": "→", "⟹": "⇒",
          "⇄": "⇌", "″": "''", "′": "'", "⩽": "≤", "⩾": "≥", "→": "→", "⇌": "⇌", "∣": "|"}
_TWO = {"<=": "≤", ">=": "≥", "!=": "≠", "->": "→", "=>": "⇒"}

# LaTeX command -> the symbol it draws (inverse of the converter's tables, plus what OMML/chemistry output and providers use).
_CMD: dict[str, str] = {}
for table in (GREEK, RELATIONS, ADD, MUL, ATOMS, PREFIX, BIG):
    for ch, cmd in table.items():
        if cmd.startswith("\\") and len(ch) == 1:
            _CMD.setdefault(cmd[1:], ch)
_CMD.update({"mu": "μ", "sum": "∑", "prod": "∏", "Sigma": "∑", "Pi": "∏", "to": "→", "rightarrow": "→", "longrightarrow": "→",
             "leftarrow": "←", "rightleftharpoons": "⇌", "leftrightarrow": "↔", "lvert": "|", "rvert": "|", "vert": "|", "mid": "|",
             "partial": "∂", "infty": "∞", "cdot": "·", "times": "×", "le": "≤", "ge": "≥", "ne": "≠", "neq": "≠", "leq": "≤", "geq": "≥",
             "circ": "°", "prime": "'", "ldots": "…", "cdots": "…", "dots": "…", "%": "%", "#": "#", "$": "$", "_": "_", "&": "&",
             "pm": "±", "mp": "∓", "div": "÷", "equiv": "≡", "approx": "≈", "propto": "∝", "angle": "∠", "triangle": "△", "nabla": "∇",
             "bigcup": "⋃", "bigcap": "⋂", "lfloor": "⌊", "rfloor": "⌋", "lceil": "⌈", "rceil": "⌉", "langle": "⟨", "rangle": "⟩",
             "|": "‖", "Vert": "‖", "therefore": "∴", "because": "∵", "hbar": "ℏ", "ell": "ℓ", "forall": "∀", "exists": "∃",
             "neg": "¬", "land": "∧", "lor": "∨", "Rightarrow": "⇒"})
_CMD = {k: _CANON.get(v, v) for k, v in _CMD.items()}
# Font commands are transparent here (this check compares symbols and levels); fonts are compared by verify.verify_omml.
_SKIP = {"left", "right", "mathrm", "mathit", "mathbf", "boldsymbol", "mathbb", "mathcal", "operatorname", "text", "textrm", "displaystyle", "limits", "big", "Big", "bigl",
         "bigr", "Bigl", "Bigr", "quad", "qquad", ",", ";", ":", "!", " ", "{", "}", "\\", "lbrace", "rbrace"}
_GROUP_CMDS = {"vec": "vec", "hat": "hat", "bar": "bar", "overline": "bar", "tilde": "tilde", "dot": "dot", "ddot": "ddot",
               "underline": "underline", "boxed": "", "overrightarrow": "vec", "underbrace": "underbrace", "overbrace": "overbrace"}
_FUNC_WORDS = re.compile(r"[A-Za-z]")


class _Out:
    def __init__(self):
        self.atoms: list[tuple[str, str]] = []
        self.marks: list[str] = []       # accents (vector arrows, hats): compared as a multiset

    def add(self, ch: str, level: str):
        ch = _CANON.get(ch, ch)
        for c in ch:
            if c not in IGNORED:
                self.atoms.append((c, level))


_LABEL_AFTER_DEGREE = re.compile(r"[a-z]{1,8}(?![A-Za-z])")
_CAPITAL_LABEL = re.compile(r"(?<![A-Za-z])[A-Z](cell|red|ox|rxn|sys|surr)(?![A-Za-z])")


def source_atoms(text: str, science: bool = False) -> tuple[list[tuple[str, str]], list[str]]:
    """Symbols of the provider's text with their script level ('' normal, '^' superscript, '_' subscript, '^_' nested ...).
    Labels typed on the line are subscripts: E°cell (letters after a degree sign), Ecell, and in Physics/Chemistry (`science`) t½."""
    out = _Out()
    i, n = 0, len(text)

    def one(level: str) -> None:
        """Consume one ASCII-script operand after ^ or _: (group), a number, a letter, a Greek letter, ∞, with an optional sign."""
        nonlocal i
        if level.endswith("_") and (m := re.match(r"[A-Za-z]{3,}(?=$|[\s),=<>≈≠≤≥+\-−×·*/])|[A-Za-z]{2}(?=$|[\s),=<>≈≠≤≥])", text[i:])):
            walk(m.group(), level)                       # R_eq, v_rms=: a lettered label subscript (a relation or operator ends it)
            i += m.end()
            return
        if i < n and text[i] in "+-−":
            out.add(text[i], level)
            i += 1
        if i < n and text[i] in "({":                     # x^(n+1) and a provider's x^{n+1} group the same way
            opener, closer = text[i], ")" if text[i] == "(" else "}"
            depth, j = 0, i
            while j < n:
                depth += {opener: 1, closer: -1}.get(text[j], 0)
                if depth == 0:
                    break
                j += 1
            walk(text[i + 1:j], level)
            i = j + 1
        elif i < n and text[i].isdigit():
            j = i
            while j < n and (text[j].isdigit() or (text[j] == "." and j + 1 < n and text[j + 1].isdigit())):
                j += 1
            walk(text[i:j], level)
            i = j
        elif i < n:
            walk(text[i], level)
            i += 1

    def walk(s: str, level: str) -> None:
        nonlocal i, n, text
        saved = (i, n, text)
        text, i, n = s, 0, len(s)
        while i < n:
            c = text[i]
            if text[i:i + 2] in _TWO:
                out.add(_TWO[text[i:i + 2]], level)
                i += 2
            elif c in SUPERSCRIPTS:
                out.add(SUPERSCRIPTS[c], level + "^")
                i += 1
            elif c in SUBSCRIPTS:
                out.add(SUBSCRIPTS[c], level + "_")
                i += 1
            elif c in "^_" and i + 1 < n:
                i += 1
                one(level + c)
            elif c in VULGAR:
                a, b = VULGAR[c]
                half_life = science and i > 0 and text[i - 1].isascii() and text[i - 1].isalpha() and not (i > 1 and text[i - 2].isalpha()) \
                    and not (i + 1 < n and (text[i + 1].isalnum()))
                out.add(f"{a}/{b}", level + ("_" if half_life else ""))
                i += 1
            elif (m := _CAPITAL_LABEL.match(text, i)) and (i == 0 or not text[i - 1].isalpha()):
                out.add(c, level)
                walk(m.group(1), level + "_")
                i = m.end()
            elif c in ROOTS:
                out.add("√" + (ROOTS[c] or ""), level)
                i += 1
            elif c == "⃗":                        # combining arrow: a vector
                out.marks.append("vec")
                i += 1
            elif c == "\u0302":                        # combining circumflex: a unit vector (k̂)
                out.marks.append("hat")
                i += 1
            elif not c.isascii() and len(d := unicodedata.normalize("NFD", c)) == 2 and d[1] == "\u0302" and d[0].isalpha():
                out.add(d[0], level)                    # î, ĵ typed as one character
                out.marks.append("hat")
                i += 1
            elif c == "°":
                out.add("°", level)
                i += 1
                if i < n and (m := _LABEL_AFTER_DEGREE.match(text, i)) and i > 1 and text[i - 2].isalpha():
                    walk(m.group(), level + "_")            # E°cell: the label after the degree sign
                    i = m.end()
            elif not c.isascii() and unicodedata.decomposition(c).startswith("<font>"):
                out.add(unicodedata.normalize("NFKC", c), level)   # 𝐅 ℝ 𝒜: the letter; its font is compared by verify.verify_text
                i += 1
            else:
                out.add(c, level)
                i += 1
        i, n, text = saved

    walk(text, "")
    return out.atoms, sorted(out.marks)


def latex_atoms(latex: str) -> tuple[list[tuple[str, str]], list[str]]:
    """The same reading of LaTeX: \\frac{a}{b} reads 'a / b', x^{2} reads x then 2 at '^', \\sqrt[3]{x} reads '√3 x'."""
    out = _Out()
    toks = re.findall(r"\\[A-Za-z]+|\\.|[{}^_]|[^\\{}^_]", latex)
    pos = 0

    def group() -> list[str]:
        """Tokens of the next {...} group or the next single token."""
        nonlocal pos
        while pos < len(toks) and toks[pos] == " ":
            pos += 1
        if pos >= len(toks):
            return []
        if toks[pos] != "{":
            pos += 1
            return [toks[pos - 1]]
        depth, start = 0, pos
        while pos < len(toks):
            depth += {"{": 1, "}": -1}.get(toks[pos], 0)
            pos += 1
            if depth == 0:
                break
        return toks[start + 1:pos - 1]

    def run(seq: list[str], level: str) -> None:
        nonlocal toks, pos
        saved = (toks, pos)
        toks, pos = seq, 0
        while pos < len(toks):
            t = toks[pos]
            pos += 1
            if t in "^_":
                sub = group()
                if t == "^" and sub == ["\\circ"]:
                    out.add("°", level)                        # 90^{\circ} is the degree sign typed as °
                elif t == "^" and all(x == "\\prime" or x == "'" for x in sub) and sub:
                    out.add("'" * len(sub), level)
                else:
                    run(sub, level + t)
            elif t == "\\frac" or t == "\\dfrac" or t == "\\tfrac":
                run(group(), level)
                out.add("/", level)
                run(group(), level)
            elif t == "\\sqrt":
                index = ""
                if pos < len(toks) and toks[pos] == "[":
                    j = toks.index("]", pos)
                    index = "".join(toks[pos + 1:j])
                    pos = j + 1
                out.add("√" + index, level)
                run(group(), level)
            elif t in ("\\left", "\\right"):
                if pos < len(toks) and toks[pos] == ".":
                    pos += 1                                     # \left. : an invisible delimiter
            elif t in ("\\underset", "\\overset"):
                script, body = group(), group()
                run(body, level)
                run(script, level + ("_" if t == "\\underset" else "^"))
            elif t in ("\\xrightarrow", "\\xleftarrow"):
                out.add("→" if t == "\\xrightarrow" else "←", level)
                if pos < len(toks) and toks[pos] == "[":
                    j = toks.index("]", pos)
                    run(toks[pos + 1:j], level)
                    pos = j + 1
                run(group(), level)
            elif t in ("\\begin", "\\end"):
                env = "".join(group())
                if env == "vmatrix":
                    out.add("|", level)
            elif t.startswith("\\") and t[1:] in _GROUP_CMDS:
                if _GROUP_CMDS[t[1:]]:
                    out.marks.append(_GROUP_CMDS[t[1:]])
                run(group(), level)
            elif t in ("\\mathrm", "\\operatorname", "\\text", "\\textrm", "\\mathit", "\\mathbf"):
                if pos < len(toks) and toks[pos] == "*":
                    pos += 1                                     # \operatorname*{max}
                run(group(), level)
            elif t.startswith("\\"):
                name = t[1:]
                if name in _CMD:
                    out.add(_CMD[name], level)
                elif name in _SKIP:
                    pass
                else:                                            # \sin, \log, \lim: the letters of the name
                    out.add(name, level)
            elif t not in "{}":
                out.add(t, level)
        toks, pos = saved

    run(toks, "")
    return out.atoms, sorted(out.marks)


def _flat_limits(atoms: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """limₓ→₀, lim x→0 and \\lim_{x \\to 0} are the same limit: after 'lim' the limit's own subscript level is dropped."""
    out, mode, base = [], False, ""
    for k, (ch, level) in enumerate(atoms):
        if mode:
            if level.startswith(base + "_"):
                level = base + level[len(base) + 1:]
            elif not (ch == "→" and level == base):
                mode = False
        out.append((ch, level))
        if k >= 2 and [a for a, _ in atoms[k - 2:k + 1]] == ["l", "i", "m"] and len({lv for _, lv in atoms[k - 2:k + 1]}) == 1:
            mode, base = True, level
    return out


def _read(original: str, latex: str, science: bool = False):
    (a, ma), (b, mb) = source_atoms(_clean(original), science), latex_atoms(latex)
    return (_flat_limits(a), ma), (_flat_limits(b), mb)


def _clean(original: str) -> str:
    return original.replace("\ue000SCRIPTS\ue002\ue001", "")


def preserved(original: str, latex: str, science: bool = False) -> bool:
    original = _clean(original)
    x, y = _read(original, latex, science)
    return x == y


def difference(original: str, latex: str, science: bool = False) -> str:
    """A short description of the first mismatch (for reports and tests)."""
    (a, ma), (b, mb) = _read(original, latex, science)
    if ma != mb:
        return f"accents differ: {ma} vs {mb}"
    for k, (x, y) in enumerate(zip(a, b, strict=False)):
        if x != y:
            return f"symbol {k + 1}: {x} vs {y}"
    return f"length {len(a)} vs {len(b)}" if len(a) != len(b) else ""


# ---------------------------------------------------------------------------------------------------------------------------------
# Structural invariants. The symbol/level comparison above cannot see grouping or upright-vs-italic; these checks cover the
# meaning-changing classes that slip through it. Each returns (code, message) for the first violation, or None.

_UNIT_LETTERS = set("msgJNKMAVWCTLh")
_SUP_TO_ASCII = {"⁻": "-", "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9"}
_TO_SUP = {v: k for k, v in _SUP_TO_ASCII.items()}
_TO_SUB = {"0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉", "-": "₋"}
_CARET_INT = re.compile(r"([\^_])([-−]?[0-9]+)(?![0-9A-Za-z(]|\.[0-9])")


def _walk(node):
    if isinstance(node, tuple):
        yield node
        for x in node[1:]:
            yield from _walk(x)
    elif isinstance(node, list):
        for x in node:
            yield from _walk(x)


def _is_unit_letter_var(node) -> bool:
    while isinstance(node, tuple) and node[0] in ("sup", "sub", "subsup"):
        node = node[1]
    return isinstance(node, tuple) and node[0] == "var" and node[1] in _UNIT_LETTERS


def tree_problems(tree) -> tuple[str, str] | None:
    """Invariants on the parse tree: no script on an already-scripted base (chained powers, nested sub/sup); no unit word followed
    by a unit letter set as a variable (rad s⁻¹ -> rad·s); no fraction directly after '÷'."""
    for n in _walk(tree):
        kind = n[0]
        if kind in ("sup", "sub", "subsup") and isinstance(n[1], tuple) and n[1][0] in ("sup", "sub", "subsup"):
            return "STRUCTURE_NOT_PRESERVED", "a script was applied to an expression that already has one"
        if kind == "seq":
            for a, b in zip(n[1], n[1][1:], strict=False):
                if a[0] in ("unit", "quantity") and _is_unit_letter_var(b):
                    return "AMBIGUOUS_UNIT", "a unit is followed by a letter set as a variable"
        if kind == "op" and n[2] == r"\div" and isinstance(n[3], tuple) and n[3][0] == "frac":
            return "AMBIGUOUS_FRACTION", "a fraction directly after '÷'"
    return None


def text_problems(original: str, latex: str, before: str) -> tuple[str, str] | None:
    """Invariants on the span text: a converted span never starts with a binary operator that continues text before it ((4)(5)
    + ... -> '+ ...' alone), and a spaced unit letter with a negative power after a number is never left as an italic variable."""
    if original[:1] in "+×·÷*/=" and before.strip() and not before.rstrip().endswith(("(", "[", "{")):
        return "JOINED_TO_TEXT", "the formula starts with an operator that continues the text before it"
    for m in re.finditer(r"[0-9]\s+([msgJNKMAVWCTLh])(?:\^-|\^−|⁻)", original):
        if r"\mathrm{" + m.group(1) + "}" not in latex:
            return "AMBIGUOUS_UNIT", f"'{m.group(1)}' with a negative power after a number is set as a variable"
    return None


def caret_to_unicode(text: str) -> str:
    """x^-2 -> x⁻², a_1 -> a₁: the same expression in the other notation, for the parity check."""
    def one(m):
        table = _TO_SUP if m.group(1) == "^" else _TO_SUB
        return "".join(table.get(c, c) for c in m.group(2).replace("−", "-"))
    return _CARET_INT.sub(one, text)


# ---------------------------------------------------------------------------------------------------------------------------------
# Word native equations: the expected symbols are read straight from the OMML tree (not from the converter's output), so a
# converted equation is accepted only if its LaTeX reads back to the same symbols, order and script levels.

_M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
_ACC_MARK = {"̂": "hat", "^": "hat", "̃": "tilde", "~": "tilde", "̇": "dot", "̈": "ddot", "⃗": "vec",
             "̅": "bar", "¯": "bar", "̄": "bar"}


class OmmlShape(Exception):
    pass


def _mval(el, tag: str, default: str | None = None) -> str | None:
    if el is None or (x := el.find(f"{_M}{tag}")) is None:
        return default
    return x.get(f"{_M}val", default if default is not None else "")


_NUMBER_SETS = {"ℂ": "C", "ℕ": "N", "ℙ": "P", "ℚ": "Q", "ℝ": "R", "ℤ": "Z"}


def omml_atoms(el) -> tuple[list[tuple[str, str]], list[str]]:
    out = _Out()

    def chars(s: str, level: str):
        for c in s:
            if c in SUPERSCRIPTS:
                out.add(SUPERSCRIPTS[c], level + "^")
            elif c in SUBSCRIPTS:
                out.add(SUBSCRIPTS[c], level + "_")
            elif c in _NUMBER_SETS:
                out.add(_NUMBER_SETS[c], level)                  # ℝ is the letter R in \mathbb{R}
            elif not c.isascii() and unicodedata.decomposition(c).startswith("<font>"):
                out.add(unicodedata.normalize("NFKC", c), level)  # 𝑥 𝐅 𝛼 ℎ (Word for the web): the letter; fonts are verify_omml's
            elif c not in "⁡⁢⁣​" and not c.isspace():          # U+2008 etc. between units (Word for the web): spacing only
                out.add(c, level)

    def kids(e, level: str):
        for c in (e if e is not None else []):
            node(c, level)

    def part(e, tag: str, level: str):
        kids(e.find(f"{_M}{tag}"), level)

    def node(e, level: str):
        name = e.tag[len(_M):] if e.tag.startswith(_M) else None
        if name is None or name.endswith("Pr") or name in ("ctrlPr", "argPr"):
            return
        if name == "r":
            for t in e.findall(f"{_M}t"):
                chars(t.text or "", level)
        elif name in ("e", "num", "den", "sub", "sup", "deg", "fName", "lim", "oMath"):
            kids(e, level)
        elif name == "f":
            part(e, "num", level)
            out.add("/", level)
            part(e, "den", level)
        elif name == "sSup":
            part(e, "e", level)
            part(e, "sup", level + "^")
        elif name == "sSub":
            part(e, "e", level)
            part(e, "sub", level + "_")
        elif name == "sSubSup":
            part(e, "e", level)
            part(e, "sub", level + "_")
            part(e, "sup", level + "^")
        elif name == "sPre":
            part(e, "sub", level + "_")
            part(e, "sup", level + "^")
            part(e, "e", level)
        elif name == "rad":
            pr = e.find(f"{_M}radPr")
            deg = "" if _mval(pr, "degHide", "0") in ("1", "on", "true") else "".join(
                t.text or "" for t in (e.find(f"{_M}deg") if e.find(f"{_M}deg") is not None else []).iter(f"{_M}t"))
            out.add("√" + deg, level)
            part(e, "e", level)
        elif name == "nary":
            pr = e.find(f"{_M}naryPr")
            out.add(_mval(pr, "chr", "∫") or "∫", level)
            if _mval(pr, "subHide", "0") not in ("1", "on", "true"):
                part(e, "sub", level + "_")
            if _mval(pr, "supHide", "0") not in ("1", "on", "true"):
                part(e, "sup", level + "^")
            part(e, "e", level)
        elif name == "d":
            pr = e.find(f"{_M}dPr")
            beg, end, sep = _mval(pr, "begChr", "("), _mval(pr, "endChr", ")"), _mval(pr, "sepChr", "|")
            chars(beg or "", level)
            for k, x in enumerate(e.findall(f"{_M}e")):
                if k:
                    chars(sep or "", level)
                kids(x, level)
            chars(end or "", level)
        elif name == "func":
            part(e, "fName", level)
            part(e, "e", level)
        elif name in ("limLow", "limUpp"):
            part(e, "e", level)
            part(e, "lim", level + ("_" if name == "limLow" else "^"))
        elif name == "acc":
            out.marks.append(_ACC_MARK.get(_mval(e.find(f"{_M}accPr"), "chr", "̂"), "accent?"))
            part(e, "e", level)
        elif name == "bar":
            out.marks.append("bar" if _mval(e.find(f"{_M}barPr"), "pos", "bot") == "top" else "underline")
            part(e, "e", level)
        elif name == "m":
            for row in e.findall(f"{_M}mr"):
                for x in row.findall(f"{_M}e"):
                    kids(x, level)
        elif name == "eqArr":
            for x in e.findall(f"{_M}e"):
                kids(x, level)
        elif name in ("box", "borderBox"):
            part(e, "e", level)
        elif name == "groupChr":
            ch = _mval(e.find(f"{_M}groupChrPr"), "chr", "⏟")
            if ch in ("⏟", "⏞"):
                out.marks.append("underbrace" if ch == "⏟" else "overbrace")
            else:
                chars(ch, level)
            part(e, "e", level)
        elif name == "oMathPara":
            for x in e.findall(f"{_M}oMath"):
                kids(x, level)
        else:
            raise OmmlShape(name)

    node(el, "")
    return out.atoms, sorted(out.marks)


def omml_preserved(el, latex: str) -> bool:
    try:
        a, ma = omml_atoms(el)
    except OmmlShape:
        return False
    b, mb = latex_atoms(latex)
    return (_flat_limits(a), ma) == (_flat_limits(b), mb)
