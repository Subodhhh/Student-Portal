"""Three-scope Question Bank (docs/institute-portal/THREE_SCOPE_QUESTION_BANK_PLAN.md, decisions D1-D10):
  * every PROVIDER question - provider-wide or uploaded by a branch - is shared with ALL branches of its provider, never copied;
  * seeing a question is separate from changing it (the origin branch, the Provider Admin and platform roles change it);
  * provider-wide uploads and questions: the provider's own Provider Admin only; GLOBAL uploads: platform administrators only;
  * strict provider isolation; the worker re-checks every rule; duplicates by content; pictures follow their questions;
  * built papers (exact question versions) never change.
The worker's functions are called directly with the test database, as in test_bulk_import."""
import io
from uuid import UUID

import pytest

from institute_common.core.roles import Role
from institute_common.models.staff import StaffRoleAssignment
from institute_common.models.bulk_import import BulkImportJob
from institute_common.models.papers import PaperVersionQuestion, Question, QuestionImageAsset, QuestionVersion
from tests.test_bulk_import import q, store, work, xlsx  # noqa: F401  (store: fixture)
from tests.test_question_images import png

API = "/api/v1"


@pytest.fixture()
def sc(client, world, make_staff, login, db_factory, store):  # noqa: F811
    """Provider p1 (branches p1b1 "Mumbai", p1b2 "Thane") offers Physics and Chemistry with the chapter "Laws of Motion" > "Force";
    p2 offers Physics. Staff: platform Super Admin; p1 Provider Admin; Branch Admins of both p1 branches; a p1 teacher of Physics in
    Mumbai; the p2 Provider Admin."""
    from institute_common.models.academics import ProviderSubject, Subject, TeacherSubjectAssignment
    from inst_paper.services import taxonomy
    from uuid import uuid4
    c = {}
    with db_factory() as db:
        phy, chem = Subject(code="PHYSICS", name="Physics"), Subject(code="CHEM", name="Chemistry")
        db.add_all([phy, chem])
        db.flush()
        for prov, subjects in (("p1", (phy, chem)), ("p2", (phy,))):
            for s in subjects:
                db.add(ProviderSubject(provider_id=world[prov], subject_id=s.subject_id))
        for prov in ("p1", "p2"):
            ch = taxonomy.create_node(db, provider_id=world[prov], subject_id=phy.subject_id, level="CHAPTER", parent_id=None,
                                      name="Laws of Motion", staff_id=uuid4(), ip=None)
            taxonomy.create_node(db, provider_id=world[prov], subject_id=phy.subject_id, level="TOPIC", parent_id=ch.node_id,
                                 name="Force", staff_id=uuid4(), ip=None)
        db.commit()
        c["phy"], c["chem"] = phy.subject_id, chem.subject_id
    _, c["sa"] = make_staff([(Role.SUPER_ADMIN, None, None)])
    c["pa_id"], c["pa"] = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    _, c["mumbai"] = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    _, c["thane"] = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b2"])])
    t, c["teacher"] = make_staff([(Role.TEACHER, world["p1"], None)])
    _, c["pa2"] = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    with db_factory() as db:
        db.add(TeacherSubjectAssignment(staff_id=t, provider_id=world["p1"], branch_id=world["p1b1"], subject_id=c["phy"]))
        db.commit()
    state: dict = {"who": None, "headers": None}

    def headers(who):
        if state["who"] != who:            # the test client holds one session: log in again only when switching user
            state["who"], state["headers"] = who, login(c[who])
        return state["headers"]
    c["h"] = headers
    return c


def upload(client, sc, who, data: bytes, subject=None, **form):
    return client.post(f"{API}/bulk-imports", headers=sc["h"](who), data={"subject_id": str(subject or sc["phy"]), **form},
                       files={"file": ("questions.xlsx", io.BytesIO(data), "application/octet-stream")})


def imported(client, sc, who, db_factory, store, data: bytes, **form) -> dict:  # noqa: F811
    """Upload, let the worker read it, confirm, let the worker import; the final job."""
    r = upload(client, sc, who, data, **form)
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    work(db_factory, store)
    assert client.post(f"{API}/bulk-imports/{job_id}/confirm", headers=sc["h"](who)).status_code == 200
    work(db_factory, store)
    return client.get(f"{API}/bulk-imports/{job_id}", headers=sc["h"](who)).json()


