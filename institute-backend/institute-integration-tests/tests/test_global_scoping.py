"""Global Test exams: staff access by the PAPERS an attempt sat (its snapshot when the student chose papers, otherwise every paper of
its exam).

World: tests.test_global_unified.uni (STU000001's exam holds p1's Physics (teacher tA) and Chemistry (teacher tC) and p2's Biology (the
tutor, p2's provider admin)). The Student services are faked. Checked: answer-key corrections only of the caller's own papers (refused
whole otherwise) and re-scoring only of their takers; results and integrity filtered by the papers sat, never by subject name; a
provider admin decides a RETAKE only for an attempt of their papers alone; competition ranks; normal exams unchanged."""
from uuid import UUID, uuid4

import pytest

from institute_common.clients.attempt_client import set_attempt_client
from institute_common.clients.result_client import set_result_client
from inst_exam.services import unified
from tests.test_global_contributions import client, login  # noqa: F401
from tests.test_global_schedule import gs, run_generation  # noqa: F401
from tests.test_global_unified import _exam_of, uni  # noqa: F401
from tests.test_result_corrections import FakeCorrectionResults, FakeKeys
from tests.test_exams import ctx, pa  # noqa: F401  (fixtures)
from tests.test_student_integration import live_exam  # noqa: F401  (fixture)

API = "/api/v1"
KEY = "Idempotency-Key"


class GlobalResults(FakeCorrectionResults):
    """The result service for a Global Test: re-evaluation and adjustments may carry papers and subject marks."""

    def __init__(self, subject_rows=None, overall=None):
        super().__init__()
        self.subject_rows, self.overall = subject_rows or [], overall

    def exam_results(self, test_id):
        return self.overall if self.overall is not None else super().exam_results(test_id)

    def exam_subject_results(self, test_id):
        return self.subject_rows

    def reevaluate(self, *, test_id, reason, actor, correction_ref, paper_ids=None):
        self.calls.append(("reevaluate", test_id, paper_ids))
        return {"changed": ["STU000001"], "unchanged": 0, "skipped_manual": [], "already_applied": 0, "no_questions": 0}

    def adjust(self, *, test_id, student_id, obtained_marks, reason, actor, correction_ref, subjects=None):
        self.calls.append(("adjust", student_id, obtained_marks, subjects))
        return {"previous": {"obtained_marks": 7}, "new": {"obtained_marks": obtained_marks}}


@pytest.fixture()
def gt(client, world, gs, uni, db_factory):  # noqa: F811
    """STU000001's Global Test exam; {subject: (submission id, first question order)} of its papers."""
    from institute_common.models.exams import ExamVersion
    t, occ = uni
    run_generation(db_factory, t, occ)
    exam_id = _exam_of(db_factory, occ, "STU000001")
    names = {gs["phy"]: "Physics", gs["chem"]: "Chemistry", gs["bio"]: "Biology"}
    with db_factory() as db:
        ev = db.query(ExamVersion).filter_by(exam_id=UUID(exam_id)).one()
        papers = {names[evp.subject_id]: (str(sub.submission_id), r.start) for r, evp, sub in unified.paper_orders(db, ev.exam_version_id)}
    results, keys = GlobalResults(), FakeKeys()
    from institute_common.clients.answer_key_client import set_answer_key_client
    set_result_client(results)
    set_answer_key_client(keys)
    yield {"exam_id": exam_id, "ev": str(ev.exam_version_id), "papers": papers, "results": results, "keys": keys, "occ": occ}
    set_result_client(None)
    set_answer_key_client(None)
    set_attempt_client(None)


def correct(client, gt, h, *subjects, key=None):  # noqa: F811
    changes = [{"question_order": gt["papers"][s][1], "correct_option": "B"} for s in subjects]
    return client.post(f"{API}/exams/{gt['exam_id']}/answer-key-corrections", json={"changes": changes, "reason": "Key printed wrongly"},
                       headers={**h, KEY: key or uuid4().hex})


def test_contributors_correct_only_their_own_papers_and_only_their_takers_are_rescored(client, gs, login, gt):  # noqa: F811
    phy, chem, bio = (gt["papers"][s][0] for s in ("Physics", "Chemistry", "Biology"))
    ok = correct(client, gt, login(gs["tA"]), "Physics")                               # the teacher who submitted Physics
    assert ok.status_code == 200, ok.text
    assert gt["results"].calls[-1] == ("reevaluate", gt["ev"], [phy])                  # only Physics' takers are re-scored
    assert correct(client, gt, login(gs["tA"]), "Chemistry").status_code == 403         # another teacher's paper
    assert correct(client, gt, login(gs["tA"]), "Physics", "Chemistry").status_code == 403   # refused as a whole ...
    assert len(gt["keys"].calls) == 1                                                  # ... nothing of it was applied
    assert correct(client, gt, login(gs["pa"]), "Physics", "Chemistry").status_code == 200   # p1's provider admin: both p1 papers
    assert gt["results"].calls[-1] == ("reevaluate", gt["ev"], sorted([phy, chem]))
    assert correct(client, gt, login(gs["pa"]), "Biology").status_code == 403           # p2's paper
    assert correct(client, gt, login(gs["tutor"]), "Biology").status_code == 200        # p2's provider admin
    assert correct(client, gt, login(gs["ba"]), "Chemistry").status_code == 200         # every audience branch is the admin's (p1-b1)
    assert correct(client, gt, login(gs["admin"]), "Physics").status_code == 403        # ADMIN never corrects
    assert correct(client, gt, login(gs["sa"]), "Physics", "Biology").status_code == 200   # the platform: any paper
    assert gt["results"].calls[-1] == ("reevaluate", gt["ev"], sorted([phy, bio]))


