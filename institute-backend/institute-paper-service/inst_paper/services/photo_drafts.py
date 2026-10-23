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

from inst_paper.services.documents import _FIELD, _OPT, _Q, parse_question_blocks
from inst_paper.services.extraction import _KEY_LINE, REQUIRED, ExtractionResult, parse_answer_key

FIELDS = ["question", "option_a", "option_b", "option_c", "option_d", "correct", "marks", "negative_marks", "explanation", "topic"]
CRITICAL = {"answer", "ans", "correct answer", "correct", "marks", "mark", "negative marks", "negative mark", "negative", "neg"}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


_OCR_KEY = re.compile(r"^\s*\(?\s*(?:q(?:uestion)?\s*\.?\s*)?(\d{1,4})\s*\)?\s*[.):;,\-=]?\s*\(?\s*([A-Ea-e])\s*\)?\s*[.,;]?\s*$", re.IGNORECASE)


_NOISY_KEY = re.compile(r"^\s*\(?\s*(\d{1,4})\s*\)?\s*[.):;,\-=]?\s*(.*)$")
# One entry of a key written several to a line ("1-A 2-C 3.B", "1) A, 2) D"); a line counts as such only if nothing else is on it.
_KEY_ENTRY = re.compile(r"(?<![\w])(?:q\s*\.?\s*)?(\d{1,4})\s*[.):\-=]?\s*\(?([A-Ea-e])\)?(?![\w])", re.IGNORECASE)


def _key_entries(text: str) -> list[tuple[int, str]]:
    """'1-A 2-C 3-B' -> [(1, 'A'), (2, 'C'), (3, 'B')] when the line holds two or more entries and nothing else; else []."""
    found = list(_KEY_ENTRY.finditer(text))
    if len(found) < 2 or re.sub(r"[\s,;|/]+", "", _KEY_ENTRY.sub("", text)):
        return []
    return [(int(m.group(1)), m.group(2).upper()) for m in found]


def ocr_answer_key(lines: list[list], max_questions: int, min_confidence: float = 0.98, sources: dict[int, str] | None = None,
                   order: list[tuple[int, str, str]] | None = None) -> tuple[dict[int, str], list[str], set[int]]:
    """A PHOTOGRAPHED answer key, line by line -> (answers by number, issues, numbers whose answer is UNCERTAIN).

    A clean 'number answer' line read confidently is used as is; so is a line holding several such entries and nothing else. A line
    that starts with a number and contains exactly one capital A-D among otherwise stray marks ("2B. wy oe"), or that was read with
    low confidence, gives that answer but marks it uncertain: its question must be checked by a person before the paper can be
    accepted. Any other line with a digit is reported and gives no answer. One smudged line never discards the readable ones, and no
    answer is ever accepted unseen. Answers are matched to questions by their number only, never by position. `sources` (optional)
    receives the key line each answer was read from; `order` (optional) every entry (number, answer, line) in reading order. Typed .csv/.txt keys go through the strict extraction.parse_answer_key instead."""
    key: dict[int, str] = {}
    issues: list[str] = []
    uncertain: set[int] = set()
    for text, weakest, *_ in lines:
        clean = re.sub(r"[|\[\]{}_~`'\"]+", " ", text).strip()
        entries: list[tuple[int, str, bool]] = []
        m = _OCR_KEY.match(clean) or _KEY_LINE.match(clean)
        if m:
            entries = [(int(m.group(1)), m.group(2).upper(), False)]
        else:
            entries = [(n, a, False) for n, a in _key_entries(clean)]
        if not entries:
            nm = _NOISY_KEY.match(clean)
            letters = re.findall(r"(?<![A-Za-z])([A-D])(?![A-Za-z])", nm.group(2)) if nm else []
            if nm and len(letters) == 1:
                entries = [(int(nm.group(1)), letters[0], True)]
        if not entries:
            if re.search(r"\d", clean):
                issues.append(f"Unreadable answer-key line: \"{text[:40]}\". Enter that answer by hand.")
            continue
        for n, answer, noisy in entries:
            if order is not None:
                order.append((n, answer, text))
            if noisy or weakest < min_confidence * 100:
                uncertain.add(n)
            if n > max_questions:
                issues.append(f"Answer-key line for question {n}: more questions than allowed.")
            elif answer not in "ABCD":
                issues.append(f"The answer key gives {answer} for question {n}, but papers support options A to D: enter the answer.")
                key[n] = ""
            elif n in key and key[n] != answer:
                issues.append(f"The answer key gives two answers for question {n} ({key[n]} and {answer}): enter the correct one.")
                key[n] = ""
            else:
                key[n] = answer
                if sources is not None:
                    sources[n] = text
    return dict(key), issues, uncertain


