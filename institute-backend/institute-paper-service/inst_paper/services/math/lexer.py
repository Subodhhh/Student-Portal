"""Tokens for mathematical recognition, and the segmentation that decides which parts of ordinary text are mathematics.

Segmentation is deliberately conservative: a run of tokens becomes a formula candidate only when it contains a STRONG signal
(a superscript, a Greek letter, an integral sign, a function applied to an argument, ...). Ordinary words break runs, so prose,
single letters, prices, dates, units like m/s and labels like "(a)" are never treated as formulas.
"""
import re
import unicodedata
from dataclasses import dataclass

from .chem import _Flag, bracket_species
from .markers import SPLIT_TEXT
from .symbols import (BRACKET_FUNCTIONS, math_alphanumeric, CONTEXT_UNITS, ADD, ATOMS, BIG, BIG_GREEK, COMBINING_ARROW, FUNCTIONS, GREEK, MUL, POSTFIX, PREFIX, PROSE_2, RELATIONS, ROOTS,
                      SUBSCRIPTS, SUPERSCRIPTS, UNITS, VULGAR)

TWO_CHAR = ("<=", ">=", "!=", "->", "=>")
# ^ or _ + an optionally signed integer, glued, not continued by a digit, a letter or a decimal point (x^2y, x^2.5 stay ASCII scripts)
_ASCII_SCRIPT = re.compile(r"([-−+]?[0-9]+)(?![0-9A-Za-z(]|\.[0-9])")
ROMAN = {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"}


@dataclass
class Tok:
    kind: str           # NUM WORD VAR POINTS UNIT FUNC PROSE GREEK SUP SUB VFRAC REL ADD MUL SLASH CARET USCORE OPEN CLOSE BAR COMMA
    value: object       # SEMI BIG ROOT PARTIAL ATOM PREFIX POSTFIX VEC OTHER
    start: int
    end: int
    space: bool         # whitespace before this token
    raw: str = ""
    role: str = "W"     # S(trong) / W(eak) / B(reak)
    next_word: bool = False   # the last token of a formula candidate, followed by an ordinary word ("= 3 half-lives")
    flag: str | None = None   # a review reason found by a detector (INLINE_EXPONENT ...)


def lex(text: str, offset: int = 0) -> list[Tok]:
    toks: list[Tok] = []
    i, n, space = 0, len(text), False

    def add(kind, value, j):
        nonlocal space
        toks.append(Tok(kind, value, offset + i, offset + j, space, text[i:j]))
        space = False

    while i < n:
        c = text[i]
        if c.isspace():
            space, i = True, i + 1
            continue
        if text.startswith(SPLIT_TEXT, i):
            i += len(SPLIT_TEXT)                 # two separately formatted scripts: they stay two tokens (never merged into one)
            continue
        if text[i:i + 2] in TWO_CHAR:
            add("REL", RELATIONS[text[i:i + 2]], i + 2)
            i += 2
            continue
        if c.isdigit() and c.isascii():
            j = i
            while j < n and text[j].isdigit() and text[j].isascii():
                j += 1
            if j + 1 < n and text[j] == "." and text[j + 1].isdigit():
                j += 1
                while j < n and text[j].isdigit() and text[j].isascii():
                    j += 1
            add("NUM", text[i:j], j)
        elif c in "kMm" and i + 1 < n and text[i + 1] == "Ω" and space and toks and toks[-1].kind == "NUM":
            add("UNIT", c + "Ω", i + 2)                         # 4 kΩ, 2.2 MΩ, 5 mΩ: a prefixed ohm after a number
        elif (styled := math_alphanumeric(c)) is not None and styled[1] and styled[0].isascii() and styled[0].isalpha() and (
                styled[1] in ("mathbf", "boldsymbol") or styled[0].isupper()):
            add("GREEK", "\\" + styled[1] + "{" + styled[0] + "}", i + 1)      # 𝐅 𝒂 ℝ 𝒜: one symbol, its font is its meaning
        elif c.isascii() and c.isalpha():
            j = i
            while j < n and text[j].isascii() and text[j].isalpha():
                j += 1
            if j < n and text[j] == COMBINING_ARROW:         # a⃗: the arrow belongs to the last letter only
                if j - 1 > i:
                    add("WORD", text[i:j - 1], j - 1)
                    i = j - 1
                add("VEC", text[j - 1], j + 1)
                j += 1
            elif j < n and text[j] == COMBINING_HAT and j - i == 1 and not (j + 1 < n and text[j + 1].isalpha()):
                add("HAT", text[i], j + 1)                      # k̂: a unit vector (a single letter, never inside a word)
            else:
                add("WORD", text[i:j], j)
        elif c in "^_" and (m := _ASCII_SCRIPT.match(text, i + 1)):
            # x^2, s^-1, 10^-7, a_1: an ASCII integer script is the same token as the Unicode one (x², s⁻¹, a₁), so every rule
            # (units, dimensions, derivatives, chained powers) reads CSV and Word/Excel notation identically.
            value = m.group(1).replace("−", "-").replace("+", "")
            add("SUP" if c == "^" else "SUB", value, m.end())
        elif c in SUPERSCRIPTS or c in SUBSCRIPTS:
            table, kind = (SUPERSCRIPTS, "SUP") if c in SUPERSCRIPTS else (SUBSCRIPTS, "SUB")
            j = i
            while j < n and text[j] in table:
                j += 1
            add(kind, "".join(table[ch] for ch in text[i:j]), j)
        elif c in BIG_GREEK and i + 1 < n and (text[i + 1] in SUPERSCRIPTS or text[i + 1] in SUBSCRIPTS or text[i + 1] in "_^"):
            add("BIG", BIG_GREEK[c], i + 1)
        elif c in "μµ" and i + 1 < n and text[i + 1] in "FmsAgCHVWJLlΩTN" and (i + 2 >= n or not text[i + 2].isalpha()) and toks and toks[-1].kind == "NUM":
            add("UNIT", "μ" + text[i + 1], i + 2)                # 10 μF, 5 μm: a prefixed unit after a number
        elif c == "°" and i + 1 < n and text[i + 1] == "C" and (i + 2 >= n or not text[i + 2].isalpha()) and toks and toks[-1].kind == "NUM":
            add("UNIT", "°C", i + 2)                            # 25 °C: degree Celsius, one unit
        elif c in GREEK and i + 1 < n and text[i + 1] in (COMBINING_ARROW, COMBINING_HAT):
            add("VEC" if text[i + 1] == COMBINING_ARROW else "HAT", GREEK[c], i + 2)    # ω⃗, τ⃗: a Greek vector
        elif c in GREEK:
            add("GREEK", GREEK[c], i + 1)
        elif _standalone_hat(text, i):
            add("HAT", unicodedata.normalize("NFD", c)[0], i + 1)                       # î ĵ typed as single characters
        elif c in VULGAR:
            add("VFRAC", VULGAR[c], i + 1)
        elif c in RELATIONS:
            add("REL", RELATIONS[c], i + 1)
        elif c in ADD:
            add("ADD", ADD[c], i + 1)
        elif c in MUL:
            add("MUL", MUL[c], i + 1)
        elif (c == "'" and toks and not space and ((toks[-1].kind == "WORD" and len(toks[-1].value) == 1)
                                                    or (toks[-1].kind == "POSTFIX" and toks[-1].raw == "'"))
              and not (i + 1 < n and text[i + 1].isalpha())):                 # D'Alembert, O'Neil: a name, not a prime; y'' is
            add("POSTFIX", "'", i + 1)                       # f'(x); apostrophes inside words stay text
        elif c in POSTFIX:
            add("POSTFIX", POSTFIX[c], i + 1)
        else:
            kind = {"/": "SLASH", "^": "CARET", "_": "USCORE", "(": "OPEN", "[": "OPEN", "{": "OPEN", ")": "CLOSE", "]": "CLOSE",
                    "}": "CLOSE", "⌊": "OPEN", "⌈": "OPEN", "⌋": "CLOSE", "⌉": "CLOSE", "|": "BAR", ",": "COMMA", ";": "SEMI", "∂": "PARTIAL"}.get(c)
            if kind:
                add(kind, c, i + 1)
            elif c in BIG:
                add("BIG", BIG[c], i + 1)
            elif c in ROOTS:
                add("ROOT", ROOTS[c], i + 1)
            elif c in ATOMS:
                add("ATOM", ATOMS[c], i + 1)
            elif c in PREFIX:
                add("PREFIX", PREFIX[c], i + 1)
            else:
                add("OTHER", c, i + 1)
        i = toks[-1].end - offset if toks else i + 1
    return toks


OPERAND_START = {"NUM", "VAR", "POINTS", "UNIT", "GREEK", "SUP", "SUB", "VFRAC", "ATOM", "VEC", "HAT", "OPEN", "BAR", "ROOT", "PARTIAL",
                 "BIG", "FUNC", "PREFIX"}
OPERAND_END = {"NUM", "VAR", "POINTS", "UNIT", "GREEK", "SUP", "SUB", "VFRAC", "ATOM", "VEC", "HAT", "CLOSE", "BAR", "POSTFIX"}
ALWAYS_STRONG = {"SUP", "SUB", "VFRAC", "GREEK", "BIG", "ROOT", "PARTIAL", "ATOM", "PREFIX", "VEC", "HAT", "CARET", "USCORE", "FUNC"}
COMBINING_HAT = "\u0302"


def _standalone_hat(text: str, i: int) -> bool:
    """î, ĵ (one precomposed character with a circumflex) standing alone - not inside a word such as Hôpital."""
    c = text[i]
    if c.isascii():
        return False
    d = unicodedata.normalize("NFD", c)
    return (len(d) == 2 and d[1] == COMBINING_HAT and d[0].isascii() and d[0].isalpha()
            and not (i > 0 and text[i - 1].isalpha()) and not (i + 1 < len(text) and text[i + 1].isalpha()))


def _split_word(t: Tok, bracket_next: bool = False) -> list[Tok]:
    """Give a WORD its meaning: function, unit, prose, a single variable, geometry points (ABC) or a product of variables (ab).
    `bracket_next`: an opening bracket is glued after the word."""
    w = t.value
    if w in FUNCTIONS and (bracket_next or w not in BRACKET_FUNCTIONS):
        return [Tok("FUNC", w, t.start, t.end, t.space, t.raw)]
    for name in sorted(FUNCTIONS, key=len, reverse=True):               # sinx, cosx, logx: a function glued to x, y or z
        if len(w) == len(name) + 1 and w.startswith(name) and w[-1] in "xyz":
            return [Tok("FUNC", name, t.start, t.end - 1, t.space, name), Tok("VAR", w[-1], t.end - 1, t.end, False, w[-1])]
    if w in UNITS:
        return [Tok("UNIT", w, t.start, t.end, t.space, w)]
    if len(w) == 1:
        return [Tok("VAR", w, t.start, t.end, t.space, w)]
    if w.isupper() and len(w) <= 4:
        return [Tok("POINTS", w, t.start, t.end, t.space, w)]
    if re.fullmatch(r"[a-z][A-Z]{1,2}", w) and w not in UNITS | CONTEXT_UNITS:  # qB, kT: a product (mA, pH, eV are units)
        return [Tok("VAR", ch, t.start + k, t.start + k + 1, t.space and k == 0, ch) for k, ch in enumerate(w)]
    if w.islower() and len(w) == 2 and w not in PROSE_2:                  # ab, xy, dx: one variable per letter
        return [Tok("VAR", ch, t.start + k, t.start + k + 1, t.space and k == 0, ch) for k, ch in enumerate(w)]
    return [Tok("PROSE", w, t.start, t.end, t.space, w)]


ORDINAL_SUFFIXES = {"st", "nd", "rd", "th", "ths"}


def classify(toks: list[Tok]) -> list[Tok]:
    """Resolve WORD tokens; mark labels such as "(a)" / "(ii)" as prose."""
    out: list[Tok] = []
    k = 0
    while k < len(toks):
        t = toks[k]
        after_label = toks[k + 3] if k + 3 < len(toks) else None
        if (t.kind == "OPEN" and t.value == "(" and k + 2 < len(toks) and toks[k + 2].kind == "CLOSE" and toks[k + 2].value == ")"
                and (not out or out[-1].kind in ("PROSE", "OTHER"))
                and not (after_label is not None and not after_label.space                      # (4)(5) + ...: a product, not a label
                         and after_label.kind in ("OPEN", "ADD", "MUL", "SLASH", "REL", "SUP", "SUB", "CARET", "USCORE"))
                and ((toks[k + 1].kind == "WORD" and toks[k + 1].value.lower() in ROMAN | set("abcdefgh")) or toks[k + 1].kind == "NUM")):
            out.extend(Tok("PROSE", x.raw, x.start, x.end, x.space, x.raw) for x in toks[k:k + 3])
            k += 3
            continue
        if (t.kind == "WORD" and t.value in ("A", "a") and k + 1 < len(toks) and toks[k + 1].kind == "NUM" and toks[k + 1].space
                and (not out or out[-1].kind == "PROSE" or (out[-1].kind == "OTHER" and out[-1].raw in END_PUNCT))):
            out.append(Tok("PROSE", t.value, t.start, t.end, t.space, t.raw))     # "A 100 Ω resistor": an article, not a variable
            k += 1
            continue
        if (t.kind == "WORD" and t.value.lower() in ORDINAL_SUFFIXES and out and out[-1].kind == "NUM" and not t.space
                and (k + 1 >= len(toks) or toks[k + 1].space or toks[k + 1].kind == "OTHER" and toks[k + 1].raw in END_PUNCT)
                and not (len(out) >= 3 and out[-2].kind == "SLASH" and out[-3].kind != "NUM")):            # x/4th stays a candidate
            # 3/4th of the class, the 5th question: ordinal prose, never a formula (3/4 times t·h)
            j = len(out) - 1
            if j >= 2 and out[j - 1].kind == "SLASH" and out[j - 2].kind == "NUM" and not out[j].space and not out[j - 1].space:
                j -= 2
            out[j:] = [Tok("PROSE", x.raw, x.start, x.end, x.space, x.raw) for x in out[j:]]
            out.append(Tok("PROSE", t.value, t.start, t.end, t.space, t.raw))
            k += 1
            continue
        nxt = toks[k + 1] if k + 1 < len(toks) else None
        glued_to_number = len(out) >= 2 and out[-1].kind == "NUM" and not t.space and out[-2].kind in ("ADD", "MUL", "REL")
        if (t.kind == "WORD" and t.value in PROSE_2 and len(out) >= 2 and (out[-1].kind in ("ADD", "MUL", "REL")
                and out[-2].kind in OPERAND_END or glued_to_number)
                and (nxt is None or nxt.kind in ("ADD", "MUL", "REL", "CLOSE", "SUP", "SUB", "SLASH", "COMMA", "SEMI")
                     or (nxt.kind == "OTHER" and nxt.raw in END_PUNCT)
                     or glued_to_number and nxt.space)):                # + 2as with u = 0: glued to a number, never the word "as"
            # v = u + at, v = at.: after an operator between operands and before the formula ends, "at" is a product of variables;
            # "5 at noon", "x + at most 3" (a word follows) stay prose
            out.extend(Tok("VAR", ch, t.start + j, t.start + j + 1, t.space and j == 0, ch) for j, ch in enumerate(t.value))
            k += 1
            continue
        nxt = toks[k + 1] if k + 1 < len(toks) else None
        out.extend(_split_word(t, nxt is not None and nxt.kind == "OPEN" and not nxt.space) if t.kind == "WORD" else [t])
        k += 1
    _differentials(out)
    _scripted_letter_groups(out)
    _glued_letter_groups(out)
    for k, t in enumerate(out):                                          # 2.4 MJ, 3 nC: prefixed units only right after a number
        if t.kind in ("POINTS", "PROSE") and t.raw in CONTEXT_UNITS and k and out[k - 1].kind == "NUM" and t.space:
            out[k] = Tok("UNIT", t.raw, t.start, t.end, t.space, t.raw)
    for k, t in enumerate(out):                                          # 5 min: the minute, not the minimum function
        if (t.kind == "FUNC" and t.value == "min" and k and out[k - 1].kind == "NUM" and t.space
                and not (k + 1 < len(out) and out[k + 1].kind == "OPEN" and not out[k + 1].space)):
            out[k] = Tok("UNIT", "min", t.start, t.end, t.space, t.raw)
    for k, t in enumerate(out):                                          # a function name not followed by an argument is a word
        if t.kind == "FUNC":
            nxt = out[k + 1] if k + 1 < len(out) else None
            if nxt is None or nxt.kind not in OPERAND_START | {"CARET", "USCORE", "ADD"}:
                t.kind = "PROSE"
    return out


# Common three-letter English words: never read as a product of variables even when glued to '=' ("mass=..." is never split either).
ENGLISH_3 = set("""the and for are but not you all any can had her was one our out day get has him his how man new now old see two way
who boy did its let put say she too use yes yet off own few far why try ask end men set top red big low per via max min sum net avg tot
ans sol eqn lhs rhs""".split())
def _scripted_letter_groups(out: list[Tok]) -> None:
    """½at², Kq₁q₂/d, Gm₁m₂: a 2-3 letter group glued to a superscript/subscript on its right is a product of variables when it is
    also glued to mathematics on its left (½at²) or is a capital+small group (Kq₁, Gm₁). English words (the¹ footnotes) never."""
    k = 0
    while k < len(out):
        t = out[k]
        nxt = out[k + 1] if k + 1 < len(out) else None
        prev = out[k - 1] if k else None
        if (t.kind == "PROSE" and t.raw.isascii() and t.raw.isalpha() and 2 <= len(t.raw) <= 3 and t.raw.lower() not in ENGLISH_3
                and t.raw not in FUNCTIONS and nxt is not None and not nxt.space and nxt.kind in ("SUP", "SUB")):
            left_glued = prev is not None and not t.space and prev.kind in ("VFRAC", "NUM", "VAR", "GREEK", "SUP", "SUB", "CLOSE")
            capital_small = t.raw[0].isupper() and t.raw[1:].islower()
            if left_glued or capital_small:
                out[k:k + 1] = [Tok("VAR", ch, t.start + n, t.start + n + 1, t.space and n == 0, ch) for n, ch in enumerate(t.raw)]
                k += len(t.raw) - 1
        k += 1


def _differentials(out: list[Tok]) -> None:
    """∮ E · dA, ∭ ρ dV: after an integral sign and before any word, "d" + one capital letter is a differential, not a word."""
    integral = False
    k = 0
    while k < len(out):
        t = out[k]
        if t.kind == "BIG" and t.value in (r"\int", r"\iint", r"\iiint", r"\oint"):
            integral = True
        elif t.kind == "PROSE" and integral and len(t.raw) == 2 and t.raw[0] == "d" and t.raw[1].isupper() and t.raw.isascii():
            out[k:k + 1] = [Tok("VAR", "d", t.start, t.start + 1, t.space, "d"), Tok("VAR", t.raw[1], t.start + 1, t.end, False, t.raw[1])]
            k += 1
        elif t.kind in ("PROSE", "OTHER"):
            integral = False
        k += 1


GLUE_EVIDENCE = {"VAR", "NUM", "GREEK", "SUP", "SUB", "ADD", "MUL", "SLASH", "BAR", "VFRAC", "ATOM", "ROOT"}


def _glued_letter_groups(out: list[Tok]) -> None:
    """v=u+at, PV=nRT, Q=It, ΔU=mgh, F=|q|vB: inside a run typed WITHOUT spaces around '=', short letter groups are products of
    variables, not words. Only when the run also has a single-letter variable, a number, an operator or a camel-case group (nRT),
    so that "Ans=Yes" stays prose. Groups longer than 4 letters, function names and units are never split."""
    k = 0
    while k < len(out):
        j = k + 1
        while j < len(out) and not out[j].space:
            j += 1
        run = out[k:j]                                                   # tokens glued together (no whitespace)
        if any(t.kind == "REL" and t.raw == "=" for t in run):
            def letters(w: str) -> bool:
                is_camel = any(a.islower() and b.isupper() for a, b in zip(w, w[1:], strict=False))
                return w.isascii() and w.isalpha() and (len(w) <= 4 if is_camel else len(w) <= 3 and w.lower() not in ENGLISH_3)

            words = [t for t in run if t.kind == "PROSE" and letters(t.raw)]
        else:
            # nRT/V, qV×B: a camel-case group (a capital after a small letter, never an English word) glued to '/', '×' or '·'
            # √(GMr), √(abc): a short letter group under a root sign glued to it is a product of variables (no English word is)
            root = any(x.kind == "ROOT" for x in run)
            words = [t for n, t in enumerate(run) if t.kind == "PROSE" and t.raw.isascii() and t.raw.isalpha() and len(t.raw) <= 4
                     and (any(a.islower() and b.isupper() for a, b in zip(t.raw, t.raw[1:], strict=False))
                          and any(x.kind in ("SLASH", "MUL") for x in run[max(0, n - 1):n + 2])
                          or root and (len(t.raw) <= 3 or not t.raw.islower()) and t.raw.lower() not in ENGLISH_3
                          and t.raw not in FUNCTIONS and t.raw not in UNITS)]
        if words:
            camel = any(any(a.islower() and b.isupper() for a, b in zip(w.raw, w.raw[1:], strict=False)) for w in words)
            evidence = camel or any(t.kind in GLUE_EVIDENCE or t.kind == "POINTS" and len(t.raw) == 1 for t in run)
            prose = [t for n, t in enumerate(run) if t.kind in ("PROSE", "OTHER") and t not in words and not _degree_label(run, n)]
            follows = out[j] if j < len(out) else None
            sentence_goes_on = (follows is not None and follows.kind == "PROSE" and not (run[-1].kind in ("OTHER", "COMMA", "SEMI") and run[-1].raw in ".,;:?!")
                                and not (follows.raw[:1].isupper()))         # "x=at most 5": 'at' is a word of the sentence
            if words and evidence and not sentence_goes_on and not any(t.raw not in ".,;:?!%" for t in prose):
                for w in words:
                    i = out.index(w)
                    out[i:i + 1] = [Tok("VAR", ch, w.start + n, w.start + n + 1, w.space and n == 0, ch) for n, ch in enumerate(w.raw)]
                    j += len(w.raw) - 1
        k = j


def _degree_label(run: list[Tok], n: int) -> bool:
    """E°cell: the label glued after a degree sign is part of the symbol (see refine), not a word of the sentence."""
    t = run[n]
    return (n > 0 and run[n - 1].kind == "POSTFIX" and run[n - 1].raw == "°" and not t.space and t.raw.isascii() and t.raw.isalpha()
            and t.raw.islower() and len(t.raw) <= 8)


def _row_list_matrix(toks: list[Tok], k: int) -> bool:
    """[[1,0],[0,2]], [[1,2,3]], [[1],[2]]: '[[' glued, whose matching bracket holds at least one comma. Its shape (rows of equal
    length, brackets closed) is checked by the parser; [x], [a, b] and [[x]] are not this notation."""
    if toks[k].raw != "[" or k + 1 >= len(toks) or toks[k + 1].raw != "[" or toks[k + 1].space:
        return False
    depth = 0
    for j in range(k, len(toks)):
        depth += {"OPEN": 1, "CLOSE": -1}.get(toks[j].kind, 0)
        if depth == 0:
            return any(x.kind == "COMMA" for x in toks[k:j])
    return any(x.kind == "COMMA" for x in toks[k:])                     # never closed: the parser refuses it, so it is flagged


def _roles(toks: list[Tok]) -> None:
    depth, bars = 0, 0
    for k, t in enumerate(toks):
        prev = toks[k - 1] if k else None
        nxt = toks[k + 1] if k + 1 < len(toks) else None
        kind = t.kind
        if kind == "OPEN":
            depth += 1
        elif kind == "CLOSE":
            depth = max(0, depth - 1)
        elif kind == "BAR":
            bars += 1
        if kind in ("PROSE", "OTHER"):
            t.role = "B"
        elif kind == "COMMA":
            t.role = "B" if depth == 0 and bars % 2 == 0 else "W"
        elif kind == "SEMI":
            t.role = "S" if depth > 0 or bars % 2 else "B"
        elif kind == "OPEN" and _row_list_matrix(toks, k):
            t.role = "S"                                                 # [[1,0],[0,2]]: rows in brackets, only ever a matrix
        elif kind in ALWAYS_STRONG or t.raw in "⌊⌋⌈⌉":
            t.role = "S"                                                 # floor/ceiling brackets: only ever mathematics
        elif kind == "ADD" and t.raw in "∪∩":
            t.role = "S"                                                 # set operators never occur in prose
        elif kind == "MUL":
            t.role = "W" if t.raw == "*" else "S"
        elif kind == "POSTFIX":
            t.role = "S" if t.raw in "′″'" else "W"
        elif kind == "REL":
            if t.raw in ("=", "<", ">"):
                t.role = "S" if (prev is not None and prev.kind in OPERAND_END and nxt is not None and nxt.kind in OPERAND_START | {"ADD"}) else "W"
            else:
                t.role = "S"
        elif kind == "SLASH":
            before2 = toks[k - 2] if k >= 2 else None
            after2 = toks[k + 2] if k + 2 < len(toks) else None
            num_over_num = (prev is not None and nxt is not None and prev.kind == "NUM" and nxt.kind == "NUM"
                            and not (before2 is not None and before2.kind == "SLASH") and not (after2 is not None and after2.kind == "SLASH"))
            parens = (prev is not None and prev.kind == "CLOSE") or (nxt is not None and nxt.kind == "OPEN")
            derivative = nxt is not None and (nxt.kind == "PARTIAL" or (nxt.kind == "VAR" and nxt.value == "d"))
            t.role = "S" if (num_over_num or parens or derivative) else "W"
        else:
            t.role = "W"


END_PUNCT = set(".,;:?!")


UNIT_SYMBOLS = set("msgJNKMAVWCTLh")


def _has_quantity(run: list[Tok]) -> bool:
    """A number followed (after a space) by a unit: 8 J, 500 nm, 3 m s⁻¹."""
    for x, y in zip(run, run[1:], strict=False):
        if x.kind == "NUM" and y.space and (y.kind == "UNIT" or (y.kind == "VAR" and y.value in UNIT_SYMBOLS)):
            return True
    return False


def _unequal_numbers(run: list[Tok]) -> bool:
    """"to 2 = 8": a number equal to a different plain number is never an equation."""
    return (len(run) == 3 and run[2].kind == "NUM" and run[0].kind == "NUM" and float(run[0].value) != float(run[2].value))


HYPHEN_DIGIT = ("-", "−")
DIMENSION_LETTERS = set("MLTINJ")


DETECTOR_REASONS = {
    "AMBIGUOUS_CHARACTER": "A digit is typed against a letter that looks like it (0 and O, 1 and l or I, as text read from a photo or a "
                           "PDF often has): check which character is meant.",
    "UNIT_DIGIT_POWER": "A digit is typed straight after a unit (like 6 m2): it could be a power (m²) or a separate number; write "
                        "the power as a superscript (m²) or put a space before the number.",
    "AMBIGUOUS_DENOMINATOR": "It is unclear how much of the text after '/' is in the denominator (like x/2y); add brackets.",
    "INLINE_EXPONENT": "A power seems to be typed with a hyphen after a unit (like 6 ms-1): it could be a power or a minus sign; "
                       "write the power as a superscript (m s⁻¹).",
    "FLATTENED_POWER": "'e' written against a letter or a number (like ex or e13x) is either e to that power - a superscript lost when the equation was "
                       "copied as plain text - or e times the letter: choose the reading meant.",
    "FLATTENED_BRACKET_POWER": "A number after a bracketed expression (like (x + 4) 2) is either its power - a superscript lost when the "
                               "equation was copied as plain text - or a product: choose the reading meant.",
    "AMBIGUOUS_CHEMICAL_CHARGE": "A charge typed as a digit and a sign inside a concentration bracket (like [Ca2+]) could be a charge or "
                                 "a count; type the charge as a superscript ([Ca²⁺]).",
    "DIMENSION_NOTATION": "This looks like a dimensional formula written without brackets or superscripts (like MLT-2); write it as "
                          "[M L T⁻²].",
    "FLATTENED_FUNCTION_POWER": "A number is typed straight after a function name (like sin3 x or log2x): it could be a power of the "
                                "function (sin³x), a base (log₂x) or part of the angle (sin 3x) - a superscript or subscript lost when "
                                "the equation was copied as plain text: choose the reading meant.",
    "AMBIGUOUS_NUMBER_ADJACENT": "A number is written right after a letter (like x2): it could be a power (x²), a small index below "
                                 "(x₂) or a multiplication: choose the reading meant.",
}

# Functions whose name may carry a power or a base: sin²x, log₂x. A digit glued to the name is never silently read as the argument.
_POWERABLE = {"sin", "cos", "tan", "cot", "sec", "csc", "cosec", "sinh", "cosh", "tanh", "coth", "sech", "csch", "log", "ln", "lg",
              "arcsin", "arccos", "arctan"}


_FORMULA_KINDS = {"VAR", "NUM", "POINTS", "GREEK", "SLASH", "ADD", "MUL", "OPEN", "CLOSE", "SUP", "SUB", "ROOT", "VFRAC", "ATOM", "BAR"}


def _is_whole_formula(toks: list[Tok]) -> bool:
    """An answer option that is exactly one formula: letters, numbers and operators only (h/p, qB/v, 2GM/R, F/4, 1/r, 2n), with an
    operator or a number times a letter. Never an English word, a unit, or capitals without a number (N/A, A/B: "not applicable",
    "A or B") - those stay text."""
    if len(toks) < 2 or any(t.kind not in _FORMULA_KINDS for t in toks):
        return False
    letters = [t for t in toks if t.kind in ("VAR", "POINTS")]
    if not any(t.kind == "NUM" for t in toks) and letters and all(str(t.value).isupper() for t in letters):
        return False
    return any(t.kind in ("SLASH", "ADD", "MUL", "SUP", "SUB", "ROOT") for t in toks) or any(
        a.kind == "NUM" and b.kind in ("VAR", "POINTS", "GREEK") and not b.space for a, b in zip(toks, toks[1:]))


def _is_whole_quantity(toks: list[Tok]) -> bool:
    """The whole text is one quantity: an optional sign, a number (or n × 10ᵏ), then spaced unit symbols with their powers."""
    k = 0
    if k < len(toks) and toks[k].kind == "ADD":
        k += 1
    if k >= len(toks) or toks[k].kind != "NUM":
        return False
    k += 1
    if k + 2 < len(toks) and toks[k].kind == "MUL" and toks[k + 1].raw == "10" and toks[k + 2].kind == "SUP":
        k += 3
    units = 0
    while k < len(toks):
        x = toks[k]
        if (_is_unitish(x) or (x.kind == "GREEK" and x.raw == "Ω")) and x.space:
            units += 1
        elif x.kind in ("SUP", "SUB") and not x.space and units:
            pass
        elif x.kind == "SLASH" and units:
            pass
        elif _is_unitish(x) and not x.space and units and toks[k - 1].kind == "SLASH":
            pass
        else:
            return False
        k += 1
    return units > 0


def _is_unitish(t: Tok) -> bool:
    return t.kind == "UNIT" or (t.kind == "VAR" and t.value in UNIT_SYMBOLS)


def _detectors(toks: list[Tok]) -> None:
    """Typings with no superscript at all that are still notation and must be reviewed, never converted or passed over:
    6 ms-1, 4 m s-2, 9 kg m-3 (a power typed with a hyphen after unit letters: m s⁻¹? m minus 1?) and MLT-2, ML2T-2 (a
    dimensional formula without brackets or superscripts). Ranges and labels (10-12, 2019-20, XI-A, NH-48) do not match."""
    n = len(toks)
    for i, t in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < n else None
        if nxt is not None and not nxt.space and (
                t.kind == "NUM" and t.raw[-1] in "01" and nxt.kind in ("VAR", "POINTS") and nxt.raw[0] in "OoIl"
                or t.kind in ("VAR", "POINTS") and t.raw[-1] in "OoIl" and nxt.kind == "NUM" and nxt.raw[0] in "01"):
            for x in (t, nxt):
                x.role, x.flag = "S", "AMBIGUOUS_CHARACTER"             # 0O, 1l, l0: look-alike characters (OCR), never a product
        if t.kind == "NUM" and i + 1 < n and toks[i + 1].space and _is_unitish(toks[i + 1]):
            j = i + 1
            while j < n and _is_unitish(toks[j]):
                j += 1
            if (j + 1 < n and toks[j].kind == "ADD" and toks[j].raw in HYPHEN_DIGIT and not toks[j].space and toks[j + 1].kind == "NUM"
                    and not toks[j + 1].space and (j + 2 >= n or toks[j + 2].space or toks[j + 2].raw in ".,;:)")):
                for x in toks[i:j + 2]:
                    x.role, x.flag = "S", "INLINE_EXPONENT"
        if (t.kind == "SLASH" and 0 < i and i + 2 < n and toks[i + 1].kind == "NUM" and not toks[i + 1].space
                and toks[i + 2].kind in ("VAR", "PROSE", "GREEK") and not toks[i + 2].space and not constant_group(toks[i + 1:])):
            for x in toks[max(0, i - 1):i + 3]:
                x.role, x.flag = "S", "AMBIGUOUS_DENOMINATOR"           # x/2y: is y in the denominator?
        if (t.kind == "NUM" and i + 2 < n and toks[i + 1].space and _is_unitish(toks[i + 1]) and toks[i + 2].kind == "NUM"
                and not toks[i + 2].space and len(toks[i + 2].raw) == 1 and (i + 3 >= n or toks[i + 3].space)):
            for x in toks[i:i + 3]:
                x.role, x.flag = "S", "UNIT_DIGIT_POWER"                # 6 m2: is it m²?
        # copied from a rendered equation, a superscript arrives flattened onto the line: eˣ as "ex", (x + 4)² as "(x + 4) 2".
        # e (Euler's number) glued to one letter, or a number after a bracketed expression, is a power or a product - never guessed
        prev = toks[i - 1] if i else None
        after = toks[i + 2] if i + 2 < n else None
        if (t.kind == "VAR" and t.value == "e" and nxt is not None and not nxt.space and nxt.kind in ("VAR", "GREEK") and len(nxt.raw) == 1
                and (prev is None or t.space or prev.kind in ("NUM", "ADD", "MUL", "SLASH", "OPEN", "REL"))
                and (after is None or after.space or after.raw in END_PUNCT
                     or after.kind in ("SLASH", "ADD", "MUL", "OPEN", "CLOSE", "REL", "COMMA"))
                and not (after is not None and after.kind == "ADD" and i + 3 < n and toks[i + 3].kind == "PROSE")):   # ex-officio
            for x in (t, nxt):
                x.role, x.flag = "S", "FLATTENED_POWER"
        # e13x, e2x: e, a number and one letter glued together - e¹³x or e¹³ˣ copied without its superscript (e12 alone is a label)
        third = toks[i + 3] if i + 3 < n else None
        if (t.kind == "VAR" and t.value == "e" and nxt is not None and not nxt.space and nxt.kind == "NUM" and nxt.raw.isdigit()
                and after is not None and not after.space and after.kind in ("VAR", "GREEK") and len(after.raw) == 1
                and (prev is None or t.space or prev.kind in ("ADD", "MUL", "SLASH", "OPEN", "REL"))
                and (third is None or third.space or third.raw in END_PUNCT
                     or third.kind in ("SLASH", "ADD", "MUL", "OPEN", "CLOSE", "REL", "COMMA"))):
            for x in (t, nxt, after):
                x.role, x.flag = "S", "FLATTENED_POWER"
        after = toks[i + 2] if i + 2 < n else None
        # sin3 x, cos2x, log2x: a number glued to a function name is a flattened power (sin³x), a flattened base (log₂x) or the
        # start of the argument (sin 3x). The copy lost the formatting that decided it, so it is never decided here.
        # Only when an argument follows the number: ln2/k and sin30° have nothing after it, so the number is the argument.
        if (t.kind == "FUNC" and t.value in _POWERABLE and nxt is not None and not nxt.space and nxt.kind == "NUM"
                and nxt.raw.isdigit() and after is not None and after.kind in ("VAR", "GREEK", "OPEN", "POINTS", "FUNC")):
            for x in (t, nxt):
                x.role, x.flag = "S", "FLATTENED_FUNCTION_POWER"
        # x2 + 2x + 1, v2 = u2 + 2as: a lowercase letter with a number glued after it, next to an operator with an operand on its
        # other side, is mathematics with a flattened power or index (x², v₂ ...). Labels (Q2, B12) and prose never match.
        if (t.kind == "VAR" and len(t.raw) == 1 and t.raw.isascii() and t.raw.islower() and nxt is not None and not nxt.space
                and nxt.kind == "NUM" and nxt.raw.isdigit() and _beside_operator(toks, i, i + 1)):
            for x in (t, nxt):
                x.role = "S"
                x.flag = x.flag or "AMBIGUOUS_NUMBER_ADJACENT"
        if t.kind == "CLOSE" and nxt is not None and nxt.kind == "NUM" and (i + 2 >= n or toks[i + 2].space or toks[i + 2].raw in ".,;:)") \
                and _expression_bracket(toks, i):
            for x in (t, nxt):
                x.role, x.flag = "S", "FLATTENED_BRACKET_POWER"
        if t.kind in ("POINTS", "VAR") and (i == 0 or toks[i].space) and t.raw.isascii() and set(t.raw) <= DIMENSION_LETTERS:
            j, letters, hyphen = i, set(), False
            while j < n and (j == i or not toks[j].space):
                x = toks[j]
                if x.kind in ("POINTS", "VAR") and x.raw.isascii() and set(x.raw) <= DIMENSION_LETTERS:
                    letters |= set(x.raw)
                elif x.kind == "NUM":
                    pass
                elif x.kind == "ADD" and x.raw in HYPHEN_DIGIT and j + 1 < n and toks[j + 1].kind == "NUM" and not toks[j + 1].space:
                    hyphen = True
                else:
                    break
                j += 1
            ends = j >= n or toks[j].space or toks[j].raw in ".,;:)"
            if hyphen and len(letters) >= 2 and ends:
                for x in toks[i:j]:
                    x.role, x.flag = "S", "DIMENSION_NOTATION"


def _beside_operator(toks: list[Tok], first: int, last: int) -> bool:
    """True when toks[first..last] has a +, −, = or relation right before or after it whose other side is an operand
    (a number or a letter): `x2 + 2x`, `= u2`, never `Q2 of the paper`."""
    def operand(k: int) -> bool:
        return 0 <= k < len(toks) and toks[k].kind in ("NUM", "VAR", "GREEK", "OPEN", "POINTS")
    after, before = last + 1, first - 1
    if after < len(toks) and toks[after].kind in ("ADD", "REL") and operand(after + 1):
        return True
    return before >= 0 and toks[before].kind in ("ADD", "REL") and operand(before - 1)


VACUUM_CONSTANTS = {r"\varepsilon", r"\epsilon", r"\mu"}


def constant_group(toks: list[Tok]) -> bool:
    """h/2π, 1/4πε₀, μ₀/4π: a number glued to π, then optionally the vacuum constants ε₀ / μ₀, and nothing else glued after it, is one
    constant factor (2π, 4πε₀) - never "a half times π". A number before a variable (1/2x, 1/2πr, 1/2mv²) is not."""
    if len(toks) < 2 or toks[0].kind != "NUM" or not (toks[1].kind == "GREEK" and toks[1].value == r"\pi" and not toks[1].space):
        return False
    k = 2
    while (k + 1 < len(toks) and toks[k].kind == "GREEK" and toks[k].value in VACUUM_CONSTANTS and not toks[k].space
           and toks[k + 1].kind == "SUB" and toks[k + 1].value == "0" and not toks[k + 1].space):
        k += 2
    nxt = toks[k] if k < len(toks) else None
    return nxt is None or nxt.space or nxt.kind not in OPERAND_START | {"SUP", "SUB", "CARET", "USCORE"}


def _expression_bracket(toks: list[Tok], close: int) -> bool:
    """The bracket ending at `close` holds an expression with an operator ((x + 4), (2a − b)), not a label like (b) or (ii)."""
    depth = 0
    for j in range(close, -1, -1):
        depth += {"CLOSE": 1, "OPEN": -1}.get(toks[j].kind, 0)
        if depth == 0:
            return any(x.kind in ("ADD", "MUL", "SLASH") for x in toks[j + 1:close])
    return False


def _cut_at_open_remark(toks: list[Tok], a: int, b: int) -> int:
    """'g(x) = 2x + 1 (x real)': a spaced opening bracket that is never closed inside the run starts a remark in prose."""
    stack = []
    for j in range(a, b):
        if toks[j].kind == "OPEN":
            stack.append(j)
        elif toks[j].kind == "CLOSE" and stack:
            stack.pop()
    for j in reversed(stack):
        if toks[j].space and j > a and toks[j].role != "S":         # an unclosed '[[' matrix is refused by the parser, not cut off
            return j
    return b


LABEL_WORDS = {"cell", "red", "ox", "rxn", "sys", "surr"}


def refine(toks: list[Tok], chemistry: bool = False, science: bool = False) -> list[Tok]:
    """Subject notation on classified tokens (used for the field and for its ^-written twin alike):
      [NH₃], [H⁺], [Al³⁺], [SO4^2−] in a Chemistry paper: the species inside a concentration bracket is ONE upright atom, so a power
        after the bracket ([OH⁻]³) is a power, never a charge; [Ca2+] (charge or count?) is flagged;
      E°cell, E°red, Ecell: a lowercase label glued after a degree sign (or one of the electrochemical labels after a capital) is a
        subscript label, set upright;
      t½, T½ in Physics and Chemistry: a ½ glued to a letter is the half-life subscript, never "t times a half"."""
    out: list[Tok] = []
    k = 0
    while k < len(toks):
        t = toks[k]
        if chemistry and t.kind == "OPEN" and t.value == "[":
            j = k + 1
            while j < len(toks) and toks[j].kind not in ("OPEN", "CLOSE") and not toks[j].space:
                j += 1
            if j < len(toks) and j > k + 1 and toks[j].kind == "CLOSE" and toks[j].value == "]" and not toks[j].space:
                content = "".join(x.raw for x in toks[k + 1:j])
                try:
                    latex = bracket_species(content)
                except _Flag:
                    latex = None
                    for x in toks[k:j + 1]:
                        x.role, x.flag = "S", "AMBIGUOUS_CHEMICAL_CHARGE"
                if latex is not None:
                    out += [t, Tok("ATOM", latex, toks[k + 1].start, toks[j - 1].end, False, content), toks[j]]
                    k = j + 1
                    continue
        if (t.kind == "PROSE" and not t.space and out and out[-1].kind == "POSTFIX" and out[-1].raw == "°" and not out[-1].space
                and t.raw.isascii() and t.raw.isalpha() and t.raw.islower() and len(t.raw) <= 8):
            out.append(Tok("SUB", t.raw, t.start, t.end, False, t.raw))          # E°cell: the cell label
            k += 1
            continue
        nxt = toks[k + 1] if k + 1 < len(toks) else None
        if t.kind == "UNIT" and t.raw == "pH" and nxt is not None and nxt.kind == "SUB" and not nxt.space:
            # pH₂ (partial pressure of H₂ in Kp): a subscript count after "pH" means p times H₂ - never the pH label
            out += [Tok("VAR", "p", t.start, t.start + 1, t.space, "p"), Tok("VAR", "H", t.start + 1, t.end, False, "H")]
            k += 1
            continue
        if t.kind == "PROSE" and len(t.raw) > 1 and t.raw[0].isupper() and t.raw[1:] in LABEL_WORDS:
            out += [Tok("VAR", t.raw[0], t.start, t.start + 1, t.space, t.raw[0]), Tok("SUB", t.raw[1:], t.start + 1, t.end, False, t.raw[1:])]
            k += 1
            continue
        if (science and t.kind == "VFRAC" and not t.space and out and out[-1].kind == "VAR" and len(out[-1].raw) == 1
                and (k + 1 >= len(toks) or toks[k + 1].space or toks[k + 1].kind not in ("VAR", "NUM", "POINTS"))):
            out.append(Tok("SUB", f"{t.value[0]}/{t.value[1]}", t.start, t.end, False, t.raw))   # t½: the half-life
            k += 1
            continue
        out.append(t)
        k += 1
    return out


def find_spans(text: str, offset: int = 0, whole_quantity: bool = False,
               whole_formula: bool = False, chemistry: bool = False,
               science: bool = False) -> list[tuple[int, int, list[Tok], str | None]]:
    """Formula candidates in ordinary text: (start, end, tokens, problem) with absolute positions. `problem` is set when the
    candidate is joined to surrounding text (3.33:6.7:3.33 = 1:2:1) and must not be converted on its own."""
    toks = refine(classify(lex(text, offset)), chemistry, science)
    flagged = [(x, x.flag) for x in toks if x.flag]
    _roles(toks)
    for x, f in flagged:
        x.role, x.flag = "S", f                          # [Ca2+]: keep the doubt through the role pass
    _detectors(toks)
    if (whole_quantity and _is_whole_quantity(toks)) or (whole_formula and _is_whole_formula(toks)):
        for x in toks:
            x.role = "S"                                 # an option cell that is exactly "7 J" or "4.5 eV", or exactly "h/p", "2n"
    spans = []
    k = 0
    while k < len(toks):
        if toks[k].role == "B":
            k += 1
            continue
        a = k
        while k < len(toks) and toks[k].role != "B":
            k += 1
        b = k                                                            # run = toks[a:b]
        glued_lead = False
        while a < b and toks[a].kind in ("REL", "MUL", "SLASH", "COMMA", "SEMI"):   # "= Nₐ": the relation belongs to the prose
            # "nRT/V": an operator glued to the word before it cuts a formula in two; never convert the half after it alone
            glued_lead = glued_lead or (toks[a].kind in ("MUL", "SLASH") and not toks[a].space and a > 0
                                        and toks[a - 1].kind in ("PROSE", "OTHER") and toks[a - 1].raw not in "([\"'")
            a += 1
        if (b - a >= 3 and a > 0 and toks[a - 1].kind == "PROSE" and toks[a].kind == "NUM" and toks[a].space
                and toks[a + 1].kind == "REL" and toks[a + 1].raw == "=" and (_has_quantity(toks[a + 2:b]) or _unequal_numbers(toks[a:b]))):
            a += 2      # "from 0 to 2 = 8 J": the number ends the sentence's phrase; "2 = 8 J" is not an equation
        b = _cut_at_open_remark(toks, a, b)
        dangling = False                                                 # v=u+at: the formula runs on into a word
        while b > a and toks[b - 1].kind in ("REL", "ADD", "MUL", "SLASH", "CARET", "USCORE", "COMMA", "SEMI"):
            if toks[b - 1].kind not in ("COMMA", "SEMI") and not toks[b - 1].space and b < len(toks) and not toks[b].space:
                dangling = True
            b -= 1
        after = toks[b] if b < len(toks) else None
        if (b - a >= 2 and toks[b - 1].kind == "VAR" and toks[b - 1].value in ("a", "A") and toks[b - 1].space and toks[b - 2].kind in OPERAND_END
                and after is not None and after.kind == "PROSE"):          # "O₂ a bond": an article, not a variable
            b -= 1
        while b > a and toks[b - 1].kind == "CLOSE" and sum(x.kind == "CLOSE" for x in toks[a:b]) > sum(x.kind == "OPEN" for x in toks[a:b]):
            b -= 1                                                       # "(molar mass 18 g mol⁻¹)": the bracket belongs to the prose
        # (an unclosed '[[' matrix opener is kept: the parser refuses it, so it is flagged rather than left as silent text)
        while a < b and toks[a].kind == "OPEN" and toks[a].role != "S" and sum(x.kind == "OPEN" for x in toks[a:b]) > sum(x.kind == "CLOSE" for x in toks[a:b]):
            a += 1
        run = toks[a:b]
        if not run or not any(x.role == "S" for x in run):
            continue
        before = toks[a - 1] if a else None
        after = toks[b] if b < len(toks) else None
        joined = glued_lead or dangling or (before is not None and not run[0].space and before.kind in ("PROSE", "OTHER")
                                            and before.raw not in "([\"'")
        if after is not None and not after.space:
            if after.kind == "PROSE":
                joined = True
            elif after.kind == "OTHER" and after.raw in END_PUNCT:
                nxt = toks[b + 1] if b + 1 < len(toks) else None
                joined = joined or (nxt is not None and not nxt.space)
            elif after.kind == "OTHER" and after.raw not in ")]\"'":
                joined = True
        run[-1].next_word = after is not None and after.space and after.kind == "PROSE"
        flagged = next((x.flag for x in run if x.flag), None)
        problem = (f"{flagged}|" + DETECTOR_REASONS[flagged] if flagged else
                   "The formula is joined to the text around it, so it is not clear where it starts and ends." if joined else None)
        spans.append((run[0].start, run[-1].end, run, problem))
    return spans
