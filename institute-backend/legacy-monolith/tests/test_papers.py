import io

import pytest

from app.core.roles import Role
from app.services import extraction
from app.services.filestore import LocalFileStore, set_store

API = "/api/v1"

GOOD = ("question,option_a,option_b,option_c,option_d,correct,marks,negative_marks,explanation,topic\n"
        "What is 2+2?,1,2,3,4,D,4,1,Basic sum,Arithmetic\n"
        "Capital of France?,Paris,Rome,Berlin,Madrid,A,4,1,,Geography\n")


@pytest.fixture(autouse=True)
def tmp_store(tmp_path):
    set_store(LocalFileStore(str(tmp_path / "files")))
    yield
    set_store(None)


@pytest.fixture()
def setup(client, world, make_staff, login, db_factory):
    from app.models.academics import ProviderSubject, Subject, TeacherSubjectAssignment
    with db_factory() as db:
        phy, chem = Subject(code="PHYSICS", name="Physics"), Subject(code="CHEM", name="Chemistry")
        db.add_all([phy, chem])
        db.flush()
        for prov in ("p1", "p2"):
            for s in (phy, chem):
                db.add(ProviderSubject(provider_id=world[prov], subject_id=s.subject_id))
        db.commit()
        ids = {"phy": phy.subject_id, "chem": chem.subject_id}
    teacher, tcode = make_staff([(Role.TEACHER, world["p1"], None)])
    other_teacher, ocode = make_staff([(Role.TEACHER, world["p1"], None)])
    with db_factory() as db:
        for t in (teacher, other_teacher):
            db.add(TeacherSubjectAssignment(staff_id=t, provider_id=world["p1"], branch_id=world["p1b1"], subject_id=ids["phy"]))
        db.commit()
    return {**ids, "teacher": teacher, "tcode": tcode, "ocode": ocode}


def upload(client, h, world, setup, csv=GOOD, branch="p1b1", subject="phy", name="paper.csv", ctype="text/csv", title="Mock Test 1", paper_id=None):
    data = {"title": title}
    if paper_id:
        data["paper_id"] = paper_id
    return client.post(f"{API}/branches/{world[branch]}/subjects/{setup[subject]}/papers/upload",
                       files={"file": (name, io.BytesIO(csv.encode()), ctype)}, data=data, headers=h)


# ------------------------------------------------------------------ extraction unit tests

def test_extraction_accepts_valid_sheet():
    r = extraction.extract_csv(GOOD.encode(), 500)
    assert r.ok and len(r.questions) == 2 and r.questions[0].correct == "D"


@pytest.mark.parametrize("bad,needle", [
    ("question,option_a\nq,a\n", "Missing required"),
    (GOOD + "Broken?,a,,c,d,A,4,1,,\n", "Option B is empty"),
    (GOOD + "Q3?,a,b,c,d,E,4,1,,\n", "A, B, C or D"),
    (GOOD + "Q3?,a,b,c,d,A,zero,1,,\n", "Marks must be"),
    (GOOD + "Q3?,a,b,c,d,A,4,-1,,\n", "Negative marks"),
    (GOOD + "What is 2+2?,1,2,3,4,D,4,1,,\n", "Duplicate of row 2"),
    (GOOD + "Q4?,a,a,c,d,A,4,1,,\n", "identical"),
    (GOOD + ",a,b,c,d,A,4,1,,\n", "Question text is empty"),
])
def test_one_bad_row_rejects_entire_paper(bad, needle):
    r = extraction.extract_csv(bad.encode(), 500)
    assert not r.ok and r.questions == []                      # nothing partially imported
    assert any(needle in e["message"] for e in r.errors), r.errors


def test_extraction_hostile_inputs():
    assert not extraction.extract_csv(b"\x00\x01\x02", 500).ok
    assert not extraction.extract_csv("\xff\xfe".encode("latin1") + b"abc", 500).ok
    assert not extraction.extract_csv(b"", 500).ok
    big = GOOD + "".join(f"Q{i}?,a{i},b,c,d,A,4,1,,\n" for i in range(10))
    assert not extraction.extract_csv(big.encode(), 5).ok
    cell = GOOD + "x" * 6000 + ",a,b,c,d,A,4,1,,\n"
    assert not extraction.extract_csv(cell.encode(), 500).ok
    formula = extraction.extract_csv((GOOD + "=cmd|' /C calc'!A0,a,b,c,d,A,4,1,,\n").encode(), 500)
    assert formula.ok and formula.questions[-1].stem.startswith("=cmd")   # stored as inert text, never evaluated


# ------------------------------------------------------------------ API workflow

