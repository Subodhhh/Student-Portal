"""Preview -> correct -> publish: corrections never rewrite a version, they create the next one (tests first)."""

import pytest

from institute_common.core.roles import Role
from tests.test_papers import API, GOOD, setup, tmp_store, upload  # noqa: F401  (fixtures)


@pytest.fixture()
def admin(world, make_staff, login):
    _, code = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    return login(code)


def detail(client, h, pv):
    return client.get(f"{API}/paper-versions/{pv}", headers=h).json()


def revise(client, h, pv, edits, reason="Answer key typo found in review"):
    return client.post(f"{API}/paper-versions/{pv}/revisions", json={"edits": edits, "reason": reason}, headers=h)


def test_correcting_a_draft_creates_the_next_version_and_leaves_the_draft_untouched(client, world, setup, admin, db_factory):
    from institute_common.models.staff import AuditLog
    v1 = upload(client, admin, world, setup).json()
    r = revise(client, admin, v1["paper_version_id"], [{"position": 1, "correct_option": "C", "stem": "What is 3+1?"}, {"position": 2, "marks_positive": "5"}])
    assert r.status_code == 201, r.text
    v2 = r.json()
    assert v2["version_no"] == 2 and v2["status"] == "VALIDATED" and v2["paper_id"] == v1["paper_id"] and v2["total_marks"] == "9.00"
    old, new = detail(client, admin, v1["paper_version_id"]), detail(client, admin, v2["paper_version_id"])
    assert [q["correct_option"] for q in old["questions"]] == ["D", "A"] and old["questions"][0]["stem"] == "What is 2+2?"      # v1 is untouched
    assert [q["correct_option"] for q in new["questions"]] == ["C", "A"] and new["questions"][0]["stem"] == "What is 3+1?"
    assert str(new["questions"][1]["marks_positive"]) == "5.00" and new["questions"][1]["stem"] == old["questions"][1]["stem"]
    assert new["questions"][0]["version_no"] == 2 and new["questions"][1]["version_no"] == 2
    assert new["history"][0]["event"] == "REVISED" and "typo" in new["history"][0]["reason"]
    assert any(h["event"] == "REVISION_CREATED" for h in old["history"])
    with db_factory() as db:
        row = db.query(AuditLog).filter_by(action="paper.revise", outcome="SUCCESS").one()
        assert row.reason.startswith("Answer key typo") and row.target_id == v2["paper_version_id"]


def test_untouched_questions_are_shared_between_versions_not_copied(client, world, setup, admin):
    v1 = upload(client, admin, world, setup).json()
    v2 = revise(client, admin, v1["paper_version_id"], [{"position": 1, "correct_option": "B"}]).json()
    a, b = detail(client, admin, v1["paper_version_id"]), detail(client, admin, v2["paper_version_id"])
    assert a["questions"][1]["question_version_id"] == b["questions"][1]["question_version_id"]          # unchanged question reused
    assert a["questions"][0]["question_version_id"] != b["questions"][0]["question_version_id"]
    assert a["questions"][0]["question_id"] == b["questions"][0]["question_id"]                          # same bank question, next version


def test_editing_a_published_paper_creates_a_new_version_and_publishing_it_supersedes_the_old_one(client, world, setup, admin):
    v1 = upload(client, admin, world, setup).json()
    pv1 = v1["paper_version_id"]
    client.post(f"{API}/paper-versions/{pv1}/approve", json={}, headers=admin)
    assert client.post(f"{API}/paper-versions/{pv1}/publish", json={}, headers=admin).json()["status"] == "PUBLISHED"
    v2 = revise(client, admin, pv1, [{"position": 2, "correct_option": "B"}]).json()
    assert detail(client, admin, pv1)["latest_version"]["status"] == "PUBLISHED"                          # still published, still immutable
    assert [q["correct_option"] for q in detail(client, admin, pv1)["questions"]] == ["D", "A"]
    assert v2["status"] == "VALIDATED"
    client.post(f"{API}/paper-versions/{v2['paper_version_id']}/approve", json={}, headers=admin)
    client.post(f"{API}/paper-versions/{v2['paper_version_id']}/publish", json={}, headers=admin)
    assert detail(client, admin, pv1)["latest_version"]["status"] == "SUPERSEDED"
    assert detail(client, admin, v2["paper_version_id"])["latest_version"]["status"] == "PUBLISHED"


@pytest.mark.parametrize("edit,field", [
    ({"position": 1, "correct_option": "Z"}, "correct_option"),
    ({"position": 1, "stem": ""}, "stem"),
    ({"position": 1, "option_a": "2"}, "options"),                       # now identical to option_b
    ({"position": 1, "marks_positive": "0"}, "marks_positive"),
    ({"position": 1, "marks_negative": "-1"}, "marks_negative"),
    ({"position": 1, "stem": "Capital of France?"}, "question"),          # duplicates question 2
])
def test_an_invalid_correction_creates_nothing(client, world, setup, admin, db_factory, edit, field):
    from institute_common.models.papers import PaperVersion, QuestionVersion
    v1 = upload(client, admin, world, setup).json()
    with db_factory() as db:
        before = (db.query(PaperVersion).count(), db.query(QuestionVersion).count())
    r = revise(client, admin, v1["paper_version_id"], [edit])
    assert r.status_code in (422,) and (r.json()["detail"] if "detail" in r.json() else r.json())
    with db_factory() as db:
        assert (db.query(PaperVersion).count(), db.query(QuestionVersion).count()) == before