def search(client, sc, who, **params) -> dict:
    r = client.get(f"{API}/questions", params={"page_size": 100, **params}, headers=sc["h"](who))
    assert r.status_code == 200, r.text
    return {x["question_id"]: x for x in r.json()["items"]}


def count(db_factory) -> int:
    with db_factory() as db:
        return db.query(Question).count()


def create(client, sc, who, **body) -> str:
    base = {"subject_id": str(sc["phy"]), "stem": "What is the SI unit of force?", "option_a": "Newton", "option_b": "Joule",
            "option_c": "Watt", "option_d": "Pascal", "correct_option": "A", "chapter": "Laws of Motion"}
    r = client.post(f"{API}/questions", json={**base, **body}, headers=sc["h"](who))
    assert r.status_code == 201, r.text
    return r.json()["question_id"]


def build(client, sc, who, branch, question_ids, check=False):
    body = {"scope": "PROVIDER", "branch_id": str(branch), "subject_id": str(sc["phy"]), "title": "Shared bank paper",
            "question_ids": [str(x) for x in question_ids]}
    return client.post(f"{API}/papers" + ("/check" if check else ""), json=body, headers=sc["h"](who))


ROWS = [q(stem="Which law explains rocket propulsion?", a="First", b="Second", c="Third", d="None"),
        q(stem="A body at rest stays at rest unless acted upon by?", a="Force", b="Mass", c="Speed", d="Time")]


# ------------------------------------------------------------------ sharing without copies (finalized rule, D9)

def test_a_branch_upload_is_shared_with_every_branch_of_the_provider_without_copies(client, world, sc, db_factory, store):  # noqa: F811
    before = count(db_factory)
    job = imported(client, sc, "mumbai", db_factory, store, xlsx(ROWS), branch_id=str(world["p1b1"]))
    assert job["status"] == "COMPLETED" and job["imported_rows"] == 2 and job["scope"] == "PROVIDER"
    assert count(db_factory) == before + 2                                       # exactly N rows, nothing per branch
    with db_factory() as db:
        ids = {str(x.question_id) for x in db.query(Question).filter_by(branch_id=world["p1b1"])}
        assert len(ids) == 2                                                     # the origin branch is kept

    thane = search(client, sc, "thane", scope="PROVIDER", branch_id=str(world["p1b2"]))
    assert ids <= set(thane)                                                     # Thane sees Mumbai's questions
    assert all(thane[i]["branch_id"] == str(world["p1b1"]) and thane[i]["can_edit"] is False for i in ids)
    built = build(client, sc, "thane", world["p1b2"], sorted(ids))               # ... and builds its own paper with them
    assert built.status_code == 201, built.text
    assert count(db_factory) == before + 2                                       # building copies nothing
    assert ids <= set(search(client, sc, "thane", scope="PROVIDER", origin_branch_id=str(world["p1b1"])))
    assert not ids & set(search(client, sc, "thane", scope="PROVIDER", origin="PROVIDER_WIDE"))

    # provider isolation: p2 never sees them, and naming p1 is refused (audited)
    assert not ids & set(search(client, sc, "pa2", scope="ALL"))
    assert client.get(f"{API}/questions", params={"scope": "PROVIDER", "provider_id": str(world["p1"])},
                      headers=sc["h"]("pa2")).status_code == 403
    assert client.get(f"{API}/questions", params={"scope": "PROVIDER", "branch_id": str(world["p1b2"])},
                      headers=sc["h"]("pa2")).status_code == 403
    assert client.get(f"{API}/questions/{sorted(ids)[0]}", headers=sc["h"]("pa2")).status_code == 403
    assert build(client, sc, "pa2", world["p2b1"], sorted(ids)).status_code == 422          # "Question not found."


