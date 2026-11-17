"""Word native equations (Office Math, OMML <m:oMath>) -> LaTeX.

Supported: runs (m:r), fractions (m:f, bar/skewed/linear), superscripts/subscripts/both/pre-scripts (m:sSup, m:sSub, m:sSubSup,
m:sPre), radicals (m:rad), n-ary operators with limits (m:nary: ∫ ∬ ∭ ∮ ∑ ∏ ⋃ ⋂), delimiters (m:d), functions (m:func), lower/upper
limits (m:limLow, m:limUpp: lim, max, min), accents (m:acc: hat, bar, tilde, dot, double dot, vector arrow), bars (m:bar), matrices
(m:m), equation arrays (m:eqArr), boxes (m:box, m:borderBox), grouping characters (m:groupChr: under/over-braces, arrows).

Anything else, or any character the student view cannot render, raises Unsupported: the caller keeps the equation's extracted text
and flags it (UNSUPPORTED_WORD_EQUATION). Nothing is silently dropped.
"""
import unicodedata
from xml.etree import ElementTree as ET

from .symbols import ADD, ATOMS, BIG, FUNCTIONS, GREEK, MUL, RELATIONS, SUBSCRIPTS, SUPERSCRIPTS, math_alphanumeric

M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_NODES = 2000


class Unsupported(Exception):
    pass


_CHARS = {**GREEK, **{k: v for k, v in RELATIONS.items() if len(k) == 1}, **ADD, **MUL, **ATOMS, **BIG,
          "∂": r"\partial", "′": "'", "″": "''", "…": r"\ldots", "⋯": r"\cdots", "°": r"^{\circ}", "∠": r"\angle", "∴": r"\therefore",
          "∵": r"\because", "ℏ": r"\hbar", "ℓ": r"\ell", "∀": r"\forall", "∃": r"\exists", "¬": r"\neg", "∧": r"\land", "∨": r"\lor",
          "→": r"\rightarrow", "←": r"\leftarrow", "⟶": r"\longrightarrow", "⇌": r"\rightleftharpoons", "⇒": r"\Rightarrow",
          "{": r"\{", "}": r"\}", "%": r"\%", "#": r"\#", "&": r"\&", "$": r"\$", "_": r"\_", "\\": None, "~": None, "^": None,
          "⁡": "", "⁢": "", "⁣": "", "​": "", " ": " ", " ": r"\,", " ": r"\,", "−": "-", "–": "-"}
_NARY = {"∫": r"\int", "∬": r"\iint", "∭": r"\iiint", "∮": r"\oint", "∑": r"\sum", "∏": r"\prod", "⋃": r"\bigcup", "⋂": r"\bigcap"}
_ACCENTS = {"̂": r"\hat", "̃": r"\tilde", "̇": r"\dot", "̈": r"\ddot", "⃗": r"\vec", "̅": r"\bar",
            "¯": r"\bar", "̄": r"\bar", "^": r"\hat", "~": r"\tilde"}
_DELIMS = {"(": "(", ")": ")", "[": "[", "]": "]", "{": r"\{", "}": r"\}", "|": "|", "‖": r"\|", "⌊": r"\lfloor", "⌋": r"\rfloor",
           "⌈": r"\lceil", "⌉": r"\rceil", "⟨": r"\langle", "⟩": r"\rangle", "": "."}
_LIMIT_WORDS = {"lim", "max", "min", "sup", "inf"}


def _val(el: ET.Element | None, tag: str, default: str | None = None) -> str | None:
    if el is None:
        return default
    x = el.find(f"{M}{tag}")
    if x is None:
        return default
    return x.get(f"{M}val", default if default is not None else "")


def _text(el: ET.Element) -> str:
    """Plain text of an equation (what the provider sees): used when it cannot be converted."""
    return "".join(t.text or "" for t in el.iter() if t.tag in (f"{M}t", f"{W}t"))


