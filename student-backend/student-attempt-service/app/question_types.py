"""Question types: the ONE rulebook for MCQ, MSQ and NAT (docs/institute-portal/QUESTION_TYPES_ARCHITECTURE.md).

Pure standard library on purpose: the Student services (a separate codebase) carry an identical copy of this file
(student-backend/*/app/question_types.py), and both test suites run the same golden vectors (contracts/question-types/golden.json),
so the Institute and the Student side can never disagree about what a valid question, a valid answer or a score is.

    MCQ_SINGLE  one correct option of A-D (the existing columns; key = the letter)
    MCQ_MULTI   2-8 options A-H, one or more correct; scored ALL-OR-NOTHING: exactly the correct set = full marks, anything else 0,
                never negative (decisions D5 / R5)
    NUMERICAL   a number; correct when it matches the key exactly, within +/- a tolerance, or inside [min, max] (inclusive); wrong
                answers take the test's negative marks (D6). Numbers: at most 12 significant digits and 6 decimal places, optional
                leading "-", no exponent, no thousands separators, no NaN/Infinity, exact Decimal arithmetic (R4)

Shapes (stored on the immutable question version, delivered to the Student test service, never shown to a student before review):
    MCQ_MULTI  content    {"options": [{"id": "A", "text": "...", "image": "<picture ref or null>"}, ...]}
               answer_key {"correct": ["A", "C"]}
    NUMERICAL  content    {"format": "INTEGER" | "DECIMAL", "decimals": 0-6, "unit": "m/s" | null}
               answer_key {"value": "12.5", "tolerance": {"kind": "EXACT"} | {"kind": "ABS", "amount": "0.05"}
                                                    | {"kind": "RANGE", "min": "12.4", "max": "12.6"}}
Student answers (attempt_answers.selected_answer, canonical text): MCQ "B"; MSQ sorted letters "AC"; NAT canonical number "-12.5".
"""
import re
from decimal import Decimal, InvalidOperation

MCQ, MSQ, NAT = "MCQ_SINGLE", "MCQ_MULTI", "NUMERICAL"
TYPES = (MCQ, MSQ, NAT)
SCHEMES = {MCQ: "CLASSIC", MSQ: "ALL_OR_NOTHING", NAT: "NUMERIC_TOLERANCE"}
SCHEMA_VERSION = 1
MCQ_OPTIONS = "ABCD"
MSQ_OPTIONS = "ABCDEFGH"
MSQ_MIN, MSQ_MAX = 2, 8
MAX_SIGNIFICANT, MAX_DECIMALS = 12, 6
MAX_ANSWER_LENGTH = 64
TEXT_MAX = 5000
UNIT_MAX = 30
LABEL = {MCQ: "MCQ", MSQ: "MSQ", NAT: "NAT"}
_NUMBER = re.compile(r"^-?(\d+)(?:\.(\d+))?$")


