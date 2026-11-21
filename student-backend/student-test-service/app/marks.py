"""Marks are exact decimals (at most 2 decimal places), never floats.

`Marks` is the one type for every mark in this service's API:
- In: a JSON integer (4), a JSON number (0.25) or a decimal string ("0.25") is accepted. NaN, Infinity, booleans and more than
  2 decimal places are rejected, so a mark can never be silently rounded.
- Out: a whole value is a JSON integer (4) and a fractional one a JSON number (0.25), so responses for whole-number data are
  exactly what they were when marks were integers.
"""

from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import BeforeValidator, PlainSerializer

CENT = Decimal("0.01")
MAX_QUESTION_MARKS = Decimal("9999.99")    # questions.marks / negative_marks are NUMERIC(6,2)
MAX_TOTAL_MARKS = Decimal("999999.99")     # tests.total_marks is NUMERIC(8,2)


def parse_marks(value) -> Decimal:
    """Any accepted wire form -> exact Decimal. Raises ValueError for anything that is not a mark with <= 2 decimals."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError("marks must be a number or a decimal string")
    try:
        marks = Decimal(str(value).strip())     # str() of a float is its shortest exact-looking form (0.25 -> "0.25")
        if not marks.is_finite() or marks != marks.quantize(CENT):
            raise ValueError
    except (InvalidOperation, ValueError):
        raise ValueError("marks must be a finite number with at most 2 decimal places") from None
    return marks


def marks_to_json(value: Decimal) -> int | float:
    """Whole -> int, fractional -> float. A float is safe here: a 2-decimal value prints back as the same 2 decimals."""
    return int(value) if value == value.to_integral_value() else float(value)


Marks = Annotated[Decimal, BeforeValidator(parse_marks), PlainSerializer(marks_to_json, return_type=int | float, when_used="json")]
