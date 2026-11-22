"""Resumable bulk uploads (routes/bulk_uploads.py): a question file sent in pieces continues where it stopped, belongs to the person
who started it, becomes an ordinary import job when complete, and is cleaned up when abandoned. The worker tells the uploader through
the bell when the preview is ready and when the import has finished (paper_notification_outbox -> operations)."""
from datetime import timedelta

import pytest

from institute_common.config import settings
from institute_common.database import utcnow
from institute_common.models.bulk_import import BulkImportJob, BulkImportUpload, PaperNotificationOutbox
from inst_paper.services import bulk_import, notify
from tests.test_bulk_import import API, bi, job_of, q, store, work, xlsx  # noqa: F401  (fixtures)

CHUNK = 1000


@pytest.fixture(autouse=True)
def small_pieces(monkeypatch):
    monkeypatch.setattr(settings, "bulk_upload_chunk_bytes", CHUNK)


def start(client, world, bi, data: bytes, who="pa", fingerprint="questions.xlsx|1|1", **over):
    body = {"subject_id": str(bi["phy"]), "target": "PROVIDER", "provider_id": str(world["p1"]), "file_name": "questions.xlsx",
            "size": len(data), "fingerprint": fingerprint, **over}
    return client.post(f"{API}/bulk-imports/uploads", json=body, headers=bi["h"](who))


def send(client, bi, upload_id, data: bytes, n: int, who="pa"):
    piece = data[n * CHUNK:(n + 1) * CHUNK]
    return client.put(f"{API}/bulk-imports/uploads/{upload_id}/parts/{n}", content=piece,
                      headers={**bi["h"](who), "Content-Type": "application/octet-stream"})


