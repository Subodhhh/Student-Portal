"""A question paper, or its answer key, as a PDF for authorized staff: a structured header (provider, branch, Global Test, paper, subject,
totals), the subject-wise marking scheme, then every question with its options and marks. The question paper never contains correct
answers or explanations: its input carries none (routes/papers._pdf_papers builds it without them). The answer key adds each question's
correct option and explanation and is marked ANSWER KEY on every page. No internal ids. English text; symbols use DejaVu Sans (installed
in the paper service image), and without it the PDF falls back to Helvetica where characters outside Latin-1 become '?'.
Rendered with ReportLab (BSD)."""
import io
import os
import re
import zipfile
from datetime import datetime
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FONT_DIR = os.environ.get("PDF_FONT_DIR", "/usr/share/fonts/truetype/dejavu")
IST = ZoneInfo("Asia/Kolkata")
MARGIN = 18 * mm


def _fonts() -> tuple[str, str, bool]:
    """(regular, bold, unicode): DejaVu Sans when installed, registered once per process; else Helvetica."""
    regular, bold = os.path.join(FONT_DIR, "DejaVuSans.ttf"), os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")
    if not (os.path.exists(regular) and os.path.exists(bold)):
        return "Helvetica", "Helvetica-Bold", False
    if "Body" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("Body", regular))
        pdfmetrics.registerFont(TTFont("Body-Bold", bold))
    return "Body", "Body-Bold", True


def _clean(text) -> str:
    """Printable text only (a PDF cannot show control characters); line breaks kept, a tab becomes a space."""
    text = "" if text is None else str(text).replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    return "".join(ch for ch in text if ch == "\n" or ch.isprintable())


def _num(value) -> str:
    return format(value.normalize(), "f") if hasattr(value, "normalize") else str(value)


def plus_marks(value) -> str:
    return f"+{_num(value)}"


def minus_marks(value) -> str:
    """A deduction: '−1', and '0' when nothing is deducted (never '−0')."""
    return f"−{_num(value)}" if value else "0"


def _span(values: tuple, sign) -> str:
    low, high = values
    return sign(low) if low == high else f"{sign(low)} to {sign(high)}"


class _Writer:
    def __init__(self):
        self.regular, self.bold, self.unicode = _fonts()

    def t(self, s) -> str:
        """Text for a Paragraph: cleaned, Latin-1 only without the Unicode font, markup characters escaped, line breaks kept."""
        s = _clean(s)
        if not self.unicode:
            s = s.encode("latin-1", "replace").decode("latin-1")
        return escape(s).replace("\n", "<br/>")

    def style(self, size=11, bold=False, indent=0, leading=None, color=colors.black, after=0):
        return ParagraphStyle("p", fontName=self.bold if bold else self.regular, fontSize=size, leading=leading or size * 1.35,
                              leftIndent=indent, textColor=color, spaceAfter=after)

    def p(self, text, **style) -> Paragraph:
        return Paragraph(self.t(text), self.style(**style))


