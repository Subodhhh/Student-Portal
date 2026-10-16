"""Word (.docx) and PDF paper extraction: same all-or-nothing validator as CSV/XLSX, hostile files rejected, nothing guessed."""
import io
import zipfile

from inst_paper.services import documents

W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
CT = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
      '<Default Extension="xml" ContentType="application/xml"/></Types>')


def _p(text: str) -> str:
    return f"<w:p><w:r><w:t xml:space=\"preserve\">{text}</w:t></w:r></w:p>"


def docx(body_xml: str, extra: dict[str, bytes] | None = None, rels: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", CT)
        z.writestr("word/document.xml", f'<?xml version="1.0" encoding="UTF-8"?><w:document {W_NS}><w:body>{body_xml}</w:body></w:document>')
        if rels is not None:
            z.writestr("word/_rels/document.xml.rels", rels)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


BLOCKS = [
    "Physics unit test", "Default marks: 4", "Default negative: 1",
    "1. What is the SI unit of force?", "A) Newton", "B) Joule", "C) Watt", "D) Pascal", "Answer: A", "Explanation: F = ma",
    "2. Which quantity is a vector?", "A) Mass", "B) Speed", "C) Velocity", "D) Time", "Answer: C", "Marks: 2", "Negative: 0.5",
]


def test_docx_numbered_blocks_with_paper_defaults():
    r = documents.extract_docx(docx("".join(_p(x) for x in BLOCKS)), 500)
    assert r.ok, r.errors
    q1, q2 = r.questions
    assert (q1.stem, q1.correct, str(q1.marks), str(q1.negative), q1.explanation) == ("What is the SI unit of force?", "A", "4", "1", "F = ma")
    assert (q2.correct, str(q2.marks), str(q2.negative), q2.options["C"]) == ("C", "2", "0.5", "Velocity")


def test_docx_multiline_question_text_is_kept():
    lines = ["Default marks: 4", "Default negative: 1", "1. A body moves", "with constant velocity. Its acceleration is",
             "A) zero", "B) positive", "C) negative", "D) infinite", "Answer: A"]
    r = documents.extract_docx(docx("".join(_p(x) for x in lines)), 500)
    assert r.ok and r.questions[0].stem == "A body moves with constant velocity. Its acceleration is"


def test_docx_table_with_csv_headers():
    head = ["question", "option_a", "option_b", "option_c", "option_d", "correct", "marks", "negative_marks"]
    rows = [head, ["2+2?", "1", "2", "3", "4", "D", "4", "1"], ["3*3?", "6", "9", "12", "3", "B", "4", "1"]]
    tbl = "<w:tbl>" + "".join("<w:tr>" + "".join(f"<w:tc>{_p(c)}</w:tc>" for c in r) + "</w:tr>" for r in rows) + "</w:tbl>"
    r = documents.extract_docx(docx(tbl), 500)
    assert r.ok and [q.correct for q in r.questions] == ["D", "B"]


def test_one_incomplete_question_rejects_the_whole_document():
    broken = BLOCKS[:-9] + ["2. Which quantity is a vector?", "A) Mass", "B) Speed", "D) Time", "Answer: C"]   # option C missing
    r = documents.extract_docx(docx("".join(_p(x) for x in broken)), 500)
    assert not r.ok and r.questions == []
    assert any(e["row"] == 2 and e["field"] == "option_c" for e in r.errors)


def test_a_numbering_gap_is_reported_as_a_missing_question():
    gap = ["Default marks: 4", "Default negative: 0", "1. Q one", "A) a", "B) b", "C) c", "D) d", "Answer: A",
           "3. Q three", "A) a", "B) b", "C) c", "D) d", "Answer: B"]
    r = documents.extract_docx(docx("".join(_p(x) for x in gap)), 500)
    assert not r.ok and "may be missing" in r.errors[0]["message"]


def test_missing_marks_without_a_default_is_not_guessed():
    r = documents.extract_docx(docx("".join(_p(x) for x in ["1. Q", "A) a", "B) b", "C) c", "D) d", "Answer: A"])), 500)
    assert not r.ok and any(e["field"] == "marks" for e in r.errors)


def test_hostile_docx_variants_are_rejected():
    body = "".join(_p(x) for x in BLOCKS)
    macro = documents.extract_docx(docx(body, {"word/vbaProject.bin": b"x"}), 500)
    assert not macro.ok and "macros" in macro.errors[0]["message"]
    external = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                'Target="http://evil.example/x.png" TargetMode="External"/></Relationships>')
    assert not documents.extract_docx(docx(body, rels=external), 500).ok
    bomb = f'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><w:document {W_NS}><w:body>{_p("&a;")}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", bomb)
    assert not documents.extract_docx(buf.getvalue(), 500).ok
    assert not documents.extract_docx(b"not a zip", 500).ok


def pdf(lines: list[str] | None) -> bytes:
    """A minimal valid PDF with one text page (or no text at all when lines is None)."""
    if lines is None:
        content = b"0 0 m 10 10 l S"
    else:
        ops = ["BT", "/F1 11 Tf", "14 TL", "50 780 Td"]
        for ln in lines:
            ops.append("(" + ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") Tj T*")
        ops.append("ET")
        content = "\n".join(ops).encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def test_text_pdf_is_extracted():
    r = documents.extract_pdf(pdf(BLOCKS), 500)
    assert r.ok, r.errors
    assert [q.correct for q in r.questions] == ["A", "C"] and r.questions[0].options["A"] == "Newton"


def test_scanned_pdf_without_text_is_rejected_with_guidance():
    r = documents.extract_pdf(pdf(None), 500)
    assert not r.ok and "no text layer" in r.errors[0]["message"]


def test_not_a_pdf():
    assert not documents.extract_pdf(b"hello", 500).ok


def test_docx_upload_through_the_api(client, world, make_staff, login, db_factory):
    from institute_common.core.roles import Role
    from institute_common.models.academics import ProviderSubject, Subject
    with db_factory() as db:
        s = Subject(code="PHYDOC", name="Physics")
        db.add(s)
        db.flush()
        db.add(ProviderSubject(provider_id=world["p1"], subject_id=s.subject_id))
        db.commit()
        sid = s.subject_id
    _, code = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    h = login(code)
    files = {"file": ("paper.docx", docx("".join(_p(x) for x in BLOCKS)),
                      "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
    r = client.post(f"/api/v1/branches/{world['p1b1']}/subjects/{sid}/papers/upload", data={"title": "Doc paper"}, files=files, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["question_count"] == 2 and r.json()["status"] == "VALIDATED"
    files = {"file": ("paper.pdf", pdf(None), "application/pdf")}
    r = client.post(f"/api/v1/branches/{world['p1b1']}/subjects/{sid}/papers/upload", data={"title": "Scan"}, files=files, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "EXTRACTION_FAILED"
