"""Builds the two bulk-import templates from content.py:

    python -m inst_paper.import_templates.build        (from institute-paper-service; needs openpyxl and Pillow - developer tools)

writes question_import_template.xlsx and question_import_template.docx next to this file. The files are committed and served as they
are (GET /api/v1/bulk-import-templates/{xlsx|docx}); the service itself never runs this script, so openpyxl is not a runtime
dependency. Run it again after changing content.py, and run tests/test_import_templates.py.

One template for every question type (MCQ, MSQ, NAT): each row names its own type.
Excel: a "Questions" sheet (header only, with drop-down lists), an "Instructions" sheet and an "Examples" sheet (filled in, with pictures,
rows of every type mixed). Word: instructions, then the "Your questions" table (the FIRST table: the only one imported), then an
examples table.
"""
import io
import os
import zipfile
from xml.sax.saxutils import escape

from inst_paper.import_templates import content

HERE = os.path.dirname(os.path.abspath(__file__))
XLSX_PATH = os.path.join(HERE, "question_import_template.xlsx")
DOCX_PATH = os.path.join(HERE, "question_import_template.docx")
QUESTION_ROWS = 100_001                      # rows that get the drop-down lists (header + 100,000 questions)


# ------------------------------------------------------------------ example pictures (simple drawings, made here)

