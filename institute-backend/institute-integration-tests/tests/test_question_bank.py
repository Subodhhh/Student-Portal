"""Question bank (global + provider questions, taxonomy, archive, usage), building papers from bank questions, separate answer keys and
the import history."""
import io
from decimal import Decimal
from uuid import UUID

import pytest

from institute_common.core.roles import Role

API = "/api/v1"


def q_body(subject, **over):
    body = {"subject_id": str(subject), "stem": "What is the SI unit of force?", "option_a": "Newton", "option_b": "Joule",
            "option_c": "Watt", "option_d": "Pascal", "correct_option": "A", "marks_positive": "4", "marks_negative": "1",
            "exam_type": "NEET", "target_class": "Class 11", "chapter": "Laws of Motion", "topic": "Force", "subtopic": "Units",
            "difficulty": "EASY", "pattern": "SINGLE_CORRECT"}
    body.update(over)
    return body


@pytest.fixture()
def bank(client, world, make_staff, login, db_factory):
    from institute_common.models.academics import ProviderSubject, Subject, TeacherSubjectAssignment
    c = {}
    with db_factory() as db:
        phy, chem = Subject(code="PHYSICS", name="Physics"), Subject(code="CHEM", name="Chemistry")
        db.add_all([phy, chem])
        db.flush()
        c["phy"], c["chem"] = phy.subject_id, chem.subject_id
        for prov in ("p1", "p2"):
            for s in (phy, chem):
                db.add(ProviderSubject(provider_id=world[prov], subject_id=s.subject_id))
        db.commit()
    _, c["sa"] = make_staff([(Role.SUPER_ADMIN, None, None)])
    _, c["pa1"] = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    _, c["pa2"] = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    t, c["teacher"] = make_staff([(Role.TEACHER, world["p1"], None)])
    with db_factory() as db:
        db.add(TeacherSubjectAssignment(staff_id=t, provider_id=world["p1"], branch_id=world["p1b1"], subject_id=c["phy"]))
        db.commit()
    return c


def create(client, login, code, body):
    r = client.post(f"{API}/questions", json=body, headers=login(code))
    assert r.status_code == 201, r.text
    return r.json()["question_id"]


def test_global_questions_are_platform_managed_and_readable_by_providers(client, world, bank, login):
    gid = create(client, login, bank["sa"], q_body(bank["phy"], scope="GLOBAL"))
    h = login(bank["pa1"])
    assert client.post(f"{API}/questions", json=q_body(bank["phy"], scope="GLOBAL"), headers=h).status_code == 403
    # a provider admin sees global questions plus its own branch's questions, never another provider's
    own = create(client, login, bank["pa1"], q_body(bank["phy"], branch_id=str(world["p1b1"]), stem="Own provider question?"))
    other = create(client, login, bank["pa2"], q_body(bank["phy"], branch_id=str(world["p2b1"]), stem="Other provider question?"))
    h = login(bank["pa1"])
    ids = {x["question_id"] for x in client.get(f"{API}/questions?branch_id={world['p1b1']}", headers=h).json()["items"]}
    assert ids == {gid, own}
    assert other not in {x["question_id"] for x in client.get(f"{API}/questions", headers=h).json()["items"]}   # without a branch: global only
    assert client.get(f"{API}/questions?branch_id={world['p2b1']}", headers=h).status_code == 403
    assert client.get(f"{API}/questions/{other}", headers=h).status_code == 403
    item = next(x for x in client.get(f"{API}/questions?scope=GLOBAL", headers=h).json()["items"] if x["question_id"] == gid)
    assert item["scope"] == "GLOBAL" and item["can_edit"] is False and item["latest"]["correct_option"] == "A"
    assert client.patch(f"{API}/questions/{gid}", json={"chapter": "Dynamics"}, headers=h).status_code == 403
    # the platform admin sees everything and edits global questions
    h = login(bank["sa"])
    assert {gid, own, other} <= {x["question_id"] for x in client.get(f"{API}/questions?scope=ALL", headers=h).json()["items"]}
    assert client.patch(f"{API}/questions/{gid}", json={"chapter": "Dynamics"}, headers=h).json()["chapter"] == "Dynamics"