def test_upload_creates_validated_immutable_version(client, world, setup, make_staff, login):
    _, ba = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    h = login(ba)
    r = upload(client, h, world, setup)
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["status"] == "VALIDATED" and j["question_count"] == 2 and j["version_no"] == 1 and j["total_marks"] == "8.00"
    detail = client.get(f"{API}/paper-versions/{j['paper_version_id']}", headers=h).json()
    assert [q["correct_option"] for q in detail["questions"]] == ["D", "A"]
    assert detail["history"][0]["to"] == "VALIDATED"
    dup = upload(client, h, world, setup, title="Same again")
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "DUPLICATE_PAPER"


def test_rejected_upload_imports_nothing_and_reports_rows(client, world, setup, make_staff, login, db_factory):
    from app.models.papers import Paper, Question
    _, ba = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    h = login(ba)
    r = upload(client, h, world, setup, csv=GOOD + "Q3?,a,b,c,d,Z,4,1,,\n")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "EXTRACTION_FAILED"
    errs = r.json()["detail"]["errors"]
    assert errs[0]["row"] == 4 and errs[0]["field"] == "correct"
    with db_factory() as db:
        assert db.query(Paper).count() == 0 and db.query(Question).count() == 0
    up = client.get(f"{API}/uploads/{r.json()['detail']['upload_id']}", headers=h).json()
    assert up["status"] == "REJECTED" and up["errors"]


def test_upload_validation_of_file_type_size_and_subject(client, world, setup, make_staff, login, monkeypatch):
    from app.config import settings
    _, ba = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    h = login(ba)
    assert upload(client, h, world, setup, name="paper.exe").status_code == 415
    assert upload(client, h, world, setup, ctype="application/x-msdownload").status_code == 415
    monkeypatch.setattr(settings, "max_upload_bytes", 50)
    assert upload(client, h, world, setup).status_code == 413


def test_lifecycle_approve_publish_supersede_withdraw(client, world, setup, make_staff, login):
    _, ba = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    h = login(ba)
    v1 = upload(client, h, world, setup).json()
    pv1 = v1["paper_version_id"]
    assert client.post(f"{API}/paper-versions/{pv1}/publish", json={}, headers=h).status_code == 409      # must approve first
    assert client.post(f"{API}/paper-versions/{pv1}/approve", json={}, headers=h).json()["status"] == "APPROVED"
    assert client.post(f"{API}/paper-versions/{pv1}/approve", json={}, headers=h).status_code == 409
    assert client.post(f"{API}/paper-versions/{pv1}/publish", json={}, headers=h).json()["status"] == "PUBLISHED"
    v2 = upload(client, h, world, setup, csv=GOOD + "Extra?,a,b,c,d,B,4,1,,\n", paper_id=v1["paper_id"]).json()
    assert v2["version_no"] == 2
    client.post(f"{API}/paper-versions/{v2['paper_version_id']}/approve", json={}, headers=h)
    client.post(f"{API}/paper-versions/{v2['paper_version_id']}/publish", json={}, headers=h)
    old = client.get(f"{API}/paper-versions/{pv1}", headers=h).json()["latest_version"]
    assert old["status"] == "SUPERSEDED"                                  # v1 content/identity preserved
    assert len(client.get(f"{API}/paper-versions/{pv1}", headers=h).json()["questions"]) == 2
    assert client.post(f"{API}/paper-versions/{v2['paper_version_id']}/withdraw", json={"reason": ""}, headers=h).status_code == 422
    assert client.post(f"{API}/paper-versions/{v2['paper_version_id']}/withdraw", json={"reason": "wrong key found"}, headers=h).json()["status"] == "WITHDRAWN"


def test_teacher_can_approve_and_publish_only_own_paper(client, world, setup, make_staff, login):
    th = login(setup["tcode"])
    mine = upload(client, th, world, setup, title="Teacher paper").json()
    assert client.post(f"{API}/paper-versions/{mine['paper_version_id']}/approve", json={}, headers=th).status_code == 200
    assert client.post(f"{API}/paper-versions/{mine['paper_version_id']}/publish", json={}, headers=th).status_code == 200
    # a colleague teaching the same subject cannot approve someone else's paper
    other = upload(client, th, world, setup, csv=GOOD + "Another?,a,b,c,d,C,4,1,,\n", title="Second").json()
    oh = login(setup["ocode"])
    assert client.post(f"{API}/paper-versions/{other['paper_version_id']}/approve", json={}, headers=oh).status_code == 403
    # ...but may read it (same subject, same branch) and cannot read other subjects
    assert client.get(f"{API}/paper-versions/{other['paper_version_id']}", headers=oh).status_code == 200


def test_teacher_cannot_upload_outside_teaching_assignment(client, world, setup, make_staff, login):
    th = login(setup["tcode"])
    assert upload(client, th, world, setup, subject="chem").status_code == 403          # not their subject
    assert upload(client, th, world, setup, branch="p1b2").status_code == 403           # not their branch
    assert upload(client, th, world, setup, branch="p2b1").status_code == 403           # other provider


