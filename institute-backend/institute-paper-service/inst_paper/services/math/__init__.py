"""Scientific notation recognition for imported question text: mathematics, physics and chemistry -> LaTeX for KaTeX.

    recognise("Solve x² − 5x + 6 = 0.")
      -> text  "Solve \\(x^{2} - 5x + 6 = 0\\)."   outcome "CONVERTED"

* Ordinary text is never changed. Only formula candidates are converted, into \\( ... \\); the original text is always kept.
* Chemistry (chem.py) is recognised first, with the paper's subject as a hint; mathematics/physics (lexer.py, parser.py) on the rest.
* A candidate that cannot be read in exactly one way is left as typed and flagged with a stable reason code (NEEDS_REVIEW).
* Every conversion must pass the independent meaning-preservation check (safety.py) and the LaTeX allow-list, or it is flagged.
* Existing LaTeX ($...$, \\(...\\)) is kept as typed but must use only commands KaTeX renders; Word equations and objects arrive as
  markers from the extractors (markers.py): converted equations, or EXTRACTION_ERROR for content that could not be extracted.
* Nothing is ever simplified, reordered, evaluated, balanced or corrected; conversion only changes notation.
"""
import re
from dataclasses import dataclass, field

from . import markers
from .chem import explicit_brackets, find_chem
from .lexer import find_spans
from .parser import UNIT_LETTERS, Ambiguous, Unreadable, check_latex, parse_tokens, to_latex, tree_repr
from .lexer import _has_quantity, classify, lex, refine
from .safety import caret_to_unicode, difference, preserved, text_problems, tree_problems
from .verify import verify_text
from .symbols import PROVIDER_COMMANDS, UNITS, math_alphanumeric

ENGINE_VERSION = "2.5.0"
DOMAINS = ("mathematics", "physics", "chemistry")
MAX_FIELD_CHARS = 5000

CONVERTED, PLAIN_TEXT, NEEDS_REVIEW, EXTRACTION_ERROR = "CONVERTED", "PLAIN_TEXT", "NEEDS_REVIEW", "EXTRACTION_ERROR"


@dataclass
class Span:
    start: int                  # position in the original text
    end: int
    original: str
    status: str                 # "converted" | "flagged" | "latex" (already LaTeX, kept) | "error" (not extracted reliably)
    latex: str | None = None
    tree: str | None = None
    reason: str | None = None
    code: str | None = None     # stable reason code for flagged/error spans
    domain: str | None = None   # mathematics | chemistry | latex | word-equation | extraction


@dataclass
class Recognition:
    original: str               # the field as extracted (Word equations/objects as markers)
    text: str                   # the field to store when accepted: conversions in \\( ... \\)
    spans: list[Span] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.text != self.original

    @property
    def flags(self) -> list[Span]:
        return [s for s in self.spans if s.status in ("flagged", "error")]

    @property
    def outcome(self) -> str:
        if any(s.status == "error" for s in self.spans):
            return EXTRACTION_ERROR
        if any(s.status == "flagged" for s in self.spans):
            return NEEDS_REVIEW
        if any(s.status in ("converted", "latex") for s in self.spans):
            return CONVERTED
        return PLAIN_TEXT

    @property
    def readable_original(self) -> str:
        """The provider's text with extraction markers shown as text (what "keep the original" stores)."""
        return markers.readable(self.original)

    @property
    def reasons(self) -> list[str]:
        return sorted({s.code for s in self.flags if s.code})


def domain_of_subject_id(subject_id, categories: dict[str, str]) -> str | None:
    """The recognition domain of the paper's authoritative backend subject_id, from the explicit configured mapping
    (settings.recognition_subject_categories). An unlisted subject has no domain: no subject convention applies. The subject's name,
    the file's topic column and the question text are never consulted."""
    # OTHER (e.g. Biology) is an explicit decision that the subject has no scientific domain: the same as an unlisted subject
    return {"CHEMISTRY": "chemistry", "PHYSICS": "physics", "MATHEMATICS": "mathematics"}.get(categories.get(str(subject_id), ""))


