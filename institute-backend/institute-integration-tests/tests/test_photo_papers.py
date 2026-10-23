"""Photo papers: several page photos plus separate answer-key photos -> OCR -> editable preview -> explicit confirmation -> the normal
all-or-nothing validator -> a VALIDATED paper version in the usual approval workflow. The OCR engine is replaced by a stand-in that
returns known lines per photo (real Tesseract is exercised in test_ocr_tesseract.py / the paper-service image)."""
import hashlib
import io
import uuid

import pytest

from inst_paper.services import extraction
from tests.test_global_contributions import client, login  # noqa: F401  (one session per staff member)
from tests.test_global_schedule import aud, exams_of, gs, published, run_generation, submit  # noqa: F401

API = "/api/v1"


def photo(seed: int) -> bytes:
    """A real (tiny) PNG, different per seed, so the stand-in OCR can tell pages apart by content."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (seed % 255, (seed * 7) % 255, (seed * 13) % 255)).save(buf, "PNG")
    return buf.getvalue()


class FakeOcr:
    """Returns the lines registered for an image (by content hash): [text, weakest word confidence, mean confidence]."""

    def __init__(self):
        self.pages: dict[str, list[list]] = {}
        self.unreadable: set[str] = set()      # photos refused unless the uploader turns them (read again with a rotation)
        self.calls = 0

    def page(self, seed: int, lines: list[str], low: set[int] = frozenset()) -> bytes:
        data = photo(seed)
        self.pages[hashlib.sha256(data).hexdigest()] = [[t, 40.0 if i in low else 99.0, 60.0 if i in low else 99.5] for i, t in enumerate(lines)]
        return data

    def read_lines(self, image: bytes, mime: str, purpose: str = "page", rotate: int | None = None) -> list[list]:
        self.calls += 1
        self.last = (purpose, rotate)
        digest = hashlib.sha256(image).hexdigest()
        if digest in self.unreadable and rotate is None:
            raise extraction.OcrUnreadable("No writing was found in this photo. Photograph the whole page, flat and well lit.")
        return self.pages.get(digest, [])

    def read_questions(self, image, mime):  # pragma: no cover - not used by photo papers
        return []


@pytest.fixture()
def ocr():
    fake = FakeOcr()
    extraction.set_ocr_backend(fake)
    yield fake
    extraction.set_ocr_backend(None)


PAGE1 = ["Default marks: 4", "Default negative: 1", "1. What is the SI unit of force?", "A) Newton", "B) Joule", "C) Watt", "D) Pascal",
         "2. Which gas do plants absorb?", "A) Oxygen", "B) Carbon dioxide", "C) Nitrogen", "D) Helium"]
PAGE2 = ["3. Speed of light is about", "A) 3 x 10^8 m/s", "B) 3 x 10^6 m/s", "C) 340 m/s", "D) 1500 m/s",
         "4. H2O is commonly called", "A) Water", "B) Salt", "C) Sugar", "D) Acid"]
KEY = ["Answer key", "1. A", "2. B", "3. A", "4. A"]
PAPER_B = ["Default marks: 4", "Default negative: 1", "1. Speed of light is about", "A) 3 x 10^8 m/s", "B) 3 x 10^6 m/s", "C) 340 m/s", "D) 1500 m/s",
           "2. H2O is commonly called", "A) Water", "B) Salt", "C) Sugar", "D) Acid"]


def new_draft(client, h, world, gs, title="Physics photo paper", key=None, who_branch="p1b1"):
    r = client.post(f"{API}/branches/{world[who_branch]}/subjects/{gs['phy']}/photo-drafts", json={"title": title},
                    headers={**h, "Idempotency-Key": key or f"photo-{uuid.uuid4()}"})
    assert r.status_code == 201, r.text
    return r.json()["draft_id"]


def put_file(client, h, draft, kind, position, data, name="page.png", mime="image/png"):
    return client.post(f"{API}/photo-drafts/{draft}/files", data={"kind": kind, "position": str(position)},
                       files={"file": (name, data, mime)}, headers=h)


def upload_paper(client, h, ocr, draft, pages=(PAGE1, PAGE2), key=KEY, low_pages=None, seed=0):
    for i, lines in enumerate(pages, start=1):
        r = put_file(client, h, draft, "PAGE", i, ocr.page(seed + i, lines, (low_pages or {}).get(i, set())))
        assert r.status_code == 201, r.text
    if key is not None:
        assert put_file(client, h, draft, "ANSWER_KEY", 1, ocr.page(seed + 100, key), name="key.jpg").status_code == 201


def test_multi_page_photos_and_a_separate_answer_key_are_matched_by_number(client, world, gs, login, ocr):
    h = login(gs["tA"])
    d = new_draft(client, h, world, gs)
    upload_paper(client, h, ocr, d)
    r = client.post(f"{API}/photo-drafts/{d}/extract", headers=h)
    assert r.status_code == 200, r.text
    qs = r.json()["questions"]
    assert [(q["number"], q["correct"], q["page"]) for q in qs] == [(1, "A", 1), (2, "B", 1), (3, "A", 2), (4, "A", 2)]
    assert qs[2]["option_a"] == "3 x 10^8 m/s" and qs[0]["marks"] == "4" and not any(q["needs_review"] for q in qs)
    files = r.json()["files"]
    assert [(f["kind"], f["position"]) for f in files] == [("ANSWER_KEY", 1), ("PAGE", 1), ("PAGE", 2)]
    img = client.get(f"{API}/photo-drafts/{d}/files/{files[1]['draft_file_id']}/content", headers=login(gs["tA"]))
    assert img.status_code == 200 and img.headers["content-type"] == "image/png" and img.headers["cache-control"] == "private, no-store"


def test_uncertain_or_broken_questions_are_flagged_and_block_acceptance_until_corrected(client, world, gs, login, ocr, db_factory):
    from institute_common.models.papers import PhotoDraft, PaperVersion
    h = login(gs["tA"])
    d = new_draft(client, h, world, gs)
    broken = PAGE2[:4] + PAGE2[5:]                                      # question 3 lost option D on the photo
    upload_paper(client, h, ocr, d, pages=(PAGE1, broken), low_pages={1: {2}})   # question 1's text read with low confidence
    qs = client.post(f"{API}/photo-drafts/{d}/extract", headers=h).json()["questions"]
    flagged = {q["number"]: q for q in qs if q["needs_review"]}
    assert set(flagged) == {1, 3} and "Option C could not be read." not in flagged[3]["issues"]
    assert any("Option D" in i for i in flagged[3]["issues"]) and flagged[1]["confidence"] < 0.98
    r = client.post(f"{API}/photo-drafts/{d}/accept", json={"confirm": True}, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "REVIEW_REQUIRED" and r.json()["detail"]["questions"] == [1, 3]
    # the person checks both but forgets to type option D: the shared validator still rejects the whole paper
    edited = [{**{k: q[k] for k in ("number", "question", "option_a", "option_b", "option_c", "option_d", "correct", "marks", "negative_marks")},
               "reviewed": True} for q in qs]
    edited[0]["question"] = "What is the SI unit of force (in physics)?"
    assert client.put(f"{API}/photo-drafts/{d}/questions", json={"questions": edited}, headers=h).status_code == 200
    r = client.post(f"{API}/photo-drafts/{d}/accept", json={"confirm": True}, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "EXTRACTION_FAILED" and r.json()["detail"]["questions"] == [3]
    with db_factory() as db:
        assert db.get(PhotoDraft, uuid.UUID(d)).status == "DRAFT" and db.query(PaperVersion).count() == 5    # gs's papers only: nothing imported
    edited[2]["option_d"] = "1500 m/s"
    client.put(f"{API}/photo-drafts/{d}/questions", json={"questions": edited}, headers=h)
    assert client.post(f"{API}/photo-drafts/{d}/accept", json={"confirm": False}, headers=h).status_code == 422       # explicit confirmation
    r = client.post(f"{API}/photo-drafts/{d}/accept", json={"confirm": True}, headers=h)
    assert r.status_code == 200, r.text
    pv = r.json()["paper_version_id"]
    with db_factory() as db:
        draft = db.get(PhotoDraft, uuid.UUID(d))
        assert draft.status == "ACCEPTED" and draft.extracted[0]["question"] != draft.questions[0]["question"]    # OCR text kept as read
        assert {(c["number"], c["field"]) for c in draft.corrections} >= {(1, "question"), (3, "option_d"), (1, "reviewed")}
        assert db.get(PaperVersion, uuid.UUID(pv)).status == "VALIDATED"                                      # not published automatically
    h = login(gs["tA"])
    assert client.post(f"{API}/paper-versions/{pv}/approve", json={"note": "checked"}, headers=h).status_code == 200   # the usual approval
    again = client.post(f"{API}/photo-drafts/{d}/accept", json={"confirm": True}, headers=h)
    assert again.json()["paper_version_id"] == pv                                                            # retried confirmation


def test_answer_key_problems_are_never_guessed(client, world, gs, login, ocr):
    h = login(gs["tA"])
    d = new_draft(client, h, world, gs)
    inline = PAGE1[:7] + ["Answer: B"] + PAGE1[7:]                       # question 1 printed with an answer that contradicts the key
    upload_paper(client, h, ocr, d, pages=(inline, PAGE2), key=["1. A", "2. B", "3. A", "7. C"])     # no answer for 4; 7 does not exist
    r = client.post(f"{API}/photo-drafts/{d}/extract", headers=h).json()
    q = {x["number"]: x for x in r["questions"]}
    assert q[1]["correct"] == "" and any("answer key says A" in i for i in q[1]["issues"])
    assert q[4]["correct"] == "" and any("no answer" in i for i in q[4]["issues"])
    assert any("not found in the paper: 7" in i for i in r["issues"])
    # a corrected key photo is uploaded separately and re-matched without losing a text correction
    edited = [{k: x[k] for k in ("number", "question", "option_a", "option_b", "option_c", "option_d", "correct", "marks", "negative_marks")} for x in r["questions"]]
    edited[1]["question"] = "Which gas do green plants absorb?"
    client.put(f"{API}/photo-drafts/{d}/questions", json={"questions": edited}, headers=h)
    assert put_file(client, h, d, "ANSWER_KEY", 1, ocr.page(500, ["1. A", "2. B", "3. A", "4. A"]), name="key2.jpg").status_code == 201
    r = client.post(f"{API}/photo-drafts/{d}/extract?answer_key_only=true", headers=h).json()
    q = {x["number"]: x for x in r["questions"]}
    assert q[4]["correct"] == "A" and q[2]["question"] == "Which gas do green plants absorb?"
    assert q[1]["correct"] == "" and q[1]["needs_review"]                 # still contradicts the printed answer: a person decides


def test_overlapping_photos_are_flagged_not_merged_silently(client, world, gs, login, ocr):
    h = login(gs["tA"])
    d = new_draft(client, h, world, gs)
    overlap = ["2. Which gas do plants absorb?", "A) Oxygen", "B) Carbon dioxide", "C) Nitrogen", "D) Helium"] + PAGE2
    upload_paper(client, h, ocr, d, pages=(PAGE1, overlap))
    qs = {q["number"]: q for q in client.post(f"{API}/photo-drafts/{d}/extract", headers=h).json()["questions"]}
    assert any("appears again" in i for i in qs[2]["issues"]) and qs[2]["needs_review"]


def test_diagram_questions_are_never_dropped(client, world, gs, login, ocr):
    h = login(gs["tA"])
    d = new_draft(client, h, world, gs)
    upload_paper(client, h, ocr, d)
    qs = client.post(f"{API}/photo-drafts/{d}/extract", headers=h).json()["questions"]
    edited = [{k: x[k] for k in ("number", "question", "option_a", "option_b", "option_c", "option_d", "correct", "marks", "negative_marks")} for x in qs]
    edited[2]["has_diagram"] = True
    client.put(f"{API}/photo-drafts/{d}/questions", json={"questions": edited}, headers=h)
    r = client.post(f"{API}/photo-drafts/{d}/accept", json={"confirm": True}, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "DIAGRAM_NOT_SUPPORTED" and r.json()["detail"]["questions"] == [3]


def test_retries_never_duplicate_drafts_files_or_questions(client, world, gs, login, ocr, db_factory):
    from institute_common.models.papers import PhotoDraft, PhotoDraftFile, Question
    h = login(gs["tA"])
    d = new_draft(client, h, world, gs, key="photo-retry-0001")
    assert new_draft(client, h, world, gs, key="photo-retry-0001") == d
    upload_paper(client, h, ocr, d)
    calls = ocr.calls
    assert put_file(client, h, d, "PAGE", 1, photo(1)).json()["position"] == 1                          # the same photo again
    assert ocr.calls == calls                                                                           # not OCR'd twice
    client.post(f"{API}/photo-drafts/{d}/extract", headers=h)
    assert client.post(f"{API}/photo-drafts/{d}/accept", json={"confirm": True}, headers=h).status_code == 200
    with db_factory() as db:
        before = db.query(Question).count()
        assert db.query(PhotoDraftFile).filter_by(draft_id=uuid.UUID(d)).count() == 3 and db.query(PhotoDraft).count() == 1
    d2 = new_draft(client, h, world, gs, title="Same paper again")
    upload_paper(client, h, ocr, d2)
    client.post(f"{API}/photo-drafts/{d2}/extract", headers=h)
    r = client.post(f"{API}/photo-drafts/{d2}/accept", json={"confirm": True}, headers=h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "DUPLICATE_PAPER"                    # identical paper: reused, not copied
    with db_factory() as db:
        assert db.query(Question).count() == before


def test_photo_drafts_follow_paper_access_rules(client, world, gs, login, ocr):
    h = login(gs["tA"])
    d = new_draft(client, h, world, gs)
    assert client.get(f"{API}/photo-drafts/{d}", headers=login(gs["tB"])).status_code == 404            # another teacher's draft
    assert client.get(f"{API}/photo-drafts/{d}", headers=login(gs["tutor"])).status_code == 404         # another provider
    assert client.get(f"{API}/photo-drafts/{d}", headers=login(gs["ba"])).status_code == 200            # the branch's admin
    r = client.post(f"{API}/branches/{world['p1b1']}/subjects/{gs['chem']}/photo-drafts", json={"title": "Not my subject"},
                    headers={**login(gs["tA"]), "Idempotency-Key": "photo-chem-0001"})
    assert r.status_code == 403
    h = login(gs["tA"])
    assert put_file(client, h, d, "PAGE", 1, b"not really a png", mime="image/png").status_code == 422
    assert put_file(client, h, d, "PAGE", 1, b"%PDF-1.4", name="x.pdf", mime="application/pdf").status_code == 415
    assert put_file(client, h, d, "PAGE", 21, photo(9)).status_code == 422
    extraction.set_ocr_backend(None)
    r = put_file(client, h, d, "PAGE", 1, photo(9))
    assert r.status_code == 503 and r.json()["detail"]["code"] == "OCR_UNAVAILABLE"


def test_two_teachers_photo_papers_reach_only_their_own_students(client, world, gs, login, ocr, db_factory):
    """Acceptance 9: different photo papers for the same Global Test date; each batch receives only its teacher's paper."""
    versions = {}
    for who, seed, lines in (("tA", 0, (PAGE1,)), ("tB", 300, (PAPER_B,))):
        h = login(gs[who])
        d = new_draft(client, h, world, gs, title=f"{who} photo paper")
        upload_paper(client, h, ocr, d, pages=lines, key=None if who == "tB" else ["1. A", "2. B"], seed=seed)
        if who == "tB":                                                    # tB's key is a typed .csv file instead of photos
            assert put_file(client, h, d, "ANSWER_KEY", 1, b"1,A\n2,A\n", name="key.csv", mime="text/csv").status_code == 201
        client.post(f"{API}/photo-drafts/{d}/extract", headers=h)
        acc = client.post(f"{API}/photo-drafts/{d}/accept", json={"confirm": True}, headers=h)
        assert acc.status_code == 200, acc.text
        pv = acc.json()["paper_version_id"]
        assert client.post(f"{API}/paper-versions/{pv}/approve", json={"note": "ok"}, headers=h).status_code == 200
        assert client.post(f"{API}/paper-versions/{pv}/publish", json={"note": None}, headers=h).status_code == 200
        versions[who] = pv
    test, occ = published(client, login, gs)
    assert submit(client, login, gs["tA"], occ, gs["phy"], versions["tA"], aud(world, gs, batches=("batch_a",))).status_code == 201
    assert submit(client, login, gs["tB"], occ, gs["phy"], versions["tB"], aud(world, gs, batches=("batch_b",))).status_code == 201
    run_generation(db_factory, test, occ)
    got = exams_of(db_factory, occ["occurrence_id"])
    assert got["STU000001"] == [(world["p1"], [versions["tA"]])] and got["STU000002"] == [(world["p1"], [versions["tB"]])]