def test_a_normal_exam_keeps_its_rules(client, live_exam, make_staff, login):  # noqa: F811
    """Corrections of an exam that is not a Global Test stay Super Admin only and re-score as before (no papers sent)."""
    from institute_common.clients.answer_key_client import set_answer_key_client
    from institute_common.core.roles import Role
    results, keys = FakeCorrectionResults(), FakeKeys()          # the existing fake: no papers or subject marks accepted
    set_result_client(results)
    set_answer_key_client(keys)
    try:
        url = f"{API}/exams/{live_exam['exam_id']}"
        body = {"changes": [{"question_order": 1, "correct_option": "B"}], "reason": "Key printed wrongly"}
        assert client.post(f"{url}/answer-key-corrections", json=body, headers={**live_exam["h"], KEY: uuid4().hex}).status_code == 403
        _, sa = make_staff([(Role.SUPER_ADMIN, None, None)])
        r = client.post(f"{url}/answer-key-corrections", json=body, headers={**login(sa), KEY: uuid4().hex})
        assert r.status_code == 200, r.text
        adj = {"obtained_marks": 5, "reason": "Re-checked by hand", "subjects": [{"subject": "Physics", "obtained_marks": 5}]}
        assert client.post(f"{url}/results/STU000001/adjustments", json=adj, headers={**login(sa), KEY: uuid4().hex}).status_code == 422
    finally:
        set_result_client(None)
        set_answer_key_client(None)


def test_unknown_question_is_not_found(client, gs, login, gt):  # noqa: F811
    r = client.post(f"{API}/exams/{gt['exam_id']}/answer-key-corrections", headers={**login(gs["sa"]), KEY: uuid4().hex},
                    json={"changes": [{"question_order": 999, "correct_option": "A"}], "reason": "Key printed wrongly"})
    assert r.status_code == 404


def test_subject_marks_reach_the_result_service(client, gs, login, gt):  # noqa: F811
    body = {"obtained_marks": 9, "reason": "Re-checked by hand", "subjects": [{"subject": "Physics", "obtained_marks": 9}]}
    url = f"{API}/exams/{gt['exam_id']}/results/STU000001/adjustments"
    r = client.post(url, json=body, headers={**login(gs["sa"]), KEY: uuid4().hex})
    assert r.status_code == 200, r.text
    assert gt["results"].calls[-1] == ("adjust", "STU000001", 9, [{"subject": "Physics", "obtained_marks": 9}])
    assert client.post(url, json=body, headers={**login(gs["pa"]), KEY: uuid4().hex}).status_code == 403   # adjustments: platform only


def _row(subject, paper_id, obtained, total):
    return {"subject": subject, "paper_id": paper_id, "obtained_marks": obtained, "total_marks": total, "attempted_questions": 1,
            "correct_answers": 1, "incorrect_answers": 0, "unanswered_questions": 0, "total_questions": 1}


def test_results_are_scoped_by_paper_never_by_subject_name(client, gs, login, gt):  # noqa: F811
    _phy, chem, bio = (gt["papers"][s][0] for s in ("Physics", "Chemistry", "Biology"))
    other = str(uuid4())                                                               # another contributor's Physics paper
    gt["results"].subject_rows = [{"student_id": "STU000001", "submitted_at": "2026-01-01T00:00:00",
                                   "subjects": [_row("Physics", other, 4, 4), _row("Chemistry", chem, 8, 8), _row("Biology", bio, 2, 8)]}]
    r1 = client.get(f"{API}/exams/{gt['exam_id']}/results", headers=login(gs["pa"])).json()
    assert r1["global_test"] is True
    assert [s["subject"] for s in r1["results"][0]["subjects"]] == ["Chemistry"]       # same subject name, not p1's paper: hidden
    rt = client.get(f"{API}/exams/{gt['exam_id']}/results", headers=login(gs["tA"])).json()
    assert rt["results"] == []                                                         # tA's Physics paper was not this attempt's
    r2 = client.get(f"{API}/exams/{gt['exam_id']}/results", headers=login(gs["tutor"])).json()
    assert [s["subject"] for s in r2["results"][0]["subjects"]] == ["Biology"]


