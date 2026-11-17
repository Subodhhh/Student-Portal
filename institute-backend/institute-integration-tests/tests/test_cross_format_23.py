"""Cycle 2.3, phase B3: the same logical questions through CSV, Excel rich-text runs, Word tables, Word paragraphs and native Word
equations must give equivalent recognition - compared on the independent validator's canonical structure, so brace style in the
LaTeX does not matter. Intentional differences (richer formatting that removes an ambiguity CSV cannot) are asserted separately.

Limitation: every fixture here is synthetic. No real Excel- or Word-generated file with split superscript runs was available, so
the split-run policy is NOT validated against genuine Office output by these tests."""

from inst_paper.services.math.verify import _normal, read_latex
from tests.test_document_formats import docx
from tests.test_paper_recognition import DOCX_MIME, HEAD, XLSX_MIME, admin, post, word, xlsx_rich  # noqa: F401  (fixture)
from tests.test_papers import setup, tmp_store  # noqa: F401  (fixtures)

ITEMS = [  # (CSV text, the same as Office runs, expected outcome)
    ("A force of 5 kg m s^-2 acts.", [("A force of 5 kg m s", None), ("-2", "superscript"), (" acts.", None)], "CONVERTED"),
    ("Solve x^2 + 1 = 5 now.", [("Solve x", None), ("2", "superscript"), (" + 1 = 5 now.", None)], "CONVERTED"),
    ("Speed 6 ms^-1 here.", [("Speed 6 ms", None), ("-1", "superscript"), (" here.", None)], "NEEDS_REVIEW"),
    ("Units [M L^2 T^-2] here.", [("Units [M L", None), ("2", "superscript"), (" T", None), ("-2", "superscript"), ("] here.", None)],
     "CONVERTED"),
    ("Take R = 4 kΩ here.", [("Take R = 4 kΩ here.", None)], "CONVERTED"),
]
OPTIONS = ["one", "two", "three", "four"]


def _run(text, align=None):
    rpr = f'<w:rPr><w:vertAlign w:val="{align}"/></w:rPr>' if align else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{text}</w:t></w:r>'


def _shape(question):
    f = question["fields"]["question"]
    return f["outcome"], [_normal(read_latex(s["latex"])) for s in f["spans"] if s["status"] == "converted"]


def _preview(client, world, setup, admin, data, name, ctype):
    r = post(client, admin, world, setup["phy"], data, name=name, ctype=ctype, preview=True)
    assert r.status_code == 200, (name, r.text)
    return [_shape(q) for q in r.json()["questions"]]


def test_same_content_gives_the_same_structure_in_every_format(client, world, setup, admin):
    head = HEAD.strip().split(",")
    csv = (HEAD + "".join(",".join([f'"{t}"', *OPTIONS, "A", "4", "1", "", ""]) + "\n" for t, _, _ in ITEMS)).encode()
    xlsx = xlsx_rich([head] + [[runs, *OPTIONS, "A", 4, 1, "", ""] for _, runs, _ in ITEMS])
    cells = lambda xs: "<w:tr>" + "".join(f"<w:tc><w:p>{x}</w:p></w:tc>" for x in xs) + "</w:tr>"
    table = docx("<w:tbl>" + cells([_run(h) for h in head]) + "".join(
        cells(["".join(_run(t, a) for t, a in runs), *(_run(o) for o in OPTIONS), _run("A"), _run("4"), _run("1"), _run(""), _run("")])
        for _, runs, _ in ITEMS) + "</w:tbl>")
    paras = ""
    for n, (_, runs, _) in enumerate(ITEMS, start=1):
        paras += "<w:p>" + _run(f"{n}. ") + "".join(_run(t, a) for t, a in runs) + "</w:p>"
        paras += "".join(f"<w:p>{_run(x)}</w:p>" for x in ("A) one", "B) two", "C) three", "D) four", "Answer: A", "Marks: 4", "Negative: 1"))
    results = {
        "csv": _preview(client, world, setup, admin, csv, "p.csv", "text/csv"),
        "xlsx": _preview(client, world, setup, admin, xlsx, "p.xlsx", XLSX_MIME),
        "docx-table": _preview(client, world, setup, admin, table, "t.docx", DOCX_MIME),
        "docx-paragraphs": _preview(client, world, setup, admin, word(paras), "p.docx", DOCX_MIME),
    }
    assert results["csv"] == results["xlsx"] == results["docx-table"] == results["docx-paragraphs"], results
    assert [o for o, _ in results["csv"]] == [x[2] for x in ITEMS]


def test_a_native_word_equation_has_the_same_structure_as_the_typed_form(client, world, setup, admin):
    eq = ('<m:oMath><m:sSup><m:e><m:r><m:t>x</m:t></m:r></m:e><m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup>'
          '<m:r><m:t>+1=5</m:t></m:r></m:oMath>')
    body = "<w:p>" + _run("1. Solve ") + eq + _run(" now.") + "</w:p>"
    body += "".join(f"<w:p>{_run(x)}</w:p>" for x in ("A) one", "B) two", "C) three", "D) four", "Answer: A", "Marks: 4", "Negative: 1"))
    omml = _preview(client, world, setup, admin, word(body), "e.docx", DOCX_MIME)
    csv = _preview(client, world, setup, admin, (HEAD + '"Solve x^2 + 1 = 5 now.",one,two,three,four,A,4,1,,\n').encode(), "p.csv", "text/csv")
    assert omml == csv


def test_richer_formatting_can_remove_an_ambiguity_csv_cannot():
    # intentional difference: in CSV, e^3t is ambiguous (review); as a superscript run "3t" the power is explicit (converted)
    from inst_paper.services.math import recognise
    from inst_paper.services.math.office import xlsx_string_text
    from xml.etree import ElementTree as ET
    S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    si = ET.fromstring(f'<si xmlns="{S}"><r><t>y = e</t></r><r><rPr><vertAlign val="superscript"/></rPr><t>3t</t></r></si>')
    assert recognise("y = e^3t").outcome == "NEEDS_REVIEW"
    assert recognise(xlsx_string_text(si)).text == r"\(y = e^{3t}\)"


def test_the_backend_subject_decides_molar_m_never_the_sheet(client, world, setup, admin):
    # the topic column (and the text) say Chemistry; only the subject_id of the upload decides
    csv = (HEAD + '"Chemistry: the pH of 10⁻³ M HCl is",1,2,3,4,C,4,1,,Chemistry\n').encode()
    def outcome(subject):
        r = post(client, admin, world, setup[subject], csv, preview=True)
        assert r.status_code == 200, r.text
        return r.json()["questions"][0]["fields"]["question"]
    chem, phy = outcome("chem"), outcome("phy")
    assert chem["outcome"] == "CONVERTED" and r"{10}^{-3}\,\mathrm{M}" in chem["text"]
    assert phy["outcome"] == "NEEDS_REVIEW" and phy["text"] == "Chemistry: the pH of 10⁻³ M HCl is"