# ------------------------------------------------------------------ page lines as photographed -> the numbered-block layout
# The Word/PDF parser (documents.parse_question_blocks) accepts "1." / "1)" / "1:" and one option per line. Photographed papers use more
# styles, so their OCR lines are first rewritten into that layout. Only the MARKERS are rewritten; the text is never changed.
_NUMBER_STYLE = re.compile(r"^\s*(?:\(\s*(\d{1,4})\s*\)|q(?:uestion)?\s*\.?\s*(\d{1,4})\s*[.):\-]?|(\d{1,4})\s*\])\s*(?=\S)(.*)$", re.IGNORECASE)
_MARKER_GLYPH = re.compile(r"^\s*(\(?)(?:©|€|([A-Ea-e])\2)(\s*[).:])", re.IGNORECASE)        # "©) 1" / "Cc) 1": OCR glyphs for "C)"
_INLINE_OPT = re.compile(r"(?:^|(?<=\s))([^\sA-Za-z(]?)\(?([A-Ea-e©€])\s*[).]\s*")
_OPTION_E = re.compile(r"^\s*\(?[Ee]\s*[).:]\s*(.*)$")


def _fix_marker(line: str) -> str:
    m = _MARKER_GLYPH.match(line)
    if not m:
        return line
    letter = (m.group(2) or "C").upper()
    return f"{m.group(1)}{letter}{m.group(3)}{line[m.end():]}"


def _split_options(line: str) -> list[str]:
    """'A) Zero  B) Negative  C) 1  D) None' -> one line per option, only when the line starts with an option marker and its markers
    are consecutive letters (A, B, C ...), so a stray 'b)' inside a sentence never splits it."""
    found = list(_INLINE_OPT.finditer(line))
    if len(found) < 2 or found[0].start() != 0 or found[0].group(1):
        return [line]
    letters = [{"©": "C", "€": "C"}.get(m.group(2), m.group(2).upper()) for m in found]
    cases = {m.group(2).isupper() for m in found if m.group(2).isalpha()}
    if any(ord(b) - ord(a) != 1 for a, b in zip(letters, letters[1:], strict=False)) or len(cases) > 1:
        return [line]
    # a stray mark read just before a marker ("Newton 8B) Joule") is not part of either option; its line goes to review (see caller)
    starts = [m.start() + len(m.group(1)) for m in found]
    ends = [m.start() for m in found[1:]] + [len(line)]
    return [_fix_marker(line[s:e].strip()) for s, e in zip(starts, ends, strict=True)]


def normalise_page_lines(lines: list[list]) -> list[list]:
    """OCR lines of a page -> the same lines in the numbered-block layout: '(1) ...', 'Q1 ...', 'Q.1 ...', '1] ...' become '1. ...';
    an option marker read as '©)' or 'Cc)' becomes 'C)'; a leading '|' (the sheet's edge) is dropped; options written on one line
    are split, each keeping the line's confidence."""
    out = []
    for text, *conf in lines:
        text = re.sub(r"^\s*\|+\s*", "", text)                    # a sliver of the sheet's edge read as "|" before the text
        m = _NUMBER_STYLE.match(text)
        if m and not _Q.match(text):
            text = f"{m.group(1) or m.group(2) or m.group(3)}. {m.group(4)}"
        text = _fix_marker(text)
        parts = _split_options(text)
        if len(parts) > 1 and len("".join(parts).replace(" ", "")) < len(text.replace(" ", "")) and conf:
            conf = [min(c, 50.0) for c in conf[:2]] + conf[2:]   # a stray mark between options was dropped: a person checks the line
        out += [[part, *conf] for part in parts]
    return out


_LOOSE_NUMBER = re.compile(r"^\s*(\d{1,4})\s*(?:[,;]\s*|\s+)(?=[A-Za-z(\"'])(.*)$")


