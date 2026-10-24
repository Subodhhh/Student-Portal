"""Class / Academic Level and Academic Year: the controlled values shared by enrollment, batch defaults and exam matching.

A student's Class belongs to each provider ENROLLMENT (enrollment_academic_profiles), never to the global student identity: the same
student may be in 12th at one institute and in a Dropper course at another. Stored as codes; shown with LABELS.

Exam matching (exam_generation.resolve_audience) uses an exam plan's `target_class`, a label, through `class_target`:
  * blank            -> the exam is not class-specific: no Class filter (the behaviour before Classes existed);
  * a known Class    -> batch/branch/provider audiences reach only enrollments whose Class is exactly that one; an enrollment without an
                        academic profile is EXCLUDED and counted (never silently included);
  * "Other" or an unrecognised (legacy) label -> NOT matchable: generation fails with an explicit reason and previews refuse it, so no
                        student is silently included or excluded. Labels are never interpreted ("Class 11", "XI" are not guessed).
Students listed explicitly by staff are an override and are never filtered by Class (as with subject selections).
"""
import re
from typing import Annotated

from pydantic import AfterValidator

CLASS_LEVELS = ("6TH", "7TH", "8TH", "9TH", "10TH", "11TH", "12TH", "DROPPER", "OTHER")
LABELS = {"6TH": "6th", "7TH": "7th", "8TH": "8th", "9TH": "9th", "10TH": "10th", "11TH": "11th", "12TH": "12th", "DROPPER": "Dropper", "OTHER": "Other"}
TARGETABLE = tuple(c for c in CLASS_LEVELS if c != "OTHER")     # an exam can target these; "Other" has no shared meaning
OTHER_MAX = 120
_YEAR = re.compile(r"^(\d{4})-(\d{4})$")


def normalize_class(value: str | None) -> str | None:
    """The code for an exact code or label (case and surrounding spaces ignored), else None. Nothing else is interpreted."""
    if value is None:
        return None
    v = " ".join(value.split()).upper()
    return v if v in CLASS_LEVELS else next((c for c, label in LABELS.items() if label.upper() == v), None)


def class_target(label: str | None) -> tuple[str | None, str | None]:
    """(code, problem) for an exam plan's target_class: (None, None) = not class-specific; problem = cannot be matched safely."""
    if label is None or not label.strip():
        return None, None
    code = normalize_class(label)
    if code is None:
        return None, f"The exam's class '{label}' is not a recognised Class ({', '.join(LABELS[c] for c in TARGETABLE)}); set one or leave it empty."
    if code == "OTHER":
        return None, "An exam cannot target the Class 'Other'; choose a specific Class or leave it empty."
    return code, None


def clean_target_class(value: str | None) -> str | None:
    """Request validator for an exam/test's target_class: blank -> None, a targetable Class -> its label, anything else rejected."""
    if value is None or not value.strip():
        return None
    _code, problem = class_target(value)
    if problem:
        raise ValueError(problem)
    return LABELS[_code]


def check_academic_year(value: str) -> str:
    """'YYYY-YYYY' with consecutive years (2026-2027), 2000-2100."""
    v = (value or "").strip()
    m = _YEAR.match(v)
    if not m or int(m.group(2)) != int(m.group(1)) + 1 or not 2000 <= int(m.group(1)) <= 2099:
        raise ValueError("must look like 2026-2027 (two consecutive years)")
    return v


def check_profile(class_level: str, other: str | None) -> tuple[str, str | None]:
    """(code, description) for a Class and its 'Other' description: required for Other, not allowed otherwise."""
    code = normalize_class(class_level)
    if code is None:
        raise ValueError(f"class_level must be one of {', '.join(LABELS[c] for c in CLASS_LEVELS)}")
    desc = " ".join((other or "").split()) or None
    if code == "OTHER" and not desc:
        raise ValueError("describe the class when it is 'Other'")
    if code != "OTHER" and desc:
        raise ValueError("other_class_description is only for the class 'Other'")
    if desc and len(desc) > OTHER_MAX:
        raise ValueError(f"other_class_description must be at most {OTHER_MAX} characters")
    return code, desc


TargetClass = Annotated[str | None, AfterValidator(clean_target_class)]
AcademicYear = Annotated[str, AfterValidator(check_academic_year)]
