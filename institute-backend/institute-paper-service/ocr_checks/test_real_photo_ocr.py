"""Real Tesseract on phone-style photos (run inside the paper-service image, which has the engine): scripts/run-real-ocr-check.sh.

Synthetic pages are rendered like printed papers, then degraded the way phone photos are (slight skew, noise, JPEG compression; one
page turned sideways) and must come back as the exact questions, options and answers - or be flagged, never guessed."""
import difflib
import io
import random

import pytest

pytesseract = pytest.importorskip("pytesseract")
from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: E402

from inst_paper.services import photo_drafts as pd  # noqa: E402
from inst_paper.services.ocr_tesseract import TesseractBackend  # noqa: E402

PAGES = [
    ["Default marks: 4", "Default negative: 1", "1. What is the SI unit of force?", "A) Newton", "B) Joule", "C) Watt", "D) Pascal",
     "2. Which gas do plants absorb from the air?", "A) Oxygen", "B) Carbon dioxide", "C) Nitrogen", "D) Helium"],
    ["3. Which planet is closest to the Sun?", "A) Venus", "B) Earth", "C) Mercury", "D) Mars",
     "4. What is the chemical symbol for sodium?", "A) So", "B) Na", "C) Sd", "D) Sn"],
]
KEY = ["Answer Key", "1. A", "2. B", "3. C", "4. B"]


def render(lines: list[str], skew: float = 1.5, rotate: int = 0, seed: int = 1) -> bytes:
    font = ImageFont.load_default(size=34)
    img = Image.new("L", (1400, 120 + 56 * len(lines)), 255)
    draw = ImageDraw.Draw(img)
    for i, text in enumerate(lines):
        draw.text((90, 60 + 56 * i), text, fill=0, font=font)
    img = img.rotate(skew, expand=True, fillcolor=255, resample=Image.BICUBIC)
    rnd = random.Random(seed)
    px = img.load()
    for _ in range(img.width * img.height // 6000):                     # sensor noise / dust specks
        x, y = rnd.randrange(img.width), rnd.randrange(img.height)
        px[x, y] = rnd.choice((90, 170))
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    if rotate:
        img = img.rotate(rotate, expand=True, fillcolor=255)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=80)
    return buf.getvalue()


def test_phone_style_photos_and_a_key_photo_extract_exactly():
    ocr = TesseractBackend()
    pages = [ocr.read_lines(render(PAGES[0], seed=1), "image/jpeg"), ocr.read_lines(render(PAGES[1], skew=-1.2, seed=2), "image/jpeg")]
    key = ocr.read_lines(render(KEY, seed=3), "image/jpeg")
    questions, issues = pd.build_questions(pages, key, 0.80, 500)
    expected = [("What is the SI unit of force?", ["Newton", "Joule", "Watt", "Pascal"], "A"),
                ("Which gas do plants absorb from the air?", ["Oxygen", "Carbon dioxide", "Nitrogen", "Helium"], "B"),
                ("Which planet is closest to the Sun?", ["Venus", "Earth", "Mercury", "Mars"], "C"),
                ("What is the chemical symbol for sodium?", ["So", "Na", "Sd", "Sn"], "B")]
    assert [q["number"] for q in questions] == [1, 2, 3, 4] and [q["page"] for q in questions] == [1, 1, 2, 2], (questions, issues)
    for q, (stem, options, answer) in zip(questions, expected, strict=True):
        # OCR may drop a space or misread a letter in running text (that is what the preview correction is for), but options and the
        # answer from the separate key must be exact - or the question must be flagged for a person.
        assert difflib.SequenceMatcher(None, q["question"], stem).ratio() > 0.9, (q, issues)
        exact = [q[f"option_{k}"] for k in "abcd"] == options and q["correct"] == answer
        assert exact or q["needs_review"], (q, issues)
    assert sum(1 for q, (_s, options, answer) in zip(questions, expected, strict=True) if q["correct"] == answer) >= 3, (questions, issues)


def test_a_page_photographed_sideways_is_turned_upright():
    ocr = TesseractBackend()
    lines = ocr.read_lines(render(PAGES[1], rotate=90, seed=4), "image/jpeg")
    questions, _issues = pd.build_questions([lines], None, 0.80, 500)
    assert [q["option_c"] for q in questions] == ["Mercury", "Sd"]