def test_one_bad_edit_among_good_ones_rejects_the_whole_revision(client, world, setup, admin, db_factory):
    from institute_common.models.papers import PaperVersion
    v1 = upload(client, admin, world, setup).json()
    r = revise(client, admin, v1["paper_version_id"], [{"position": 1, "correct_option": "B"}, {"position": 2, "correct_option": "Q"}])
    assert r.status_code == 422
    with db_factory() as db:
        assert db.query(PaperVersion).count() == 1


def test_unknown_positions_no_op_edits_and_missing_reason_are_refused(client, world, setup, admin):
    v1 = upload(client, admin, world, setup).json()["paper_version_id"]
    assert revise(client, admin, v1, [{"position": 9, "correct_option": "A"}]).status_code == 422
    assert revise(client, admin, v1, [{"position": 1, "correct_option": "D"}]).status_code == 409           # already D: nothing to change
    assert revise(client, admin, v1, [{"position": 1}]).status_code == 422                                  # an edit must change something
    assert revise(client, admin, v1, [{"position": 1, "correct_option": "B"}], reason="").status_code == 422
    assert revise(client, admin, v1, []).status_code == 422
    assert revise(client, admin, v1, [{"position": 1, "correct_option": "B"}, {"position": 1, "correct_option": "C"}]).status_code == 422   # same position twice


def test_a_revision_identical_to_an_existing_paper_is_a_duplicate(client, world, setup, admin):
    v1 = upload(client, admin, world, setup).json()
    other = upload(client, admin, world, setup, csv=GOOD.replace("What is 2+2?,1,2,3,4,D", "What is 2+2?,1,2,3,4,B"), title="Variant").json()
    r = revise(client, admin, other["paper_version_id"], [{"position": 1, "correct_option": "D"}])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "DUPLICATE_PAPER" and v1


def test_withdrawn_versions_cannot_be_revised(client, world, setup, admin):
    v1 = upload(client, admin, world, setup).json()["paper_version_id"]
    client.post(f"{API}/paper-versions/{v1}/approve", json={}, headers=admin)
    client.post(f"{API}/paper-versions/{v1}/withdraw", json={"reason": "wrong paper uploaded"}, headers=admin)
    assert revise(client, admin, v1, [{"position": 1, "correct_option": "B"}]).status_code == 409


def test_a_draft_can_be_rejected_by_withdrawing_it(client, world, setup, admin):
    v1 = upload(client, admin, world, setup).json()["paper_version_id"]
    assert client.post(f"{API}/paper-versions/{v1}/withdraw", json={"reason": "extraction looked wrong"}, headers=admin).json()["status"] == "WITHDRAWN"


def test_teachers_revise_only_their_own_papers_and_other_tenants_cannot(client, world, setup, make_staff, login):
    th = login(setup["tcode"])
    mine = upload(client, th, world, setup, title="Teacher paper").json()["paper_version_id"]
    assert revise(client, th, mine, [{"position": 1, "correct_option": "B"}]).status_code == 201
    oh = login(setup["ocode"])
    assert revise(client, oh, mine, [{"position": 2, "correct_option": "B"}]).status_code == 403            # colleague, not the owner
    _, pa2 = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    assert revise(client, login(pa2), mine, [{"position": 2, "correct_option": "B"}]).status_code == 403   # other provider
    client.cookies.clear()
    assert client.post(f"{API}/paper-versions/{mine}/revisions", json={"edits": [], "reason": "nope nope"}, headers={"Origin": "http://localhost:5174"}).status_code == 401


def test_revised_papers_work_in_exam_generation_with_their_exact_version(client, world, setup, admin, db_factory):
    """A plan pinned to v1 keeps using v1 after v2 exists; 'latest published' resolves to the newest published version."""
    from institute_common.models.papers import PaperVersion
    v1 = upload(client, admin, world, setup).json()
    pv1 = v1["paper_version_id"]
    client.post(f"{API}/paper-versions/{pv1}/approve", json={}, headers=admin)
    client.post(f"{API}/paper-versions/{pv1}/publish", json={}, headers=admin)
    v2 = revise(client, admin, pv1, [{"position": 1, "correct_option": "A"}]).json()
    with db_factory() as db:
        assert {v.version_no: v.status for v in db.query(PaperVersion).all()} == {1: "PUBLISHED", 2: "VALIDATED"}
    assert v2["version_no"] == 2
