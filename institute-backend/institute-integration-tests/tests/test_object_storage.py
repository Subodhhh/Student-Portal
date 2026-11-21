"""Object storage (Phase C): the store interface, the local-directory and S3 adapters (S3 against moto's in-memory fake - no AWS
account, no network), object-stored question pictures, secure delivery (authorised endpoint, then stream or signed URL), cleanup
without orphaned objects, and picture delivery to the Student services from the object store."""
import hashlib
import os
import stat
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from institute_common.database import utcnow
from institute_common.models.papers import QuestionImageAsset
from institute_common.objectstore import (
    LocalObjectStore, ObjectMissing, ObjectStoreError, S3ObjectStore, check_key, question_image_key, set_object_store, stored_asset_bytes,
)
from inst_paper.services import image_assets
from inst_paper.services.question_images import ExtractedImage
from tests.test_question_images import admin, png  # noqa: F401  (fixture)
from tests.test_papers import API, setup, tmp_store  # noqa: F401  (fixtures)

BUCKET = "test-private-bucket"


@pytest.fixture()
def local_store(tmp_path):
    store = LocalObjectStore(root=str(tmp_path / "objects"), prefix="")
    set_object_store(store)
    yield store
    set_object_store(None)


@pytest.fixture()
def s3_store(monkeypatch):
    """An S3 store backed by moto: fake credentials only, so nothing can ever reach a real AWS account."""
    moto = pytest.importorskip("moto")
    for name, value in (("AWS_ACCESS_KEY_ID", "testing"), ("AWS_SECRET_ACCESS_KEY", "testing"), ("AWS_SESSION_TOKEN", "testing"),
                        ("AWS_DEFAULT_REGION", "ap-south-1")):
        monkeypatch.setenv(name, value)
    with moto.mock_aws():
        import boto3
        boto3.client("s3", region_name="ap-south-1").create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "ap-south-1"})
        store = S3ObjectStore(bucket=BUCKET, region="ap-south-1", prefix="test/", signed_url_seconds=120)
        set_object_store(store)
        yield store
        set_object_store(None)


def picture(color="red") -> ExtractedImage:
    data = png(color)
    return ExtractedImage(name=f"{color}.png", mime="image/png", sha256=hashlib.sha256(data).hexdigest(), width=48, height=32, data=data)


# ------------------------------------------------------------------ keys

@pytest.mark.parametrize("bad", ["../etc/passwd", "/abs/key", "Upper/key", "a b", "a/../b", "", "x" * 301, "key\x00"])
def test_only_server_shaped_keys_are_accepted(bad):
    with pytest.raises(ValueError):
        check_key(bad)


def test_question_image_keys_are_per_provider_and_content():
    provider, sha = uuid4(), "a" * 64
    assert question_image_key(provider, sha) == f"question-images/{provider}/{sha}"
    with pytest.raises(ValueError):
        question_image_key(provider, "not-a-sha")


# ------------------------------------------------------------------ both adapters behave the same

@pytest.mark.parametrize("kind", ["local", "s3"])
def test_put_get_exists_delete(kind, request):
    store = request.getfixturevalue(f"{kind}_store")
    key = question_image_key(uuid4(), "b" * 64)
    assert not store.exists(key)
    with pytest.raises(ObjectMissing):
        store.get(key)
    store.put(key, b"\x89PNG bytes", "image/png")
    assert store.exists(key) and store.get(key) == b"\x89PNG bytes"
    store.put(key, b"\x89PNG again", "image/png")                       # same key again: replaced, never duplicated
    assert store.get(key) == b"\x89PNG again"
    assert store.delete(key) is True and store.delete(key) is False      # deleting twice is safe
    assert not store.exists(key)


def test_local_files_are_private_and_stay_inside_the_root(local_store):
    key = question_image_key(uuid4(), "c" * 64)
    local_store.put(key, b"x", "image/png")
    path = local_store._path(key)
    assert local_store.root in path.parents and stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert local_store.signed_url(key, "image/png") is None              # streamed by the API instead