def _numbered(footer: str, font: str):
    """A canvas that writes 'Page N of M' and the footer once the page count is known."""
    class NumberedCanvas(rl_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pages = []

        def showPage(self):          # noqa: N802 (ReportLab API)
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            for state in self._pages:
                self.__dict__.update(state)
                self.setFont(font, 8)
                self.setFillColor(colors.Color(110 / 255, 110 / 255, 110 / 255))
                self.drawString(MARGIN, 10 * mm, footer)
                self.drawRightString(A4[0] - MARGIN, 10 * mm, f"Page {self._pageNumber} of {len(self._pages)}")
                super().showPage()
            super().save()
    return NumberedCanvas


def _header(w: _Writer, paper: dict, answer_key: bool, width: float) -> list:
    out = []
    if answer_key:
        banner = Table([[Paragraph("ANSWER KEY", ParagraphStyle("b", fontName=w.bold, fontSize=14, leading=18, textColor=colors.white,
                                                              alignment=1))]], colWidths=[width])
        banner.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.Color(178 / 255, 34 / 255, 34 / 255)),
                                    ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        out += [banner, Spacer(1, 2 * mm)]
    out += [w.p(paper["title"], size=16, bold=True, after=2 * mm)]
    rows = [("Provider", paper["provider"]), ("Branch", paper.get("branch"))]
    rows += [("Global Test", name) for name in paper.get("global_tests", [])]
    rows += [("Paper", f"{paper['title']} (version {paper['version_no']})"), ("Subject", paper["subject"]),
             ("Exam / class", " · ".join(x for x in (paper.get("exam_type"), paper.get("target_class")) if x)),
             ("Total questions", str(paper["total_questions"])), ("Total marks", _num(paper["total_marks"]))]
    facts = [[w.p(label, size=9.5, bold=True), w.p(value, size=9.5)] for label, value in rows if value]   # a field that does not apply is left out
    table = Table(facts, colWidths=[38 * mm, width - 38 * mm])
    table.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0.5),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 0.5), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    out += [table, Spacer(1, 3 * mm), w.p("Marking scheme", size=11, bold=True, after=1 * mm)]
    head = ["Subject", "Questions", "Max marks", "Correct", "Incorrect", "Unanswered"]
    body = [[m["subject"], str(m["questions"]), _num(m["max_marks"]), _span(m["correct"], plus_marks), _span(m["incorrect"], minus_marks), "0"]
            for m in paper["marking"]]
    grid = Table([[w.p(h, size=8.5, bold=True) for h in head]] + [[w.p(v, size=8.5) for v in row] for row in body],
                 colWidths=[width * x / 174 for x in (40, 22, 24, 30, 30, 28)])
    grid.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.Color(160 / 255, 160 / 255, 160 / 255)),
                              ("BACKGROUND", (0, 0), (-1, 0), colors.Color(238 / 255, 238 / 255, 238 / 255)),
                              ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    out.append(grid)
    if any(m["correct"][0] != m["correct"][1] or m["incorrect"][0] != m["incorrect"][1] for m in paper["marking"]):
        out.append(w.p("A range means the marks vary by question; each question shows its own marks.", size=8.5))
    out += [w.p(note, size=8.5) for note in paper.get("marking_notes", [])]
    out.append(Spacer(1, 2 * mm))
    if not answer_key:
        out.append(w.p("Instructions", size=11, bold=True, after=1 * mm))
        out += [w.p(f"•  {line}", size=9.5, indent=2 * mm) for line in (
            "Each question has four options (A)–(D); choose the one correct option.",
            "Marks for a correct and an incorrect answer are given with each question and in the marking scheme above.",
            "An unanswered question scores 0.")]
    out.append(w.p(f"Downloaded {datetime.now(IST).strftime('%d %b %Y, %H:%M')} IST", size=8, after=1 * mm))
    rule = Table([[""]], colWidths=[width], rowHeights=[1])
    rule.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.Color(180 / 255, 180 / 255, 180 / 255))]))
    return out + [rule, Spacer(1, 4 * mm)]


def render(paper: dict, answer_key: bool = False) -> bytes:
    """paper (routes/papers._pdf_papers): title, version_no, subject, provider, branch (None for a platform paper), global_tests,
    exam_type, target_class, total_questions, total_marks, marking [{subject, questions, max_marks, correct (low, high), incorrect
    (low, high)}], marking_notes and questions [{stem, options [A, B, C, D], marks_positive, marks_negative}] in paper order; with
    answer_key, each question also has answer and explanation."""
    w = _Writer()
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=16 * mm, bottomMargin=18 * mm,
                            title=_clean(paper["title"]), author="", creator="", subject="")
    width = doc.width - 12              # the frame's own 6 pt padding on each side: tables line up with the paragraphs
    story = _header(w, paper, answer_key, width)
    grey = colors.Color(90 / 255, 90 / 255, 90 / 255)
    for n, q in enumerate(paper["questions"], start=1):
        block = [w.p(f"{n}. {q['stem']}", bold=True, after=0.5 * mm)]
        for letter, option in zip("ABCD", q["options"], strict=True):
            correct = answer_key and q["answer"] == letter
            block.append(w.p(f"({letter}) {option}{'   ✓ correct' if correct else ''}", bold=correct, indent=6 * mm))
        block.append(w.p(f"Marks: {plus_marks(q['marks_positive'])} / {minus_marks(q['marks_negative'])}", size=9, indent=6 * mm, color=grey))
        if answer_key:
            block.append(w.p(f"Answer: ({q['answer']})", size=10, bold=True, indent=6 * mm))
            if q.get("explanation"):
                block.append(w.p(f"Explanation: {q['explanation']}", size=9.5, indent=6 * mm))
        story += [KeepTogether(block), Spacer(1, 3 * mm)]       # a question stays on one page where it fits
    kind = "Answer key" if answer_key else "Question paper"
    footer = _clean(f"{'ANSWER KEY · ' if answer_key else ''}{paper['title']} · v{paper['version_no']} · {kind}")
    doc.build(story, canvasmaker=_numbered(footer if w.unicode else footer.encode("latin-1", "replace").decode("latin-1"), w.regular))
    return out.getvalue()


def filename(paper: dict, answer_key: bool = False) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", paper["title"]).strip("-")[:80] or "paper"
    return f"{slug}-v{paper['version_no']}{'-answer-key' if answer_key else ''}.pdf"


def bundle(papers: list[dict], answer_key: bool = False) -> bytes:
    """One PDF per paper in a ZIP; two papers with the same title and version get distinct names."""
    out, used = io.BytesIO(), set()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for paper in papers:
            name, i = filename(paper, answer_key), 2
            while name in used:
                name, i = filename(paper, answer_key).replace(".pdf", f"-{i}.pdf"), i + 1
            used.add(name)
            z.writestr(name, render(paper, answer_key))
    return out.getvalue()
