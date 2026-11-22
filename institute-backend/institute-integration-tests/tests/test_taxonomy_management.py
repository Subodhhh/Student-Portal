"""Chapters & Topics management (docs/institute-portal/TAXONOMY_ALIASES_PLAN.md): "Did you mean …?" suggestions, the tree with
counts, merging duplicates, removing aliases, alias requests (anyone asks, the Provider Admin decides, the requester is told), the
platform list for platform questions, restoring retired records and the "Unlinked names" queue."""
from uuid import UUID, uuid4

from institute_common.models.bulk_import import PaperNotificationOutbox
from institute_common.models.papers import Question
from tests.test_question_bank import bank, q_body  # noqa: F401  (fixture)
from tests.test_question_taxonomy import API, node, tree  # noqa: F401  (fixture)


def suggest(client, h, world, bank, level, value, parent=None, provider="p1"):
    url = f"{API}/question-taxonomy/suggest?subject_id={bank['phy']}&level={level}&value={value}"
    url += f"&provider_id={world[provider]}" if provider else ""
    r = client.get(url + (f"&parent_id={parent}" if parent else ""), headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_suggestions_preselect_the_one_clearly_meant_and_say_where_else_a_name_lives(client, world, bank, tree):
    h, ch = tree["h"], tree["chapter"]
    node(client, h, world["p1"], bank["phy"], "CHAPTER", "Gravitation")
    s = suggest(client, h, world, bank, "CHAPTER", "Law of Motions")
    assert s["match"] is None and s["preselect"] == ch["node_id"] and s["in_use"] is True
    assert s["suggestions"][0]["band"] == "HIGH" and s["suggestions"][0]["matched"] == "Laws of Motion"
    assert suggest(client, h, world, bank, "CHAPTER", "laws of motion")["match"]["node_id"] == ch["node_id"]
    assert suggest(client, h, world, bank, "CHAPTER", "Optics")["suggestions"] == []
    other = node(client, h, world["p1"], bank["phy"], "CHAPTER", "Kinematics")
    s = suggest(client, h, world, bank, "TOPIC", "Forces", parent=other["node_id"])        # "Force" is a topic of another chapter
    assert s["in_use"] is False and [(e["name"], e["parent_name"]) for e in s["elsewhere"]] == [("Force", "Laws of Motion")]


def test_the_tree_nests_records_with_aliases_and_question_counts(client, world, bank, tree, login):
    h = tree["h"]
    client.post(f"{API}/question-taxonomy/nodes/{tree['chapter']['node_id']}/aliases", json={"alias": "Newton's Laws"}, headers=h)
    assert client.post(f"{API}/questions", json=q_body(bank["phy"], branch_id=str(world["p1b1"])), headers=h).status_code == 201
    r = client.get(f"{API}/question-taxonomy/tree?provider_id={world['p1']}&subject_id={bank['phy']}", headers=login(bank["teacher"]))
    assert r.status_code == 200, r.text
    [chapter] = r.json()
    assert chapter["name"] == "Laws of Motion" and [a["alias"] for a in chapter["aliases"]] == ["Newton's Laws"]
    assert chapter["questions"] == 1 and chapter["children"][0]["name"] == "Force"
    assert chapter["children"][0]["children"][0]["name"] == "Units"
    url = f"{API}/question-taxonomy/tree?provider_id={world['p1']}&subject_id={bank['phy']}"
    assert client.get(url, headers=login(bank["pa2"])).status_code == 403                    # another provider


def test_merging_moves_questions_children_and_aliases_and_keeps_the_name_as_an_alias(client, world, bank, tree, db_factory):
    h, keep = tree["h"], tree["chapter"]
    dup = node(client, h, world["p1"], bank["phy"], "CHAPTER", "Newtons Laws")
    node(client, h, world["p1"], bank["phy"], "TOPIC", "Force", dup["node_id"])                     # same-named topic: merged too
    extra = node(client, h, world["p1"], bank["phy"], "TOPIC", "Inertia", dup["node_id"])
    client.post(f"{API}/question-taxonomy/nodes/{dup['node_id']}/aliases", json={"alias": "Motion laws"}, headers=h)
    r = client.post(f"{API}/questions", json=q_body(bank["phy"], branch_id=str(world["p1b1"]), chapter="Newtons Laws", topic="Inertia"),
                    headers=h)
    qid = r.json()["question_id"]
    r = client.post(f"{API}/question-taxonomy/nodes/{dup['node_id']}/merge", json={"into_id": keep["node_id"]}, headers=h)
    assert r.status_code == 200, r.text
    assert {a["alias"] for a in r.json()["aliases"]} == {"Newtons Laws", "Motion laws"}
    q = client.get(f"{API}/questions/{qid}", headers=h).json()["question"]
    assert (q["chapter_id"], q["chapter"], q["topic_id"]) == (keep["node_id"], "Laws of Motion", extra["node_id"])
    tree_now = client.get(f"{API}/question-taxonomy/tree?provider_id={world['p1']}&subject_id={bank['phy']}", headers=h).json()
    assert [c["name"] for c in tree_now] == ["Laws of Motion"]
    assert sorted(t["name"] for t in tree_now[0]["children"]) == ["Force", "Inertia"]
    # the old name now links new questions to the kept record
    s = suggest(client, h, world, bank, "CHAPTER", "newtons laws")
    assert s["match"]["node_id"] == keep["node_id"] and s["matched_by"] == "ALIAS"
    again = client.post(f"{API}/question-taxonomy/nodes/{dup['node_id']}/merge", json={"into_id": keep["node_id"]}, headers=h)
    assert again.status_code == 409
    other = node(client, h, world["p1"], bank["phy"], "CHAPTER", "Optics")
    bad = client.post(f"{API}/question-taxonomy/nodes/{tree['topic']['node_id']}/merge", json={"into_id": other["node_id"]}, headers=h)
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "NOT_SAME_PLACE"


def test_an_alias_can_be_removed(client, world, bank, tree):
    h, ch = tree["h"], tree["chapter"]
    alias = client.post(f"{API}/question-taxonomy/nodes/{ch['node_id']}/aliases", json={"alias": "Newton's Laws"}, headers=h).json()["aliases"][0]
    r = client.delete(f"{API}/question-taxonomy/aliases/{alias['alias_id']}", headers=h)
    assert r.status_code == 200 and r.json()["aliases"] == []
    assert suggest(client, h, world, bank, "CHAPTER", "Newton's Laws")["match"] is None
    assert client.delete(f"{API}/question-taxonomy/aliases/{alias['alias_id']}", headers=h).status_code == 404


def test_alias_requests_are_decided_by_the_provider_admin_and_the_requester_is_told(client, world, bank, tree, login, db_factory):
    ch = tree["chapter"]
    teacher = login(bank["teacher"])                     # one client: each login replaces the session, so log in again per actor
    body = {"node_id": ch["node_id"], "alias": "Newtons1st Law of Motion", "source": "QUESTION"}
    r = client.post(f"{API}/question-taxonomy/alias-requests", json=body, headers=teacher)
    assert r.status_code == 201 and r.json()["approved"] is False
    rid = r.json()["request"]["request_id"]
    assert client.post(f"{API}/question-taxonomy/alias-requests", json=body, headers=teacher).json()["request"]["request_id"] == rid
    mine = client.get(f"{API}/question-taxonomy/alias-requests?provider_id={world['p1']}", headers=teacher).json()
    assert [m["request_id"] for m in mine] == [rid] and mine[0]["node_name"] == "Laws of Motion"
    assert client.post(f"{API}/question-taxonomy/alias-requests/{rid}/approve", headers=teacher).status_code == 404     # not theirs
    assert client.post(f"{API}/question-taxonomy/alias-requests/{rid}/approve", headers=login(bank["pa2"])).status_code == 404
    h = login(bank["pa1"])
    assert suggest(client, h, world, bank, "CHAPTER", "Newtons1st Law of Motion")["match"] is None          # not yet an alias
    r = client.post(f"{API}/question-taxonomy/alias-requests/{rid}/approve", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "APPROVED", r.text
    assert suggest(client, h, world, bank, "CHAPTER", "newtons1st law of motion")["matched_by"] == "ALIAS"
    assert client.post(f"{API}/question-taxonomy/alias-requests/{rid}/reject", headers=h).status_code == 409    # already decided
    other = client.post(f"{API}/question-taxonomy/alias-requests", json={**body, "alias": "LOM"}, headers=login(bank["teacher"])).json()["request"]
    h = login(bank["pa1"])
    r = client.post(f"{API}/question-taxonomy/alias-requests/{other['request_id']}/reject", json={"reason": "Too short"}, headers=h)
    assert r.json()["status"] == "REJECTED" and r.json()["decision_reason"] == "Too short"
    with db_factory() as db:
        sent = [row.payload for row in db.query(PaperNotificationOutbox).order_by(PaperNotificationOutbox.created_at)]
    assert [p["kind"] for p in sent] == ["TAXONOMY_ALIAS_DECIDED"] * 2
    assert "approved" in sent[0]["title"] and "Reason: Too short" in sent[1]["body"]
    # the provider admin's own "remember this spelling" is approved at once
    r = client.post(f"{API}/question-taxonomy/alias-requests", json={**body, "alias": "Motion laws"}, headers=h)
    assert r.json()["approved"] is True and "Motion laws" in [a["alias"] for a in r.json()["node"]["aliases"]]


def test_the_platform_list_is_managed_by_platform_administrators_and_links_platform_questions(client, world, bank, login):
    body = {"subject_id": str(bank["phy"]), "level": "CHAPTER", "name": "Laws of Motion"}
    assert client.post(f"{API}/question-taxonomy/nodes", json=body, headers=login(bank["pa1"])).status_code == 403
    sa = login(bank["sa"])
    ch = client.post(f"{API}/question-taxonomy/nodes", json=body, headers=sa)
    assert ch.status_code == 201 and ch.json()["provider_id"] is None, ch.text
    assert client.post(f"{API}/question-taxonomy/nodes", json=body, headers=sa).status_code == 409
    r = client.post(f"{API}/questions", json=q_body(bank["phy"], scope="GLOBAL", chapter="laws of MOTION"), headers=sa)
    q = client.get(f"{API}/questions/{r.json()['question_id']}", headers=sa).json()["question"]
    assert q["chapter_id"] == ch.json()["node_id"] and q["chapter"] == "Laws of Motion"
    pa = login(bank["pa1"])
    tree_ = client.get(f"{API}/question-taxonomy/tree?subject_id={bank['phy']}", headers=pa)                 # readable by provider staff
    assert [c["name"] for c in tree_.json()] == ["Laws of Motion"] and tree_.json()[0]["questions"] == 1
    node(client, pa, world["p1"], bank["phy"], "CHAPTER", "Laws of Motion")                              # a provider's own list is separate
    nid = ch.json()["node_id"]
    assert client.patch(f"{API}/question-taxonomy/nodes/{nid}", json={"name": "x"}, headers=pa).status_code == 404


def test_a_retired_record_can_be_restored(client, world, bank, tree):
    h, sub = tree["h"], tree["subtopic"]["node_id"]
    assert client.patch(f"{API}/question-taxonomy/nodes/{sub}", json={"status": "RETIRED"}, headers=h).json()["status"] == "RETIRED"
    assert client.patch(f"{API}/question-taxonomy/nodes/{sub}", json={"status": "ACTIVE"}, headers=h).json()["status"] == "ACTIVE"


def test_unlinked_names_are_grouped_and_resolving_links_every_question(client, world, bank, tree, login, db_factory):
    h, ch = tree["h"], tree["chapter"]
    with db_factory() as db:
        ids = []
        for chapter in ("Law of Motions", "law of  motions", "Optics"):
            q = Question(provider_id=world["p1"], branch_id=world["p1b1"], subject_id=bank["phy"], owner_staff_id=uuid4(), chapter=chapter)
            db.add(q)
            db.flush()
            ids.append(q.question_id)
        db.commit()
    url = f"{API}/question-taxonomy/unlinked?provider_id={world['p1']}&subject_id={bank['phy']}"
    assert client.get(url, headers=login(bank["teacher"])).status_code == 403
    h = login(bank["pa1"])
    groups = client.get(url, headers=h).json()
    motion = next(g for g in groups if g["level"] == "CHAPTER" and g["questions"] == 2)
    assert motion["preselect"] == ch["node_id"]
    r = client.post(f"{API}/question-taxonomy/unlinked/resolve", headers=h, json={
        "subject_id": str(bank["phy"]), "provider_id": str(world["p1"]), "level": "CHAPTER", "value": motion["value"],
        "node_id": ch["node_id"], "remember": True})
    assert r.status_code == 200, r.text
    assert r.json()["questions"] == 2 and r.json()["remembered"] is True
    with db_factory() as db:
        linked = [db.get(Question, i) for i in ids]
        assert [(q.chapter_id, q.chapter) for q in linked[:2]] == [(UUID(ch["node_id"]), "Laws of Motion")] * 2
        assert linked[2].chapter_id is None
    assert [g["value"] for g in client.get(url, headers=h).json() if g["level"] == "CHAPTER"] == ["Optics"]
