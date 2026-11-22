"""Canonical question taxonomy (Phase B): provider-scoped Chapter -> Topic -> Subtopic records, normalised duplicate prevention,
approved aliases, matching that never auto-maps by similarity, permissions and provider isolation, question linking and the
backfill of existing labels."""
from uuid import uuid4

import pytest

from institute_common.core.roles import Role
from institute_common.core.taxonomy import label_key
from tests.test_question_bank import bank, q_body  # noqa: F401  (fixture)

API = "/api/v1"


def node(client, h, provider, subject, level, name, parent=None, status=201):
    r = client.post(f"{API}/question-taxonomy/nodes", headers=h, json={"provider_id": str(provider), "subject_id": str(subject),
                                                                       "level": level, "name": name, "parent_id": parent})
    assert r.status_code == status, r.text
    return r.json()


@pytest.fixture()
def tree(client, world, bank, login):
    """p1 / Physics: chapter "Laws of Motion" > topic "Force" > subtopic "Units"."""
    h = login(bank["pa1"])
    ch = node(client, h, world["p1"], bank["phy"], "CHAPTER", "Laws of Motion")
    tp = node(client, h, world["p1"], bank["phy"], "TOPIC", "Force", ch["node_id"])
    st = node(client, h, world["p1"], bank["phy"], "SUBTOPIC", "Units", tp["node_id"])
    return {"h": h, "chapter": ch, "topic": tp, "subtopic": st}


def test_label_key_ignores_case_spacing_and_width():
    assert label_key("  Laws   of MOTION ") == label_key("laws of motion") == label_key("ｌａｗｓ of motion")
    assert label_key("") is None and label_key(None) is None


# ------------------------------------------------------------------ records

def test_names_are_unique_per_place_ignoring_case_and_spacing(client, world, bank, tree):
    h = tree["h"]
    r = node(client, h, world["p1"], bank["phy"], "CHAPTER", "  laws   OF motion ", status=409)
    assert r["detail"]["code"] == "NAME_TAKEN"
    # the same name is fine elsewhere: another subject, another provider, under another parent
    node(client, h, world["p1"], bank["chem"], "CHAPTER", "Laws of Motion")
    node(client, h, world["p1"], bank["phy"], "TOPIC", "Units", tree["topic"]["parent_id"])
    other = node(client, h, world["p1"], bank["phy"], "CHAPTER", "Gravitation")
    node(client, h, world["p1"], bank["phy"], "TOPIC", "Force", other["node_id"])


@pytest.mark.parametrize("level, parent_key, code", [
    ("CHAPTER", "chapter", "INVALID_PARENT"),        # a chapter has no parent
    ("TOPIC", None, "INVALID_PARENT"),               # a topic needs a chapter
    ("SUBTOPIC", "chapter", "INVALID_PARENT"),       # a subtopic's parent is a topic, not a chapter
])
def test_the_hierarchy_is_enforced(client, world, bank, tree, level, parent_key, code):
    parent = tree[parent_key]["node_id"] if parent_key else None
    r = node(client, tree["h"], world["p1"], bank["phy"], level, "Something", parent, status=422)
    assert r["detail"]["code"] == code


def test_invalid_names_are_rejected(client, world, bank, tree):
    for bad in ("", "   ", "!!!", "x" * 81, "bad\u0007name"):
        node(client, tree["h"], world["p1"], bank["phy"], "CHAPTER", bad, status=422)


# ------------------------------------------------------------------ permissions and isolation

def test_only_taxonomy_managers_of_the_provider_may_change_it(client, world, bank, tree, make_staff, login):
    _, branch_admin = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    for code in (bank["teacher"], branch_admin, bank["pa2"]):
        node(client, login(code), world["p1"], bank["phy"], "CHAPTER", "Optics", status=403)
    node(client, login(bank["sa"]), world["p1"], bank["phy"], "CHAPTER", "Optics")              # platform administrators may
    # another provider's admin cannot see, rename, retire or alias p1's records (404, as if they did not exist)
    h2 = login(bank["pa2"])
    nid = tree["chapter"]["node_id"]
    assert client.patch(f"{API}/question-taxonomy/nodes/{nid}", json={"name": "Hijacked"}, headers=h2).status_code == 404
    assert client.post(f"{API}/question-taxonomy/nodes/{nid}/aliases", json={"alias": "x"}, headers=h2).status_code == 404
    assert client.get(f"{API}/question-taxonomy/nodes?provider_id={world['p1']}&subject_id={bank['phy']}", headers=h2).status_code == 403
    # a parent of another provider is refused like a missing one
    r = node(client, h2, world["p2"], bank["phy"], "TOPIC", "Force", tree["chapter"]["node_id"], status=422)
    assert r["detail"]["code"] == "INVALID_PARENT"