def test_an_interrupted_upload_continues_where_it_stopped_and_becomes_an_import(client, world, bi, db_factory, store):
    data = xlsx([q(), q(stem="What is the SI unit of power?", correct="C")])
    pieces = -(-len(data) // CHUNK)
    assert pieces >= 3
    r = start(client, world, bi, data)
    assert r.status_code == 201 and r.json()["chunk_size"] == CHUNK and r.json()["received_bytes"] == 0
    uid = r.json()["upload_id"]
    assert send(client, bi, uid, data, 0).json()["received_parts"] == 1
    assert send(client, bi, uid, data, 1).json()["received_parts"] == 2
    # ... the tab was closed. Choosing the same file again resumes the same upload:
    again = start(client, world, bi, data)
    assert again.status_code == 201 and again.json()["upload_id"] == uid and again.json()["resumed"] is True
    assert again.json()["received_bytes"] == 2 * CHUNK
    assert [u["upload_id"] for u in client.get(f"{API}/bulk-imports/uploads", headers=bi["h"]()).json()] == [uid]
    assert send(client, bi, uid, data, 1).json()["received_parts"] == 2                        # a repeated piece changes nothing
    out_of_order = send(client, bi, uid, data, 3) if pieces > 3 else None
    if out_of_order is not None:
        assert out_of_order.status_code == 409 and out_of_order.json()["detail"]["code"] == "OUT_OF_ORDER"
    early = client.post(f"{API}/bulk-imports/uploads/{uid}/complete", headers=bi["h"]())
    assert early.status_code == 409 and early.json()["detail"]["code"] == "INCOMPLETE"
    for n in range(2, pieces):
        assert send(client, bi, uid, data, n).status_code == 200
    done = client.post(f"{API}/bulk-imports/uploads/{uid}/complete", headers=bi["h"]())
    assert done.status_code == 202, done.text
    job = done.json()
    assert job["status"] == "UPLOADED" and job["branch_id"] is None
    with db_factory() as db:
        assert db.get(BulkImportJob, __import__("uuid").UUID(job["job_id"])).source_size == len(data)          # the whole file
    assert client.post(f"{API}/bulk-imports/uploads/{uid}/complete", headers=bi["h"]()).json()["job_id"] == job["job_id"]   # idempotent
    assert not any(store.exists(bulk_import.upload_part_key(uid, n)) for n in range(pieces))                    # pieces deleted
    assert client.get(f"{API}/bulk-imports/uploads", headers=bi["h"]()).json() == []
    work(db_factory, store)
    assert job_of(client, bi, job["job_id"])["valid_rows"] == 2                                  # an ordinary import from here on


def test_pieces_are_checked_and_an_upload_is_only_its_owners(client, world, bi, store):
    data = xlsx([q()])
    uid = start(client, world, bi, data).json()["upload_id"]
    short = client.put(f"{API}/bulk-imports/uploads/{uid}/parts/0", content=data[:10],
                       headers={**bi["h"](), "Content-Type": "application/octet-stream"})
    assert short.status_code == 422 and short.json()["detail"]["code"] == "BAD_PIECE"
    fake = b"MZ" + data[2:CHUNK]
    not_zip = client.put(f"{API}/bulk-imports/uploads/{uid}/parts/0", content=fake,
                         headers={**bi["h"](), "Content-Type": "application/octet-stream"})
    assert not_zip.status_code == 422 and not_zip.json()["detail"]["code"] == "INVALID_FILE"
    for who in ("ba", "pa2"):                                                                    # not theirs: it does not exist
        assert client.get(f"{API}/bulk-imports/uploads/{uid}", headers=bi["h"](who)).status_code == 404
        assert send(client, bi, uid, data, 0, who=who).status_code == 404
    assert start(client, world, bi, data, file_name="questions.csv").json()["detail"]["code"] == "UNSUPPORTED_FORMAT"
    assert start(client, world, bi, data, size=settings.bulk_import_max_bytes + 1).status_code == 413
    assert start(client, world, bi, data, who="pa2").status_code == 403                         # another provider
    gone = client.delete(f"{API}/bulk-imports/uploads/{uid}", headers=bi["h"]())
    assert gone.status_code == 200 and gone.json()["status"] == "DISCARDED"
    assert send(client, bi, uid, data, 0).json()["detail"]["code"] == "UPLOAD_CLOSED"


def test_abandoned_uploads_are_deleted_after_the_resume_period(client, world, bi, db_factory, store):
    data = xlsx([q()])
    uid = start(client, world, bi, data).json()["upload_id"]
    send(client, bi, uid, data, 0)
    assert store.exists(bulk_import.upload_part_key(uid, 0))
    with db_factory() as db:
        assert bulk_import.housekeeping(db, store)["uploads_discarded"] == 0                    # still within the resume period
        late = utcnow() + timedelta(days=settings.bulk_upload_resume_days, hours=1)
        assert bulk_import.housekeeping(db, store, now=late)["uploads_discarded"] == 1
        assert db.get(BulkImportUpload, __import__("uuid").UUID(uid)).status == "DISCARDED"
    assert not store.exists(bulk_import.upload_part_key(uid, 0))


class FakeOperations:
    def __init__(self, status=200):
        self.sent, self.status = [], status

    def request(self, method, path, json=None):
        self.sent.append((method, path, json))
        return type("R", (), {"status_code": self.status})()


def test_the_uploader_is_told_when_the_preview_is_ready_and_when_the_import_finished(client, world, bi, db_factory, store):
    data = xlsx([q(), q(stem="What is the SI unit of power?", correct="C")])
    uid = start(client, world, bi, data).json()["upload_id"]
    for n in range(-(-len(data) // CHUNK)):
        send(client, bi, uid, data, n)
    job_id = client.post(f"{API}/bulk-imports/uploads/{uid}/complete", headers=bi["h"]()).json()["job_id"]
    work(db_factory, store)
    assert client.post(f"{API}/bulk-imports/{job_id}/revalidate", headers=bi["h"]()).status_code in (200, 202)
    work(db_factory, store)                                                                      # a re-check notifies nobody again
    assert client.post(f"{API}/bulk-imports/{job_id}/confirm", headers=bi["h"]()).status_code == 200
    work(db_factory, store)
    with db_factory() as db:
        rows = db.query(PaperNotificationOutbox).order_by(PaperNotificationOutbox.created_at).all()
        assert [r.payload["kind"] for r in rows] == ["BULK_IMPORT_PREVIEW_READY", "BULK_IMPORT_COMPLETED"]
        assert rows[0].payload["staff_ids"] == [str(bi["pa_id"])] and rows[0].payload["email"] is False
        assert "2 question(s) are ready" in rows[0].payload["body"] and "2 question(s) were added" in rows[1].payload["body"]
        ops = FakeOperations()
        assert notify.deliver_pending(db, client=ops) == {"sent": 2, "retry": 0, "failed": 0}
        assert [p for _m, p, _j in ops.sent] == ["/notifications", "/notifications"]
        assert notify.deliver_pending(db, client=ops) == {"sent": 0, "retry": 0, "failed": 0}           # delivered once
        assert db.get(BulkImportJob, __import__("uuid").UUID(job_id)).status == "COMPLETED"
