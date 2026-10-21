"""Photo papers: turn OCR'd page photos and a separately uploaded answer key into an editable preview, and back into rows for the shared
validator. Pure functions (no database, no OCR engine), so every rule is unit-testable.

Nothing is guessed:
  * the page text is parsed with the same numbered-block layout as Word/PDF papers (services/documents.py); structural problems become
    issues on the question they concern instead of rejecting the draft, so the uploader can correct them in the preview;
  * a question whose OCR confidence is below the threshold, or that has any issue, is marked `needs_review` and must be explicitly
    checked by a person before the paper can be accepted;
  * the answer key is matched to questions by number; a missing, unreadable or contradicting answer leaves the answer EMPTY with an
    issue (never a guessed answer), and keys for questions that do not exist are reported;
  * a question number that appears again (overlapping photos) or two near-identical questions are flagged, never silently merged or
    deleted;
  * a question marked as needing its diagram cannot be accepted (diagrams are not delivered to students yet), so nothing is dropped.
"""
import difflib
import re

from inst_paper.services.documents import _FIELD, _Q, parse_question_blocks
from inst_paper.services.extraction import _KEY_LINE, REQUIRED, ExtractionResult, parse_answer_key

FIELDS = ["question", "option_a", "option_b", "option_c", "option_d", "correct", "marks", "negative_marks", "explanation", "topic"]
CRITICAL = {"answer", "ans", "correct answer", "correct", "marks", "mark", "negative marks", "negative mark", "negative", "neg"}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


_OCR_KEY = re.compile(r"^\s*\(?\s*(?:q(?:uestion)?\s*\.?\s*)?(\d{1,4})\s*\)?\s*[.):;,\-=]?\s*\(?\s*([A-Da-d])\s*\)?\s*[.,;]?\s*$")


_NOISY_KEY = re.compile(r"^\s*\(?\s*(\d{1,4})\s*\)?\s*[.):;,\-=]?\s*(.*)$")


def ocr_answer_key(lines: list[list], max_questions: int, min_confidence: float = 0.98) -> tuple[dict[int, str], list[str], set[int]]:
    """A PHOTOGRAPHED answer key, line by line -> (answers by number, issues, numbers whose answer is UNCERTAIN).

    A clean 'number answer' line read confidently is used as is. A line that starts with a number and contains exactly one capital
    A-D among otherwise stray marks ("2B. wy oe"), or that was read with low confidence, gives that answer but marks it uncertain: its
    question must be checked by a person before the paper can be accepted. Any other line with a digit is reported and gives no
    answer. One smudged line never discards the readable ones, and no answer is ever accepted unseen.
    Typed .csv/.txt keys go through the strict extraction.parse_answer_key instead."""
    key: dict[int, str] = {}
    issues: list[str] = []
    uncertain: set[int] = set()
    for text, weakest, *_ in lines:
        clean = re.sub(r"[|\[\]{}_~`'\"]+", " ", text).strip()
        m = _OCR_KEY.match(clean) or _KEY_LINE.match(clean)
        noisy = False
        if not m:
            nm = _NOISY_KEY.match(clean)
            letters = re.findall(r"(?<![A-Za-z])([A-D])(?![A-Za-z])", nm.group(2)) if nm else []
            if nm and len(letters) == 1:
                m, noisy = (nm.group(1), letters[0]), True
        if m:
            n, answer = (int(m[0]), m[1].upper()) if noisy else (int(m.group(1)), m.group(2).upper())
            if noisy or weakest < min_confidence * 100:
                uncertain.add(n)
            if n > max_questions:
                issues.append(f"Answer-key line for question {n}: more questions than allowed.")
            elif n in key and key[n] != answer:
                issues.append(f"The answer key gives two answers for question {n} ({key[n]} and {answer}): enter the correct one.")
                key[n] = ""
            else:
                key[n] = answer
        elif re.search(r"\d", clean):
            issues.append(f"Unreadable answer-key line: \"{text[:40]}\". Enter that answer by hand.")
    return {n: a for n, a in key.items()}, issues, uncertain