def _misread_number(lines: list[tuple], i: int, expected: int, current: int | None, last_option: str | None,
                    inferred: dict[int, str], key_numbers: set[int] | None = None) -> tuple | None:
    """A question-number marker damaged by OCR, recognised only from the surrounding structure; returns the line rewritten as
    'N. text', or None. Two cases:
      * '4, The power ...' / '5 Which ...': the number read, its '.' read as ',' or lost; accepted only when it is exactly the next
        expected number (a number inside a sentence never matches it);
      * 'A) Which vitamin ...' where the '4' of '4)' was read as 'A': accepted only right after a question that already has its
        options A-D, when the next line is an option A;
      * '9. ...' where 5 is expected: taken as 5 only when the next numbered line is 6, or - for the last question - when the answer
        key has an answer for 5 and none for 9 (otherwise one misread digit would swallow every following question).
    Every recovered question is sent to review with an explanation."""
    text = lines[i][0]
    q = _Q.match(text)
    if q and int(q.group(1)) > expected and current is not None:
        later = next((int(m.group(1)) for line in lines[i + 1:] if (m := _Q.match(line[0]))), None)
        continues = later == expected + 1                                     # 4 ... "9" ... 6: the "9" was a 5
        last_by_key = (later is None and key_numbers is not None and expected in key_numbers and int(q.group(1)) not in key_numbers
                       and _confusable(int(q.group(1)), expected))
        if continues or last_by_key:
            inferred[expected] = (f"The question number was read as {q.group(1)}; it was taken as question {expected} because "
                                  + ("the next question is " + str(expected + 1) if continues else "the answer key has a question "
                                     + str(expected) + " but no question " + q.group(1)) + ". Check the number.")
            return (f"{expected}. {q.group(2)}", *lines[i][1:])
    if q and int(q.group(1)) >= expected:
        return None
    m = _LOOSE_NUMBER.match(text)
    if m and int(m.group(1)) == expected and not _OPT.match(text):
        return (f"{expected}. {m.group(2)}", *lines[i][1:])
    o = _OPT.match(text)
    nxt = _OPT.match(lines[i + 1][0]) if i + 1 < len(lines) else None
    if current is not None and last_option == "D" and o and nxt and nxt.group(1).upper() == "A" and len(o.group(2).split()) >= 3:
        inferred[expected] = (f"The question number was read as \u201c{text[:3].strip()}\u201d; this was taken as question {expected} because "
                              f"question {current} already has options A to D. Check the number and the text.")
        return (f"{expected}. {o.group(2)}", *lines[i][1:])
    return None


# Mathematical notation is where Tesseract's confidence is least trustworthy (x² read as "x?" or "x2", θ as "9" or "@", 2⁵ as "25",
# all with high confidence). A question whose text shows any of these signs is always sent to review; nothing is rewritten.
_MATH_SIGNS = re.compile(
    r"[Ͱ-Ͽ⁰-₟²³¹°µ∀-⋿√←-⇿±×÷=@*]"   # Greek, super/subscripts, math symbols (a typed '^' is read reliably and is not flagged)
    r"|(?<![A-Za-z])[A-Za-z][?°º](?=[\s/)+\-,=]|$)"          # a single-letter variable followed by '?' or a degree sign: x?  3x?
    r"|(?<![A-Za-z])[a-z]\d+\b"                              # a variable glued to digits: x2  x3/3
    r"|\b(?:sin|cos|tan|cot|sec|cosec|log|ln|lim|exp)\b"
)


def looks_mathematical(text: str) -> bool:
    return bool(_MATH_SIGNS.search(text or ""))


# Digit pairs Tesseract confuses in printed numbers (seen in the evaluation: 5->9, 5->3); anything else is never "repaired".
_CONFUSABLE = {frozenset(p) for p in ("59", "53", "56", "58", "38", "39", "68", "08", "09", "17", "14", "49", "27")}


def _confusable(read: int, intended: int) -> bool:
    a, b = str(read), str(intended)
    diff = [(x, y) for x, y in zip(a, b, strict=False) if x != y]
    return len(a) == len(b) and len(diff) == 1 and frozenset(diff[0]) in _CONFUSABLE


