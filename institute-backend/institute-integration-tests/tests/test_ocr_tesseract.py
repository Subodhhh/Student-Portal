"""Tesseract OCR backend (runs where the tesseract binary exists, e.g. inside the paper service image; skipped elsewhere):

    docker run --rm -v $PWD/backend:/src -w /src/institute-integration-tests -e PYTHONPATH=/src/institute-paper-service \
        institute-paper-service:latest sh -c "pip install -q pytest && python -m pytest -q tests/test_ocr_tesseract.py -p no:cacheprovider --noconftest"
"""
import io
import shutil

import pytest

pytestmark = pytest.mark.skipif(shutil.which("tesseract") is None, reason="needs the tesseract binary")

LINES = ["Default marks: 4", "Default negative: 1", "", "1. What is the SI unit of force?", "A) Newton", "B) Joule", "C) Watt", "D) Pascal",
         "Answer: A", "", "2. Which quantity is a vector?", "A) Mass", "B) Speed", "C) Velocity", "D) Time", "Answer: C"]


def page(lines, blur: float = 0.0, scale: float = 1.0) -> bytes:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    font = ImageFont.load_default(size=34)
    img = Image.new("L", (1400, 60 + 52 * len(lines)), 255)
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((60, 30 + 52 * i), line, fill=0, font=font)
    if scale != 1.0:
        img = img.resize((int(img.width * scale), int(img.height * scale))).resize(img.size)
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def extract(data, threshold=0.85):
    from inst_paper.services import extraction
    from inst_paper.services.ocr_tesseract import TesseractBackend
    return extraction.extract_image(data, "image/png", TesseractBackend(), 500, threshold)


def test_a_clean_printed_page_is_extracted_exactly():
    r = extract(page(LINES))
    assert r.ok, r.errors
    q1, q2 = r.questions
    assert (q1.stem, q1.options["A"], q1.correct, str(q1.marks), str(q1.negative)) == ("What is the SI unit of force?", "Newton", "A", "4", "1")
    assert (q2.correct, q2.options["C"]) == ("C", "Velocity")


def test_a_degraded_scan_is_rejected_not_guessed():
    r = extract(page(LINES, blur=2.2, scale=0.35))
    assert not r.ok and r.questions == []


def test_the_confidence_bar_is_enforced():
    r = extract(page(LINES), threshold=0.999)       # no OCR reading is that certain: the whole paper is rejected
    assert not r.ok and any("confidence" in e["message"] for e in r.errors)


def test_a_numbering_gap_rejects_the_page():
    gap = LINES[:9] + ["", "3. Which quantity is a vector?"] + LINES[11:]
    r = extract(page(gap))
    assert not r.ok and any("may be missing" in e["message"] for e in r.errors)
