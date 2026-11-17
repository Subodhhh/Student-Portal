"""Readings a reviewer can choose between for an expression the engine would not decide on its own (unit or variable).

Nothing here is applied automatically: a suggestion only fills the reviewer's correction box, and the corrected field is recognised
and checked again on import like any other correction. A reading is offered only when it keeps exactly the symbols of the source
(the same preservation check the converter passes) and uses only commands KaTeX renders in the portals."""
import re
from dataclasses import dataclass, field

from .parser import UNIT_LETTERS, Unreadable, check_latex
from .safety import latex_atoms, preserved
from .symbols import PROVIDER_COMMANDS, SUBSCRIPTS, SUPERSCRIPTS, UNITS

_UNIT_WORDS = UNITS | UNIT_LETTERS
_UNIT_ALT = "|".join(sorted(_UNIT_WORDS, key=len, reverse=True))
# Word equation LaTeX: the unit symbols after a number - "5 m {s}^{-1}", "5N" - each with its own power
_LATEX_UNITS = re.compile(r"(?<![A-Za-z\\])(\d+(?:\.\d+)?)((?:(?:\s|\\[,;: ])*\{?(?:" + _UNIT_ALT + r")\}?(?:\^\{[^{}]*\})?(?![A-Za-z]))+)")
_LATEX_UNIT = re.compile(r"(\s|\\[,;: ])*\{?(" + _UNIT_ALT + r")\}?(\^\{[^{}]*\})?")
# plain text: only letters, numbers, spaces, Unicode/caret scripts, ·, /, = and signs - anything else is not suggested
_PLAIN = re.compile(r"[A-Za-z0-9 .·/=+\-−^²³¹⁰⁴-⁹⁻⁺₀-₉()]+")


def _ok(source: str, latex: str, source_is_latex: bool) -> bool:
    """Only KaTeX-safe commands, and exactly the source's symbols at the same levels (fonts are what the reviewer chooses)."""
    try:
        check_latex(latex, PROVIDER_COMMANDS)
    except Unreadable:
        return False
    return latex_atoms(source) == latex_atoms(latex) if source_is_latex else preserved(source, latex)


def word_equation_units(latex: str) -> list[tuple[str, str]] | None:
    """For a Word equation whose italic letter after a number may be a unit: (label, LaTeX) for the unit reading (upright symbols,
    as Word's Normal Text would show them) and for the variable reading (exactly as the file shows it)."""
    m = _LATEX_UNITS.search(latex)
    if not m:
        return None
    units = _LATEX_UNIT.sub(lambda u: r"\," + r"\mathrm{" + u.group(2) + "}" + (u.group(3) or ""), m.group(2))
    as_units = latex[:m.start(2)] + units + latex[m.end(2):]
    return [("as units (upright)", as_units), ("as variables (italic, as in the file)", latex)]


def _plain_latex(text: str, upright: bool) -> str:
    out, k = [], 0
    while k < len(text):
        c = text[k]
        if c.isascii() and c.isalpha():
            j = k
            while j < len(text) and text[j].isascii() and text[j].isalpha():
                j += 1
            word = text[k:j]
            if upright and word in _UNIT_WORDS:
                out.append(r"\mathrm{" + word + "}")
            else:
                out.append(word)
            k = j
        elif c in SUPERSCRIPTS or c in SUBSCRIPTS:
            table, mark = (SUPERSCRIPTS, "^") if c in SUPERSCRIPTS else (SUBSCRIPTS, "_")
            j = k
            while j < len(text) and text[j] in table:
                j += 1
            out.append(mark + "{" + "".join(table[x] for x in text[k:j]) + "}")
            k = j
        elif c == " ":
            out.append(r"\," if upright else " ")
            k += 1
        else:
            out.append({"·": r"\cdot ", "−": "-"}.get(c, c))
            k += 1
    return "".join(out)


def plain_units(text: str) -> list[tuple[str, str]] | None:
    """For typed text such as "m² s" or "3 m = 6": the unit reading (unit symbols upright) and the variable reading (italic)."""
    if not _PLAIN.fullmatch(text) or "^" in text:
        return None
    as_units, as_vars = _plain_latex(text, True), _plain_latex(text, False)
    if as_units == as_vars:
        return None
    return [("as units (upright)", as_units), ("as variables (italic)", as_vars)]