def _repair_duplicate_key_number(order: list[tuple[int, str, str]], numbers: set[int], key: dict[int, str]):
    """When the key names one question twice with different answers and exactly one question has no entry, and the second (or
    first) occurrence stands exactly where that question belongs and the numbers differ by a commonly confused digit ("3) (B)"
    ... "3) (A)" where the second is 5): ((read, its answer, line), (intended, its answer, line)); otherwise None."""
    nums = [n for n, _a, _t in order]
    dups = {n for n in nums if nums.count(n) == 2}
    missing = [n for n in numbers if n not in nums]
    if len(dups) != 1 or len(missing) != 1:
        return None
    d, m = dups.pop(), missing[0]
    if not _confusable(d, m) or key.get(d) not in ("", None):
        return None
    first, second = [i for i, n in enumerate(nums) if n == d]
    for stray, kept in ((second, first), (first, second)):
        rest = [n for i, n in enumerate(nums) if i != stray]
        placed = rest[:stray] + [m] + rest[stray:]
        if all(a < b for a, b in zip(placed, placed[1:], strict=False)):
            return order[kept], (m, order[stray][1], order[stray][2])
    return None


def _repair_key_number(key: dict[int, str], numbers: set[int]) -> tuple[int, int] | None:
    """(misread, intended) when exactly one key entry names a question that does not exist, exactly one question has no key entry,
    the two numbers differ by one commonly confused digit, and the stray entry stands in the key exactly where that question belongs (between its neighbours, keys being in order) - the
    "5" of "5. A" read as "9" or "3". Otherwise None. The answer is then marked uncertain, so a person checks it."""
    extra = [n for n in key if n not in numbers]
    missing = [n for n in numbers if n not in key]
    if len(extra) != 1 or len(missing) != 1 or not _confusable(extra[0], missing[0]):
        return None
    order = list(key)                                   # the key's lines in reading order
    i = order.index(extra[0])
    before = order[i - 1] if i > 0 else None
    after = order[i + 1] if i + 1 < len(order) else None
    if (before is None or before < missing[0]) and (after is None or missing[0] < after) and all(
            a < b for a, b in zip([n for n in order if n != extra[0]], [n for n in order if n != extra[0]][1:], strict=False)):
        return extra[0], missing[0]
    return None


