"""Superscript/subscript FORMATTING in Word and Excel cells (x with a raised 2), which the current readers flatten to "x2".

Formatted runs are turned back into Unicode superscript/subscript characters when every character has one (x², a₁, x⁻¹),
otherwise into ^(...) / _(...) (x^(n+k)), so the meaning reaches the recogniser and stays readable as plain text.

Word paragraphs also carry native equations (converted by omml.py), images/embedded objects and symbol-font characters: these become
markers (markers.py) so that recognition reports them instead of the text silently losing them. Deleted tracked changes are skipped.
"""
import re
import unicodedata
from xml.etree import ElementTree as ET

from . import markers
from .omml import M, Unsupported, omml_text, omml_to_latex
from .safety import omml_preserved
from .verify import verify_omml
from .symbols import TO_SUBSCRIPT, TO_SUPERSCRIPT

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def scripted(text: str, align: str | None) -> str:
    if align not in ("superscript", "subscript") or not text:
        return text
    table, mark = (TO_SUPERSCRIPT, "^") if align == "superscript" else (TO_SUBSCRIPT, "_")
    if all(c in table for c in text):
        return "".join(table[c] for c in text)
    return f"{mark}({text})"


def _join(runs: list[tuple]) -> str:
    """Runs (align, text[, run identity, formatting key, strict]) -> text. Consecutive runs with the same alignment are one script
    (x + ²³ -> x²³) - EXCEPT two separately formatted superscript (or subscript) runs side by side: 5 + "2" + "3" displays exactly like
    5²³ but may have been typed as 5^2 then ^3. That boundary is kept as a marker and flagged for review (never merged silently).
    Excel never splits a run on its own, so any two adjacent script runs count (strict); Word splits runs for spelling/revision
    marks, so there the boundary counts only when the two runs' formatting differs."""
    out, cur, buf, last = [], None, [], None
    for item in list(runs) + [("<end>", "", None, None, False)]:
        align, text = item[0], item[1]
        ident, key, strict = (item[2], item[3], item[4]) if len(item) > 2 else (None, None, False)
        if align != cur:
            out.append(scripted("".join(buf), cur))
            cur, buf = align, []
        elif (align in ("superscript", "subscript") and last is not None and ident is not None and ident != last[0]
              and text and buf and (strict or key != last[1])):
            out.append(scripted("".join(buf), cur))
            out.append(markers.make(markers.SCRIPT_SPLIT, ""))
            buf = []
        buf.append(text)
        if ident is not None:
            last = (ident, key)
    return "".join(out)


def _rpr_key(rpr) -> str:
    """Formatting of a Word run, ignoring revision ids, language and proofing marks (which Word changes without the author)."""
    if rpr is None:
        return ""
    keep = [x for x in rpr if x.tag not in (f"{W}lang", f"{W}noProof", f"{W}rStyle")]
    return "|".join(ET.tostring(x, encoding="unicode") for x in keep)


# Symbol-font private-use characters (w:sym, font "Symbol") for the Greek letters and operators teachers insert with Insert > Symbol.
_SYMBOL_FONT = dict(zip(range(0xF061, 0xF07A), "αβχδεφγηιϕκλμνοπθρστυϖωξψζ", strict=False)) | dict(zip(range(0xF041, 0xF05A), "ΑΒΧΔΕΦΓΗΙϑΚΛΜΝΟΠΘΡΣΤΥςΩΞΨΖ", strict=False)) | {
    0xF0B4: "×", 0xF0B8: "÷", 0xF0B1: "±", 0xF0A3: "≤", 0xF0B3: "≥", 0xF0B9: "≠", 0xF0BB: "≈", 0xF0AE: "→", 0xF0A5: "∞", 0xF0B6: "∂",
    0xF0D6: "√", 0xF0F2: "∫", 0xF0E5: "∑", 0xF0D1: "∇", 0xF0B0: "°", 0xF02D: "−", 0xF0A2: "′", 0xF0B2: "″", 0xF0AC: "←", 0xF0DB: "⇔",
    0xF0DE: "⇒", 0xF0CE: "∈", 0xF0C7: "∩", 0xF0C8: "∪", 0xF0D7: "·"}


_FIELD_LINE = re.compile(r"^\s*(answer|ans|correct answer|correct|marks?|negative marks?|negative|neg|topic)\s*[:\-]", re.IGNORECASE)


def _on(el: ET.Element | None, attr: str) -> bool:
    return el is not None and (el.get(attr) or "true").lower() not in ("0", "false", "off")