def picture(name: str) -> bytes:
    """One example picture as PNG bytes: a small, clearly labelled drawing."""
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.load_default(size=18)
    small = ImageFont.load_default(size=14)
    img = Image.new("RGB", (360, 220), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 359, 219], outline="#9aa5b1")
    if name == "incline":
        d.polygon([(30, 190), (330, 190), (330, 60)], outline="#1f3a5f", width=3)
        d.polygon([(190, 128), (240, 106), (258, 146), (208, 168)], fill="#f2b134", outline="#7a5200")
        d.line([(224, 137), (196, 75)], fill="#c0392b", width=3)
        d.text((150, 45), "N ?", font=font, fill="#c0392b")
        d.text((40, 196), "Block on an inclined plane", font=small, fill="#333")
    elif name == "whole_question":
        lines = ["Q. A 2 kg body is pushed with 10 N.", "    Its acceleration is:", "",
                 "(A) 2 m/s^2     (B) 20 m/s^2", "(C) 5 m/s^2     (D) 0.2 m/s^2"]
        for i, line in enumerate(lines):
            _text_with_squares(d, (20, 25 + i * 34), line, font, small)
    else:                                    # velocity-time graphs for the picture options
        d.line([(40, 180), (330, 180)], fill="#333", width=2)
        d.line([(40, 180), (40, 30)], fill="#333", width=2)
        d.text((300, 186), "t", font=small, fill="#333")
        d.text((18, 28), "v", font=small, fill="#333")
        shape = {"graph_flat": [(40, 100), (320, 100)], "graph_rising": [(40, 175), (320, 45)],
                 "graph_falling": [(40, 45), (320, 175)]}.get(name)
        if shape:
            d.line(shape, fill="#1f6feb", width=4)
        else:
            d.line([(40 + x, 175 - (x * x) // 620) for x in range(0, 281, 10)], fill="#1f6feb", width=4)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def _text_with_squares(d, xy, line: str, font, small) -> None:
    """Draw `line`, writing each "^2" as a small raised 2 (the built-in font has no ² character)."""
    x, y = xy
    for n, part in enumerate(line.split("^2")):
        if n:
            d.text((x, y - 6), "2", font=small, fill="#111")
            x += d.textlength("2", font=small)
        d.text((x, y), part, font=font, fill="#111")
        x += d.textlength(part, font=font)


# ------------------------------------------------------------------ Excel

def build_xlsx(examples_on_questions_sheet: bool = False) -> bytes:
    """The Excel template. `examples_on_questions_sheet` (tests only) also writes the examples on the Questions sheet, so the real
    importer can be run on exactly the rows and pictures the template shows."""
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    header_font, header_fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F3A5F")
    wrap = Alignment(wrap_text=True, vertical="top")
    wb = Workbook()

    def header(ws, titles):
        ws.append(titles)
        for cell in ws[1]:
            cell.font, cell.fill, cell.alignment = header_font, header_fill, wrap
        ws.freeze_panes = "A2"
        for i, title in enumerate(titles, start=1):
            ws.column_dimensions[get_column_letter(i)].width = 28 if title.endswith("Image") or title == "Question Text" else 16

    def examples(ws, first_row: int):
        """Write the example rows from `first_row`, each picture placed over its image cell."""
        for offset, example in enumerate(content.EXAMPLES):
            row = first_row + offset
            for i, title in enumerate(content.TITLES, start=1):
                if title.endswith("Image"):
                    if example.get(title):
                        img = XLImage(io.BytesIO(picture(example[title])))
                        img.width, img.height = 180, 110
                        img.anchor = f"{get_column_letter(i)}{row}"
                        ws.add_image(img)
                else:
                    ws.cell(row=row, column=i, value=example.get(title) or None).alignment = wrap
            ws.row_dimensions[row].height = 90 if any(t.endswith("Image") and example.get(t) for t in content.TITLES) else 45

    questions = wb.active
    questions.title = "Questions"
    header(questions, content.TITLES)
    # Drop-down lists: they help typing; the importer still checks every value itself.
    for title, choices in (("Difficulty", content.DIFFICULTY_CHOICES), ("Question Type", content.QUESTION_TYPE_CHOICES),
                           ("Tolerance Type", content.TOLERANCE_TYPE_CHOICES), ("Answer Format", content.ANSWER_FORMAT_CHOICES)):
        letter = get_column_letter(content.TITLES.index(title) + 1)
        rule = DataValidation(type="list", formula1='"' + ",".join(choices) + '"', allow_blank=True, showErrorMessage=True,
                              errorTitle=title, error=f"Choose {', '.join(choices)}.", showInputMessage=True, promptTitle=title,
                              prompt=dict((c[0], c[2]) for c in content.COLUMNS)[title][:255])
        rule.add(f"{letter}2:{letter}{QUESTION_ROWS}")
        questions.add_data_validation(rule)
    if examples_on_questions_sheet:
        examples(questions, 2)

    guide = wb.create_sheet("Instructions")
    guide.column_dimensions["A"].width, guide.column_dimensions["B"].width = 20, 34
    guide.column_dimensions["C"].width, guide.column_dimensions["D"].width = 80, 34
    guide.append(["Question import template - how to fill it in"])
    guide["A1"].font = Font(bold=True, size=14)
    guide.append([content.EXCEL_NOTE])
    guide.append([])
    guide.append(["Column", "Required?", "What to enter", "Example"])
    for cell in guide[4]:
        cell.font, cell.fill = header_font, header_fill
    for title, required, help_text, example in content.COLUMNS:
        guide.append([title, required, help_text, example])
    guide.append([])
    guide.append(["Adding pictures in Excel"])
    guide.cell(row=guide.max_row, column=1).font = Font(bold=True, size=12)
    for n, step in enumerate(content.EXCEL_PICTURE_STEPS, start=1):
        guide.append([f"Step {n}", "", step])
    guide.append([])
    guide.append(["Rules"])
    guide.cell(row=guide.max_row, column=1).font = Font(bold=True, size=12)
    for rule in content.GENERAL_RULES:
        guide.append(["•", "", rule])
    for row in guide.iter_rows(min_row=5):
        for cell in row:
            cell.alignment = wrap

    shown = wb.create_sheet("Examples")
    header(shown, content.TITLES)
    examples(shown, 2)
    note_row = len(content.EXAMPLES) + 3
    shown.cell(row=note_row, column=1, value="These rows are examples only and are never imported. "
                                             "Write your questions on the Questions sheet.").font = Font(italic=True)
    for offset, example in enumerate(content.EXAMPLES):
        shown.cell(row=note_row + 1 + offset, column=1, value=f"Row {offset + 2}: {example['kind']}")

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# ------------------------------------------------------------------ Word (written as plain OOXML: no extra library needed)

W_NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"')
EMU_PER_PIXEL = 9525


class _Docx:
    """A tiny writer for the parts of a .docx the template needs: paragraphs, tables and inline pictures."""

    def __init__(self):
        self.body: list[str] = []
        self.media: dict[str, bytes] = {}         # relationship id -> png

    @staticmethod
    def run(text: str, bold=False, size=None, italic=False) -> str:
        props = ("<w:b/>" if bold else "") + ("<w:i/>" if italic else "") + (f'<w:sz w:val="{size}"/>' if size else "")
        return f'<w:r><w:rPr>{props}</w:rPr><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'

    def paragraph(self, text: str = "", bold=False, size=None, italic=False) -> None:
        self.body.append(f"<w:p>{self.run(text, bold, size, italic)}</w:p>")

    def picture_run(self, png: bytes, width=150, height=92) -> str:
        rid = f"rIdImg{len(self.media) + 1}"
        n = len(self.media) + 1
        self.media[rid] = png
        cx, cy = width * EMU_PER_PIXEL, height * EMU_PER_PIXEL
        return ('<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
                f'<wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="{n}" name="Picture {n}"/>'
                '<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>'
                '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic>'
                f'<pic:nvPicPr><pic:cNvPr id="{n}" name="example{n}.png"/><pic:cNvPicPr/></pic:nvPicPr>'
                f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
                f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
                '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic>'
                '</wp:inline></w:drawing></w:r>')

    def table(self, rows: list[list[str]], header: bool = True) -> None:
        """rows of cell XML runs; the first row is repeated on every page and shaded."""
        width = 15700 // len(rows[0])                # the landscape text width shared by the columns
        grid = "".join(f'<w:gridCol w:w="{width}"/>' for _ in rows[0])
        borders = "".join(f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="9AA5B1"/>'
                          for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
        xml = (f'<w:tbl><w:tblPr><w:tblW w:w="5000" w:type="pct"/><w:tblBorders>{borders}</w:tblBorders>'
               f'<w:tblLayout w:type="fixed"/></w:tblPr><w:tblGrid>{grid}</w:tblGrid>')
        for i, cells in enumerate(rows):
            head = i == 0 and header
            xml += "<w:tr>" + ("<w:trPr><w:tblHeader/></w:trPr>" if head else "")
            for runs in cells:
                shade = '<w:shd w:val="clear" w:color="auto" w:fill="DCE6F2"/>' if head else ""
                xml += f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shade}</w:tcPr><w:p>{runs}</w:p></w:tc>'
            xml += "</w:tr>"
        self.body.append(xml + "</w:tbl>")

    def save(self) -> bytes:
        # A4 landscape with narrow margins: the question table needs the width
        section = ('<w:sectPr><w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/>'
                   '<w:pgMar w:top="720" w:right="567" w:bottom="720" w:left="567" w:header="360" w:footer="360" w:gutter="0"/></w:sectPr>')
        document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {W_NS}><w:body>'
                    + "".join(self.body) + section + "</w:body></w:document>")
        rels = "".join(f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                       f'Target="media/{rid}.png"/>' for rid in self.media)
        files = {
            "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                                   '<Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>'
                                   '<Override PartName="/word/document.xml" '
                                   'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
            "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                           '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                           '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                           'Target="word/document.xml"/></Relationships>',
            "word/document.xml": document,
            "word/_rels/document.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                                            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                            + rels + "</Relationships>",
        }
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for name, text in files.items():
                z.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), text)       # fixed dates: same input, same file
            for rid, png in self.media.items():
                z.writestr(zipfile.ZipInfo(f"word/media/{rid}.png", date_time=(2026, 1, 1, 0, 0, 0)), png)
        return out.getvalue()


