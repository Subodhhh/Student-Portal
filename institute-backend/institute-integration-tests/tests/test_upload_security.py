"""Security workstream S: hardened XML parsing (defusedxml), archive and document complexity limits, isolated processing with
timeout/CPU/memory limits, and file-type checks. Legitimate CSV/XLSX/DOCX uploads keep working. All inputs are synthetic."""
import io
import time
import zipfile

import pytest

from institute_common.config import settings
from inst_paper.services import documents, extraction, isolation
from tests import isolation_helpers as helpers
from tests.test_document_formats import CT, W_NS, docx
from tests.test_paper_recognition import DOCX_MIME, HEAD, XLSX_MIME, admin, post, xlsx_rich  # noqa: F401  (fixture)
from tests.test_papers import API, setup, tmp_store  # noqa: F401  (fixtures)

LIMITS = isolation.Limits(timeout_seconds=3, memory_mb=128)


def _zip(parts: dict[str, bytes], method=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as z:
        for name, data in parts.items():
            z.writestr(name, data)
    return buf.getvalue()


def _docx_body(xml: bytes) -> bytes:
    return _zip({"[Content_Types].xml": CT.encode(), "word/document.xml": xml})


def _errors(res) -> str:
    return " ".join(e["message"] for e in res.errors)


# ---- XML attacks (defusedxml + the byte check)
BILLION_LAUGHS = (b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
                  b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>&lol2;</w:body></w:document>')
XXE = (b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
       b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>&x;</w:body></w:document>')


@pytest.mark.parametrize("payload", [BILLION_LAUGHS, XXE])
def test_dtd_and_entity_documents_are_rejected(payload):
    res = documents.extract_docx(_docx_body(payload), 10)
    assert not res.ok and "DOCTYPE" in _errors(res)


@pytest.mark.parametrize("payload", [BILLION_LAUGHS, XXE, b'<!DOCTYPE a><a/>'])
def test_the_xml_parser_itself_refuses_dtds_even_without_the_byte_check(payload):
    with pytest.raises(extraction._Reject, match="DOCTYPE|entity|external"):
        extraction._xml(payload)                                   # defense in depth: defusedxml, not only the regex in _part


def test_utf16_encoded_dtd_is_rejected():
    res = documents.extract_docx(_docx_body(BILLION_LAUGHS.decode().encode("utf-16")), 10)
    assert not res.ok


@pytest.mark.parametrize("payload", [b"<w:document><w:body>", b"not xml at all", b"<a><b></a>"])
def test_malformed_xml_is_rejected_cleanly(payload):
    res = documents.extract_docx(_docx_body(payload), 10)
    assert not res.ok and res.questions == []


# ---- archives and complexity
def test_compression_bomb_part_is_rejected():
    bomb = b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + b" " * 3_000_000 + b"</w:body></w:document>"
    data = _docx_body(bomb)
    assert len(data) < 100_000                                      # tiny upload, millions of bytes when expanded
    res = documents.extract_docx(data, 10)
    assert not res.ok and "compression bomb" in _errors(res)


def test_an_oversized_part_is_rejected():
    import os
    data = _zip({"[Content_Types].xml": CT.encode(), "word/document.xml": os.urandom(extraction.MAX_PART_BYTES + 1024)}, zipfile.ZIP_STORED)
    res = documents.extract_docx(data, 10)
    assert not res.ok and "oversized part" in _errors(res)


def test_the_total_expansion_budget_is_enforced(monkeypatch):
    wb = xlsx_rich([HEAD.strip().split(","), ["What is 2+2?", "1", "2", "3", "4", "D", 4, 1, "", ""]])
    assert extraction.extract_xlsx(wb, 10).ok                       # the same workbook is fine within the normal budget
    monkeypatch.setattr(extraction, "MAX_TOTAL_BYTES", 500)
    res = extraction.extract_xlsx(wb, 10)
    assert not res.ok and "too much data" in _errors(res)


def test_too_many_archive_members_is_rejected():
    data = _zip({"[Content_Types].xml": CT.encode(), **{f"x/{i}.txt": b"x" for i in range(150)}})
    assert not documents.extract_docx(data, 10).ok and not extraction.extract_xlsx(data, 10).ok


def test_deeply_nested_xml_is_rejected():
    deep = b"<a>" * (extraction.MAX_XML_DEPTH + 5) + b"</a>" * (extraction.MAX_XML_DEPTH + 5)
    with pytest.raises(extraction._Reject, match="nested too deeply"):
        extraction._xml(deep)


def test_too_many_elements_is_rejected(monkeypatch):
    monkeypatch.setattr(extraction, "MAX_XML_ELEMENTS", 1000)
    with pytest.raises(extraction._Reject, match="too many elements"):
        extraction._xml(b"<a>" + b"<b/>" * 1500 + b"</a>")


def test_too_many_word_runs_is_rejected():
    runs = b"<w:p>" + b"<w:r><w:t>x</w:t></w:r>" * (documents.MAX_DOCX_RUNS + 1) + b"</w:p>"
    xml = b'<?xml version="1.0"?><w:document ' + W_NS.encode() + b"><w:body>" + runs + b"</w:body></w:document>"
    res = documents.extract_docx(_zip({"[Content_Types].xml": CT.encode(), "word/document.xml": xml}, zipfile.ZIP_STORED), 10)
    assert not res.ok and "too many text runs" in _errors(res)


def test_too_many_sheet_rows_is_rejected():
    head = HEAD.strip().split(",")
    rows = [head] + [[f"Q{i}", "a", "b", "c", "d", "A", 4, 1, "", ""] for i in range(extraction.MAX_SHEET_ROWS + 1)]
    res = extraction.extract_xlsx(xlsx_rich(rows), 10_000)
    assert not res.ok and "rows" in _errors(res)


# ---- isolated processing
def test_isolated_call_returns_the_result():
    assert isolation.run_isolated(helpers.echo, {"a": [1, 2]}, limits=LIMITS) == {"a": [1, 2]}


def test_isolated_call_is_killed_at_the_timeout():
    start = time.monotonic()
    with pytest.raises(isolation.IsolationError) as err:
        isolation.run_isolated(helpers.sleep_forever, limits=isolation.Limits(timeout_seconds=1, memory_mb=128))
    assert err.value.kind == "timeout" and time.monotonic() - start < 10


def test_isolated_call_cannot_exceed_its_memory_budget():
    with pytest.raises(isolation.IsolationError) as err:
        isolation.run_isolated(helpers.allocate, 2048, limits=LIMITS)
    assert err.value.kind == "memory"


@pytest.mark.parametrize("fn, kind", [(helpers.crash, "crashed"), (helpers.recurse, "complexity")])
def test_isolated_failures_are_explicit(fn, kind):
    with pytest.raises(isolation.IsolationError) as err:
        isolation.run_isolated(fn, limits=LIMITS)
    assert err.value.kind == kind


def test_cpu_limit_stops_a_busy_loop_even_with_a_generous_wall_clock(monkeypatch):
    with pytest.raises(isolation.IsolationError) as err:
        isolation.run_isolated(helpers.burn_cpu, limits=isolation.Limits(timeout_seconds=2, memory_mb=128))
    assert err.value.kind in ("timeout", "crashed")


def test_uploads_are_processed_in_the_isolated_process(client, world, setup, admin, monkeypatch):
    calls = []
    real = isolation.run_isolated

    def spy(fn, *args, limits):
        calls.append((fn.__name__, limits))
        return real(fn, *args, limits=limits)
    monkeypatch.setattr(isolation, "run_isolated", spy)
    r = post(client, admin, world, setup["phy"], (HEAD + "What is 2+2?,1,2,3,4,D,4,1,,\n").encode())
    assert r.status_code == 201, r.text
    assert calls and calls[0][0] == "extract_and_review" and calls[0][1].timeout_seconds == settings.extraction_timeout_seconds


def test_a_processing_timeout_rejects_the_upload_explicitly(client, world, setup, admin, monkeypatch):
    monkeypatch.setattr(settings, "extraction_timeout_seconds", 0.000001)
    r = post(client, admin, world, setup["phy"], (HEAD + "What is 2+2?,1,2,3,4,D,4,1,,\n").encode())
    assert r.status_code == 422
    d = r.json()["detail"]
    assert d["code"] == "EXTRACTION_FAILED" and d["errors"][0]["code"] == "PROCESSING_TIMEOUT"
    assert "took too long" in d["errors"][0]["message"]
    preview = post(client, admin, world, setup["phy"], (HEAD + "What is 3+3?,1,2,3,6,D,4,1,,\n").encode(), preview=True)
    assert preview.status_code == 422 and preview.json()["detail"]["errors"][0]["code"] == "PROCESSING_TIMEOUT"


# ---- file types and misleading extensions
@pytest.mark.parametrize("data, name, ctype, status", [
    (b"question,option_a\n", "paper.exe", "application/octet-stream", 415),
    (b"question,option_a\n", "paper.csv", "application/x-msdownload", 415),
    (b"just text", "paper.docx", DOCX_MIME, 422),                                     # not a zip
    (b"PK\x03\x04garbage", "paper.xlsx", XLSX_MIME, 422),                              # zip signature, broken archive
    (b"%PDF-1.4 fake", "paper.docx", DOCX_MIME, 422),
    (b"a,b\x00c", "paper.csv", "text/csv", 422),                                       # binary content in a "CSV"
])
def test_unexpected_or_disguised_files_are_rejected(client, world, setup, admin, data, name, ctype, status):
    assert post(client, admin, world, setup["phy"], data, name=name, ctype=ctype).status_code == status


def test_a_workbook_renamed_to_docx_is_rejected(client, world, setup, admin):
    wb = xlsx_rich([HEAD.strip().split(","), ["What is 2+2?", "1", "2", "3", "4", "D", 4, 1, "", ""]])
    r = post(client, admin, world, setup["phy"], wb, name="paper.docx", ctype=DOCX_MIME)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "EXTRACTION_FAILED"


# ---- legitimate files still work (through the isolated path)
@pytest.mark.parametrize("fmt", ["csv", "xlsx", "docx"])
def test_legitimate_uploads_still_import(client, world, setup, admin, fmt):
    head = HEAD.strip().split(",")
    row = ["What is the SI unit of force?", "newton", "joule", "watt", "pascal", "A", 4, 1, "F = ma", "Mechanics"]
    if fmt == "csv":
        data, name, ctype = (HEAD + ",".join(map(str, row)) + "\n").encode(), "p.csv", "text/csv"
    elif fmt == "xlsx":
        data, name, ctype = xlsx_rich([head, row]), "p.xlsx", XLSX_MIME
    else:
        cells = lambda xs: "<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{x}</w:t></w:r></w:p></w:tc>" for x in xs) + "</w:tr>"
        data, name, ctype = docx("<w:tbl>" + cells(head) + cells(row) + "</w:tbl>"), "p.docx", DOCX_MIME
    r = post(client, admin, world, setup["phy"], data, name=name, ctype=ctype)
    assert r.status_code == 201, r.text
    assert r.json()["question_count"] == 1


def test_ordinary_documents_are_far_inside_the_limits():
    # a 200-question paragraph-layout document: depth, elements, runs and ratio stay well below every limit
    lines = []
    for n in range(1, 201):
        lines += [f"{n}. Question number {n} about x² + {n}?", "A) one", "B) two", "C) three", "D) four", "Answer: A", "Marks: 4",
                  "Negative: 1", f"Explanation: because ({n})."]
    data = docx("".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{x}</w:t></w:r></w:p>" for x in lines))
    res = documents.extract_docx(data, 500)
    assert res.ok and len(res.questions) == 200
