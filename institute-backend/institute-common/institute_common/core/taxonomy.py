"""Question-bank classification shared by the paper and exam services.

Hierarchy: exam type -> target class -> subject -> chapter -> topic -> subtopic. Subjects are the canonical platform catalogue
(`subjects`); exam type, class, chapter, topic and subtopic are labels on the question (free text, validated), so providers can
classify their own content without a central taxonomy administration. Difficulty, pattern and question type are closed sets.

Every question is still one stem with four options and exactly one correct answer (the only format the Student test and result
services evaluate); the pattern describes how the stem is written (assertion/reason, statements, match the column, ...)."""
import unicodedata
from typing import Annotated

from pydantic import AfterValidator

DIFFICULTIES = ("EASY", "MEDIUM", "HARD")
PATTERNS = ("SINGLE_CORRECT", "ASSERTION_REASON", "STATEMENT_BASED", "MATCH_THE_COLUMN", "DIAGRAM_BASED", "CASE_BASED")
QUESTION_TYPES = ("MCQ_SINGLE",)
SCOPES = ("PROVIDER", "GLOBAL")

LABEL_MAX = 80        # exam type, class, chapter, subtopic, subtype
TOPIC_MAX = 200       # topic: same limit the question versions have always had
JOINERS = {"\u200c", "\u200d"}   # zero-width (non-)joiners are part of correct spelling in Indic scripts

# Exam types the UI suggests; any label accepted by clean_label is allowed.
SUGGESTED_EXAM_TYPES = ("NEET", "AIIMS", "JEE Main", "JEE Advanced", "CUET", "Board")



def sql_in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def clean_label(value: str | None, max_len: int = LABEL_MAX) -> str | None:
    """Validate a free-text classification label. Any language and common punctuation are allowed ("Organic Chemistry: Basics",
    "Newton’s laws", "Cell – structure", "Topic_1", Hindi ...). Rejected: control/format characters, text without a single letter
    or digit, and anything longer than `max_len` after trimming. Blank becomes None."""
    if value is None:
        return None
    v = " ".join(value.split())          # trim and collapse whitespace (also removes tabs/newlines)
    if not v:
        return None
    if len(v) > max_len:
        raise ValueError(f"must be at most {max_len} characters")
    if any(unicodedata.category(ch)[0] == "C" and ch not in JOINERS for ch in v):
        raise ValueError("contains control characters")
    if not any(ch.isalnum() for ch in v):
        raise ValueError("must contain a letter or a digit")
    return v


Label = Annotated[str | None, AfterValidator(clean_label)]
TopicLabel = Annotated[str | None, AfterValidator(lambda v: clean_label(v, TOPIC_MAX))]
