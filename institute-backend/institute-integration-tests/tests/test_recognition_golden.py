"""Golden corpus (recognition_golden.json): every mathematical meaning written in each representation a provider may paste - plain and
Unicode text, ^ notation, LaTeX, Excel rich-text superscript/subscript runs, Word text runs, native Word equations - goes through the
real extraction adapter for its format (CSV text as is, extract_xlsx, extract_docx) and then the one recognition step every import uses.
Each must give the expected structure (the independent validator's canonical reading, so brace spelling does not matter), or - for a
genuinely ambiguous source - the expected doubt, left as typed, with the expected readings typeset among those offered."""
import json
from pathlib import Path

import pytest

from inst_paper.services import documents, extraction
from inst_paper.services.math import split_latex
from inst_paper.services.math.verify import _normal, read_latex
from inst_paper.services.recognition import _field
from tests.test_paper_recognition import xlsx_rich, word

CORPUS = json.loads((Path(__file__).parent / "recognition_golden.json").read_text(encoding="utf-8"))["entries"]
HEAD = ["question", "option_a", "option_b", "option_c", "option_d", "correct", "marks", "negative_marks", "explanation", "topic"]
CASES = [(e, fmt) for e in CORPUS for fmt in e["sources"]]


def _canon(latex: str):
    return _normal(read_latex(latex))


def _row(entry, cell) -> list:
    """A question row with the value under test in the question or in option A (the other options are distinct plain words)."""
    if entry["field"] == "option":
        return ["Which is it?", cell, "pear", "plum", "fig", "A", "4", "1", "", ""]
    return [cell, "pear", "plum", "fig", "kiwi", "A", "4", "1", "", ""]


def _docx_run(text: str, align: str | None) -> str:
    rpr = f'<w:rPr><w:vertAlign w:val="{align}"/></w:rPr>' if align else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{text}</w:t></w:r>'


def _extracted(entry, fmt: str) -> str:
    """The field's text as the import pipeline sees it after the format's own extraction."""
    src = entry["sources"][fmt]
    if fmt == "xlsx_runs":
        res = extraction.extract_xlsx(xlsx_rich([HEAD, _row(entry, [tuple(r) for r in src])]), 10)
    elif fmt in ("docx_runs", "omml"):
        cell = "".join(_docx_run(t, a) for t, a in src) if fmt == "docx_runs" else f"<m:oMath>{src}</m:oMath>"
        cells = lambda xs: "<w:tr>" + "".join(f"<w:tc><w:p>{x}</w:p></w:tc>" for x in xs) + "</w:tr>"
        row = [cell if isinstance(v, list) else _docx_run(v, None) for v in _row(entry, [])]
        res = documents.extract_docx(word("<w:tbl>" + cells([_docx_run(h, None) for h in HEAD]) + cells(row) + "</w:tbl>"), 10)
    else:
        return src                                                          # a CSV cell: the text exactly as pasted
    assert not res.errors and res.questions, (fmt, res.errors)
    q = res.questions[0]
    return q.options["A"] if entry["field"] == "option" else q.stem


def _maths(text: str) -> list[str]:
    return [text[a:b] for kind, a, b in split_latex(text) if kind == "latex"]


@pytest.mark.parametrize("entry, fmt", CASES, ids=[f"{e['id']}[{fmt}]" for e, fmt in CASES])
def test_every_representation_gives_the_expected_mathematics(entry, fmt):
    text = _extracted(entry, fmt)
    got = _field(text, entry["domain"], "option_a" if entry["field"] == "option" else "question")
    want = entry["expect"]
    assert got["outcome"] == want["outcome"], (fmt, text, got["outcome"], got["reasons"], got["text"])
    if want["outcome"] == "CONVERTED":
        found = [s["latex"] for s in got["spans"] if s["status"] in ("converted", "latex") and s["latex"]]
        assert len(found) == 1 and _canon(found[0]) == _canon(want["latex"]), (fmt, text, got["text"], found)
    if want["outcome"] == "NEEDS_REVIEW":
        assert got["reasons"] == want["reasons"], (fmt, got["reasons"])
        assert got["text"] == text.strip() or text in got["text"] or all(s["original"] in got["text"] for s in got["spans"]
                                                                          if s["status"] == "flagged")     # the doubt is left as typed
        offered = [_canon(m[2:-2]) for s in got["suggestions"] for m in _maths(s["text"])]
        for reading in want["readings"]:
            assert _canon(reading) in offered, (fmt, reading, [s["text"] for s in got["suggestions"]])
        for s in got["suggestions"]:                                          # every reading offered is itself accepted when chosen
            assert _field(s["text"], entry["domain"], "option_a" if entry["field"] == "option" else "question")["outcome"] == "CONVERTED"


def test_equivalent_representations_agree_with_each_other():
    """The acceptance criterion directly: for each meaning, every source representation gives one and the same structure."""
    for entry in CORPUS:
        if entry["expect"]["outcome"] != "CONVERTED":
            continue
        shapes = {}
        for fmt in entry["sources"]:
            got = _field(_extracted(entry, fmt), entry["domain"], "option_a" if entry["field"] == "option" else "question")
            shapes[fmt] = [_canon(s["latex"]) for s in got["spans"] if s["status"] in ("converted", "latex") and s["latex"]]
        assert len({json.dumps(v, sort_keys=True, default=str) for v in shapes.values()}) == 1, (entry["id"], shapes)
        assert len(entry["sources"]) >= 2 or entry["id"] in (), entry["id"]