def test_search_filters_follow_the_taxonomy(client, world, bank, login):
    b = str(world["p1b1"])
    create(client, login, bank["pa1"], q_body(bank["phy"], branch_id=b, stem="Easy force question?"))
    create(client, login, bank["pa1"], q_body(bank["phy"], branch_id=b, stem="Hard rotation question?", chapter="Rotation", topic="Torque",
                                              subtopic=None, difficulty="HARD", pattern="ASSERTION_REASON", target_class="Class 12"))
    create(client, login, bank["pa1"], q_body(bank["chem"], branch_id=b, stem="Mole concept question?", chapter="Mole Concept", topic="Moles"))
    h = login(bank["pa1"])

    def stems(qs):
        return sorted(x["latest"]["stem"] for x in client.get(f"{API}/questions?branch_id={b}&{qs}", headers=h).json()["items"])
    assert stems(f"subject_id={bank['phy']}") == ["Easy force question?", "Hard rotation question?"]
    assert stems(f"subject_id={bank['phy']}&subject_id={bank['chem']}&chapter=Rotation&chapter=Mole%20Concept") == ["Hard rotation question?", "Mole concept question?"]
    assert stems("difficulty=HARD") == ["Hard rotation question?"]
    assert stems("pattern=ASSERTION_REASON&target_class=Class%2012") == ["Hard rotation question?"]
    assert stems("topic=Force&subtopic=Units") == ["Easy force question?"]
    assert stems("q=mole") == ["Mole concept question?"]
    facets = client.get(f"{API}/question-taxonomy?branch_id={b}&subject_id={bank['phy']}", headers=h).json()
    assert facets["chapter"] == ["Laws of Motion", "Rotation"] and facets["target_class"] == ["Class 11", "Class 12"]
    assert client.post(f"{API}/questions", json=q_body(bank["phy"], branch_id=b, difficulty="IMPOSSIBLE"), headers=h).status_code == 422


def test_teachers_see_and_edit_only_their_subjects_and_own_questions(client, world, bank, login):
    b = str(world["p1b1"])
    create(client, login, bank["pa1"], q_body(bank["chem"], branch_id=b, stem="Chemistry by the admin?"))
    admin_phy = create(client, login, bank["pa1"], q_body(bank["phy"], branch_id=b, stem="Physics by the admin?"))
    h = login(bank["teacher"])
    mine = create(client, login, bank["teacher"], q_body(bank["phy"], branch_id=b, stem="Physics by the teacher?"))
    h = login(bank["teacher"])
    items = client.get(f"{API}/questions?branch_id={b}&scope=PROVIDER", headers=h).json()["items"]
    assert sorted(x["latest"]["stem"] for x in items) == ["Physics by the admin?", "Physics by the teacher?"]
    assert {x["question_id"]: x["can_edit"] for x in items} == {admin_phy: False, mine: True}
    assert client.patch(f"{API}/questions/{admin_phy}", json={"difficulty": "HARD"}, headers=h).status_code == 403
    assert client.post(f"{API}/questions", json=q_body(bank["chem"], branch_id=b), headers=h).status_code == 403


