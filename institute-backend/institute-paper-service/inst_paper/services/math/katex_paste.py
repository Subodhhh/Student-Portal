"""Formulas copied from a web page that renders maths with KaTeX (or MathJax with its assistive MathML).

Copying such a formula pastes it THREE times, run together: the MathML text, the LaTeX source, and the visible characters in the
page's order (a fraction's denominator first, invisible U+200B after it), e.g. for x^3+y^3=3xy:

    x3+y3=3xy  x^3+y^3=3xy  x3+y3=3xy        (no spaces in the real paste)

The LaTeX source is the author's own formula, so it can be used as written - but only when the paste proves where it starts and
ends: flattening the candidate source must give exactly the MathML text before it (character for character), and the text after it
must be the same characters in the page's order. Anything that does not prove itself this way is left to the normal engine (which
flags it); nothing is guessed. A formula pasted with only its visible text (no source) cannot be recovered: its order is the page's
layout order, not the formula's.
"""
from __future__ import annotations

import re
from collections import Counter

from inst_paper.services.math.symbols import ADD, ATOMS, BIG, GREEK, MUL, PREFIX, RELATIONS

INVISIBLE = "\u2061\u2062\u2063\u2064\u200b"         # function application, invisible times/separator/plus, zero-width space
_SYMBOL = {cmd[1:]: ch for table in (GREEK, RELATIONS, MUL, ATOMS, BIG, PREFIX) for ch, cmd in table.items() if cmd.startswith("\\")}
_SYMBOL |= {cmd[1:]: ch for ch, cmd in ADD.items() if cmd.startswith("\\")}
_SYMBOL |= {"ne": "≠", "le": "≤", "ge": "≥", "to": "→", "rightarrow": "→", "cdots": "⋯", "ldots": "…", "dots": "…", "prime": "′"}
_FUNCTION = {"sin", "cos", "tan", "cot", "sec", "csc", "log", "ln", "exp", "lim", "max", "min", "sinh", "cosh", "tanh", "arcsin",
             "arccos", "arctan", "det", "gcd", "deg"}
_DROP = {"left", "right", "displaystyle", "textstyle", "big", "Big", "bigg", "Bigg", ",", ";", "!", ":", "quad", "qquad", " ",
         "limits", "nolimits"}
_ONE_ARG_TEXT = {"text", "mathrm", "mathbf", "mathit", "mathsf", "mathcal", "mathbb", "operatorname", "boldsymbol", "vec", "bar",
                 "hat", "overline", "underline", "tilde", "dot", "ddot"}
_FRACTIONS = {"frac", "dfrac", "tfrac", "cfrac", "binom", "dbinom", "tbinom"}


def visible(text: str) -> str:
    """The characters that matter when two renderings are compared: invisible operators and spaces removed, minus signs unified."""
    return "".join("−" if ch == "-" else ch for ch in text if ch not in INVISIBLE and not ch.isspace())


def flatten(latex: str) -> str | None:
    """The MathML text of a LaTeX source (what the first copy holds): symbols as characters, a fraction as numerator then denominator,
    scripts inline, layout commands dropped. None when the source uses something this reader does not know (no recovery then)."""
    out: list[str] = []
    k = 0
    n = len(latex)

    def group(i: int) -> tuple[str | None, int]:
        """A {...} group or a single token starting at i: (its flattened text, the index after it)."""
        while i < n and latex[i] == " ":
            i += 1
        if i >= n:
            return None, i
        if latex[i] == "{":
            depth, j = 0, i
            while j < n:
                depth += {"{": 1, "}": -1}.get(latex[j], 0)
                if depth == 0:
                    inner = flatten(latex[i + 1:j])
                    return inner, j + 1
                j += 1
            return None, n
        if latex[i] == "\\":
            m = re.match(r"\\([A-Za-z]+|.)", latex[i:])
            if m is None:
                return None, n
            inner = flatten(m.group(0))
            return inner, i + len(m.group(0))
        return visible(latex[i]), i + 1

    while k < n:
        ch = latex[k]
        if ch == "\\":
            m = re.match(r"\\([A-Za-z]+|.)", latex[k:])
            if m is None:                                         # a lone backslash at the end
                return None
            name = m.group(1)
            k += len(m.group(0))
            if name in _DROP:
                continue
            if name in _FRACTIONS:
                a, k = group(k)
                b, k = group(k)
                if a is None or b is None:
                    return None
                out.append(a + b)
            elif name == "sqrt":
                if k < n and latex[k] == "[":                     # \sqrt[n]{x}: the index, then the radicand
                    end = latex.find("]", k)
                    if end < 0:
                        return None
                    index = flatten(latex[k + 1:end])
                    k = end + 1
                    if index is None:
                        return None
                    out.append(index)
                a, k = group(k)
                if a is None:
                    return None
                out.append(a)
            elif name in _ONE_ARG_TEXT:
                a, k = group(k)
                if a is None:
                    return None
                out.append(a)
            elif name in _FUNCTION:
                out.append(name)
            elif name in _SYMBOL:
                out.append(_SYMBOL[name])
            elif name in "{}[]()|":
                out.append(name)
            else:
                return None                                       # unknown command: never recover
        elif ch in "^_":
            a, k = group(k + 1)
            if a is None:
                return None
            out.append(a)
        elif ch in "{}":
            k += 1
        else:
            out.append(visible(ch))
            k += 1
    return "".join(out)


def _looks_like_source(candidate: str) -> bool:
    return bool(re.search(r"\\[A-Za-z]|[\^_{]", candidate))


def islands(text: str) -> list[tuple[int, int, str]]:
    """Every proven KaTeX paste in `text`: (start, end, LaTeX source), start/end covering all three copies. Left to right; at each
    position the longest proven source wins."""
    found: list[tuple[int, int, str]] = []
    n = len(text)
    s = 0
    while s < n:
        best = None
        if text[s] not in INVISIBLE and not text[s].isspace():
            for e in range(n, s, -1):                             # longest source first
                source = text[s:e]
                if not _looks_like_source(source) or source[-1].isspace():
                    continue
                flat = flatten(source)
                if not flat or len(flat) < 2:
                    continue
                before = _ending_with(text, s, flat)              # the MathML copy, just before the source
                if before is None:
                    continue
                after = _same_characters_after(text, e, flat)     # the visible copy, just after it
                if after is None:
                    continue
                best = (before, after, source)
                break
        if best:
            found.append(best)
            s = best[1]
        else:
            s += 1
    return found


def _ending_with(text: str, end: int, flat: str) -> int | None:
    """Where the copy that ends at `end` starts, when its visible characters are exactly `flat` (invisible characters may sit inside)."""
    k, j = end, len(flat)
    while j > 0 and k > 0:
        ch = text[k - 1]
        if ch in INVISIBLE:
            k -= 1
            continue
        if visible(ch) != flat[j - 1]:
            return None
        k -= 1
        j -= 1
    return k if j == 0 else None


def _same_characters_after(text: str, start: int, flat: str) -> int | None:
    """Where the copy that starts at `start` ends, when it holds exactly the characters of `flat` in some order (the page's order)."""
    want = Counter(flat)
    have: Counter = Counter()
    k = start
    while k < len(text) and sum(have.values()) < len(flat):
        ch = text[k]
        k += 1
        if ch in INVISIBLE:
            continue
        v = visible(ch)
        if not v:
            return None
        have[v] += 1
        if have[v] > want[v]:
            return None
    while k < len(text) and text[k] in INVISIBLE:                # the zero-width space KaTeX leaves after a fraction
        k += 1
    return k if have == want else None
