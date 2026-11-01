"""GET /exams: the Results page's list of every exam the caller may read, across all of their providers and branches (the page has no
provider/branch selector). Each exam is authorised exactly as its own detail page (exam_access), so nothing new becomes visible."""
from institute_common.core.roles import Role
from tests.test_exams import API, _published_exam, ctx, pa  # noqa: F401


def listed(client, h, **query):
    r = client.get(f"{API}/exams", params={"page_size": 100, **query}, headers=h)
    assert r.status_code == 200, r.text
    return {x["exam_id"]: x for x in r.json()["items"]}


def test_every_exam_in_scope_is_listed_once_with_its_detail_page_counts(client, world, ctx, pa, make_staff, login, db_factory):
    exam_id = _published_exam(client, world, ctx, login(pa), db_factory)
    _, sa = make_staff([(Role.SUPER_ADMIN, None, None)])
    _, admin = make_staff([(Role.ADMIN, None, None)])
    _, ba = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    for who in (pa, sa, admin, ba, ctx["tphy"]):
        h = login(who)
        rows = listed(client, h, status="PUBLISHED")
        assert list(rows) == [exam_id]
        detail = client.get(f"{API}/exams/{exam_id}", headers=h).json()
        assert rows[exam_id]["assigned_students"] == detail["assigned_students"] == 2
        assert rows[exam_id]["assignment_status"] == detail["assignment_status"]
        assert (rows[exam_id]["provider_name"], rows[exam_id]["branch_name"]) == ("Provider p1", "p1-b1")
    h = login(pa)
    assert listed(client, h, status="CANCELLED") == {}
    assert list(listed(client, h, q="weekly")) == [exam_id] and listed(client, h, q="nothing like it") == {}


def test_other_providers_branches_and_untaught_teachers_see_nothing_and_leave_no_denials(client, world, ctx, pa, make_staff, login, db_factory):
    from institute_common.models.academics import TeacherSubjectAssignment
    from institute_common.models.staff import AuditLog
    exam_id = _published_exam(client, world, ctx, login(pa), db_factory)
    _, other_provider = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    _, other_branch = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b2"])])
    with db_factory() as db:          # the Physics teacher now teaches only a batch this exam does not target
        for t in db.query(TeacherSubjectAssignment).filter_by(staff_id=ctx["t_phy_id"]):
            t.batch_id = ctx["other_batch"]
        db.commit()
        denied = db.query(AuditLog).filter_by(action="authz.denied").count()
    for who in (other_provider, other_branch, ctx["tphy"]):
        h = login(who)
        assert listed(client, h) == {}
        assert client.get(f"{API}/exams/{exam_id}", headers=h).status_code == 403        # the detail page agrees
    with db_factory() as db:          # only the three direct requests were recorded as denied, never the list
        assert db.query(AuditLog).filter_by(action="authz.denied").count() == denied + 3


def test_listing_needs_a_session(client):
    assert client.get(f"{API}/exams", headers={"Origin": "http://localhost:5174"}).status_code == 401