def test_seeing_is_separate_from_changing(client, world, sc, db_factory):
    qid = create(client, sc, "mumbai", branch_id=str(world["p1b1"]))
    h = sc["h"]("thane")
    assert client.get(f"{API}/questions/{qid}", headers=h).status_code == 200
    denied = client.patch(f"{API}/questions/{qid}", json={"status": "ARCHIVED"}, headers=h)
    assert denied.status_code == 403 and "use it, not change it" in denied.text
    version = {"stem": "What is the SI unit of force, exactly?", "option_a": "Newton", "option_b": "Joule", "option_c": "Watt",
               "option_d": "Pascal", "correct_option": "A"}
    assert client.post(f"{API}/questions/{qid}/versions", json=version, headers=h).status_code == 403
    assert client.post(f"{API}/questions/{qid}/versions", json=version, headers=sc["h"]("mumbai")).status_code == 201     # origin
    assert client.patch(f"{API}/questions/{qid}", json={"difficulty": "HARD"}, headers=sc["h"]("pa")).status_code == 200   # provider


def test_teachers_see_their_subjects_from_every_branch(client, world, sc):
    physics_thane = create(client, sc, "thane", branch_id=str(world["p1b2"]))
    chemistry_thane = create(client, sc, "thane", branch_id=str(world["p1b2"]), subject_id=str(sc["chem"]), stem="What is pH?",
                             chapter="Acids")
    seen = search(client, sc, "teacher", scope="PROVIDER", branch_id=str(world["p1b1"]))
    assert physics_thane in seen and chemistry_thane not in seen                 # D2: subjects taught, any branch of the provider
    assert seen[physics_thane]["can_edit"] is False
    assert client.get(f"{API}/questions/{chemistry_thane}", headers=sc["h"]("teacher")).status_code == 403


def test_corrections_version_the_shared_question_for_the_provider_admin_and_fork_for_other_branches(client, world, sc, db_factory):
    qid = create(client, sc, "mumbai", branch_id=str(world["p1b1"]))
    pv = build(client, sc, "thane", world["p1b2"], [qid]).json()["paper_version_id"]
    edit = {"edits": [{"position": 1, "stem": "What is the SI unit of force (exactly)?"}], "reason": "clearer wording"}
    by_thane = client.post(f"{API}/paper-versions/{pv}/revisions", json=edit, headers=sc["h"]("thane"))
    assert by_thane.status_code == 201, by_thane.text
    with db_factory() as db:
        fork = db.query(Question).filter_by(forked_from_question_id=UUID(qid)).one()          # Thane may not change Mumbai's question
        assert fork.branch_id == world["p1b2"] and db.query(QuestionVersion).filter_by(question_id=UUID(qid)).count() == 1
    edit["edits"][0]["stem"] = "What is the SI unit of force (in SI)?"
    by_admin = client.post(f"{API}/paper-versions/{pv}/revisions", json=edit, headers=sc["h"]("pa"))
    assert by_admin.status_code == 201, by_admin.text
    with db_factory() as db:                                                       # D6: the Provider Admin versions the shared question
        assert db.query(QuestionVersion).filter_by(question_id=UUID(qid)).count() == 2
        assert db.query(Question).filter_by(forked_from_question_id=UUID(qid)).count() == 1


def test_built_papers_never_change_when_shared_questions_do(client, world, sc, db_factory):
    qid = create(client, sc, "mumbai", branch_id=str(world["p1b1"]))
    pv = build(client, sc, "thane", world["p1b2"], [qid]).json()["paper_version_id"]
    with db_factory() as db:
        before = [(r.position, r.question_version_id) for r in db.query(PaperVersionQuestion).filter_by(paper_version_id=UUID(pv))]
    h = sc["h"]("pa")
    assert client.post(f"{API}/questions/{qid}/versions", json={"stem": "Changed stem text?", "option_a": "Newton", "option_b": "Joule",
                       "option_c": "Watt", "option_d": "Pascal", "correct_option": "A"}, headers=h).status_code == 201
    assert client.patch(f"{API}/questions/{qid}", json={"status": "ARCHIVED"}, headers=h).status_code == 200
    with db_factory() as db:
        assert [(r.position, r.question_version_id) for r in db.query(PaperVersionQuestion).filter_by(paper_version_id=UUID(pv))] == before


# ------------------------------------------------------------------ provider-wide (D1, D3, D8)