def split_latex(text: str) -> list[tuple[str, int, int]]:
    """[("text"|"latex", start, end)]. Inline $...$ follows the usual rule that keeps prices as text: the opening $ is not followed
    by a space, the closing $ is not preceded by a space nor followed by a digit; \\$ is a literal dollar."""
    out, last, i, n = [], 0, 0, len(text)

    while i < n:
        if text.startswith("\\$", i):
            i += 2
            continue
        end = None
        for opener, closer in (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)")):
            if text.startswith(opener, i):
                j = text.find(closer, i + 2)
                if j > i + 2:
                    end = j + 2
                break
        else:
            if text[i] == "$" and i + 1 < n and not text[i + 1].isspace():
                j = i + 1
                while (j := text.find("$", j)) != -1:
                    if not text[j - 1].isspace() and text[j - 1] != "\\" and not (j + 1 < n and text[j + 1].isdigit()):
                        end = j + 1
                        break
                    j += 1
        if end:
            if i > last:
                out.append(("text", last, i))
            out.append(("latex", i, end))
            i = last = end
        else:
            i += 1
    if last < n:
        out.append(("text", last, n))
    return out


def _latex_span(piece: str, a: int, b: int) -> Span:
    inner = re.sub(r"^(\$\$|\$|\\\(|\\\[)|(\$\$|\$|\\\)|\\\])$", "", piece)
    try:
        check_latex(inner, PROVIDER_COMMANDS)
    except Unreadable as exc:
        return Span(a, b, piece, "flagged", reason=f"The LaTeX uses something the student view cannot show safely ({exc}).",
                    code="LATEX_VALIDATION_FAILED", domain="latex")
    return Span(a, b, piece, "latex", inner, domain="latex")


def _math(text: str, a: int, b: int, out: list[str], spans: list[Span], whole_quantity: bool = False,
          domain: str | None = None, whole_formula: bool = False, unit_answer: bool = False) -> None:
    cursor = a
    context_units = _context_units(text, domain)
    for s, e, toks, problem in find_spans(text[a:b], a, whole_quantity, whole_formula, chemistry=domain == "chemistry",
                                          science=domain in ("physics", "chemistry")):
        out.append(text[cursor:s])
        original = text[s:e]
        try:
            if problem:
                code, _, message = problem.partition("|") if "|" in problem else ("JOINED_TO_TEXT", "", problem)
                raise Ambiguous(message, code)
            tree = parse_tokens(toks, molar=domain == "chemistry", physics=domain == "physics", context_units=context_units,
                                unit_answer=unit_answer)
            latex = to_latex(tree)
            check_latex(latex)
            science = domain in ("physics", "chemistry")
            # [H3O+] is checked in its explicit form [H₃O⁺], as a formula pasted without subscripts is (see chem.explicit_counts)
            checked = explicit_brackets(original) if domain == "chemistry" else original
            if not preserved(checked, latex, science):
                raise Unreadable(f"internal check: {difference(checked, latex, science)}", "STRUCTURE_NOT_PRESERVED")
            problem = tree_problems(tree) or text_problems(original, latex, text[a:s])
            if not problem:
                verified, why = verify_text(checked, latex, domain, text.replace(original, checked),
                                            unit_answer=unit_answer)        # the independent gate: never optional
                if not verified:
                    problem = ("NOT_INDEPENDENTLY_VERIFIED", f"the independent check could not confirm the meaning ({why})")
            if problem:
                raise Ambiguous(f"Kept as typed: {problem[1]}.", problem[0])
            alt = caret_to_unicode(original)
            if alt != original and _latex_of(alt, domain, context_units, unit_answer) != latex:
                raise Ambiguous("Kept as typed: the expression reads differently with ^ powers than with superscripts.",
                                "STRUCTURE_NOT_PRESERVED")
            spans.append(Span(s, e, original, "converted", latex, tree_repr(tree), domain="mathematics"))
            out.append("\\(" + latex + "\\)")
        except Ambiguous as exc:
            spans.append(Span(s, e, original, "flagged", reason=str(exc), code=exc.code, domain="mathematics"))
            out.append(markers.readable(original))
        except (Unreadable, RecursionError) as exc:
            code = getattr(exc, "code", "UNSUPPORTED_NOTATION")
            # two separately formatted scripts side by side (s + '−' + '2'): say so, rather than the parser's symptom
            reason = (markers.REASONS[markers.SCRIPT_SPLIT] if markers.SPLIT_TEXT in original
                      else f"Could not be read as a formula ({exc}); left as typed.")
            spans.append(Span(s, e, original, "flagged", reason=reason, code=code, domain="mathematics"))
            out.append(original)
        cursor = e
    out.append(text[cursor:b])


