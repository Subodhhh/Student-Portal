"""The ONE scorer of the result service: first evaluation, authorized re-evaluation and the question review all call
`outcome`, which applies the shared rulebook (app/question_types.score; the same golden vectors as the Institute).
    MCQ  correct letter +marks, wrong -negative, blank 0 (exactly as always)
    MSQ  exactly the correct set +marks, anything else 0, never negative (all-or-nothing)
    NAT  within the key's tolerance +marks, otherwise -negative (the test's marking), blank 0"""
import json
from decimal import Decimal

from app import question_types as qtypes


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def question_type(q) -> str:
    qtype = q.get("question_type")          # absent / "MCQ": every question delivered before MSQ and NAT existed
    return qtype if qtype in (qtypes.MSQ, qtypes.NAT) else qtypes.MCQ


def key_of(q):
    """The answer key: the letter of an MCQ (correct_answer), the typed key of an MSQ / NAT (answer_key)."""
    return _json(q.get("answer_key")) if question_type(q) != qtypes.MCQ else q["correct_answer"]


def outcome(q) -> tuple[str, Decimal]:
    """(CORRECT | INCORRECT | UNANSWERED, marks awarded) of one question row (with its selected_answer)."""
    return qtypes.score(question_type(q), key_of(q), q.get("selected_answer") or None,
                        Decimal(str(q["marks"] or 0)), Decimal(str(q["negative_marks"] or 0)))


def rows(questions) -> list[dict]:
    """The per-question rows of a result (test_result_questions)."""
    out = []
    for q in questions:
        status, marks = outcome(q)
        out.append({"question_id": q["question_id"], "question_order": q["question_order"], "question_type": question_type(q),
                    "selected_answer": q.get("selected_answer") or None, "status": status, "marks_obtained": marks})
    return out