def test_a_refused_photo_says_what_to_retake_and_reading_it_again_turned_upright_recovers_it(client, world, gs, login, ocr):
    h = login(gs["tA"])
    d = new_draft(client, h, world, gs)
    sideways = ocr.page(900, PAGE1)
    ocr.unreadable.add(hashlib.sha256(sideways).hexdigest())
    f = put_file(client, h, d, "PAGE", 1, sideways).json()
    assert f["lines"] == 0 and "No writing was found" in f["ocr_error"] and ocr.last == ("page", None)
    r = client.post(f"{API}/photo-drafts/{d}/extract", headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "PAGES_UNREADABLE"             # nothing is extracted from it
    for bad in ({"rotate": 45}, {"rotate": "x"}, {"other": 1}):
        assert client.post(f"{API}/photo-drafts/{d}/files/{f['draft_file_id']}/read-again", json=bad, headers=h).status_code == 422
    again = client.post(f"{API}/photo-drafts/{d}/files/{f['draft_file_id']}/read-again", json={"rotate": 90}, headers=h)
    assert again.status_code == 200 and again.json()["ocr_error"] is None and again.json()["lines"] == len(PAGE1) and ocr.last == ("page", 90)
    assert put_file(client, h, d, "ANSWER_KEY", 1, ocr.page(901, KEY), name="key.jpg").status_code == 201 and ocr.last == ("key", None)
    qs = client.post(f"{API}/photo-drafts/{d}/extract", headers=h).json()["questions"]
    assert [q["number"] for q in qs] == [1, 2]
    img = client.get(f"{API}/photo-drafts/{d}/files/{f['draft_file_id']}/content", headers=h)
    assert img.content == sideways                                                            # the original is kept as uploaded


def test_read_again_follows_draft_access_and_refuses_typed_keys(client, world, gs, login, ocr):
    h = login(gs["tA"])
    d = new_draft(client, h, world, gs)
    f = put_file(client, h, d, "PAGE", 1, ocr.page(910, PAGE1)).json()
    key = put_file(client, h, d, "ANSWER_KEY", 1, b"1,A\n2,B\n", name="key.csv", mime="text/csv").json()
    r = client.post(f"{API}/photo-drafts/{d}/files/{key['draft_file_id']}/read-again", json={}, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "NOT_A_PHOTO"
    for who in ("tB", "tutor"):                                      # another teacher's draft; another provider
        r = client.post(f"{API}/photo-drafts/{d}/files/{f['draft_file_id']}/read-again", json={}, headers=login(gs[who]))
        assert r.status_code == 404, who
    h = login(gs["tA"])
    assert client.post(f"{API}/photo-drafts/{d}/files/{uuid.uuid4()}/read-again", json={}, headers=h).status_code == 404


def test_the_same_photo_twice_and_an_unreadable_key_photo_are_reported(client, world, gs, login, ocr):
    h = login(gs["tA"])
    d = new_draft(client, h, world, gs)
    same = ocr.page(920, PAGE1)
    assert put_file(client, h, d, "PAGE", 1, same).status_code == 201
    assert put_file(client, h, d, "PAGE", 2, same).status_code == 201
    blurred = ocr.page(921, KEY)
    ocr.unreadable.add(hashlib.sha256(blurred).hexdigest())
    assert put_file(client, h, d, "ANSWER_KEY", 1, blurred, name="key.jpg").status_code == 201
    r = client.post(f"{API}/photo-drafts/{d}/extract", headers=h).json()
    assert any("Page 2 is the same photo as page 1" in i for i in r["issues"])
    assert any("Answer-key photo 1 could not be read" in i for i in r["issues"])
    assert all(q["correct"] == "" and q["needs_review"] for q in r["questions"])               # no key read -> no answers guessed