def _chars(s: str, plain: bool, fname: bool = False) -> str:
    """Characters of one run. Word stores upright text as a plain run (m:sty p, m:nor): every letter of it stays upright - a single
    letter, a unit after a number ("5 kg"), letters after a space (" J"). A function name becomes \\sin only where Word marks it
    as a function name (m:fName); elsewhere a plain "min" is the minute, \\mathrm{min}, never the minimum operator."""
    if plain:
        word = s.strip()
        if fname and word in FUNCTIONS:
            return FUNCTIONS[word]
        out, k = [], 0
        while k < len(s):
            if s[k].isascii() and s[k].isalpha():
                j = k
                while j < len(s) and s[j].isascii() and s[j].isalpha():
                    j += 1
                out.append(r"\mathrm{" + s[k:j] + "}")
                k = j
            else:
                out.append(_chars(s[k], False))
                k += 1
        return "".join(out)
    out = []
    for ch in s:
        if not ch.isascii() and unicodedata.category(ch) == "Zs":
            out.append(" ")                    # U+2008 punctuation space, U+2009 thin space ... (Word for the web, between units)
        elif ch.isascii() and (ch.isalnum() or ch in "+-=<>()[]/|.,:;!'*? "):
            out.append(r"\cdot " if ch == "*" else ch)
        elif ch in SUPERSCRIPTS:
            out.append("^{" + SUPERSCRIPTS[ch] + "}")
        elif ch in SUBSCRIPTS:
            out.append("_{" + SUBSCRIPTS[ch] + "}")
        elif ch in _SETS:
            out.append(r"\mathbb{" + _SETS[ch] + "}")
        elif ch in _CHARS and _CHARS[ch] is not None:
            v = _CHARS[ch]
            out.append(v + " " if v.startswith("\\") and v[1:].isalpha() else v)
        else:
            raise Unsupported(f"the character {ch!r}")
    # no rstrip(): the space after a command (\Delta ) separates it from the next run's letters (\Delta k, never \Deltak)
    return "".join(out)


_SETS = {"ℂ": "C", "ℕ": "N", "ℙ": "P", "ℚ": "Q", "ℝ": "R", "ℤ": "Z"}       # number sets typed as double-struck characters



def _font(el: ET.Element, pr, nor: bool) -> str | None:
    """The LaTeX font command a Word math run's style needs (None: ordinary upright/italic handling): bold upright -> \\mathbf,
    bold italic (m:sty bi, or an italic run with w:b - a bold vector) -> \\boldsymbol, double-struck -> \\mathbb, script -> \\mathcal.
    A style that carries meaning and that no allowed command preserves - fraktur, sans-serif, monospace - is flagged, never dropped."""
    scr = _val(pr, "scr")
    if scr and scr != "roman":
        if scr in ("double-struck", "script"):
            return {"double-struck": "mathbb", "script": "mathcal"}[scr]
        raise Unsupported(f"the {scr} equation font")
    wb = el.find(f"{W}rPr/{W}b")
    bold = wb is not None and wb.get(f"{W}val", "true") not in ("0", "false", "off")
    sty = _val(pr, "sty")
    upright = nor or sty in ("p", "b")
    if sty in ("b", "bi") or bold:
        return "mathbf" if upright else "boldsymbol"
    return None


def _font_chars(s: str, cmd: str) -> str:
    out, k = [], 0
    while k < len(s):
        if s[k].isascii() and s[k].isalpha():
            j = k
            while j < len(s) and s[j].isascii() and s[j].isalpha():
                j += 1
            if cmd in ("mathbb", "mathcal") and not s[k:j].isupper():
                raise Unsupported("a lowercase double-struck or script letter")
            out.append("\\" + cmd + "{" + s[k:j] + "}")
            k = j
        elif s[k].isalpha():
            # a styled Greek or other non-Latin letter: KaTeX's font commands do not reliably style every one - flagged, never dropped
            raise Unsupported(f"the styled letter {s[k]!r}")
        else:
            out.append(_chars(s[k], False))
            k += 1
    return "".join(out)