def _context_units(text: str, domain: str | None) -> frozenset:
    """Unit letters the field itself establishes, for "x(t)=3t²−2t³ m, find acceleration at t=2 s": in a Physics or Chemistry paper,
    a field that writes a number with a spaced unit (2 s) writes units spaced; a unit letter used only once in the field is then
    not also a variable. Without that evidence a unit letter after an algebraic term (k = 5t N) stays ambiguous."""
    if domain not in ("physics", "chemistry"):
        return frozenset()
    toks = classify(lex(text))
    if not _has_quantity(toks):
        return frozenset()
    letters = [t.value for t in toks if t.kind == "VAR" and t.value in UNIT_LETTERS]
    return frozenset(x for x in letters if letters.count(x) == 1)


def _latex_of(text: str, domain: str | None, context_units: frozenset = frozenset(), unit_answer: bool = False) -> str | None:
    try:
        return to_latex(parse_tokens(refine(classify(lex(text)), domain == "chemistry", domain in ("physics", "chemistry")),
                                     molar=domain == "chemistry", physics=domain == "physics",
                                     context_units=context_units, unit_answer=unit_answer))
    except (Ambiguous, Unreadable, RecursionError):
        return None


def _plain_piece(text: str, a: int, b: int, domain: str | None, out: list[str], spans: list[Span], formula_field: bool = False,
                 unit_answer: bool = False) -> None:
    """Ordinary text: chemistry first, mathematics on the text between chemistry spans."""
    cursor = a
    for c in find_chem(text[a:b], domain, a):
        _math(text, cursor, c.start, out, spans, domain=domain, unit_answer=unit_answer)
        if c.code:
            spans.append(Span(c.start, c.end, c.original, "flagged", reason=c.reason, code=c.code, domain="chemistry"))
            out.append(c.original)
        else:
            try:
                check_latex(c.latex)
                # a formula pasted without its subscripts (H2SO4) is checked in its explicit form (H₂SO₄): the counts are the only change
                source = c.checked or c.original
                if not preserved(source, c.latex):
                    raise Unreadable(f"internal check: {difference(source, c.latex)}", "STRUCTURE_NOT_PRESERVED")
                verified, why = verify_text(source, c.latex, domain, text.replace(c.original, source) if c.checked else text)
                if not verified:
                    raise Unreadable(f"the independent check could not confirm the meaning ({why})", "NOT_INDEPENDENTLY_VERIFIED")
                spans.append(Span(c.start, c.end, c.original, "converted", c.latex, domain="chemistry"))
                out.append("\\(" + c.latex + "\\)")
            except Unreadable as exc:
                spans.append(Span(c.start, c.end, c.original, "flagged", reason=f"Could not be converted safely ({exc}).",
                                  code=exc.code, domain="chemistry"))
                out.append(c.original)
        cursor = c.end
    _math(text, cursor, b, out, spans, whole_quantity=domain in ("physics", "chemistry") and cursor == 0 and b == len(text),
          whole_formula=formula_field and cursor == 0 and b == len(text),
          domain=domain, unit_answer=unit_answer)


