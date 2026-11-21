"""Bulk import at full scale: 100,000 questions with 2,000 pictures, read -> validate -> import, with time and memory measured.
Opt-in because it takes minutes: BULK_IMPORT_SCALE=1 [TEST_DATABASE_URL=postgresql+psycopg://...disposable...] pytest tests/test_bulk_import_scale.py -s

The workbook is written directly as OOXML (openpyxl would need gigabytes to build it), the same structure Excel produces: inline
strings, pictures anchored to the Question Image cells through a drawing, media files shared by several anchors."""
import os
import resource
import time
import zipfile
from xml.sax.saxutils import escape

import pytest

from institute_common.models.bulk_import import BulkImportJob
from institute_common.models.papers import Question, QuestionImageAsset
from tests.test_bulk_import import HEADERS, bi, store, upload, work  # noqa: F401  (fixtures)
from tests.test_question_images import png

pytestmark = pytest.mark.skipif(os.environ.get("BULK_IMPORT_SCALE") != "1", reason="opt-in scale test (BULK_IMPORT_SCALE=1)")
ROWS = 100_000
PICTURE_EVERY = 50                     # 2,000 pictures ...
DISTINCT_PICTURES = 100                # ... of 100 distinct images
NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def big_workbook(path: str) -> None:
    colours = ["#%06x" % (i * 9973 % 0xFFFFFF) for i in range(DISTINCT_PICTURES)]
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="png" ContentType="image/png"/><Default Extension="xml" ContentType="application/xml"/></Types>')
        z.writestr("xl/workbook.xml", f'<workbook {NS}><sheets><sheet name="Questions" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                   'Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/_rels/sheet1.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rIdD" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" '
                   'Target="../drawings/drawing1.xml"/></Relationships>')
        for i, colour in enumerate(colours):
            z.writestr(f"xl/media/image{i}.png", png(colour))

        def cell(col: int, row: int, text: str) -> str:
            return f'<c r="{chr(65 + col)}{row}" t="inlineStr"><is><t>{escape(text)}</t></is></c>'

        with z.open("xl/worksheets/sheet1.xml", "w") as sheet:          # streamed: never the whole sheet in memory
            sheet.write(f'<worksheet {NS}><sheetData>'.encode())
            sheet.write(("<row r=\"1\">" + "".join(cell(i, 1, h) for i, h in enumerate(HEADERS)) + "</row>").encode())
            for n in range(ROWS):
                r = n + 2
                values = {"Chapter": "Laws of Motion", "Topic": "Force", "Difficulty": ("Easy", "Medium", "Hard")[n % 3], "Question Type": "MCQ",
                          "Question Text": f"Scale question {n}: which option is correct?", "Option A Text": f"Alpha {n}",
                          "Option B Text": f"Beta {n}", "Option C Text": f"Gamma {n}", "Option D Text": f"Delta {n}", "Correct Option": "ABCD"[n % 4]}
                sheet.write(("<row r=\"%d\">" % r + "".join(cell(i, r, values[h]) for i, h in enumerate(HEADERS) if h in values)
                             + "</row>").encode())
            sheet.write(b'</sheetData><drawing r:id="rIdD"/></worksheet>')
        rels = ['<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']   # 2,000 entries: small
        with z.open("xl/drawings/drawing1.xml", "w") as drawing:
            drawing.write(b'<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
                          b'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                          b'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">')
            for k, n in enumerate(range(0, ROWS, PICTURE_EVERY)):
                rels.append(f'<Relationship Id="p{k}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                            f'Target="../media/image{k % DISTINCT_PICTURES}.png"/>')
                drawing.write(f'<xdr:oneCellAnchor><xdr:from><xdr:col>6</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>{n + 1}</xdr:row>'
                              f'<xdr:rowOff>0</xdr:rowOff></xdr:from><xdr:ext cx="1" cy="1"/><xdr:pic><xdr:nvPicPr><xdr:cNvPr id="{k + 2}" '
                              f'name="p"/><xdr:cNvPicPr/></xdr:nvPicPr><xdr:blipFill><a:blip r:embed="p{k}"/></xdr:blipFill><xdr:spPr/></xdr:pic>'
                              f'<xdr:clientData/></xdr:oneCellAnchor>'.encode())
            drawing.write(b"</xdr:wsDr>")
        z.writestr("xl/drawings/_rels/drawing1.xml.rels", "".join(rels) + "</Relationships>")


def peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024           # Linux: kilobytes


def test_one_hundred_thousand_questions(client, world, bi, db_factory, store, tmp_path, monkeypatch):  # noqa: F811
    from institute_common.config import settings
    monkeypatch.setattr(settings, "bulk_import_max_active_jobs", 10)
    path = str(tmp_path / "big.xlsx")
    big_workbook(path)
    size_mb = os.path.getsize(path) / 1024 / 1024
    with open(path, "rb") as fh:
        job_id = upload(client, world, bi, fh.read()).json()["job_id"]
    baseline = peak_rss_mb()

    started = time.monotonic()
    work(db_factory, store, rounds=5)
    preview_s = time.monotonic() - started
    after_preview = peak_rss_mb()
    from uuid import UUID
    with db_factory() as db:
        job = db.get(BulkImportJob, UUID(job_id))
        assert job.status == "PREVIEW" and job.total_rows == ROWS and job.valid_rows == ROWS, (job.status, job.file_errors, job.last_error)

    client.post(f"/api/v1/bulk-imports/{job_id}/confirm", headers=bi["h"]())
    started = time.monotonic()
    work(db_factory, store, rounds=5)
    import_s = time.monotonic() - started
    with db_factory() as db:
        job = db.get(BulkImportJob, UUID(job_id))
        assert job.status == "COMPLETED" and job.imported_rows == ROWS
        assert db.query(Question).count() == ROWS and db.query(QuestionImageAsset).count() == DISTINCT_PICTURES
    print(f"\nscale: file {size_mb:.1f} MB, {ROWS:,} questions, {ROWS // PICTURE_EVERY:,} pictures ({DISTINCT_PICTURES} distinct)\n"
          f"  read + validate: {preview_s:.0f} s ({ROWS / preview_s:,.0f} rows/s)\n"
          f"  import:          {import_s:.0f} s ({ROWS / import_s:,.0f} rows/s)\n"
          f"  peak RSS: before {baseline:.0f} MB, after read+validate {after_preview:.0f} MB, after import {peak_rss_mb():.0f} MB")
    assert after_preview - baseline < 400, "reading must not grow with the file (streaming)"
