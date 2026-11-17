"""Question and option pictures delivered with an exam: the Institute uploads each picture once (named by its SHA-256), the exam
references pictures by checksum, and a student gets a picture only for a test they may open.
Run with dummy env and an in-memory DB:  DATABASE_URL=sqlite:// pytest tests/test_question_pictures.py"""
import hashlib
import uuid

import pytest

from app.auth import get_current_student
from app.main import app
from app.models import question as question_models
from app.routes import test as test_routes
from tests.test_internal_exams import H, env, payload  # noqa: F401  (shared fixtures)

PNG = b"\x89PNG\r\n\x1a\n" + b"picture bytes"
SHA = hashlib.sha256(PNG).hexdigest()


def put(client, data=PNG, checksum=SHA, mime="image/png"):
    return client.put(f"/internal/v1/question-images/{checksum}", content=data, headers={**H, "Content-Type": mime})


def picture_exam(**over):
    body = payload(**over)
    body["questions"][0] = {**body["questions"][0], "text": "", "options": {"A": "", "B": "b", "C": "c", "D": "d"},
                            "images": {"question": SHA, "A": SHA}}
    return body


def test_a_picture_is_stored_once_under_its_checksum(env):
    client, Session = env
    assert client.post("/internal/v1/question-images/missing", json={"checksums": [SHA]}, headers=H).json() == {"missing": [SHA]}
    assert put(client).status_code == 201
    assert put(client).status_code == 201                                   # again: unchanged, idempotent
    assert client.post("/internal/v1/question-images/missing", json={"checksums": [SHA]}, headers=H).json() == {"missing": []}
    with Session() as db:
        assert db.get(question_models.QuestionImage, SHA).image_data == PNG


@pytest.mark.parametrize("data,checksum,mime,status", [
    (PNG, "0" * 64, "image/png", 422),                       # bytes that do not hash to the name
    (b"<svg onload=alert(1)>", hashlib.sha256(b"<svg onload=alert(1)>").hexdigest(), "image/png", 422),   # not a PNG
    (PNG, SHA, "image/svg+xml", 415),                         # a type that is never accepted
    (PNG, "not-a-checksum", "image/png", 422),
])
def test_only_genuine_pictures_are_accepted(env, data, checksum, mime, status):
    client, _ = env
    assert put(client, data, checksum, mime).status_code == status


def test_pictures_need_the_service_key(env):
    client, _ = env
    assert client.put(f"/internal/v1/question-images/{SHA}", content=PNG, headers={"Content-Type": "image/png"}).status_code in (401, 403)


def test_an_exam_waits_for_its_pictures_and_then_stores_them_by_field(env):
    client, Session = env
    body = picture_exam()
    r = client.post("/internal/v1/exams", json=body, headers=H)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "IMAGES_MISSING"      # retryable: nothing stored
    put(client)
    assert client.post("/internal/v1/exams", json=body, headers=H).status_code in (200, 201)
    with Session() as db:
        q = db.query(question_models.Question).filter_by(question_order=1).one()
        assert (q.question_text, q.option_a, q.question_image, q.option_a_image, q.option_b_image) == ("", "", SHA, SHA, None)


@pytest.mark.parametrize("change,message", [
    (lambda q: q.update(images={}), "Each question needs its text or its picture"),
    (lambda q: q.update(options={**q["options"], "A": "text too"}), "Option A needs exactly one of its text or its picture"),
    (lambda q: q.update(images={**q["images"], "E": SHA}), "Question pictures must name fields"),
])
def test_the_text_or_picture_rule_is_enforced_on_arrival(env, change, message):
    client, _ = env
    put(client)
    body = picture_exam()
    change(body["questions"][0])
    r = client.post("/internal/v1/exams", json=body, headers=H)
    assert r.status_code == 422 and message in r.text


def test_a_student_sees_a_picture_only_for_a_test_they_may_open(env, monkeypatch):
    client, _ = env
    put(client)
    body = picture_exam()
    client.post("/internal/v1/exams", json=body, headers=H)
    test_id = body["exam_version_id"]
    allowed = {"S1"}
    monkeypatch.setattr(test_routes, "get_test_for_student", lambda db, test_id, student_id: student_id in allowed or None)
    for student, expected in (("S1", 200), ("S2", 404)):
        app.dependency_overrides[get_current_student] = lambda s=student: s
        r = client.get(f"/tests/{test_id}/images/{SHA}")
        assert r.status_code == expected
        if expected == 200:
            assert r.content == PNG and r.headers["content-type"] == "image/png" and r.headers["x-content-type-options"] == "nosniff"
    app.dependency_overrides[get_current_student] = lambda: "S1"
    other = hashlib.sha256(b"\x89PNG\r\n\x1a\nother").hexdigest()
    put(client, b"\x89PNG\r\n\x1a\nother", other)
    assert client.get(f"/tests/{test_id}/images/{other}").status_code == 404     # stored, but not used by this test
    assert client.get(f"/tests/{uuid.uuid4()}/images/{SHA}").status_code == 404