class _Conv:
    def __init__(self):
        self.nodes = 0
        self.fname = 0

    def kids(self, el: ET.Element | None) -> str:
        if el is None:
            return ""
        # spaces between runs are kept (\Delta k); at the end of a group nothing follows, so a trailing space is dropped
        return "".join(self.node(c) for c in el).rstrip()

    def arg(self, el: ET.Element, tag: str) -> str:
        return self.kids(el.find(f"{M}{tag}"))

    def node(self, el: ET.Element) -> str:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise Unsupported("the equation is too large")
        t = el.tag
        if not t.startswith(M):
            if t in (f"{W}rPr", f"{W}bookmarkStart", f"{W}bookmarkEnd", f"{W}proofErr"):
                return ""
            raise Unsupported(f"Word content inside the equation ({t.split('}')[-1]})")
        name = t[len(M):]
        if name.endswith("Pr") or name in ("ctrlPr", "argPr"):
            return ""
        if name == "r":
            pr = el.find(f"{M}rPr")
            nor = el.find(f"{M}rPr/{M}nor") is not None
            font = None if self.fname > 0 else _font(el, pr, nor)
            plain = _val(pr, "sty") == "p" or nor or self.fname > 0

            def chars(s: str) -> str:
                return _font_chars(s, font) if font else _chars(s, plain, self.fname > 0)
            text = "".join(x.text or "" for x in el if x.tag == f"{M}t")
            if not any(math_alphanumeric(c) for c in text):
                return "".join(chars(x.text or "") for x in el if x.tag == f"{M}t")
            # Word for the web: the letter's style is in the character itself, and it decides (the run's style properties are
            # those of the other characters). Italic is the maths default; bold/double-struck/script keep their command.
            out, buf = [], ""
            for c in text:
                styled = math_alphanumeric(c)
                if styled is None:
                    buf += c
                    continue
                out.append(chars(buf) if buf else "")
                buf = ""
                base, cmd = styled
                if cmd == "":
                    out.append(_chars(base, False))
                elif cmd is None or not base.isalpha():
                    raise Unsupported(f"the styled letter {c!r}")       # fraktur, sans-serif, a bold digit ...: flagged, never dropped
                else:
                    out.append(_font_chars(base, cmd))
            out.append(chars(buf) if buf else "")
            return "".join(out)
        if name == "fName":
            self.fname += 1
            try:
                return self.kids(el)
            finally:
                self.fname -= 1
        if name in ("e", "num", "den", "sub", "sup", "deg", "lim", "oMath"):
            return self.kids(el)
        if name == "f":
            kind = _val(el.find(f"{M}fPr"), "type", "bar")
            num, den = self.arg(el, "num"), self.arg(el, "den")
            if kind in ("bar", "skw"):
                return r"\frac{" + num + "}{" + den + "}"
            if kind == "lin":
                return "{" + num + "}/{" + den + "}"
            raise Unsupported("a fraction without a bar")
        if name == "sSup":
            return "{" + self.arg(el, "e") + "}^{" + self.arg(el, "sup") + "}"
        if name == "sSub":
            return "{" + self.arg(el, "e") + "}_{" + self.arg(el, "sub") + "}"
        if name == "sSubSup":
            return "{" + self.arg(el, "e") + "}_{" + self.arg(el, "sub") + "}^{" + self.arg(el, "sup") + "}"
        if name == "sPre":
            return "{}_{" + self.arg(el, "sub") + "}^{" + self.arg(el, "sup") + "}{" + self.arg(el, "e") + "}"
        if name == "rad":
            hide = _val(el.find(f"{M}radPr"), "degHide", "0") in ("1", "on", "true")
            deg = "" if hide else self.arg(el, "deg")
            return r"\sqrt" + (f"[{deg}]" if deg else "") + "{" + self.arg(el, "e") + "}"
        if name == "nary":
            pr = el.find(f"{M}naryPr")
            ch = _val(pr, "chr", "∫") or "∫"
            if ch not in _NARY:
                raise Unsupported(f"the operator {ch!r}")
            sub = "" if _val(pr, "subHide", "0") in ("1", "on", "true") else self.arg(el, "sub")
            sup = "" if _val(pr, "supHide", "0") in ("1", "on", "true") else self.arg(el, "sup")
            e = el.find(f"{M}e")
            parts = list(e) if e is not None else []
            last = parts[-1] if parts and parts[-1].tag == f"{M}r" else None
            tail = "".join(x.text or "" for x in last if x.tag == f"{M}t").strip() if last is not None else ""
            if len(tail) == 2 and tail[0] == "d" and (tail[1].isalpha()):           # ∫ ... dx: the differential typed as its own run
                body = "".join(self.node(x) for x in parts[:-1]).rstrip() + r"\,d" + _chars(tail[1], False)
            else:
                body = self.arg(el, "e")
            # where Word shows the bounds: m:limLoc, else Word's default (under/over for ∑ ∏ ⋃ ⋂, at the side for integrals)
            loc = _val(pr, "limLoc") or ("subSup" if ch in "∫∬∭∮∯∰" else "undOvr")
            lim = r"\limits" if loc == "undOvr" and (sub or sup) else ""
            return (_NARY[ch] + lim + ("_{" + sub + "}" if sub else "") + ("^{" + sup + "}" if sup else "") + " " + body)
        if name == "d":
            pr = el.find(f"{M}dPr")
            beg, end, sep = _val(pr, "begChr", "("), _val(pr, "endChr", ")"), _val(pr, "sepChr", "|")
            if beg not in _DELIMS or end not in _DELIMS:
                raise Unsupported(f"the bracket {beg!r}{end!r}")
            items = [self.kids(e) for e in el.findall(f"{M}e")]
            sep_l = r" \mid " if sep == "|" else _chars(sep, False)
            return r"\left" + _DELIMS[beg] + " " + sep_l.join(items) + r" \right" + _DELIMS[end]
        if name == "func":
            fn = el.find(f"{M}fName")
            fname = (self.node(fn) if fn is not None else "").strip()          # through node(): the run is a function name
            return fname + " " + self.arg(el, "e")
        if name in ("limLow", "limUpp"):
            base = self.arg(el, "e").strip()
            saved, self.fname = self.fname, 0               # lim under a function name: "lim" is the name, "n→∞" is ordinary maths
            try:
                lim = self.arg(el, "lim")
            finally:
                self.fname = saved
            word = base.replace(r"\mathrm{", "").replace("}", "").lstrip("\\")
            if name == "limLow" and word in _LIMIT_WORDS:
                return (FUNCTIONS[word] + r"\limits" if word in FUNCTIONS else r"\operatorname*{" + word + "}") + "_{" + lim + "} "
            return (r"\underset{" if name == "limLow" else r"\overset{") + lim + "}{" + base + "}"
        if name == "acc":
            ch = _val(el.find(f"{M}accPr"), "chr", "̂")
            if ch not in _ACCENTS:
                raise Unsupported(f"the accent {ch!r}")
            return _ACCENTS[ch] + "{" + self.arg(el, "e") + "}"
        if name == "bar":
            pos = _val(el.find(f"{M}barPr"), "pos", "bot")
            return (r"\overline{" if pos == "top" else r"\underline{") + self.arg(el, "e") + "}"
        if name == "m":
            rows = [" & ".join(self.kids(e) for e in mr.findall(f"{M}e")) for mr in el.findall(f"{M}mr")]
            return r"\begin{matrix}" + r" \\ ".join(rows) + r"\end{matrix}"
        if name == "eqArr":
            rows = [self.kids(e).replace("&", "") for e in el.findall(f"{M}e")]
            return r"\begin{gathered}" + r" \\ ".join(rows) + r"\end{gathered}"
        if name == "box":
            return self.arg(el, "e")
        if name == "borderBox":
            return r"\boxed{" + self.arg(el, "e") + "}"
        if name == "groupChr":
            pr = el.find(f"{M}groupChrPr")
            ch, pos = _val(pr, "chr", "⏟"), _val(pr, "pos", "bot")
            body = self.arg(el, "e")
            if ch == "⏟":
                return r"\underbrace{" + body + "}"
            if ch == "⏞":
                return r"\overbrace{" + body + "}"
            if ch in ("→", "⟶"):
                return r"\xrightarrow{" + body + "}" if pos == "top" else r"\xrightarrow[" + body + "]{}"
            raise Unsupported(f"the grouping character {ch!r}")
        raise Unsupported(f"the equation part {name}")


