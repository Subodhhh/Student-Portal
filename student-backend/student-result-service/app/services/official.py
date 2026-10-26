"""Which result counts after an authorized retake (exam integrity, see the attempt service): the original attempt's result counts until
the retake has a result; from then on the retake's result is official and the original is superseded (kept, never deleted)."""
from sqlalchemy import column, select, table

from app.models.test_result import TestResult

_attempts = table("test_attempts", column("attempt_id"), column("retake_of_attempt_id"))
_retake_results = table("test_results", column("attempt_id")).alias("retake_result")

# results whose attempt has an authorized retake that was evaluated
superseded_attempts = (select(_attempts.c.retake_of_attempt_id)
                       .join(_retake_results, _retake_results.c.attempt_id == _attempts.c.attempt_id)
                       .where(_attempts.c.retake_of_attempt_id.is_not(None)))

official = TestResult.attempt_id.not_in(superseded_attempts)

# the same rule for raw SQL over test_results aliased as r
OFFICIAL_SQL = """NOT EXISTS (SELECT 1 FROM test_attempts ra JOIN test_results rr ON rr.attempt_id = ra.attempt_id
                              WHERE ra.retake_of_attempt_id = r.attempt_id)"""