def test_archive_hides_from_the_bank_but_keeps_papers_and_versions(client, world, bank, login):
    b = str(world["p1b1"])
    qid = create(client, login, bank["pa1"], q_body(bank["phy"], branch_id=b))
    h = login(bank["pa1"])
    paper = client.post(f"{API}/papers", json={"branch_id": b, "subject_id": str(bank["phy"]), "title": "Force basics", "question_ids": [qid]}, headers=h)
    assert paper.status_code == 201, paper.text
    # a new content version never changes the paper that used version 1
    v2 = client.post(f"{API}/questions/{qid}/versions", json={k: v for k, v in q_body(bank["phy"], stem="Revised wording?").items()
                                                              if k in ("stem", "option_a", "option_b", "option_c", "option_d", "correct_option",
                                                                       "marks_positive", "marks_negative")}, headers=h)
    assert v2.status_code == 201 and v2.json()["version_no"] == 2
    detail = client.get(f"{API}/paper-versions/{paper.json()['paper_version_id']}", headers=h).json()
    assert [q["stem"] for q in detail["questions"]] == ["What is the SI unit of force?"]
    info = client.get(f"{API}/questions/{qid}", headers=h).json()
    assert [v["version_no"] for v in info["versions"]] == [2, 1] and info["usage"][0]["question_version_no"] == 1
    assert info["question"]["usage_count"] == 1
    assert client.get(f"{API}/questions?branch_id={b}&include_used=false", headers=h).json()["total"] == 0
    assert client.patch(f"{API}/questions/{qid}", json={"status": "ARCHIVED"}, headers=h).json()["status"] == "ARCHIVED"
    assert client.get(f"{API}/questions?branch_id={b}", headers=h).json()["total"] == 0
    assert client.get(f"{API}/questions?branch_id={b}&status=ARCHIVED", headers=h).json()["total"] == 1
    assert client.get(f"{API}/paper-versions/{paper.json()['paper_version_id']}", headers=h).status_code == 200
    # archived questions cannot go into new papers
    r = client.post(f"{API}/papers", json={"branch_id": b, "subject_id": str(bank["phy"]), "title": "Again", "question_ids": [qid]}, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["errors"][0]["message"] == "Question is archived."


def test_build_paper_reuses_questions_by_reference_and_enforces_ownership(client, world, bank, login, db_factory):
    from institute_common.models.papers import PaperVersionQuestion, Question
    b1 = str(world["p1b1"])
    g = create(client, login, bank["sa"], q_body(bank["phy"], scope="GLOBAL", stem="Global question one?"))
    own = create(client, login, bank["pa1"], q_body(bank["phy"], branch_id=b1, stem="Own question one?"))
    other = create(client, login, bank["pa2"], q_body(bank["phy"], branch_id=str(world["p2b1"]), stem="Foreign question?"))
    chem = create(client, login, bank["pa1"], q_body(bank["chem"], branch_id=b1, stem="Chemistry question?"))
    h = login(bank["pa1"])
    with db_factory() as db:
        before = db.query(Question).count()
    body = {"branch_id": b1, "subject_id": str(bank["phy"]), "title": "Mixed sources", "question_ids": [own, g]}
    r = client.post(f"{API}/papers", json=body, headers=h)
    assert r.status_code == 201 and r.json()["question_count"] == 2 and r.json()["total_marks"] == "8.00" and r.json()["status"] == "VALIDATED"
    with db_factory() as db:
        assert db.query(Question).count() == before                              # nothing duplicated
        assert db.query(PaperVersionQuestion).filter_by(paper_version_id=UUID(r.json()["paper_version_id"])).count() == 2
    assert client.post(f"{API}/papers", json=body, headers=h).status_code == 409    # identical content
    for ids, msg in (([own, other], "Question not found."), ([own, chem], "Question belongs to a different subject.")):
        r = client.post(f"{API}/papers", json={**body, "title": "Bad", "question_ids": ids}, headers=h)
        assert r.status_code == 422 and r.json()["detail"]["errors"][0]["message"] == msg
    # a new version of an existing paper, built from the bank
    pid = client.get(f"{API}/papers?branch_id={b1}", headers=h).json()["items"][0]["paper_id"]
    r = client.post(f"{API}/papers", json={**body, "paper_id": pid, "question_ids": [g]}, headers=h)
    assert r.status_code == 201 and r.json()["version_no"] == 2
    assert [v["version_no"] for v in client.get(f"{API}/papers/{pid}/versions", headers=h).json()] == [2, 1]
    # platform papers use platform questions only and are platform-managed
    gp = {"scope": "GLOBAL", "subject_id": str(bank["phy"]), "title": "Platform paper", "question_ids": [g]}
    assert client.post(f"{API}/papers", json=gp, headers=h).status_code == 403
    h = login(bank["sa"])
    assert client.post(f"{API}/papers", json={**gp, "question_ids": [own]}, headers=h).status_code == 422
    r = client.post(f"{API}/papers", json=gp, headers=h)
    assert r.status_code == 201
    gpv = r.json()["paper_version_id"]
    h = login(bank["pa1"])
    assert client.get(f"{API}/papers?scope=GLOBAL", headers=h).json()["items"][0]["title"] == "Platform paper"
    assert client.get(f"{API}/paper-versions/{gpv}", headers=h).status_code == 200
    assert client.post(f"{API}/paper-versions/{gpv}/approve", json={}, headers=h).status_code == 403
    h = login(bank["sa"])
    assert client.post(f"{API}/paper-versions/{gpv}/approve", json={}, headers=h).json()["status"] == "APPROVED"


def test_build_rejects_marks_the_student_services_cannot_deliver(client, world, bank, login):
    b1 = str(world["p1b1"])
    q = create(client, login, bank["pa1"], q_body(bank["phy"], branch_id=b1, marks_positive="2.5"))
    r = client.post(f"{API}/papers", json={"branch_id": b1, "subject_id": str(bank["phy"]), "title": "Half marks", "question_ids": [q]},
                    headers=login(bank["pa1"]))
    assert r.status_code == 422 and "whole-number" in r.json()["detail"]["errors"][0]["message"]


NO_ANSWER = ("question,option_a,option_b,option_c,option_d,marks,negative_marks\n"
             "What is the SI unit of force?,Newton,Joule,Watt,Pascal,4,1\n"
             "What is the SI unit of energy?,Newton,Joule,Watt,Pascal,4,1\n")
WITH_ANSWER = ("question,option_a,option_b,option_c,option_d,correct,marks,negative_marks\n"
               "What is the SI unit of force?,Newton,Joule,Watt,Pascal,A,4,1\n"
               "What is the SI unit of energy?,Newton,Joule,Watt,Pascal,B,4,1\n")


def upload(client, world, bank, h, paper, key, title="Units test"):
    files = {"file": ("paper.csv", io.BytesIO(paper.encode()), "text/csv")}
    if key is not None:
        files["answer_key"] = ("key.csv", io.BytesIO(key.encode()), "text/csv")
    return client.post(f"{API}/branches/{world['p1b1']}/subjects/{bank['phy']}/papers/upload", files=files, data={"title": title}, headers=h)


def test_separate_answer_key_is_matched_by_question_number(client, world, bank, login, db_factory):
    from institute_common.models.papers import QuestionVersion
    h = login(bank["pa1"])
    r = upload(client, world, bank, h, NO_ANSWER, "question,answer\n1,A\n2. b\n")
    assert r.status_code == 201, r.text
    with db_factory() as db:
        assert sorted(db.query(QuestionVersion.correct_option).all()) == [("A",), ("B",)]
    hist = client.get(f"{API}/uploads?branch_id={world['p1b1']}", headers=h).json()["items"][0]
    assert hist["has_answer_key"] is True and hist["status"] == "VALIDATED" and hist["paper_version_status"] == "VALIDATED" and hist["title"] == "Units test"


@pytest.mark.parametrize("paper,key,fragment", [
    (WITH_ANSWER, "1,A\n2,C\n", "Answer mismatch for question 2"),
    (NO_ANSWER, "1,A\n", "no answer for question 2"),
    (NO_ANSWER, "1,A\n2,B\n3,C\n", "questions that are not in the paper: 3"),
    (NO_ANSWER, "1,A\n1,B\n2,B\n", "appears twice"),
    (NO_ANSWER, "1,E\n2,B\n", "must be A, B, C or D"),
    (NO_ANSWER, "first is A\n2,B\n", "Unreadable answer-key line"),
    (NO_ANSWER, None, "Missing required column(s): correct"),
])
def test_any_answer_key_problem_rejects_the_whole_paper(client, world, bank, login, db_factory, paper, key, fragment):
    from institute_common.models.papers import Paper, Question
    h = login(bank["pa1"])
    r = upload(client, world, bank, h, paper, key)
    assert r.status_code == 422, r.text
    assert any(fragment.lower() in e["message"].lower() for e in r.json()["detail"]["errors"]), r.json()["detail"]["errors"]
    with db_factory() as db:
        assert db.query(Question).count() == 0 and db.query(Paper).count() == 0     # nothing imported
    hist = client.get(f"{API}/uploads?branch_id={world['p1b1']}&status=REJECTED", headers=h).json()
    assert hist["total"] == 1 and hist["items"][0]["error_count"] >= 1 and hist["items"][0]["paper_version_id"] is None


def test_upload_history_is_scoped(client, world, bank, login):
    h = login(bank["pa1"])
    assert upload(client, world, bank, h, WITH_ANSWER, None).status_code == 201
    h = login(bank["pa2"])
    assert client.get(f"{API}/uploads?branch_id={world['p1b1']}", headers=h).status_code == 403
    assert client.get(f"{API}/uploads", headers=h).status_code == 422          # a scoped admin must name a branch
    h = login(bank["sa"])
    assert client.get(f"{API}/uploads", headers=h).json()["total"] == 1


def test_existing_rows_default_to_provider_scope(db_factory, world, bank):
    """Rows created without the new columns (as existing data is) are provider questions with the default pattern/type."""
    from uuid import uuid4

    from institute_common.models.papers import Question
    with db_factory() as db:
        q = Question(provider_id=world["p1"], branch_id=world["p1b1"], subject_id=bank["phy"], owner_staff_id=uuid4())
        db.add(q)
        db.commit()
        db.refresh(q)
        assert (q.scope, q.pattern, q.question_type, q.difficulty) == ("PROVIDER", "SINGLE_CORRECT", "MCQ_SINGLE", None)


def test_global_scope_rules_are_enforced_by_the_database(db_factory, world, bank):
    from uuid import uuid4

    from sqlalchemy.exc import IntegrityError

    from institute_common.models.papers import Question
    with db_factory() as db:
        db.add(Question(scope="GLOBAL", provider_id=world["p1"], branch_id=world["p1b1"], subject_id=bank["phy"], owner_staff_id=uuid4()))
        with pytest.raises(IntegrityError):
            db.commit()


def test_marks_are_decimal_safe(client, world, bank, login):
    q = create(client, login, bank["pa1"], q_body(bank["phy"], branch_id=str(world["p1b1"]), marks_negative="0"))
    item = client.get(f"{API}/questions/{q}", headers=login(bank["pa1"])).json()["question"]
    assert Decimal(item["latest"]["marks_negative"]) == 0