def _italic_unit(latex: str) -> str | None:
    """The unit symbol written right after a number in ITALIC in a converted Word equation (5 m s⁻¹, 5N), or None. Word shows a variable
    in italic and a unit upright (Normal Text); Word for the web cannot make a letter upright, so there an italic m after a number may
    be the metre or a variable m. Upright units (\\mathrm{...}) and letters not right after a number are not considered."""
    plain = re.sub(r"\\(?:mathrm|operatorname\*?|text|textrm)\{[^{}]*\}", " ", latex)
    units = "|".join(sorted(UNITS | UNIT_LETTERS, key=len, reverse=True))
    m = re.search(r"(?<![A-Za-z\\])\d+(?:\.\d+)?(?:\s|\\[,;: ])*\{?(" + units + r")\}?(?![A-Za-z])", plain)
    return m.group(1) if m else None


def _plain_italics(text: str) -> str:
    """𝑥 𝛼 ℎ 𝜕 (mathematical ITALIC characters, as Word for the web and maths-aware editors write them) are the letters x α h ∂:
    italic is how a maths letter is shown anyway. Each is one character either way, so every position stays the same. Bold,
    double-struck and script letters keep their character (their font is meaning, read by the lexer)."""
    return "".join(styled[0] if (styled := math_alphanumeric(c)) is not None and styled[1] == "" else c for c in text)


def recognise(text: str | None, *, subject: str | None = None, formula_field: bool = False, unit_answer: bool = False) -> Recognition:
    """`subject`: the recognition domain from the paper's authoritative subject_id (see domain_of_subject_id), or None.
    `formula_field`: the text is a whole answer option, so an option that is exactly one formula (h/p, 2n, F/4) is a formula.
    `unit_answer`: the option answers a question that asks for a unit ("Unit of rate constant ..."), so an option that is a lone
    unit symbol with a power (s⁻¹) is that unit, not a variable."""
    source = text or ""
    text = _plain_italics(source)
    out: list[str] = []
    spans: list[Span] = []
    if len(text) > MAX_FIELD_CHARS:
        spans.append(Span(0, len(text), text, "flagged", reason="The text is too long to check safely.", code="EXPRESSION_TOO_COMPLEX"))
        return Recognition(text, text, spans)
    cursor = 0
    for m in markers.SPLITTING.finditer(text):
        _text_part(text, cursor, m.start(), subject, out, spans, formula_field, unit_answer)
        kind, payload = m.group(1), m.group(2)
        if kind == markers.EQUATION:
            try:
                check_latex(payload, PROVIDER_COMMANDS)
                unit = _italic_unit(payload) if subject in ("physics", "chemistry") else None
                if unit:
                    spans.append(Span(m.start(), m.end(), m.group(), "flagged", payload, reason=f"In this Word equation '{unit}' after a number is "
                                      "in italic, which Word uses for a variable (Word for the web cannot set a unit upright): check whether "
                                      "it is a unit or a variable.", code="AMBIGUOUS_UNIT", domain="word-equation"))
                    out.append(markers.readable(m.group()))
                    cursor = m.end()
                    continue
                spans.append(Span(m.start(), m.end(), m.group(), "converted", payload, domain="word-equation"))
                out.append("\\(" + payload + "\\)")
            except Unreadable as exc:
                spans.append(Span(m.start(), m.end(), m.group(), "error", reason=f"The Word equation could not be converted ({exc}).",
                                  code="UNSUPPORTED_WORD_EQUATION", domain="word-equation"))
                out.append(markers.readable(m.group()))
        else:
            code = {markers.EQUATION_FAILED: "UNSUPPORTED_WORD_EQUATION", markers.EQUATION_UNVERIFIED: "WORD_EQUATION_NOT_VERIFIED",
                    markers.OBJECT: "UNSUPPORTED_OBJECT",
                    markers.SYMBOL: "UNSUPPORTED_SYMBOL"}.get(kind, "STRUCTURE_NOT_PRESERVED")
            detail = payload.split(markers.DETAIL)[1] if markers.DETAIL in payload else ""
            reason = markers.REASONS.get(kind, "Content could not be extracted.") + (f" Not supported: {detail}." if detail else "")
            spans.append(Span(m.start(), m.end(), m.group(), "error", reason=reason,
                              code=code, domain="extraction"))
            out.append(markers.readable(m.group()))
        cursor = m.end()
    _text_part(text, cursor, len(text), subject, out, spans, formula_field, unit_answer)
    spans.sort(key=lambda s: s.start)
    return Recognition(source, "".join(out), spans)