def test_global_test_ranks_are_shared_by_exact_ratio(client, gs, login, gt, db_factory):  # noqa: F811
    from inst_exam.routes.exams import competition_ranked
    rows = [{"student_id": "A", "obtained_marks": 12, "total_marks": 12}, {"student_id": "B", "obtained_marks": 8, "total_marks": 8},
            {"student_id": "C", "obtained_marks": 14, "total_marks": 16}, {"student_id": "D", "obtained_marks": 0, "total_marks": 0},
            {"student_id": "E", "obtained_marks": -2, "total_marks": 8}]
    assert [(r["student_id"], r["rank"]) for r in competition_ranked(rows)] == [("A", 1), ("B", 1), ("C", 3), ("E", 4), ("D", None)]
    gt["results"].overall = [{"student_id": "STU000001", "obtained_marks": 7, "total_marks": 8, "accuracy": 80.0, "submitted_at": "x"}]
    gt["results"].subject_rows = []
    r = client.get(f"{API}/exams/{gt['exam_id']}/results", headers=login(gs["sa"])).json()
    assert [(x["student_id"], x["rank"]) for x in r["results"]] == [("STU000001", 1)]


class GlobalAttempts:
    """The attempt service: STU000001's flagged attempt with the papers it chose ({} = no choice: every paper of its exam)."""

    def __init__(self, test_id, papers):
        self.test_id, self.papers, self.decisions = test_id, papers, []
        self.aid = "11111111-1111-4111-8111-111111111111"

    def _row(self):
        return {"student_id": "STU000001", "attempt_id": self.aid, "status": "AUTO_SUBMITTED", "started_at": "x", "expires_at": "x",
                "submitted_at": "y", "submit_reason": "WARNING_THRESHOLD", "review_status": "UNDER_REVIEW", "warnings": 3,
                "retake_of_attempt_id": None, "selected_papers": self.papers}

    def exam_attempts(self, test_id):
        return [self._row()]

    def test_reviews(self, test_id):
        return []

    def attempt_integrity(self, attempt_id):
        return {**self._row(), "test_id": self.test_id, "threshold": 3, "events": [], "review": None} if attempt_id == self.aid else None

    def review_decision(self, attempt_id, body):
        self.decisions.append(body["decision"])
        return 200, {"status": "RETAKE_AUTHORIZED"}


def test_integrity_by_paper_and_retake_only_for_an_attempt_of_one_providers_papers(client, gs, login, gt):  # noqa: F811
    phy, chem, bio = (gt["papers"][s][0] for s in ("Physics", "Chemistry", "Biology"))
    url = f"{API}/exams/{gt['exam_id']}/integrity"
    only_p1 = GlobalAttempts(gt["ev"], {"Physics": phy, "Chemistry": chem})
    set_attempt_client(only_p1)
    assert [x["can_decide"] for x in client.get(url, headers=login(gs["pa"])).json()["items"]] == [True]
    assert client.get(url, headers=login(gs["tutor"])).json()["items"] == []           # sat none of p2's papers: invisible
    assert client.get(f"{url}/{only_p1.aid}", headers=login(gs["tutor"])).status_code == 404
    decide = f"{url}/{only_p1.aid}/decision"
    assert client.post(decide, json={"decision": "RETAKE", "reason": "OS update prompt"}, headers=login(gs["pa"])).status_code == 200

    mixed = GlobalAttempts(gt["ev"], {"Physics": phy, "Chemistry": chem, "Biology": bio})
    set_attempt_client(mixed)
    assert [x["can_decide"] for x in client.get(url, headers=login(gs["pa"])).json()["items"]] == [False]     # visible, not decidable
    assert [x["can_decide"] for x in client.get(url, headers=login(gs["tutor"])).json()["items"]] == [False]
    assert client.post(decide, json={"decision": "RETAKE", "reason": "OS update prompt"}, headers=login(gs["pa"])).status_code == 403
    assert client.post(decide, json={"decision": "RETAKE", "reason": "OS update prompt"}, headers=login(gs["admin"])).status_code == 200
    assert mixed.decisions == ["RETAKE"]


def test_an_attempt_without_paper_choice_sat_every_paper_of_its_exam(client, gs, login, gt):  # noqa: F811
    """An exam made for one combination (no snapshot): visible to each contributor of it; a mixed one is decided by the platform."""
    url = f"{API}/exams/{gt['exam_id']}/integrity"
    set_attempt_client(GlobalAttempts(gt["ev"], {}))
    assert [x["can_decide"] for x in client.get(url, headers=login(gs["pa"])).json()["items"]] == [False]     # p1 + p2's papers
    assert [x["can_decide"] for x in client.get(url, headers=login(gs["tutor"])).json()["items"]] == [False]
    assert [x["can_decide"] for x in client.get(url, headers=login(gs["admin"])).json()["items"]] == [True]
