"""Stored question text -> readable plain text, for outputs that cannot typeset LaTeX (the paper PDF).

\\(x^{2} - 4\\) -> x² − 4,  \\(\\frac{a+b}{c}\\) -> (a+b)/c,  \\(\\mathrm{SO_{4}^{2-}}\\) -> SO₄²⁻. Superscripts/subscripts become Unicode
characters where every character has one, otherwise ^(...) / _(...); nothing is dropped, so the meaning stays readable.
"""
import re

from . import split_latex
from .symbols import TO_SUBSCRIPT, TO_SUPERSCRIPT
from .safety import _CMD

_WORDS = {"rightarrow": "→", "to": "→", "rightleftharpoons": "⇌", "infty": "∞", "cdot": "·", "times": "×", "circ": "°", "lvert": "|",
          "rvert": "|", "ldots": "…", "cdots": "⋯", "mid": "|", "|": "‖", "{": "{", "}": "}"}
_SPACE = {",": " ", ";": " ", ":": " ", "!": "", " ": " ", "quad": "  ", "qquad": "   "}


def _script(s: str, sup: bool) -> str:
    table, mark = (TO_SUPERSCRIPT, "^") if sup else (TO_SUBSCRIPT, "_")
    if s and all(c in table for c in s):
        return "".join(table[c] for c in s)
    return f"{mark}({s})" if len(s) > 1 else f"{mark}{s}"


def latex_text(latex: str) -> str:
    toks = re.findall(r"\\[A-Za-z]+|\\.|[{}^_]|[^\\{}^_]", latex)
    pos = 0

    def group() -> str:
        nonlocal pos
        while pos < len(toks) and toks[pos] == " ":
            pos += 1
        if pos >= len(toks):
            return ""
        if toks[pos] != "{":
            pos += 1
            return run([toks[pos - 1]])
        depth, start = 0, pos
        while pos < len(toks):
            depth += {"{": 1, "}": -1}.get(toks[pos], 0)
            pos += 1
            if depth == 0:
                break
        return run(toks[start + 1:pos - 1])

    def run(seq: list[str]) -> str:
        nonlocal toks, pos
        saved = (toks, pos)
        toks, pos = seq, 0
        out = []
        while pos < len(toks):
            t = toks[pos]
            pos += 1
            if t in "^_":
                g = group()
                out.append("°" if g == "°" and t == "^" else _script(g, t == "^"))
            elif t in ("\\frac", "\\dfrac", "\\tfrac"):
                alone = pos == 1
                a, b = group(), group()
                wrap = lambda s: s if re.fullmatch(r"[\w.′'²³⁻⁺¹⁰⁴-⁹₀-₉]+", s) else f"({s})"
                frac = f"{wrap(a)}/{wrap(b)}"
                alone = alone and all(x == " " for x in toks[pos:])
                out.append(frac if alone else f"({frac})")            # ½CV² -> (1/2)CV², never 1/2CV²
            elif t == "\\sqrt":
                idx = ""
                if pos < len(toks) and toks[pos] == "[":
                    j = toks.index("]", pos)
                    idx = "".join(toks[pos + 1:j])
                    pos = j + 1
                body = group()
                out.append(({"3": "∛", "4": "∜"}.get(idx) or ("" if not idx else _script(idx, True)) + "√")
                           + (body if len(body) == 1 else f"({body})"))
            elif t in ("\\begin", "\\end"):
                env = group()
                out.append({"bmatrix": "[", "pmatrix": "(", "vmatrix": "|"}.get(env, "") if t == "\\begin"
                           else {"bmatrix": "]", "pmatrix": ")", "vmatrix": "|"}.get(env, ""))
            elif t == "\\\\":
                out.append("; ")
            elif t == "&":
                out.append(", ")
            elif t in ("\\mathrm", "\\operatorname", "\\text", "\\textrm", "\\mathit", "\\mathbf"):
                out.append(group())
            elif t in ("\\vec", "\\overrightarrow"):
                out.append(group() + "⃗")
            elif t in ("\\left", "\\right"):
                continue
            elif t.startswith("\\"):
                name = t[1:]
                if name in _SPACE:
                    out.append(_SPACE[name])
                elif name in _WORDS:
                    out.append(_WORDS[name])
                elif name in _CMD:
                    out.append(_CMD[name])
                else:
                    out.append(name if name.isalpha() else t)          # \sin -> sin
                    if name.isalpha() and pos < len(toks) and toks[pos] not in " ({[^_":
                        out.append(" ")
            elif t not in "{}":
                out.append("−" if t == "-" else t)
        toks, pos = saved
        return "".join(out)

    return re.sub(r" ([,;])", r"\1", re.sub(r" {2,}", " ", run(toks))).strip()


def readable(text: str | None) -> str:
    """A stored field with every LaTeX segment turned into plain text."""
    if not text:
        return text or ""
    out = []
    for kind, a, b in split_latex(text):
        piece = text[a:b]
        if kind != "latex":
            out.append(piece)
            continue
        inner = re.sub(r"^(\$\$|\$|\\\(|\\\[)|(\$\$|\$|\\\)|\\\])$", "", piece)
        out.append(latex_text(inner))
    return "".join(out)