def test_s3_signed_urls_are_short_lived_and_scoped_to_one_object(s3_store):
    key = question_image_key(uuid4(), "d" * 64)
    s3_store.put(key, b"x", "image/png")
    url = s3_store.signed_url(key, "image/png")
    assert f"{BUCKET}" in url and f"test/{key}" in url                   # the prefix is applied inside the bucket
    assert "X-Amz-Expires=120" in url and "X-Amz-Signature=" in url and "response-content-type=image%2Fpng" in url


def test_s3_failures_are_generic_and_never_leak_details(s3_store, caplog):
    class Broken:
        def put_object(self, **kw):
            raise RuntimeError("SignatureDoesNotMatch for AKIAEXAMPLE secret=abc")
    store = S3ObjectStore(bucket=BUCKET, client=Broken())
    with pytest.raises(ObjectStoreError) as exc:
        store.put(question_image_key(uuid4(), "e" * 64), b"x", "image/png")
    assert str(exc.value) == "The file store is unavailable. Try again later."
    assert "AKIA" not in caplog.text and "secret" not in caplog.text


def test_an_r2_or_minio_endpoint_is_configuration_only():
    store = S3ObjectStore(bucket="b", region="auto", endpoint_url="https://account.r2.cloudflarestorage.com")
    assert store._client.meta.endpoint_url == "https://account.r2.cloudflarestorage.com"     # no request is made


def test_misconfiguration_is_refused(monkeypatch):
    from institute_common import objectstore
    from institute_common.config import settings
    set_object_store(None)
    monkeypatch.setattr(settings, "object_store", "s3")
    monkeypatch.setattr(settings, "object_store_bucket", "")
    with pytest.raises(ObjectStoreError):
        objectstore.get_object_store()
    monkeypatch.setattr(settings, "object_store", "ftp")
    with pytest.raises(ObjectStoreError):
        objectstore.get_object_store()
    set_object_store(None)


# ------------------------------------------------------------------ object-stored question pictures

def stored(db_factory, world, store, color="red"):
    with db_factory() as db:
        ids = image_assets.store_images(db, world["p1"], [picture(color)], uuid4(), None, object_store=store)
        db.commit()
        return next(iter(ids.values()))


@pytest.mark.parametrize("kind", ["local", "s3"])
def test_pictures_go_to_the_store_once_and_only_keys_reach_the_database(kind, request, world, db_factory):
    store = request.getfixturevalue(f"{kind}_store")
    first = stored(db_factory, world, store)
    assert stored(db_factory, world, store) == first                    # same picture, same provider: reused
    with db_factory() as db:
        asset = db.get(QuestionImageAsset, first)
        assert asset.storage == kind and asset.image_data is None
        assert asset.storage_key == question_image_key(world["p1"], asset.checksum) and not asset.storage_key.startswith("http")
        assert stored_asset_bytes(asset) == png("red")


def test_the_database_refuses_an_object_picture_without_a_key(world, db_factory):
    with db_factory() as db:
        db.add(QuestionImageAsset(provider_id=world["p1"], file_name="x.png", mime_type="image/png", file_size=1, width=1, height=1,
                                  checksum="f" * 64, storage="s3", storage_key=None, created_by=uuid4()))
        with pytest.raises(IntegrityError):
            db.flush()


def test_local_pictures_are_streamed_to_authorised_staff_only(client, world, setup, admin, local_store, db_factory, make_staff, login):
    from institute_common.core.roles import Role
    asset = stored(db_factory, world, local_store)
    r = client.get(f"{API}/question-images/{asset}", headers=admin)
    assert r.status_code == 200 and r.content == png("red") and r.headers["content-type"] == "image/png"
    other = login(make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])[1])
    assert client.get(f"{API}/question-images/{asset}", headers=other).status_code == 404       # another provider: as if missing
    client.cookies.clear()                                                                     # really anonymous now
    assert client.get(f"{API}/question-images/{asset}").status_code in (401, 403)