def test_a_subject_not_offered_by_the_provider_is_refused(client, world, bank, tree, db_factory):
    from institute_common.models.academics import Subject
    with db_factory() as db:
        bio = Subject(code="BIO", name="Biology")
        db.add(bio)
        db.commit()
        bio_id = bio.subject_id
    assert node(client, tree["h"], world["p1"], bio_id, "CHAPTER", "Cells", status=422)["detail"]["code"] == "SUBJECT_NOT_ENABLED"


# ------------------------------------------------------------------ aliases and matching

def match(client, h, world, bank, level, value, parent=None):
    q = f"{API}/question-taxonomy/match?provider_id={world['p1']}&subject_id={bank['phy']}&level={level}&value={value}"
    r = client.get(q + (f"&parent_id={parent}" if parent else ""), headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_matching_uses_names_and_approved_aliases_and_only_suggests_similar_names(client, world, bank, tree):
    h, ch = tree["h"], tree["chapter"]
    m = match(client, h, world, bank, "CHAPTER", "LAWS OF MOTION")
    assert m["match"]["node_id"] == ch["node_id"] and m["matched_by"] == "NAME"
    m = match(client, h, world, bank, "CHAPTER", "Law of Motion")                         # similar, NOT the same: a suggestion only
    assert m["match"] is None and [s["node_id"] for s in m["suggestions"]] == [ch["node_id"]]
    r = client.post(f"{API}/question-taxonomy/nodes/{ch['node_id']}/aliases", json={"alias": "Newton's Laws"}, headers=h)
    assert r.status_code == 201 and [a["alias"] for a in r.json()["aliases"]] == ["Newton's Laws"]
    m = match(client, h, world, bank, "CHAPTER", "newton's  laws")
    assert m["match"]["node_id"] == ch["node_id"] and m["matched_by"] == "ALIAS"
    m = match(client, h, world, bank, "TOPIC", "force", parent=ch["node_id"])
    assert m["match"]["node_id"] == tree["topic"]["node_id"]
    assert match(client, h, world, bank, "TOPIC", "force")["match"] is None             # a topic exists only under its chapter


def test_aliases_never_collide(client, world, bank, tree):
    h, ch = tree["h"], tree["chapter"]
    other = node(client, h, world["p1"], bank["phy"], "CHAPTER", "Gravitation")
    url = f"{API}/question-taxonomy/nodes/{ch['node_id']}/aliases"
    assert client.post(url, json={"alias": "laws of motion"}, headers=h).status_code == 409          # its own name
    assert client.post(url, json={"alias": "GRAVITATION"}, headers=h).status_code == 409             # another record's name
    assert client.post(url, json={"alias": "Motion laws"}, headers=h).status_code == 201
    r = client.post(f"{API}/question-taxonomy/nodes/{other['node_id']}/aliases", json={"alias": "motion LAWS"}, headers=h)
    assert r.status_code == 409                                                                        # already another alias
    # and a new record cannot take an approved alias as its name
    assert node(client, h, world["p1"], bank["phy"], "CHAPTER", "Motion Laws", status=409)["detail"]["code"] == "NAME_TAKEN"


# ------------------------------------------------------------------ questions

def create_question(client, h, world, bank, **over):
    r = client.post(f"{API}/questions", json=q_body(bank["phy"], branch_id=str(world["p1b1"]), **over), headers=h)
    assert r.status_code == 201, r.text
    return client.get(f"{API}/questions/{r.json()['question_id']}", headers=h).json()["question"]


def test_question_labels_are_linked_to_records_and_unknown_labels_are_refused_where_records_exist(client, world, bank, tree):
    h = tree["h"]
    q = create_question(client, h, world, bank, chapter="laws  of MOTION", topic="FORCE", subtopic="units")
    assert (q["chapter_id"], q["topic_id"], q["subtopic_id"]) == (tree["chapter"]["node_id"], tree["topic"]["node_id"], tree["subtopic"]["node_id"])
    assert (q["chapter"], q["topic"], q["subtopic"]) == ("Laws of Motion", "Force", "Units")          # canonical spelling
    # where records exist, an unknown name is refused with suggestions (nothing is created silently, nothing saved)
    body = q_body(bank["phy"], branch_id=str(world["p1b1"]), stem="What is inertia, briefly?", chapter="Law of Motions", topic="Force")
    r = client.post(f"{API}/questions", json=body, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "TAXONOMY_UNMATCHED"
    [item] = r.json()["detail"]["items"]
    assert item["level"] == "CHAPTER" and item["preselect"] == tree["chapter"]["node_id"]
    # where a place has no records yet, a typed name is kept as text (it waits in the "Unlinked names" queue)
    optics = node(client, h, world["p1"], bank["phy"], "CHAPTER", "Optics")
    q = create_question(client, h, world, bank, stem="What is inertia, briefly?", chapter="Optics", topic="Lenses")
    assert q["chapter_id"] == optics["node_id"] and q["topic"] == "Lenses" and q["topic_id"] is None
    # re-classifying follows the new labels, and is refused for an unknown name
    r = client.patch(f"{API}/questions/{q['question_id']}", json={"chapter": "Laws of Motion", "topic": "Force"}, headers=h)
    assert r.status_code == 200 and r.json()["chapter_id"] == tree["chapter"]["node_id"] and r.json()["topic_id"] == tree["topic"]["node_id"]
    r = client.patch(f"{API}/questions/{q['question_id']}", json={"topic": "Forse"}, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["items"][0]["suggestions"][0]["node_id"] == tree["topic"]["node_id"]
    assert client.get(f"{API}/questions/{q['question_id']}", headers=h).json()["question"]["topic"] == "Force"     # unchanged


def test_renaming_relabels_linked_questions_and_retiring_keeps_their_links(client, world, bank, tree):
    h = tree["h"]
    q = create_question(client, h, world, bank)
    sub = tree["subtopic"]["node_id"]
    r = client.patch(f"{API}/question-taxonomy/nodes/{sub}", json={"name": "SI Units"}, headers=h)
    assert r.status_code == 200 and r.json()["name"] == "SI Units"
    assert client.get(f"{API}/questions/{q['question_id']}", headers=h).json()["question"]["subtopic"] == "SI Units"
    # a chapter with active topics cannot be retired; a leaf can, and it then matches nothing new
    assert client.patch(f"{API}/question-taxonomy/nodes/{tree['chapter']['node_id']}", json={"status": "RETIRED"}, headers=h).status_code == 409
    assert client.patch(f"{API}/question-taxonomy/nodes/{sub}", json={"status": "RETIRED"}, headers=h).json()["status"] == "RETIRED"
    assert match(client, h, world, bank, "SUBTOPIC", "SI Units", parent=tree["topic"]["node_id"])["match"] is None
    assert client.get(f"{API}/questions/{q['question_id']}", headers=h).json()["question"]["subtopic_id"] == sub   # existing link kept
    # retired names stay reserved, so no near-duplicate can be created next to them
    node(client, h, world["p1"], bank["phy"], "SUBTOPIC", "si units", tree["topic"]["node_id"], status=409)


def test_global_questions_are_not_linked_to_a_providers_records(client, world, bank, tree, login):
    r = client.post(f"{API}/questions", json=q_body(bank["phy"], scope="GLOBAL"), headers=login(bank["sa"]))
    assert r.status_code == 201
    q = client.get(f"{API}/questions/{r.json()['question_id']}", headers=login(bank["sa"])).json()["question"]
    assert q["chapter_id"] is None and q["chapter"] == "Laws of Motion"


# ------------------------------------------------------------------ backfill of existing labels

def test_backfill_merges_spellings_reports_orphans_and_is_safe_to_rerun(world, bank, db_factory):
    from institute_common.models.papers import Question, QuestionTaxonomyNode
    from inst_paper.services.taxonomy_backfill import backfill
    rows = [("Laws of Motion", "Force", None), ("Laws of Motion", "Force", None), ("laws of  motion", "force", "Units"),
            ("LAWS OF MOTION", "Friction", None),
            (None, "Orphan topic", None), ("Optics", None, None)]
    with db_factory() as db:
        for chapter, topic, subtopic in rows:
            db.add(Question(provider_id=world["p1"], branch_id=world["p1b1"], subject_id=bank["phy"], owner_staff_id=uuid4(),
                            chapter=chapter, topic=topic, subtopic=subtopic))
        db.commit()
        actor = uuid4()
        dry = backfill(db, staff_id=actor, dry_run=True)
        assert db.query(QuestionTaxonomyNode).count() == 0                                  # a dry run writes nothing
        # 2 chapters (Laws of Motion, Optics) + 2 topics (Force, Friction) + 1 subtopic (Units)
        assert dry["totals"]["records_to_create"] == 5 and dry["totals"]["orphan_labels"] == 1
        [subject] = dry["subjects"]
        merged = {m["name"]: m["variants"] for m in subject["merged_spellings"]}
        # the most used spelling wins; spacing is cleaned before counting
        assert merged["Laws of Motion"] == ["LAWS OF MOTION", "Laws of Motion", "laws of motion"]

        done = backfill(db, staff_id=actor, dry_run=False)
        assert done["totals"]["records_to_create"] == 5
        chapters = {n.name for n in db.query(QuestionTaxonomyNode).filter_by(level="CHAPTER")}
        assert chapters == {"Laws of Motion", "Optics"}                                       # one record per normalised name
        linked = db.query(Question).filter(Question.chapter == "Laws of Motion").all()
        assert len(linked) == 4 and len({q.chapter_id for q in linked}) == 1 and all(q.topic_id for q in linked)
        orphan = db.query(Question).filter(Question.topic == "Orphan topic").one()
        assert orphan.chapter_id is None and orphan.topic_id is None                          # kept as text, not invented

        again = backfill(db, staff_id=actor, dry_run=False)                                   # idempotent
        assert again["totals"]["records_to_create"] == 0 and again["totals"]["records_existing"] == 5
        assert db.query(QuestionTaxonomyNode).count() == 5