def build_docx(examples_in_first_table: bool = False) -> bytes:
    """The Word template. `examples_in_first_table` (tests only) puts the examples in the imported table."""
    doc = _Docx()
    small = 14                                   # 7 pt in the tables: the Word columns on one landscape page
    doc.paragraph("Question import template", bold=True, size=32)
    doc.paragraph("Type one question per row in the first table (Your questions): MCQ, MSQ and NAT questions in any order, each with "
                  "its type in Question Type. Everything else in this document is help and is not imported.")
    doc.paragraph("What to enter in each column", bold=True, size=24)
    for title, required, help_text, _example in content.COLUMNS:
        doc.body.append(f"<w:p>{doc.run(title + ' (' + required.lower() + '): ', bold=True)}{doc.run(help_text)}</w:p>")
    doc.paragraph("The table has the columns most questions need. For an MSQ with more than four options, or a NAT with a range, add "
                  "the Option E-H, Minimum, Maximum or Decimal Places columns to the table (any template column is accepted).")
    doc.paragraph("Adding pictures in Word", bold=True, size=24)
    for n, step in enumerate(content.WORD_PICTURE_STEPS, start=1):
        doc.paragraph(f"{n}. {step}")
    doc.paragraph("Rules", bold=True, size=24)
    for rule in content.GENERAL_RULES:
        doc.paragraph(f"• {rule}")

    def header_row():
        return [doc.run(t, bold=True, size=small) for t in content.WORD_TITLES]

    def example_rows():
        rows = []
        for example in content.EXAMPLES:
            cells = []
            for title in content.WORD_TITLES:
                if title.endswith("Image") and example.get(title):
                    cells.append(doc.picture_run(picture(example[title]), width=110, height=68))
                elif title.endswith("Image"):
                    cells.append("")
                else:
                    cells.append(doc.run(example.get(title, ""), size=small))
            rows.append(cells)
        return rows

    doc.paragraph("Your questions", bold=True, size=24)
    blank = [doc.run("", size=small) for _ in content.WORD_TITLES]
    doc.table([header_row()] + (example_rows() if examples_in_first_table else [blank, blank, blank]))
    doc.paragraph("")
    doc.paragraph("Examples (not imported): " + ", ".join(e["kind"] for e in content.EXAMPLES), bold=True, size=24)
    doc.table([header_row()] + example_rows())
    return doc.save()


if __name__ == "__main__":
    for path, data in ((XLSX_PATH, build_xlsx()), (DOCX_PATH, build_docx())):
        with open(path, "wb") as fh:
            fh.write(data)
        print(f"wrote {path} ({len(data) // 1024} KB)")