def test_an_unreadable_photo_is_refused_with_what_to_retake():
    from inst_paper.services.extraction import OcrUnreadable
    ocr = TesseractBackend()
    blank = io.BytesIO()
    Image.new("RGB", (900, 600), (205, 205, 205)).save(blank, "JPEG")
    with pytest.raises(OcrUnreadable, match="No writing was found"):
        ocr.read_lines(blank.getvalue(), "image/jpeg")
    blurred = Image.open(io.BytesIO(render(PAGES[0], seed=7))).filter(ImageFilter.GaussianBlur(9))
    out = io.BytesIO()
    blurred.save(out, "JPEG")
    with pytest.raises(OcrUnreadable):
        ocr.read_lines(out.getvalue(), "image/jpeg")


# ---- the reported example: question 1 photographed (upright or rotated), the answer key photographed separately
REPORTED = ["1. For a conservative force, the curl of the force is:", "A) Zero", "B) Negative", "C) 1", "D) None"]


@pytest.mark.parametrize("page_turn,key_turn", [(0, 0), (90, 0), (0, 270), (270, 90), (180, 0)])
def test_reported_example_rotated_question_and_separate_key_photo(page_turn, key_turn):
    ocr = TesseractBackend()
    page = ocr.read_lines(render(REPORTED, rotate=page_turn, seed=11), "image/jpeg", "page")
    key = ocr.read_lines(render(["Answer Key", "1. A"], rotate=key_turn, seed=12), "image/jpeg", "key")
    questions, issues = pd.build_questions([page], key, 0.85, 500)
    assert len(questions) == 1, (page, key, issues)
    q = questions[0]
    assert (q["question"], [q[f"option_{x}"] for x in "abcd"], q["correct"]) == (
        "For a conservative force, the curl of the force is:", ["Zero", "Negative", "1", "None"], "A"), (q, page, key)
    assert not any("No numbered questions" in i or "Unreadable answer-key line" in i for i in issues), issues


@pytest.mark.parametrize("first", ["(1) What is the SI unit of force?", "Q1 What is the SI unit of force?", "Q.1 What is the SI unit of force?"])
def test_other_numbering_styles_are_read(first):
    ocr = TesseractBackend()
    page = ocr.read_lines(render([first, "A) Newton", "B) Joule", "C) Watt", "D) Pascal"], seed=13), "image/jpeg")
    questions, issues = pd.build_questions([page], [["1. A", 99, 99]], 0.85, 500)
    assert [(q["number"], q["option_a"], q["correct"]) for q in questions] == [(1, "Newton", "A")], (page, issues)


def test_options_on_one_line_and_a_multi_entry_key_photo():
    ocr = TesseractBackend()
    page = ocr.read_lines(render(["1. What is the SI unit of force?", "A) Newton    B) Joule    C) Watt    D) Pascal",
                                  "2. Which gas do plants absorb from the air?", "(a) Oxygen  (b) Carbon dioxide", "(c) Nitrogen  (d) Helium"], seed=14), "image/jpeg")
    key = ocr.read_lines(render(["Answer Key", "1-A    2-B"], seed=15), "image/jpeg", "key")
    questions, issues = pd.build_questions([page], key, 0.85, 500)
    got = [([q[f"option_{x}"] for x in "abcd"], q["correct"]) for q in questions]
    assert got == [(["Newton", "Joule", "Watt", "Pascal"], "A"), (["Oxygen", "Carbon dioxide", "Nitrogen", "Helium"], "B")], (page, key, issues)


def test_a_smudged_answer_key_photo_never_gives_an_unchecked_answer():
    ocr = TesseractBackend()
    pages = [ocr.read_lines(render(PAGES[0], seed=5), "image/jpeg")]
    noisy = render(["1. A   %%  ~~", "2. B  ..  ##"], seed=6)
    questions, issues = pd.build_questions(pages, ocr.read_lines(noisy, "image/jpeg"), 0.98, 500)
    for q in questions:
        assert q["correct"] in ("", "A", "B") and (q["correct"] == "" or q["needs_review"] or not q["issues"]), (q, issues)