def readings(code: str, source: str, latex: str | None, word_equation: bool) -> list[tuple[str, str]]:
    """The checked readings of one flagged expression, [] when none is offered."""
    if code != "AMBIGUOUS_UNIT":
        return []
    found = (word_equation_units(latex) if word_equation and latex else plain_units(source)) or []
    return [(label, x) for label, x in found if _ok(latex if word_equation else source, x, word_equation)]


# ---------------------------------------------------------------------------------------------------------- other doubts
# For a doubt the engine will not decide (a superscript flattened by copying, the extent of a power, of a numerator or a
# denominator), the plausible readings are written out with explicit brackets, carets and dots - the notation that removes the
# doubt - and each is recognised by the full engine (parser and independent check) like any correction. Only readings that then
# pass are offered; nothing is applied without the reviewer choosing it.
from itertools import product as _product  # noqa: E402

_FUNC = r"(?:sin|cos|tan|cot|sec|csc|cosec|sinh|cosh|tanh|coth|sech|csch|ln|lg|log|exp|arcsin|arccos|arctan)"
_SCRIPTS = r"(?:\^\([^()]*\)|\^[A-Za-z0-9]|[⁰¹²³⁴-⁹⁺⁻]+)*"
# one factor: a bracket, a number or one letter, with its powers (f(x) is two factors - f and (x) - so both readings are offered)
_ATOM = r"(?:\([^()]*\)|\[[^\[\]]*\]|\d+(?:\.\d+)?|[A-Za-zΑ-Ωα-ω])" + _SCRIPTS
_OPERAND = r"(?:" + _FUNC + r"\s*)?" + _ATOM                         # ln x, sin(3x): a function takes its argument
_TERM_END = re.compile(r"[+\-−=<>,;:]")


def _wrap(x: str) -> str:
    """x in brackets, unless it already is exactly one bracketed group ((a+b) stays (a+b), never ((a+b)))."""
    x = x.strip()
    if x.startswith("(") and x.endswith(")"):
        depth = 0
        for k, ch in enumerate(x):
            depth += {"(": 1, ")": -1}.get(ch, 0)
            if depth == 0 and k < len(x) - 1:
                break
        else:
            return x
    return f"({x})"


def _product_reading(src: str, end: int, letter: str) -> str:
    """e·x - in brackets when a '/' follows, so the product (not only x) is what is divided."""
    return f"(e·{letter})" if src[end:].lstrip().startswith("/") else f"e·{letter}"


def _slash_points(src: str) -> list[tuple[int, int, str, list[tuple[str, str]]]]:
    integral = re.search(r"∫(?P<body>[^∫]*?)\s*(?P<d>d[a-zθ])\s*/\s*(?P<den>" + _OPERAND + ")", src)
    if integral:
        body, d, den = integral.group("body").strip(), integral.group("d"), integral.group("den")
        return [(integral.start(), integral.end(), "slash", [(f"the integral of ({body})/{den}", f"∫ {_wrap(body)}/{_wrap(den)} {d}"),
                                                    (f"the integral divided by {den}", f"(∫{body} {d})/{_wrap(den)}")])]
    m = re.search(r"/", src)
    if not m:
        return []
    # the fraction lives inside the innermost bracket around the '/' (√(RT/3M)): a reading never moves that bracket
    opened = []
    for k, ch in enumerate(src[:m.start()]):
        if ch in "([":
            opened.append(k)
        elif ch in ")]" and opened:
            opened.pop()
    group = opened[-1] + 1 if opened else 0
    # lim_(x→0) sin x/x: the limit applies to the whole fraction, so the numerator starts after it, never before it
    for prefix in re.finditer(r"lim_\([^()]*\)\s*", src[:m.start()]):
        if prefix.start() >= group:
            group = prefix.end()
    left = src[group:m.start()]
    cut, depth = 0, 0
    for k, ch in enumerate(left):                                     # the term starts after the last top-level + − = < > , ; :
        depth += {"(": 1, "[": 1, ")": -1, "]": -1}.get(ch, 0)
        if depth == 0 and _TERM_END.match(left, k) and not (ch in "+-−" and not left[:k].strip()):
            cut = k + 1
    start = group + cut + len(left[cut:]) - len(left[cut:].lstrip())
    lmax = src[start:m.start()].rstrip()
    lmin_m = re.search(_ATOM + r"$", lmax)
    r1 = re.match(r"\s*(" + _OPERAND + r")", src[m.end():])
    if not lmax or not lmin_m or not r1:
        return []
    rmin = r1.group(1)
    j = m.end() + r1.end()
    glued = re.match(r"(?:" + _ATOM + r")*", src[j:]).group(0)            # 2x, f(x): factors glued to the denominator
    rmax = rmin + glued
    alts = [(f"({lmax}) over ({rmax})", f"{_wrap(lmax)}/{_wrap(rmax)}")]
    if glued:
        alts.append((f"({lmax}) over ({rmin}), times {glued}", f"({_wrap(lmax)}/{_wrap(rmin)}){glued}"))
    prefix = lmax[:len(lmax) - len(lmin_m.group(0))]
    if re.search(_FUNC + r"\s*$", prefix):                              # sin x/x: sin of the whole fraction
        alts.append((f"{prefix.strip()} applied to ({lmin_m.group(0)})/({rmax})", f"{prefix}({_wrap(lmin_m.group(0))}/{_wrap(rmax)})"))
    end = j + len(glued)
    chain = re.match(r"\s*/\s*(" + _OPERAND + r")", src[end:])           # a/b/c
    if chain:
        c = chain.group(1)
        alts = [(f"({lmax} over {rmax}) over {c}", f"({_wrap(lmax)}/{_wrap(rmax)})/{_wrap(c)}"), (f"{lmax} over ({rmax} over {c})", f"{_wrap(lmax)}/({_wrap(rmax)}/{_wrap(c)})")]
        end += chain.end()
    return [(start, end, "slash", alts)]