def _bold_letters(runs: list[tuple], fmt: dict[int, tuple[bool, bool]], whole_is_letter_ok: bool) -> None:
    """A bold letter standing alone in ordinary text is a vector (The vector F acts on m.): it becomes the mathematical bold
    character (𝐅, or 𝑭 when also italic), which recognition converts to \\mathbf{F} / \\boldsymbol{F}, instead of the bold being
    silently dropped. Never when it could change the paper's structure or an ordinary word: a letter inside a word, an option
    label (A) at the start of a line, an Answer:/Marks: line, or (Excel: whole_is_letter_ok False) a cell that is only the letter."""
    texts = [r[1] if len(r) > 1 else "" for r in runs]
    for k, (bold, italic) in fmt.items():
        text = texts[k]
        letter = text.strip()
        if not bold or runs[k][0] not in (None, "baseline") or len(letter) != 1 or not (letter.isascii() and letter.isalpha()):
            continue
        before, after = "".join(texts[:k]) + text[:text.index(letter)], text[text.index(letter) + 1:] + "".join(texts[k + 1:])
        if (before[-1:].isalnum() or after[:1].isalnum() or (not before.strip() and after[:1] in ").:")
                or _FIELD_LINE.match(before) or (not whole_is_letter_ok and not before.strip() and not after.strip())):
            continue
        styled = unicodedata.lookup(f"MATHEMATICAL BOLD{' ITALIC' if italic else ''} {'CAPITAL' if letter.isupper() else 'SMALL'} {letter.upper()}")
        runs[k] = (runs[k][0], text.replace(letter, styled), *runs[k][2:])


def docx_paragraph_text(p: ET.Element) -> str:
    """The text of a Word paragraph keeping superscript/subscript formatting, with native equations converted to LaTeX markers and
    images/objects/unknown symbols as markers. Word order is preserved exactly."""
    runs: list[tuple[str | None, str]] = []
    fmt: dict[int, tuple[bool, bool]] = {}                 # run index -> (bold, italic) of Word text runs

    def walk(el: ET.Element) -> None:
        for child in el:
            tag = child.tag
            if tag in (f"{M}oMath", f"{M}oMathPara"):
                try:
                    latex = omml_to_latex(child)
                    if not omml_preserved(child, latex) or not verify_omml(child, latex)[0]:   # two independent reads of the source
                        runs.append((None, markers.make(markers.EQUATION_UNVERIFIED, omml_text(child))))
                        continue
                    runs.append((None, markers.make(markers.EQUATION, latex)))
                except Unsupported as exc:
                    runs.append((None, markers.make(markers.EQUATION_FAILED, (omml_text(child) or "[equation]") + markers.DETAIL + str(exc))))
            elif tag == f"{W}r":
                va = child.find(f"{W}rPr/{W}vertAlign")
                align = va.get(f"{W}val") if va is not None else None
                key = _rpr_key(child.find(f"{W}rPr"))
                rpr = child.find(f"{W}rPr")
                bi = (_on(rpr.find(f"{W}b"), f"{W}val"), _on(rpr.find(f"{W}i"), f"{W}val")) if rpr is not None else (False, False)
                for node in child:
                    if node.tag == f"{W}t":
                        fmt[len(runs)] = bi
                        runs.append((align, node.text or "", id(child), key, False))
                    elif node.tag in (f"{W}tab", f"{W}ptab"):
                        runs.append((None, " "))
                    elif node.tag in (f"{W}br", f"{W}cr"):
                        runs.append((None, "\n"))
                    elif node.tag == f"{W}noBreakHyphen":
                        runs.append((align, "-"))
                    elif node.tag == f"{W}sym":
                        code = int(node.get(f"{W}char") or "0", 16)
                        ch = _SYMBOL_FONT.get(code) or _SYMBOL_FONT.get(code + 0xF000)
                        runs.append((align, ch) if ch else (None, markers.make(markers.SYMBOL, f"[symbol {code:04X}]")))
                    elif node.tag in (f"{W}drawing", f"{W}pict", f"{W}object", f"{W}embeddedObject"):
                        runs.append((None, markers.make(markers.OBJECT, "image")))
            elif tag in (f"{W}del", f"{W}moveFrom", f"{W}pPr", f"{W}rPr"):
                continue                                         # deleted text and formatting properties are not content
            else:
                walk(child)                                      # hyperlinks, insertions, fields, content controls, smart tags

    walk(p)
    _bold_letters(runs, fmt, True)
    return _join(runs)


def xlsx_string_text(si: ET.Element) -> str:
    """The text of an Excel shared/inline string (as extraction._text_of) keeping superscript/subscript formatting. Each rich-text
    run keeps its identity: two adjacent superscript runs are a boundary (see _join)."""
    runs, fmt = [], {}
    for n, child in enumerate(si):
        if child.tag == f"{S}t":
            runs.append((None, child.text or "", ("t", n), "", True))
        elif child.tag == f"{S}r":
            va = child.find(f"{S}rPr/{S}vertAlign")
            align = va.get("val") if va is not None else None
            bi = (_on(child.find(f"{S}rPr/{S}b"), "val"), _on(child.find(f"{S}rPr/{S}i"), "val"))
            for t in child:
                if t.tag == f"{S}t":
                    fmt[len(runs)] = bi
                    runs.append((align, t.text or "", ("r", n), "", True))
    _bold_letters(runs, fmt, False)
    return _join(runs)