def test_cross_tenant_paper_access_denied(client, world, setup, make_staff, login):
    _, ba1 = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    _, pa2 = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    _, ba_sib = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b2"])])
    h1 = login(ba1)
    j = upload(client, h1, world, setup).json()
    for code in (pa2, ba_sib):
        h = login(code)
        assert client.get(f"{API}/paper-versions/{j['paper_version_id']}", headers=h).status_code == 403
        assert client.get(f"{API}/uploads/{j['upload_id']}", headers=h).status_code == 403
        assert client.get(f"{API}/uploads/{j['upload_id']}/file", headers=h).status_code == 403
        assert client.post(f"{API}/paper-versions/{j['paper_version_id']}/approve", json={}, headers=h).status_code == 403
        assert client.get(f"{API}/branches/{world['p1b1']}/papers", headers=h).status_code == 403
        assert client.get(f"{API}/branches/{world['p1b1']}/questions", headers=h).status_code == 403
    h1 = login(ba1)
    f = client.get(f"{API}/uploads/{j['upload_id']}/file", headers=h1)
    assert f.status_code == 200 and f.headers["content-disposition"].startswith("attachment") and f.text == GOOD


def test_super_admin_override_requires_reason_and_is_recorded(client, world, setup, make_staff, login):
    _, ba = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    _, ad = make_staff([(Role.ADMIN, None, None)])
    _, sa = make_staff([(Role.SUPER_ADMIN, None, None)])
    pv = upload(client, login(ba), world, setup).json()["paper_version_id"]
    body = {"to_status": "PUBLISHED", "reason": "regulator-mandated correction"}
    assert client.post(f"{API}/paper-versions/{pv}/override-status", json=body, headers=login(ba)).status_code == 403
    assert client.post(f"{API}/paper-versions/{pv}/override-status", json=body, headers=login(ad)).status_code == 403   # Admin cannot override
    h = login(sa)
    assert client.post(f"{API}/paper-versions/{pv}/override-status", json={**body, "reason": ""}, headers=h).status_code == 422
    assert client.post(f"{API}/paper-versions/{pv}/override-status", json=body, headers=h).json()["status"] == "PUBLISHED"
    hist = client.get(f"{API}/paper-versions/{pv}", headers=h).json()["history"]
    assert hist[-1]["event"] == "OVERRIDE" and hist[-1]["reason"] == body["reason"] and hist[-1]["from"] == "VALIDATED"


def test_paper_sharing_is_management_only(client, world, setup, make_staff, login):
    th = login(setup["tcode"])
    j = upload(client, th, world, setup).json()
    client.post(f"{API}/paper-versions/{j['paper_version_id']}/approve", json={}, headers=th)
    client.post(f"{API}/paper-versions/{j['paper_version_id']}/publish", json={}, headers=th)
    body = {"provider_id": str(world["p2"])}
    assert client.post(f"{API}/papers/{j['paper_id']}/shares", json=body, headers=th).status_code == 403   # teacher cannot release content
    _, pa1 = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    _, pa2 = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    assert client.post(f"{API}/papers/{j['paper_id']}/shares", json=body, headers=login(pa2)).status_code == 403   # recipient cannot self-grant
    assert client.post(f"{API}/papers/{j['paper_id']}/shares", json=body, headers=login(pa1)).status_code == 201


def test_question_bank_versions_are_append_only(client, world, setup, make_staff, login):
    th = login(setup["tcode"])
    body = {"branch_id": str(world["p1b1"]), "subject_id": str(setup["phy"]), "stem": "Unit of force?", "option_a": "N", "option_b": "J",
            "option_c": "W", "option_d": "Pa", "correct_option": "A", "marks_positive": "4", "marks_negative": "1"}
    q = client.post(f"{API}/questions", json=body, headers=th)
    assert q.status_code == 201 and q.json()["version_no"] == 1
    edit = {k: v for k, v in body.items() if k not in {"branch_id", "subject_id"}}
    v2 = client.post(f"{API}/questions/{q.json()['question_id']}/versions", json={**edit, "correct_option": "B"}, headers=th)
    assert v2.status_code == 201 and v2.json()["version_no"] == 2
    assert client.post(f"{API}/questions", json={**body, "subject_id": str(setup["chem"])}, headers=th).status_code == 403
    assert client.post(f"{API}/questions", json={**body, "correct_option": "Z"}, headers=th).status_code == 422
    oh = login(setup["ocode"])
    assert client.post(f"{API}/questions/{q.json()['question_id']}/versions", json=edit, headers=oh).status_code == 403   # not the owner