def test_provider_wide_uploads_and_questions_are_for_the_provider_admin_only(client, world, sc, db_factory, store):  # noqa: F811
    job = imported(client, sc, "pa", db_factory, store, xlsx(ROWS), target="PROVIDER")      # provider derived from the assignment
    assert job["status"] == "COMPLETED" and job["branch_id"] is None and job["provider_id"] == str(world["p1"])
    with db_factory() as db:
        wide = {str(x.question_id) for x in db.query(Question).filter_by(provider_id=world["p1"]) if x.branch_id is None}
        assert len(wide) == 2 and all(x.chapter_id for x in db.query(Question).filter(Question.branch_id.is_(None)))
    assert wide <= set(search(client, sc, "thane", scope="PROVIDER", origin="PROVIDER_WIDE"))
    assert client.get(f"{API}/bulk-imports", params={"provider_id": str(world["p1"])}, headers=sc["h"]("pa")).json()["total"] == 1

    for who in ("thane", "teacher"):                                               # D1: not Branch Admins or teachers
        assert upload(client, sc, who, xlsx(ROWS), target="PROVIDER", provider_id=str(world["p1"])).status_code == 403
    assert upload(client, sc, "sa", xlsx(ROWS), target="PROVIDER", provider_id=str(world["p1"])).status_code == 403   # D3
    assert upload(client, sc, "pa2", xlsx(ROWS), target="PROVIDER", provider_id=str(world["p1"])).status_code == 403  # another provider
    assert client.get(f"{API}/bulk-imports", params={"provider_id": str(world["p1"])}, headers=sc["h"]("pa2")).status_code == 403

    wide_one = create(client, sc, "pa", provider_id=str(world["p1"]), stem="Unit of momentum?")          # D8: one by one too
    assert wide_one in search(client, sc, "thane", scope="PROVIDER", branch_id=str(world["p1b2"]))
    base = {"subject_id": str(sc["phy"]), "stem": "Unit of impulse?", "option_a": "N s", "option_b": "J", "option_c": "W",
            "option_d": "Pa", "correct_option": "A", "provider_id": str(world["p1"])}
    for who in ("thane", "sa"):
        assert client.post(f"{API}/questions", json=base, headers=sc["h"](who)).status_code == 403
    assert client.patch(f"{API}/questions/{wide_one}", json={"difficulty": "EASY"}, headers=sc["h"]("thane")).status_code == 403


def test_the_worker_stops_a_provider_wide_import_when_the_provider_admin_role_is_removed(client, world, sc, db_factory, store):  # noqa: F811
    job_id = upload(client, sc, "pa", xlsx(ROWS), target="PROVIDER").json()["job_id"]
    work(db_factory, store)
    assert client.post(f"{API}/bulk-imports/{job_id}/confirm", headers=sc["h"]("pa")).status_code == 200
    with db_factory() as db:
        db.query(StaffRoleAssignment).filter_by(staff_id=sc["pa_id"]).update({"status": "INACTIVE"})
        db.commit()
    before = count(db_factory)
    work(db_factory, store)
    with db_factory() as db:
        job = db.get(BulkImportJob, UUID(job_id))
        assert job.status == "FAILED" and "Provider Admin" in job.last_error
    assert count(db_factory) == before


# ------------------------------------------------------------------ platform (GLOBAL) imports, pictures (D5, D10)