def test_s3_pictures_redirect_to_a_signed_url_after_authorisation(client, world, setup, admin, s3_store, db_factory, make_staff, login):
    from institute_common.core.roles import Role
    asset = stored(db_factory, world, s3_store)
    r = client.get(f"{API}/question-images/{asset}", headers=admin, follow_redirects=False)
    assert r.status_code == 307 and r.headers["cache-control"] == "private, no-store"
    assert "X-Amz-Signature=" in r.headers["location"] and BUCKET in r.headers["location"]
    other = login(make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])[1])
    r = client.get(f"{API}/question-images/{asset}", headers=other, follow_redirects=False)
    assert r.status_code == 404 and "location" not in r.headers                                   # nothing signed for others


def test_missing_or_unreachable_objects_answer_cleanly(client, world, setup, admin, local_store, db_factory, monkeypatch):
    asset = stored(db_factory, world, local_store)
    with db_factory() as db:
        local_store.delete(db.get(QuestionImageAsset, asset).storage_key)
    assert client.get(f"{API}/question-images/{asset}", headers=admin).json()["detail"]["code"] == "FILE_MISSING"

    def down(*a, **k):
        raise ObjectStoreError("The file store is unavailable. Try again later.")
    monkeypatch.setattr(local_store, "get", down)
    r = client.get(f"{API}/question-images/{asset}", headers=admin)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "STORE_UNAVAILABLE"


# ------------------------------------------------------------------ cleanup and delivery

def test_the_sweeper_deletes_the_object_before_the_row_and_retries_failures(world, local_store, db_factory, monkeypatch):
    keep, gone = stored(db_factory, world, local_store, "red"), stored(db_factory, world, local_store, "blue")
    later = utcnow() + timedelta(days=30)
    with db_factory() as db:
        keys = {a: db.get(QuestionImageAsset, a).storage_key for a in (keep, gone)}

    real_delete = local_store.delete

    def flaky(key):                                   # the store refuses one delete: that row must stay for the next run
        if key == keys[keep]:
            raise ObjectStoreError("The file store is unavailable. Try again later.")
        return real_delete(key)
    monkeypatch.setattr(local_store, "delete", flaky)
    with db_factory() as db:
        report = image_assets.sweep_unreferenced(db, dry_run=False, now=later)
    assert (report["deleted"], report["failed"]) == (1, 1)
    assert not local_store.exists(keys[gone]) and local_store.exists(keys[keep])
    with db_factory() as db:
        assert db.get(QuestionImageAsset, gone) is None and db.get(QuestionImageAsset, keep) is not None
    monkeypatch.setattr(local_store, "delete", real_delete)
    with db_factory() as db:
        assert image_assets.sweep_unreferenced(db, dry_run=False, now=later)["deleted"] == 1     # the retry finishes the job
    assert not local_store.exists(keys[keep])


def test_exam_delivery_reads_object_stored_pictures(world, local_store, db_factory, monkeypatch):
    from inst_exam.services import outbox
    asset = stored(db_factory, world, local_store)
    with db_factory() as db:
        checksum = db.get(QuestionImageAsset, asset).checksum
        assert outbox._bytes_loader(db, checksum)() == png("red")
        local_store.delete(db.get(QuestionImageAsset, asset).storage_key)
        with pytest.raises(outbox.PermanentPublishError):                # really gone: retrying cannot help
            outbox._bytes_loader(db, checksum)()

        def down(*a, **k):
            raise ObjectStoreError("The file store is unavailable. Try again later.")
        monkeypatch.setattr(local_store, "get", down)
        with pytest.raises(outbox.TransientPublishError):                # store down: the delivery is retried later
            outbox._bytes_loader(db, checksum)()
