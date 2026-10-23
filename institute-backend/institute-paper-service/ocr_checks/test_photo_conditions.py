"""Real Tesseract on generated phone photos (ocr_eval/dataset.py) under each photo condition: clean, rotated, skewed, low light,
blurred, multi-page. Includes every question the first rebuild of the pipeline lost in the 90-case evaluation (clean-01 q4, clean-08
q4-q5, rotated-06 q3, rotated-07 q1, low_light-02 q5, skewed-01 q5, skewed-04 q5, skewed-06 q1, skewed-09 q1).

For each case: every question is found (none lost), and every question is either exactly right or sent to review - a wrong
question is never accepted unflagged. Run inside the paper-service image: scripts/run-real-ocr-check.sh."""
import pytest

pytest.importorskip("pytesseract")

from inst_paper.services import photo_drafts as pd  # noqa: E402
from inst_paper.services.ocr_tesseract import TesseractBackend  # noqa: E402
from ocr_eval.dataset import build, cases  # noqa: E402

SPECS = dict(cases())
REGRESSIONS = ["clean-01", "clean-08", "rotated-06", "rotated-07", "low_light-02", "skewed-01", "skewed-04", "skewed-06", "skewed-09"]
CONDITIONS = ["clean-00", "rotated-00", "rotated-01", "rotated-02", "skewed-03", "low_light-00", "blurred-00", "multi_page-00", "multi_page-01"]


def _norm(s: str) -> str:
    return " ".join((s or "").split())


# KNOWN, UNSOLVED (reported): Tesseract reads "Iron" as "lron" (skewed-03) and "zinc" as "Zinc" (multi_page-01) with ~99% word and
# character confidence, so nothing marks them uncertain. Catching such letter confusions needs a dictionary check (a new dependency,
# not approved). strict=True: these cases start failing the suite as soon as a change fixes them, so the marker cannot go stale.
KNOWN_UNFLAGGED_ERRORS = {"skewed-03": "'Iron' read as 'lron' at ~99% confidence", "multi_page-01": "'zinc' read as 'Zinc' at ~99% confidence"}


def _params():
    return [pytest.param(c, marks=pytest.mark.xfail(strict=True, reason=KNOWN_UNFLAGGED_ERRORS[c])) if c in KNOWN_UNFLAGGED_ERRORS else c
            for c in REGRESSIONS + CONDITIONS]


@pytest.mark.parametrize("case_id", _params())
def test_no_question_is_lost_and_none_is_wrong_without_a_flag(case_id):
    case = build(case_id, SPECS[case_id])
    ocr = TesseractBackend()
    pages = [ocr.read_lines(p, "image/jpeg", "page") for p in case.pages]
    key = [line for k in case.keys for line in ocr.read_lines(k, "image/jpeg", "key")]
    questions, issues = pd.build_questions(pages, key, 0.85, 500)
    found = {q["number"]: q for q in questions}
    assert sorted(found) == sorted(case.truth), (case_id, sorted(found), issues)
    for n, (stem, options, answer) in case.truth.items():
        q = found[n]
        right = _norm(q["question"]) == _norm(stem) and [_norm(q[f"option_{x}"]) for x in "abcd"] == options and q["correct"] == answer
        assert right or q["needs_review"], (case_id, n, q, stem, options, answer)