def test_global_imports_are_for_platform_administrators_and_their_pictures_are_platform_owned(client, world, sc, db_factory, store):  # noqa: F811
    red = png("red")
    rows = [q(stem="Identify the instrument.", chapter="Measurement", topic="Instruments"), q(chapter="Any chapter, typed freely")]
    targets = client.get(f"{API}/bulk-imports/targets", headers=sc["h"]("sa")).json()
    assert targets["global"] and {s["name"] for s in targets["global"]["subjects"]} >= {"Physics", "Chemistry"} and targets["provider_wide"] == []
    job = imported(client, sc, "sa", db_factory, store, xlsx(rows, [(2, "Question Image", red)]), target="GLOBAL")
    assert job["status"] == "COMPLETED" and job["scope"] == "GLOBAL" and job["provider_id"] is None and job["branch_id"] is None
    assert client.get(f"{API}/bulk-imports/{job['job_id']}/taxonomy-issues", headers=sc["h"]("sa")).json() == []
    with db_factory() as db:
        made = db.query(Question).filter_by(scope="GLOBAL").all()
        assert {x.chapter for x in made} == {"Measurement", "Any chapter, typed freely"} and not any(x.chapter_id for x in made)
        picture = db.query(QuestionImageAsset).filter_by(checksum=__import__("hashlib").sha256(red).hexdigest()).one()
        assert picture.provider_id is None and picture.storage_key.startswith("question-images/platform/")
        asset = picture.asset_id
    for who in ("pa", "pa2"):                                                      # platform pictures: every provider's staff
        assert client.get(f"{API}/question-images/{asset}", headers=sc["h"](who)).status_code == 200
    assert upload(client, sc, "pa", xlsx(rows), target="GLOBAL").status_code == 403
    assert client.get(f"{API}/bulk-imports", params={"target": "GLOBAL"}, headers=sc["h"]("pa")).status_code == 403
    pa_targets = client.get(f"{API}/bulk-imports/targets", headers=sc["h"]("pa")).json()
    assert pa_targets["global"] is None and [p["provider_id"] for p in pa_targets["provider_wide"]] == [str(world["p1"])]
    assert client.get(f"{API}/bulk-imports/targets", headers=sc["h"]("thane")).json()["provider_wide"] == []


def test_a_provider_picture_used_by_a_platform_question_is_visible_to_every_provider(client, world, sc, db_factory):
    """D5: a provider paper copied into the platform keeps the provider's pictures (routes/internal.py)."""
    from institute_common.objectstore import get_object_store
    from inst_paper.services.image_assets import store_images
    from inst_paper.services.question_images import ExtractedImage
    data = png("teal")
    sha = __import__("hashlib").sha256(data).hexdigest()
    with db_factory() as db:
        asset = store_images(db, world["p1"], [ExtractedImage(name="a.png", mime="image/png", sha256=sha, width=48, height=32, data=data)],
                             sc["pa_id"], None, object_store=get_object_store())[sha]
        db.commit()
    h2 = sc["h"]("pa2")
    assert client.get(f"{API}/question-images/{asset}", headers=h2).status_code == 404        # p1's picture: not for p2 ...
    with db_factory() as db:
        g = Question(scope="GLOBAL", subject_id=sc["phy"], owner_staff_id=sc["pa_id"])
        db.add(g)
        db.flush()
        db.add(QuestionVersion(question_id=g.question_id, version_no=1, stem="Copied", option_a="a", option_b="b", option_c="c",
                               option_d="d", correct_option="A", created_by=sc["pa_id"], stem_image_id=asset))
        db.commit()
    assert client.get(f"{API}/question-images/{asset}", headers=h2).status_code == 200        # ... until a platform question uses it


# ------------------------------------------------------------------ duplicates (D4, D7)

def test_duplicates_are_found_across_the_providers_branches_only(client, world, sc, db_factory, store):  # noqa: F811
    create(client, sc, "sa", scope="GLOBAL", stem=ROWS[1]["Question Text"], option_a="Force", option_b="Mass", option_c="Speed",
           option_d="Time")                                                       # the same content in the platform bank
    first = imported(client, sc, "mumbai", db_factory, store, xlsx(ROWS[:1]), branch_id=str(world["p1b1"]))
    assert first["imported_rows"] == 1
    again = imported(client, sc, "thane", db_factory, store, xlsx(ROWS), branch_id=str(world["p1b2"]))
    assert again["duplicate_rows"] == 1 and again["imported_rows"] == 1       # Mumbai's question found; the platform one is not (D4)
    other = imported(client, sc, "pa2", db_factory, store, xlsx(ROWS), branch_id=str(world["p2b1"]))
    assert other["imported_rows"] == 2 and other["duplicate_rows"] == 0       # another provider's bank is never compared