_SUB_CHARS = "".join(k for k, v in SUBSCRIPTS.items())
_SUP_CHARS = "".join(k for k, v in SUPERSCRIPTS.items())
_ON_LINE = r"(?:\d+(?:\.\d+)?|[A-Za-zΑ-Ωα-ωπ∞]|\([^()]*\))"           # what a limit typed on the line can be
_TO_SUB = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
_TO_SUP = str.maketrans("0123456789+-−", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻")
_UNIT_PREFIXES = "mkMGnpcμ"


def _code_points(src: str, code: str) -> list[tuple[int, int, str, list[tuple[str, str]]]]:
    """Readings of the doubts a reason code names, each written in the explicit notation that removes the doubt."""
    pts = []
    if code == "BOUND_TYPED_ON_LINE":
        # ∫0π, ∑1n, ∫01: both limits typed on the line with no scripts - limits (lower then upper), or the start of the body
        for m in re.finditer(r"([∫∬∭∮∑∏])(\d)(\d|[A-Za-zΑ-Ωα-ωπ∞])(?=[\s(]|$)", src):
            op, lower, upper = m.groups()
            pts.append((m.start(), m.end(), "bound", [(f"from {lower} to {upper}", f"{op}_({lower})^({upper}) "),
                                                       (f"{lower}{upper} is part of what follows", f"{op} {lower}{upper}")]))
        # ∫₀π / ∑ᵢ₌₁n: the character on the line is the other limit, or a factor of the body (that reading keeps one limit)
        for m in re.finditer(rf"([∫∬∭∮∑∏])([{re.escape(_SUB_CHARS)}]+|[{re.escape(_SUP_CHARS)}]+)({_ON_LINE})", src):
            op, script, other = m.groups()
            other_limit = (f"{op}{script}^({other.strip('()')})" if script[0] in _SUB_CHARS          # lower limit first, as the
                           else f"{op}_({other.strip('()')})^({''.join(SUPERSCRIPTS[c] for c in script)})")   # engine writes it
            pts.append((m.start(), m.end(), "bound", [(f"{other} is the other limit", other_limit),
                                                       (f"{other} multiplies what follows", f"{op}{script} {other}")]))
        for m in re.finditer(r"(lim[" + re.escape(_SUB_CHARS) + r"→\-∞]+)(\S)", src):          # limₓ→∞x: where the limit ends
            pts.append((m.start(), m.end(), "bound", [("the limit ends before it", f"{m.group(1)} {m.group(2)}")]))
    elif code == "AMBIGUOUS_MATRIX":
        # |1 -2; 3 4|: is the entry −2 (a negative number) or is "1 -2" the difference 1 − 2?
        for m in re.finditer(r"([|\[(])([^|\[\]()]*;[^|\[\]()]*)([|\])])", src):
            rows = [r.split() for r in m.group(2).split(";")]
            entries = "; ".join(", ".join(r) for r in rows)
            joined = []
            for r in rows:
                cells: list[str] = []
                for c in r:
                    if cells and c[:1] in "+-−" and len(c) > 1:
                        cells[-1] += f" {c[0]} {c[1:]}"
                    else:
                        cells.append(c)
                joined.append(", ".join(cells))
            pts.append((m.start(), m.end(), "matrix", [("a sign starts an entry", m.group(1) + entries + m.group(3)),
                                                        ("a sign is subtraction", m.group(1) + "; ".join(joined) + m.group(3))]))
    elif code == "AMBIGUOUS_CHEMICAL_CHARGE":
        # Fe3+, SO42-, [Ca2+]: the digits before the sign split into a count (subscript) and a charge (superscript)
        for m in re.finditer(r"([A-Z][A-Za-z0-9()]*?[A-Za-z)])(\d+)([+\-−])(?![\w+\-−])", src):
            core, digits, sign = m.groups()
            core = re.sub(r"(?<=[A-Za-z)])\d+", lambda d: d.group(0).translate(_TO_SUB), core)   # Cr2O: earlier counts too
            alts = []
            # the charge is its last digit (Fe³⁺, SO₄²⁻) or only the sign (Fe₃⁺, SO₄₂⁻); no ion has a two-digit charge
            for count, charge in ((digits[:-1], digits[-1]), (digits, "")):
                if count.startswith("0") or charge == "0":
                    continue
                label = (f"count {count}, " if count else "") + f"charge {charge}{'+' if sign == '+' else '−'}"
                alts.append((label, core + count.translate(_TO_SUB) + (charge + sign).translate(_TO_SUP)))
            pts.append((m.start(), m.end(), "charge", alts))
    elif code == "INLINE_EXPONENT":
        # 6 ms-1, 4 m s-2: the hyphen and digits after a unit are its power; 'ms' may also be metre·second
        for m in re.finditer(r"(?<=\s)([A-Za-zμΩ]{1,3})[-−](\d+)(?![\d.])", src):
            word, power = m.group(1), "⁻" + m.group(2).translate(_TO_SUP)
            alts = [(f"{word} to the power −{m.group(2)}", word + power)]
            if len(word) == 2 and word[0] in _UNIT_PREFIXES:
                alts.insert(0, (f"{word[0]} times {word[1]} to the power −{m.group(2)}", f"{word[0]} {word[1]}{power}"))
            pts.append((m.start(), m.end(), "unit-power", alts))
    return pts


# Function names whose glued number may be a base (log₂x) as well as a power or the start of the argument
_BASE_FUNCS = {"log", "lg"}
_FUNCTION_DIGITS = re.compile(r"(?<![A-Za-z\\])(" + _FUNC + r")(\d+)(?![\d.])")


def _function_points(src: str) -> list[tuple[int, int, str, list[tuple[str, str]]]]:
    """sin3 x, cos2x, log2x: the number glued to a function name is its power (sin³x), its base (log₂x, logarithms only) or the
    start of its argument (sin 3x). Every reading is written explicitly, so the engine can check it like a correction."""
    pts = []
    for m in _FUNCTION_DIGITS.finditer(src):
        func, num = m.group(1), m.group(2)
        alts = [(f"{func} to the power {num}", f"{func}^({num})")]
        if func in _BASE_FUNCS:
            alts.append((f"{func} to base {num}", f"{func}_({num})"))
        alts.append((f"{num} is part of what {func} acts on", f"{func} {num}"))
        pts.append((m.start(), m.end(), "function", alts))
    return pts


_LEIBNIZ = re.compile(r"(?<![A-Za-z])d(\d)([A-Za-z])\s*/\s*d([A-Za-z])(\d)(?![\d.])")


def _derivative_points(src: str) -> list[tuple[int, int, str, list[tuple[str, str]]]]:
    """d2y/dx2: each digit may be a power, an index below or a factor. The reading where both are powers is the Leibniz second
    derivative d²y/dx²; every combination is offered (the engine checks each), the derivative first."""
    pts = []
    readings = (("power", "^({})"), ("index", "_({})"), ("times", "·{}"))
    for m in _LEIBNIZ.finditer(src):
        top, f, x, bottom = m.groups()
        alts = []
        for (top_name, top_form), (bottom_name, bottom_form) in _product(readings, readings):
            numerator, denominator = f"d{top_form.format(top)}{f}", f"d{x}{bottom_form.format(bottom)}"
            label = (f"derivative d{top}{f}/d{x}{bottom} (order {top})" if (top_name, bottom_name) == ("power", "power")
                     else f"{top} after d: {top_name}; {bottom} after {x}: {bottom_name}")
            alts.append((label, f"({numerator})/({denominator})"))
        pts.append((m.start(), m.end(), "derivative", alts))
    return pts


_FLAT_LIMIT = re.compile(r"\blim\s*([A-Za-zθ])\s*(→|->)\s*([+\-−]?(?:\d+(?:\.\d+)?|∞|[A-Za-zπ])[+\-−]?)")


def _explicit_limits(src: str) -> str:
    """limx→0 -> lim_(x→0): the variable and its approach were written below 'lim' before the copy flattened them onto the line.
    That has one reading, so it is written explicitly before the other doubts are read (it is never a choice to offer)."""
    src = _FLAT_LIMIT.sub(lambda m: f"lim_({m.group(1)}→{m.group(3)})", src)
    # sinx -> sin x: the lexer reads a function glued to one letter as that function of the letter; spacing it changes nothing
    # but lets every rebuilt reading (brackets moved around it) be checked the same way
    return re.sub(r"(?<![A-Za-z\\])(" + _FUNC + r")([A-Za-zθ])(?![A-Za-z(])", r"\1 \2", src)


def _points(src: str, code: str) -> list[tuple[int, int, str, list[tuple[str, str]]]]:
    """(start, end, kind, [(label, replacement)]) for every place in `src` whose reading is in doubt: every kind is looked for, since one
    expression can hold several (ex (x + 3) / (x + 4) 2: a flattened eˣ and a flattened square)."""
    pts: list[tuple[int, int, str, list[tuple[str, str]]]] = []
    for m in re.finditer(r"(?<![A-Za-z\\])e([A-Za-zΑ-Ωα-ω])(?![A-Za-z(^_])", src):
        pts.append((m.start(), m.end(), "e", [(f"e to the power {m.group(1)}", f"e^({m.group(1)})"),
                                              (f"e times {m.group(1)}", _product_reading(src, m.end(), m.group(1)))]))
    for m in re.finditer(r"(\)|(?<![A-Za-z])[A-Za-z])( ?)(\d+)(?![\d.])", src):
        base, num = m.group(1), m.group(3)
        # e13x: the letter glued after the number may belong to the power too (e¹³ˣ); it is kept, as typed, in every other reading
        tail = re.match(r"[A-Za-zΑ-Ωα-ω](?![A-Za-zΑ-Ωα-ω(])", src[m.end():]) if base == "e" and not m.group(2) else None
        t = tail.group(0) if tail else ""
        alts = [(f"power {num}", f"{base}^({num}){t}")]
        if base != ")":
            alts.append((f"subscript {num}", f"{base}_({num}){t}"))
        alts.append((f"times {num}", f"{base}·{num}{t}"))
        if t:
            alts.append((f"power {num}{t}", f"{base}^({num}{t})"))     # last, so "number" readings keep one meaning per position
        pts.append((m.start(), m.end() + len(t), "number" if base != ")" else "bracket", alts))
    # (1 + 1/x)x: one letter glued after a closing bracket is a power lost in copying, or a product - both are offered
    for m in re.finditer(r"\)([A-Za-zθ])(?![A-Za-z(_^])", src):
        pts.append((m.start(), m.end(), "bracket-letter", [(f"power {m.group(1)}", f")^({m.group(1)})"),
                                                           (f"times {m.group(1)}", f")·{m.group(1)}")]))
    for m in re.finditer(r"\^([A-Za-z0-9])([^\s()]+)", src):
        short, run = m.group(1), (m.group(1) + m.group(2)).rstrip(".,;:")
        if len(run) > 1:
            pts.append((m.start(), m.start() + 1 + len(run), "caret", [(f"power {short}", f"^({short}){run[1:]}"), (f"power {run}", f"^({run})")]))
    pts += _function_points(src)
    if code in ("AMBIGUOUS_FRACTION", "AMBIGUOUS_DENOMINATOR"):
        pts += _slash_points(src)
    pts += _code_points(src, code)
    # a whole Leibniz derivative (d2y/dx2) is one point: its two digits are read together, never as unrelated factors
    derivatives = _derivative_points(src)
    pts = [p for p in pts if not any(p[0] < d[1] and d[0] < p[1] for d in derivatives)] + derivatives
    pts.sort()
    out, last = [], -1
    for p in pts:                                                       # overlapping points: the first wins
        if p[0] >= last:
            out.append(p)
            last = p[1]
    return out


@dataclass
class CandidateSet:
    """Every reading of one doubtful expression that the engine accepts, within the declared limit.

    readings      [(label, whole field text)] that pass recognition and every check, deduplicated by what would be stored;
    combinations  how many readings the declared grammar gives (the product of every doubtful place's choices);
    checked       how many of them were checked (all of them when complete);
    rejected      checked readings the engine refused (unreadable, or failing a check) - never shown, but counted;
    complete      True when every combination was checked, so `readings` is the full list within the grammar."""
    readings: list[tuple[str, str]] = field(default_factory=list)
    combinations: int = 0
    checked: int = 0
    rejected: int = 0
    complete: bool = True


# Combinations checked per doubtful expression: 4 places with 3 readings each (81) are all checked; more is disclosed as incomplete
MAX_CANDIDATES = 81


def _ordered_choices(pts: list, limit: int):
    """Index tuples (one choice per point), at most `limit` of them: first the readings that read every place of the same kind the
    same way (all powers, all subscripts ... - how a formula is usually written), then the mixed ones in a fixed order."""
    kinds = list(dict.fromkeys(p[2] for p in pts))
    uniform = []
    for choice in _product(*(range(max(len(p[3]) for p in pts if p[2] == k)) for k in kinds)):
        pick = dict(zip(kinds, choice, strict=True))
        if all(pick[p[2]] < len(p[3]) for p in pts):
            uniform.append(tuple(pick[p[2]] for p in pts))
    seen = set()
    for combo in uniform:
        seen.add(combo)
        yield combo
        limit -= 1
        if limit <= 0:
            return
    for combo in _product(*(range(len(p[3])) for p in pts)):
        if combo in seen:
            continue
        yield combo
        limit -= 1
        if limit <= 0:
            return


def candidate_set(field_text: str, start: int, end: int, code: str, check) -> CandidateSet:
    """All readings of the flagged expression field_text[start:end], each written out as the whole corrected field and checked with
    `check(text)` (recognition of the whole field: returns what would be stored, or None while it still needs review). Readings that
    store the same thing are one reading. Nothing is ranked by how common it is; the uniform readings are only listed first."""
    src = _explicit_limits(field_text[start:end])
    pts = _points(src, code)
    result = CandidateSet()
    if not pts:
        if src != field_text[start:end]:          # only a flattened limit: its one explicit reading is the candidate
            pts = [(0, len(src), "limit", [("written explicitly", src)])]
        else:
            return result
    result.combinations = 1
    for p in pts:
        result.combinations *= len(p[3])
    seen: set[str] = set()
    for combo in _ordered_choices(pts, MAX_CANDIDATES):
        result.checked += 1
        rebuilt, cursor, labels = [], 0, []
        for (a, b, _kind, alts), k in zip(pts, combo, strict=True):
            label, replacement = alts[k]
            rebuilt += [src[cursor:a], replacement]
            labels.append((src[a:b].lstrip(")") or src[a:b], label))      # "c", not ")c", for a letter after a bracket
            cursor = b
        rebuilt.append(src[cursor:])
        candidate = field_text[:start] + "".join(rebuilt) + field_text[end:]
        stored = check(candidate)
        if stored is None:
            result.rejected += 1
            continue
        if stored in seen:
            continue
        seen.add(stored)
        names = list(dict.fromkeys(label for _, label in labels))
        result.readings.append(("; ".join(names) if len(names) == 1 or len(pts) == 1
                                else "; ".join(f"{piece}: {label}" for piece, label in labels), candidate))
    result.complete = result.checked == result.combinations
    return result


def alternatives(field_text: str, start: int, end: int, code: str, check) -> list[tuple[str, str]]:
    """The readings of candidate_set as [(label, field text)] (kept for callers that need only the list)."""
    return candidate_set(field_text, start, end, code, check).readings
