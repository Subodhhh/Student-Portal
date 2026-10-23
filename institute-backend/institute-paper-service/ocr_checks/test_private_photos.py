"""Real phone photos supplied privately by the uploader (NOT stored in the repository). Runs only when OCR_PRIVATE_DIR points at a
folder with Question.jpeg and Answer.jpeg, e.g.:
  docker run --rm --network none -v "$PWD:/repo:ro" -v ~/ocr-private:/photos:ro -e OCR_PRIVATE_DIR=/photos ... pytest ocr_checks

The example (2026-10-22) is HANDWRITTEN and photographed sideways: question 1 "For a conservative force, the curl of the force is:"
with A) Zero, B) Negative, C) 1, D) None, and a separate key "1, A". Tesseract does not read this handwriting, so the checks here
are the safety properties: no wrong question or answer may be accepted without review, and no answer may be guessed."""
import os
from pathlib import Path

import pytest

pytest.importorskip("pytesseract")

from inst_paper.services import photo_drafts as pd  # noqa: E402
from inst_paper.services.extraction import OcrUnreadable  # noqa: E402
from inst_paper.services.ocr_tesseract import TesseractBackend  # noqa: E402

DIR = Path(os.environ.get("OCR_PRIVATE_DIR", "/nonexistent"))
pytestmark = pytest.mark.skipif(not (DIR / "Question.jpeg").exists(), reason="private photos not supplied (OCR_PRIVATE_DIR)")
EXPECTED = {1: ("For a conservative force, the curl of the force is:", ["Zero", "Negative", "1", "None"], "A")}


def _read(name, purpose):
    try:
        return TesseractBackend().read_lines((DIR / name).read_bytes(), "image/jpeg", purpose), None
    except OcrUnreadable as exc:
        return [], str(exc)


def test_the_private_example_never_yields_an_unchecked_wrong_question_or_a_guessed_answer():
    page, page_error = _read("Question.jpeg", "page")
    key, _key_error = _read("Answer.jpeg", "key")
    if page_error:                                                   # refused: the uploader is told why; nothing is extracted
        assert "could not be read" in page_error
        return
    questions, _issues = pd.build_questions([page], key or None, 0.85, 500)
    for q in questions:
        t = EXPECTED.get(q["number"])
        exact = t and (q["question"], [q[f"option_{x}"] for x in "abcd"], q["correct"]) == t
        assert exact or q["needs_review"], q
        assert q["correct"] in ("", "A") or q["needs_review"], q


def test_the_handwritten_key_is_never_turned_into_a_wrong_answer():
    key, error = _read("Answer.jpeg", "key")
    answers, _issues, uncertain = pd.ocr_answer_key(key, 500, 0.85)
    assert error or answers.get(1) in (None, "", "A") or 1 in uncertain, (key, answers)