def test_papers_refuse_identical_content_and_flag_uncertain_matches(client, world, sc):
    glob = create(client, sc, "sa", scope="GLOBAL")
    mine = create(client, sc, "thane", branch_id=str(world["p1b2"]))         # the same content, as the provider's own question
    refused = build(client, sc, "thane", world["p1b2"], [glob, mine])
    assert refused.status_code == 422 and "Same question as position 1." in str(refused.json())
    reordered = create(client, sc, "thane", branch_id=str(world["p1b2"]), option_a="Joule", option_b="Newton")
    long_stem = "A block slides down a frictionless incline of angle thirty degrees. Find its acceleration."
    one = create(client, sc, "thane", branch_id=str(world["p1b2"]), stem=long_stem, option_a="5", option_b="10", option_c="2", option_d="0")
    two = create(client, sc, "thane", branch_id=str(world["p1b2"]), stem=long_stem, option_a="4.9", option_b="9.8", option_c="1", option_d="0")
    short1 = create(client, sc, "thane", branch_id=str(world["p1b2"]), stem="Find x.", option_a="1", option_b="2", option_c="3", option_d="4")
    short2 = create(client, sc, "thane", branch_id=str(world["p1b2"]), stem="Find x.", option_a="5", option_b="6", option_c="7", option_d="8")
    checked = build(client, sc, "thane", world["p1b2"], [mine, reordered, one, two, short1, short2], check=True)
    assert checked.status_code == 200, checked.text
    review = {tuple(r["positions"]): r["reason"] for r in checked.json()["review"]}
    assert review == {(1, 2): "Same question with its options in a different order.", (3, 4): "Same question text with different options."}
    built = build(client, sc, "thane", world["p1b2"], [mine, reordered, one, two, short1, short2])
    assert built.status_code == 201 and len(built.json()["review"]) == 2      # flags never block


# ------------------------------------------------------------------ taxonomy reads (F8)

def test_branch_admins_read_their_providers_taxonomy_but_not_anothers(client, world, sc):
    h = sc["h"]("thane")
    nodes = client.get(f"{API}/question-taxonomy/nodes", params={"provider_id": str(world["p1"]), "subject_id": str(sc["phy"])}, headers=h)
    assert nodes.status_code == 200 and {n["name"] for n in nodes.json()} == {"Laws of Motion", "Force"}
    assert client.get(f"{API}/question-taxonomy/nodes", params={"provider_id": str(world["p2"]), "subject_id": str(sc["phy"])},
                      headers=h).status_code == 403


def test_the_origin_branch_is_named_for_everyone_who_may_see_the_question(client, world, sc, db_factory):
    from institute_common.models.org import Branch
    qid = create(client, sc, "mumbai", branch_id=str(world["p1b1"]))
    wide = create(client, sc, "pa", provider_id=str(world["p1"]), stem="Unit of momentum?")
    with db_factory() as db:
        mumbai = db.get(Branch, world["p1b1"]).name
    seen = search(client, sc, "thane", scope="PROVIDER", branch_id=str(world["p1b2"]))
    assert seen[qid]["origin_branch_name"] == mumbai and seen[wide]["origin_branch_name"] is None
    assert client.get(f"{API}/questions/{qid}", headers=sc["h"]("thane")).json()["question"]["origin_branch_name"] == mumbai
    assert client.get(f"{API}/questions/{qid}", headers=sc["h"]("pa2")).status_code == 403        # other providers learn nothing


def test_finished_import_history_shows_the_stored_pictures(client, world, sc, db_factory, store):  # noqa: F811
    teal = png("teal")
    job = imported(client, sc, "mumbai", db_factory, store, xlsx([q(stem="Identify the instrument.")], [(2, "Question Image", teal)]),
                   branch_id=str(world["p1b1"]))
    row = client.get(f"{API}/bulk-imports/{job['job_id']}/rows", headers=sc["h"]("mumbai")).json()["items"][0]
    url = row["images"]["question_image"]["url"]
    assert url.startswith("/api/v1/question-images/")                        # the staged copy is gone; the stored picture is shown
    got = client.get(url, headers=sc["h"]("thane"))
    assert got.status_code == 200 and got.content == teal                    # and every branch of the provider may see it
    assert client.get(url, headers=sc["h"]("pa2")).status_code == 404