def build_questions(pages: list[list[list]], key_lines: list[list] | None, min_confidence: float, max_questions: int,
                    key_is_text: bool = False) -> tuple[list[dict], list[str]]:
    """Pages (in order) of OCR lines [text, min_conf, mean_conf] -> (questions for the preview, draft-level issues)."""
    lines: list[tuple[str, float, float, int, list]] = [(t, mn, mean, page, (rest[0] if rest else []) or [])
                                                        for page, page_lines in enumerate(pages, start=1)
                                                        for t, mn, mean, *rest in normalise_page_lines(page_lines)]
    # Option E lines are taken out (the parser would append them to option D) and reported on their question: papers have options A-D.
    option_e: dict[int, list[str]] = {}
    inferred: dict[int, str] = {}
    key_numbers = None                                     # question numbers the answer key mentions: evidence for misread numbers
    if key_lines and not key_is_text:
        key_numbers = set(ocr_answer_key(key_lines, max_questions, min_confidence)[0])
    elif key_lines:
        key_numbers = set(parse_answer_key("\n".join(t for t, *_ in key_lines).encode(), max_questions)[0].by_number)
    kept, current, expected, last_option = [], None, 1, None
    for i, line in enumerate(lines):
        line = _misread_number(lines, i, expected, current, last_option, inferred, key_numbers) or line
        q = _Q.match(line[0])
        if q and int(q.group(1)) >= expected:
            current, expected, last_option = int(q.group(1)), int(q.group(1)) + 1, None
        e = _OPTION_E.match(line[0]) if current is not None and last_option == "D" else None    # only right after option D
        if e:
            option_e.setdefault(current, []).append(e.group(1))
            last_option = "E"
            continue
        o = _OPT.match(line[0])
        if o:
            last_option = o.group(1).upper()
        kept.append(line)
    lines = kept
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
    source: dict[int, list[dict]] = {}
    word_conf: dict[int, list[float]] = {}
    critical: dict[int, float] = {}
    current, expected = None, 1
    math: dict[int, bool] = {}
    for text, mn, mean, page, flags in lines:
        q = _Q.match(text)
        if q and int(q.group(1)) >= expected:
            current, expected = int(q.group(1)), int(q.group(1)) + 1
            page_of[current] = page
        elif q and current is not None and int(q.group(1)) < expected and len(q.group(2).split()) >= 3:
            per_q.setdefault(current, []).append(f"Question number {q.group(1)} appears again inside question {current}: overlapping photos? "
                                                 "Check that no text from another question was merged here.")
        if current is None:
            continue
        source.setdefault(current, []).append({"page": page, "text": text, "confidence": round(mn)})
        if "script" in flags or looks_mathematical(text):
            math[current] = True
        word_conf.setdefault(current, []).append(mean)
        f = _FIELD.match(text)
        if f and f.group(1).lower() in CRITICAL:
            critical[current] = min(critical.get(current, 100.0), mn)

    # The separate answer key, matched by question number.
    key = None
    explanations: dict[int, str] = {}
    uncertain_answers: set[int] = set()
    key_source: dict[int, str] = {}
    if key_lines and not key_is_text:
        key_order: list[tuple[int, str, str]] = []
        key, key_issues, uncertain_answers = ocr_answer_key(key_lines, max_questions, min_confidence, key_source, key_order)
        numbers = {n for n, _ in rows}
        dup = _repair_duplicate_key_number(key_order, numbers, key)
        if dup:
            (n_read, a_kept, t_kept), (n_meant, a_moved, t_moved) = dup
            key[n_read], key[n_meant] = a_kept, a_moved
            key_source[n_read], key_source[n_meant] = t_kept, t_moved
            uncertain_answers |= {n_read, n_meant}
            key_issues = [i for i in key_issues if f"two answers for question {n_read} " not in i]
            draft_issues.append(f"The answer key has question {n_read} twice and no question {n_meant}; the entry in question {n_meant}'s place "
                                f"was taken as question {n_meant}. Check both answers.")
        draft_issues += key_issues
        repaired = _repair_key_number(key, numbers)
        if repaired:
            wrong, right = repaired
            key[right] = key.pop(wrong)
            key_source[right] = key_source.pop(wrong, None)
            uncertain_answers.add(right)
            draft_issues.append(f"An answer-key line was read as question {wrong}, which is not in the paper; it was taken as question {right} "
                                "(the only question without an answer, in that place in the key). Check that answer.")
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
        issues = list(per_q.get(number, [])) + ([inferred[number]] if number in inferred else [])
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
        if math.get(number):
            issues.append("This question contains mathematical notation (powers, subscripts, symbols such as \u221a, \u03b8, \u03a9, \u00b0) "
                          "that OCR often misreads: compare every symbol with the photo.")
        for text in option_e.get(number, []):
            issues.append(f"An option E (\u201c{text[:60]}\u201d) was found, but papers have four options (A to D): edit the question so it has four.")
        if not (row.get("question") or "").strip():
            issues.append("The question text could not be read.")
        stem = _norm(row.get("question", ""))
        for other_no, other in seen:
            if stem and (stem == other or (len(stem) > 30 and difflib.SequenceMatcher(None, stem, other).ratio() > 0.92)):
                issues.append(f"Looks the same as question {other_no} (overlapping or repeated photo?). Remove one if it is a duplicate.")
                break
        seen.append((number, stem))
        confs = word_conf.get(number) or [0.0]
        # A heuristic score, not a calibrated probability: the weakest of the question's lines (its stem, each option, its scoring
        # lines), so one badly read option is enough to send the question to review.
        confidence = max(0.0, min(min(confs), critical.get(number, 100.0))) / 100.0
        q = {k: (row.get(k) or "").strip() for k in FIELDS}
        if not q["explanation"] and explanations.get(number):
            q["explanation"] = explanations[number]
        q.update(number=number, correct=correct, confidence=round(confidence, 3), page=page_of.get(number), issues=issues,
                 needs_review=bool(issues) or confidence < min_confidence, reviewed=False, has_diagram=False,
                 source=source.get(number, [])[:40], answer_source=key_source.get(number))
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