class QuestionTypeError(ValueError):
    """A question, key or answer that breaks the rules: `field` names what to correct."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field, self.message = field, message


# ------------------------------------------------------------------ numbers (NAT)

def parse_number(value, field: str = "answer", integer: bool = False) -> Decimal:
    """A NAT number from text (or an int/Decimal), strictly: digits, optional "-" and one decimal point. QuestionTypeError otherwise."""
    if isinstance(value, bool) or value is None:
        raise QuestionTypeError(field, "Enter a number.")
    text = format(value, "f") if isinstance(value, Decimal) else str(value).strip()
    if isinstance(value, float):
        raise QuestionTypeError(field, "Numbers must be given as text, not floating point.")
    match = _NUMBER.match(text)
    if not match:
        raise QuestionTypeError(field, "Enter a plain number such as 12, -3 or 0.25 (no letters, exponents, spaces or thousands separators).")
    whole, fraction = match.group(1), match.group(2) or ""
    if len(fraction) > MAX_DECIMALS:
        raise QuestionTypeError(field, f"Use at most {MAX_DECIMALS} decimal places.")
    significant = (whole.lstrip("0") + fraction) if whole.lstrip("0") else fraction.lstrip("0")
    if len(significant) > MAX_SIGNIFICANT:
        raise QuestionTypeError(field, f"Use at most {MAX_SIGNIFICANT} significant digits.")
    try:
        number = Decimal(text)
    except InvalidOperation:                      # pragma: no cover  (the pattern already excludes it)
        raise QuestionTypeError(field, "Enter a number.")
    if integer and number != number.to_integral_value():
        raise QuestionTypeError(field, "This answer must be a whole number.")
    return number


def decimal_places(number: Decimal) -> int:
    """Significant decimal places: 12.50 -> 1, 3 -> 0."""
    exponent = number.normalize().as_tuple().exponent if number != 0 else 0
    return max(0, -exponent)


def canonical_number(number: Decimal) -> str:
    """Exact, exponent-free, without trailing zeros; -0 is 0 ("12.50" -> "12.5", "-0" -> "0", "1E+1" never happens)."""
    if number == 0:
        return "0"
    text = format(number.normalize(), "f")
    return text


# ------------------------------------------------------------------ questions (content + key)

def _text(value, field: str, required: bool = False) -> str:
    text = "" if value is None else str(value).strip()
    if required and not text:
        raise QuestionTypeError(field, "This field is required.")
    if len(text) > TEXT_MAX:
        raise QuestionTypeError(field, f"Use at most {TEXT_MAX} characters.")
    return text


def validate_msq(content: dict, key: dict) -> tuple[dict, dict]:
    """(content, answer_key) of an MSQ in canonical form: 2-8 options with ids A.. in order, each with its text or its picture (never
    both empty), and 1 or more correct options that exist, without repeats. QuestionTypeError otherwise."""
    options = (content or {}).get("options")
    if not isinstance(options, list) or not MSQ_MIN <= len(options) <= MSQ_MAX:
        raise QuestionTypeError("options", f"An MSQ needs {MSQ_MIN} to {MSQ_MAX} options.")
    out = []
    for i, option in enumerate(options):
        expected = MSQ_OPTIONS[i]
        if not isinstance(option, dict) or option.get("id", expected) != expected:
            raise QuestionTypeError("options", f"Options must be lettered A, B, C ... in order (option {i + 1} should be {expected}).")
        text = _text(option.get("text"), f"option_{expected.lower()}")
        image = option.get("image") or None
        if not text and not image:
            raise QuestionTypeError(f"option_{expected.lower()}", f"Option {expected} needs its text or its picture.")
        out.append({"id": expected, "text": text, "image": str(image) if image else None})
    ids = [o["id"] for o in out]
    correct = (key or {}).get("correct")
    if isinstance(correct, str):
        correct = [c for c in re.split(r"[\s,;]+", correct.upper()) if c] if any(s in correct for s in ", ;") else list(correct.upper())
    if not isinstance(correct, list) or not correct:
        raise QuestionTypeError("correct_options", "Choose at least one correct option.")
    letters = [str(c).strip().upper() for c in correct]
    if len(set(letters)) != len(letters):
        raise QuestionTypeError("correct_options", "A correct option is listed twice.")
    missing = [c for c in letters if c not in ids]
    if missing:
        raise QuestionTypeError("correct_options", f"Correct option {missing[0]} is not one of the options ({', '.join(ids)}).")
    return {"options": out}, {"correct": sorted(letters)}


def validate_nat(content: dict, key: dict) -> tuple[dict, dict]:
    """(content, answer_key) of a NAT in canonical form. QuestionTypeError otherwise."""
    content, key = content or {}, key or {}
    fmt = str(content.get("format") or "DECIMAL").upper()
    if fmt not in ("INTEGER", "DECIMAL"):
        raise QuestionTypeError("answer_format", "Answer format must be Integer or Decimal.")
    decimals = content.get("decimals")
    decimals = 0 if fmt == "INTEGER" else (MAX_DECIMALS if decimals in (None, "") else decimals)
    try:
        decimals = int(decimals)
    except (TypeError, ValueError):
        raise QuestionTypeError("decimal_places", "Decimal places must be a whole number from 0 to 6.")
    if not 0 <= decimals <= MAX_DECIMALS:
        raise QuestionTypeError("decimal_places", "Decimal places must be from 0 to 6.")
    unit = _text(content.get("unit"), "unit") or None
    if unit and len(unit) > UNIT_MAX:
        raise QuestionTypeError("unit", f"Use at most {UNIT_MAX} characters for the unit.")
    integer = fmt == "INTEGER"

    def number(value, field):
        n = parse_number(value, field, integer=integer)
        if decimal_places(n) > decimals:
            raise QuestionTypeError(field, f"Use at most {decimals} decimal places (the answer format).")
        return n

    value = number(key.get("value"), "answer")
    tolerance = key.get("tolerance") or {"kind": "EXACT"}
    kind = str(tolerance.get("kind") or "EXACT").upper()
    if kind == "EXACT":
        tol = {"kind": "EXACT"}
    elif kind == "ABS":
        amount = parse_number(tolerance.get("amount"), "tolerance")
        if amount < 0:
            raise QuestionTypeError("tolerance", "The tolerance cannot be negative.")
        tol = {"kind": "ABS", "amount": canonical_number(amount)}
    elif kind == "RANGE":
        low, high = number(tolerance.get("min"), "minimum"), number(tolerance.get("max"), "maximum")
        if low > high:
            raise QuestionTypeError("minimum", "The minimum must not be more than the maximum.")
        if not low <= value <= high:
            raise QuestionTypeError("answer", "The answer must lie between the minimum and the maximum.")
        tol = {"kind": "RANGE", "min": canonical_number(low), "max": canonical_number(high)}
    else:
        raise QuestionTypeError("tolerance_type", "Tolerance type must be Exact, Plus-minus or Range.")
    return {"format": fmt, "decimals": decimals, "unit": unit}, {"value": canonical_number(value), "tolerance": tol}


def validate(question_type: str, content: dict | None, key) -> tuple[dict | None, object]:
    """Canonical (content, key) of any type; for MCQ the key is the letter and content is None."""
    if question_type == MCQ:
        letter = str(key or "").strip().upper()
        if letter not in MCQ_OPTIONS or len(letter) != 1:
            raise QuestionTypeError("correct_option", "The correct option must be A, B, C or D.")
        return None, letter
    if question_type == MSQ:
        return validate_msq(content, key)
    if question_type == NAT:
        return validate_nat(content, key)
    raise QuestionTypeError("question_type", f"Unknown question type {question_type!r}; use MCQ, MSQ or NAT.")


# ------------------------------------------------------------------ identity (duplicates, D11)

def content_identity(question_type: str, content: dict | None, normalise=lambda s: s, picture=lambda ref: ref or "") -> list[str]:
    """The parts that make a question's CONTENT (not its key): the type, then the type's own fields. `normalise` is the caller's
    text key (case and spacing rules), `picture` turns a picture reference into its checksum. MCQ callers add stem and options."""
    parts = [question_type]
    if question_type == MSQ:
        for option in (content or {}).get("options", []):
            parts += [normalise(option.get("text") or ""), picture(option.get("image"))]
    elif question_type == NAT:
        c = content or {}
        parts += [str(c.get("format") or ""), normalise(c.get("unit") or "")]
    return parts


def key_identity(question_type: str, key) -> str:
    """A canonical text of the answer key (MCQ: the letter), to tell "same question, same key" from "same question, different key"."""
    if question_type == MCQ:
        return str(key or "").strip().upper()
    if question_type == MSQ:
        return ",".join(sorted((key or {}).get("correct", [])))
    tol = (key or {}).get("tolerance") or {}
    return "|".join([str((key or {}).get("value")), str(tol.get("kind")), str(tol.get("amount", "")), str(tol.get("min", "")),
                     str(tol.get("max", ""))])


def describe_key(question_type: str, key) -> str:
    """The key in words, for staff (answer-key PDFs, conflict messages, reviews)."""
    if question_type == MCQ:
        return str(key)
    if question_type == MSQ:
        return ", ".join((key or {}).get("correct", []))
    tol = (key or {}).get("tolerance") or {}
    value = (key or {}).get("value")
    if tol.get("kind") == "ABS":
        return f"{value} ± {tol.get('amount')}"
    if tol.get("kind") == "RANGE":
        return f"{value} (accepted {tol.get('min')} to {tol.get('max')})"
    return str(value)


# ------------------------------------------------------------------ answers (D12) and scoring (one implementation)

def normalise_answer(question_type: str, raw, option_ids: str | None = None, content: dict | None = None) -> str | None:
    """The canonical stored form of a student's answer, or None for "no answer". QuestionTypeError for anything invalid: an invalid
    answer is never stored, so it can never be scored."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise QuestionTypeError("selected_answer", "The answer must be text.")
    text = raw.strip()
    if text == "":
        return None
    if len(text) > MAX_ANSWER_LENGTH:
        raise QuestionTypeError("selected_answer", "The answer is too long.")
    if question_type == MCQ:
        letter = text.upper()
        allowed = option_ids or MCQ_OPTIONS
        if len(letter) != 1 or letter not in allowed:
            raise QuestionTypeError("selected_answer", f"Choose one of {', '.join(allowed)}.")
        return letter
    if question_type == MSQ:
        allowed = option_ids or "".join(o["id"] for o in (content or {}).get("options", [])) or MSQ_OPTIONS
        letters = [c for c in re.sub(r"[\s,;]+", "", text.upper())]
        if not letters or any(c not in allowed for c in letters):
            raise QuestionTypeError("selected_answer", f"Choose options from {', '.join(allowed)}.")
        if len(set(letters)) != len(letters):
            raise QuestionTypeError("selected_answer", "An option is selected twice.")
        return "".join(sorted(letters))
    if question_type == NAT:
        integer = str((content or {}).get("format") or "").upper() == "INTEGER"
        return canonical_number(parse_number(text, "selected_answer", integer=integer))
    raise QuestionTypeError("question_type", f"Unknown question type {question_type!r}.")