def _text_part(text: str, a: int, b: int, subject: str | None, out: list[str], spans: list[Span], formula_field: bool = False,
               unit_answer: bool = False) -> None:
    if a >= b:
        return
    from inst_paper.services.math import katex_paste
    found = katex_paste.islands(text[a:b])
    if found:                                              # formulas copied from a web page, each proven by its two renderings
        cursor = a
        for s, e, source in found:
            _text_part(text, cursor, a + s, subject, out, spans, False, unit_answer)
            try:
                check_latex(source, PROVIDER_COMMANDS)
                spans.append(Span(a + s, a + e, text[a + s:a + e], "converted", source, domain="web-copy"))
                out.append("\\(" + source + "\\)")
            except Unreadable as exc:
                spans.append(Span(a + s, a + e, text[a + s:a + e], "error", reason=f"A formula copied from a web page could not be "
                                  f"used ({exc}).", code="UNSUPPORTED_WEB_FORMULA", domain="web-copy"))
                out.append(text[a + s:a + e])
            cursor = a + e
        _text_part(text, cursor, b, subject, out, spans, False, unit_answer)
        return
    if "\u200b" in text[a:b]:                               # rendered maths copied WITHOUT its source: page order, unreadable
        cursor = a
        for m in re.finditer(r"\S*\u200b\S*", text[a:b]):
            _text_part(text, cursor, a + m.start(), subject, out, spans, False, unit_answer)
            spans.append(Span(a + m.start(), a + m.end(), m.group(), "flagged", reason="A formula copied from a web page without its "
                              "source; its characters are in the page's layout order.", code="WEB_COPY_WITHOUT_SOURCE", domain="web-copy"))
            out.append(m.group())
            cursor = a + m.end()
        _text_part(text, cursor, b, subject, out, spans, False, unit_answer)
        return
    part = text[a:b]
    for kind, s, e in split_latex(part):
        s, e = s + a, e + a
        piece = text[s:e]
        if kind == "latex":
            span = _latex_span(piece, s, e)
            spans.append(span)
            out.append(piece)
        elif re.search(r"\\[A-Za-z]", piece):
            # a field that is entirely LaTeX typed without delimiters (\int \frac{\ln x}{x}\,dx) is one formula, checked like \( \).
            # Prose mixed with commands (Set u = \ln x) is never wrapped: its words would turn into italic maths letters.
            whole = piece.strip()
            # names inside \begin{...}, \operatorname{...}, \mathrm{...} are part of the commands, not prose
            names = re.sub(r"\\(?:begin|end|operatorname|mathrm|mathbf|mathit|mathbb|mathcal)\{[^{}]*\}", " ", whole)
            # ...unless it carries invisible characters only rendered maths leaves (U+200B, U+2061): a paste of a rendered page,
            # its renderings run together with the source - never one formula
            rendered = any(ch in katex_paste.INVISIBLE for ch in whole)
            if s == 0 and e == len(text) and not rendered and not re.search(r"(?<![\\A-Za-z])[A-Za-z]{3,}", names):
                span = _latex_span("\\(" + whole + "\\)", s, e)
                if span.status == "latex":
                    spans.append(span)
                    out.append(span.original)
                    continue
            spans.append(Span(s, e, piece, "flagged", reason="LaTeX commands written without $...$ delimiters; left as typed.",
                              code="LATEX_WITHOUT_DELIMITERS", domain="latex"))
            out.append(piece)
        else:
            # a line break ends every formula: two lines are never read as one expression (x ≥ 0 / f(x) = ... is not 0·f(x));
            # each line is recognised on its own and the break is kept, so multi-line working stays on its lines
            for m in re.finditer(r"[^\n]+|\n", piece):
                if m.group() == "\n":
                    out.append("\n")
                else:
                    _plain_piece(text, s + m.start(), s + m.end(), subject, out, spans, formula_field, unit_answer and formula_field)