def omml_to_latex(el: ET.Element) -> str:
    """m:oMath (or m:oMathPara) -> LaTeX. Raises Unsupported."""
    conv = _Conv()
    if el.tag == f"{M}oMathPara":
        parts = [conv.node(x) for x in el.findall(f"{M}oMath")]
        if not parts:
            raise Unsupported("an empty equation")
        latex = r" \\ ".join(parts) if len(parts) > 1 else parts[0]
        return (r"\begin{gathered}" + latex + r"\end{gathered}") if len(parts) > 1 else latex
    latex = conv.node(el).strip()
    if not latex:
        raise Unsupported("an empty equation")
    return latex


def omml_text(el: ET.Element) -> str:
    """A readable linear form of an equation that could not be converted, keeping its structure visible: scripts as ^( ) / _( ),
    fractions as (a)/(b), roots as √( ), brackets and n-ary limits kept - never the flattened run text (y^(Δk), not yΔk)."""
    def lin(e) -> str:
        name = e.tag[len(M):] if e.tag.startswith(M) else None
        if name is None:
            return "".join(t.text or "" for t in e.iter(f"{W}t"))
        if name.endswith("Pr"):
            return ""
        if name == "r":
            return "".join(t.text or "" for t in e.findall(f"{M}t"))
        part = lambda tag: "".join(lin(c) for c in (e.find(f"{M}{tag}") if e.find(f"{M}{tag}") is not None else []))
        if name == "f":
            return f"({part('num')})/({part('den')})"
        if name in ("sSup", "sSub", "sSubSup"):
            out = part("e")
            if name != "sSup":
                out += f"_({part('sub')})"
            if name != "sSub":
                out += f"^({part('sup')})"
            return out
        if name == "sPre":
            return f"_({part('sub')})^({part('sup')}){part('e')}"
        if name == "rad":
            deg = part("deg")
            return f"{'(' + deg + ')' if deg else ''}√({part('e')})"
        if name == "nary":
            ch = _val(e.find(f"{M}naryPr"), "chr", "∫") or "∫"
            sub, sup = part("sub"), part("sup")
            return ch + (f"_({sub})" if sub else "") + (f"^({sup})" if sup else "") + " " + part("e")
        if name == "d":
            pr = e.find(f"{M}dPr")
            beg, end, sep = _val(pr, "begChr", "("), _val(pr, "endChr", ")"), _val(pr, "sepChr", "|")
            return (beg or "") + (sep or "").join("".join(lin(c) for c in x) for x in e.findall(f"{M}e")) + (end or "")
        if name in ("limLow", "limUpp"):
            return part("e") + ("_(" if name == "limLow" else "^(") + part("lim") + ")"
        if name == "m":
            return "[" + "; ".join(", ".join("".join(lin(c) for c in x) for x in r.findall(f"{M}e")) for r in e.findall(f"{M}mr")) + "]"
        return "".join(lin(c) for c in e)
    try:
        return lin(el).strip() or _text(el)
    except RecursionError:
        return _text(el)