def score(question_type: str, key, answer: str | None, marks, negative) -> tuple[str, Decimal]:
    """(status, marks awarded) of one answered question: the ONE scoring implementation (normal evaluation and re-evaluation alike).
    status is CORRECT, INCORRECT or UNANSWERED. Marks are exact Decimals; `negative` is the positive number deducted (0.25 = -0.25).
    MCQ behaves exactly as it always has (case-insensitive letter match)."""
    marks, negative = Decimal(str(marks or 0)), Decimal(str(negative or 0))
    if answer is None or str(answer).strip() == "":
        return "UNANSWERED", Decimal(0)
    answer = str(answer).strip()
    if question_type in (MCQ, "MCQ", None, ""):
        right = str(key or "").strip().upper()
        return ("CORRECT", marks) if answer.upper() == right else ("INCORRECT", -negative)
    if question_type == MSQ:
        correct = "".join(sorted((key or {}).get("correct", [])))
        given = "".join(sorted(re.sub(r"[\s,;]+", "", answer.upper())))
        return ("CORRECT", marks) if given == correct else ("INCORRECT", Decimal(0))      # all-or-nothing, never negative (D5/R5)
    if question_type == NAT:
        try:
            given = parse_number(answer)
        except QuestionTypeError:
            return "INCORRECT", -negative
        value = Decimal(str((key or {}).get("value")))
        tol = (key or {}).get("tolerance") or {"kind": "EXACT"}
        if tol.get("kind") == "ABS":
            ok = abs(given - value) <= Decimal(str(tol["amount"]))
        elif tol.get("kind") == "RANGE":
            ok = Decimal(str(tol["min"])) <= given <= Decimal(str(tol["max"]))
        else:
            ok = given == value
        return ("CORRECT", marks) if ok else ("INCORRECT", -negative)
    raise QuestionTypeError("question_type", f"Unknown question type {question_type!r}.")