def build_questions(pages: list[list[list]], key_lines: list[list] | None, min_confidence: float, max_questions: int,
                    key_is_text: bool = False) -> tuple[list[dict], list[str]]:
    """Pages (in order) of OCR lines [text, min_conf, mean_conf] -> (questions for the preview, draft-level issues)."""
    lines: list[tuple[str, float, float, int]] = [(t, mn, mean, page) for page, page_lines in enumerate(pages, start=1) for t, mn, mean in page_lines]
    res = ExtractionResult()
    rows = parse_question_blocks("\n".join(t for t, *_ in lines), res)
    draft_issues: list[str] = []
    per_q: dict[int, list[str]] = {}
    for e in res.errors:
        if e.get("row"):
            per_q.setdefault(int(e["row"]), []).append(e["message"])
        else:
            draft_issues.append(e["message"])

    # Attribute lines to questions with the parser's own numbering rule, to know each question's page and confidence.
    page_of: dict[int, int] = {}
    word_conf: dict[int, list[float]] = {}
    critical: dict[int, float] = {}
    current, expected = None, 1
    for text, mn, mean, page in lines:
        q = _Q.match(text)
        if q and int(q.group(1)) >= expected:
            current, expected = int(q.group(1)), int(q.group(1)) + 1
            page_of[current] = page
        elif q and current is not None and int(q.group(1)) < expected and len(q.group(2).split()) >= 3:
            per_q.setdefault(current, []).append(f"Question number {q.group(1)} appears again inside question {current}: overlapping photos? "
                                                 "Check that no text from another question was merged here.")
        if current is None:
            continue
        word_conf.setdefault(current, []).append(mean)
        f = _FIELD.match(text)
        if f and f.group(1).lower() in CRITICAL:
            critical[current] = min(critical.get(current, 100.0), mn)

    # The separate answer key, matched by question number.
    key = None
    explanations: dict[int, str] = {}
    uncertain_answers: set[int] = set()
    if key_lines and not key_is_text:
        key, key_issues, uncertain_answers = ocr_answer_key(key_lines, max_questions, min_confidence)
        draft_issues += key_issues
        numbers = {n for n, _ in rows}
        extra = sorted(n for n in key if n not in numbers)
        if extra:
            draft_issues.append(f"The answer key has answers for questions that were not found in the paper: {', '.join(map(str, extra[:20]))}.")
        low = [t for t, mn, *_ in key_lines if mn < min_confidence * 100 and re.search(r"\d", t)]
        if low:
            draft_issues.append(f"{len(low)} answer-key line(s) were read with low confidence: check every answer in the preview.")
    elif key_lines:
        mapping, key_errors = parse_answer_key("\n".join(t for t, *_ in key_lines).encode(), max_questions)
        if key_errors:
            draft_issues += [f"Answer key: {e['message']}" for e in key_errors]
        elif mapping.keyed_by_id:
            draft_issues.append("Answer key: photo papers are matched by question number; write the key as 'number,answer' lines (1,B).")
        else:
            key = {n: answer for n, (answer, _explanation) in mapping.by_number.items()}
            explanations = {n: expl for n, (_answer, expl) in mapping.by_number.items() if expl}
            numbers = {n for n, _ in rows}
            extra = sorted(n for n in key if n not in numbers)
            if extra:
                draft_issues.append(f"The answer key has answers for questions that were not found in the paper: {', '.join(map(str, extra[:20]))}.")
            low = [t for t, mn, *_ in key_lines if mn < min_confidence * 100 and re.search(r"\d", t)]
            if low:
                draft_issues.append(f"{len(low)} answer-key line(s) were read with low confidence: check every answer in the preview.")

    questions = []
    seen: list[tuple[int, str]] = []
    for number, row in rows:
        row = row or {}
        issues = list(per_q.get(number, []))
        inline = (row.get("correct") or "").strip().upper()
        correct = inline
        if key is not None:
            from_key = (key.get(number) or "").strip().upper()
            if number in key and not from_key:
                issues.append("The answer key is contradictory for this question.")
            if from_key and inline and from_key != inline:
                correct = ""
                issues.append(f"The paper says the answer is {inline} but the answer key says {from_key}: choose the correct answer.")
            elif from_key:
                correct = from_key
                if number in uncertain_answers:
                    issues.append(f"The answer {from_key} was read from an unclear answer-key line: check it.")
            elif not inline:
                issues.append("The answer key has no answer for this question.")
        elif not inline:
            issues.append("No answer was found for this question: upload the answer key or enter the answer.")
        for opt in "abcd":
            if not (row.get(f"option_{opt}") or "").strip():
                issues.append(f"Option {opt.upper()} could not be read.")
        if not (row.get("question") or "").strip():
            issues.append("The question text could not be read.")
        stem = _norm(row.get("question", ""))
        for other_no, other in seen:
            if stem and (stem == other or (len(stem) > 30 and difflib.SequenceMatcher(None, stem, other).ratio() > 0.92)):
                issues.append(f"Looks the same as question {other_no} (overlapping or repeated photo?). Remove one if it is a duplicate.")
                break
        seen.append((number, stem))
        confs = word_conf.get(number) or [0.0]
        confidence = max(0.0, min(sum(confs) / len(confs), critical.get(number, 100.0))) / 100.0
        q = {k: (row.get(k) or "").strip() for k in FIELDS}
        if not q["explanation"] and explanations.get(number):
            q["explanation"] = explanations[number]
        q.update(number=number, correct=correct, confidence=round(confidence, 3), page=page_of.get(number), issues=issues,
                 needs_review=bool(issues) or confidence < min_confidence, reviewed=False, has_diagram=False)
        questions.append(q)
    if not questions:
        draft_issues.append("No numbered questions were found. Photograph each page flat and well lit, with question numbers like '1.' and options like 'A)'.")
    return questions, draft_issues


def rows_for_validation(questions: list[dict]) -> list[tuple[int, dict]]:
    """Corrected preview -> rows for extraction.validate_paper (numbered by their order in the preview)."""
    return [(pos, {k: (q.get(k) or "") for k in REQUIRED + ["explanation", "topic"]}) for pos, q in enumerate(questions, start=1)]


def corrections(before: list[dict], after: list[dict], actor: str, at: str) -> list[dict]:
    """The field-level changes between two versions of the preview (by question number), for the correction log."""
    old = {q["number"]: q for q in before}
    out = []
    for q in after:
        prev = old.get(q["number"])
        if prev is None:
            out.append({"at": at, "by": actor, "number": q["number"], "field": "*", "from": None, "to": "added"})
            continue
        for k in FIELDS + ["reviewed", "has_diagram"]:
            if (prev.get(k) or "") != (q.get(k) or ""):
                out.append({"at": at, "by": actor, "number": q["number"], "field": k, "from": prev.get(k), "to": q.get(k)})
    for n in old.keys() - {q["number"] for q in after}:
        out.append({"at": at, "by": actor, "number": n, "field": "*", "from": "present", "to": "removed"})
    if [q["number"] for q in after] != [q["number"] for q in before if q["number"] in {a["number"] for a in after}]:
        out.append({"at": at, "by": actor, "number": None, "field": "order", "from": [q["number"] for q in before], "to": [q["number"] for q in after]})
    return out
