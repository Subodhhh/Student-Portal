"""Storing and reading question pictures (question_image_assets).

A picture is stored once per provider: the same image uploaded again (another paper, a second preview, two options of different
questions) reuses its asset. Bytes are read only through `image_bytes`, so moving them to an object store later changes this module
and the `storage` column, never the question tables that reference asset ids.

Pictures are stored when a paper is previewed, so the preview can show them; a preview that is never imported leaves assets no
question references, and `sweep_unreferenced` removes those after a retention period.
"""
from collections.abc import Iterable
from datetime import timedelta
from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from institute_common.database import utcnow
from institute_common.models.papers import QuestionImageAsset, QuestionVersion
from institute_common.objectstore import ObjectMissing, ObjectStore, ObjectStoreError, question_image_key, store_named, stored_asset_bytes

from inst_paper.services.question_images import ExtractedImage

UNREFERENCED_RETENTION = timedelta(days=7)        # longer than any open preview; review drafts re-store pictures when reopened


def store_images(db: Session, provider_id: UUID | None, images: Iterable[ExtractedImage], staff_id: UUID, ip: str | None,
                 object_store: ObjectStore | None = None) -> dict[str, UUID]:
    """Store every picture not yet stored for this provider - or for the platform when `provider_id` is None (GLOBAL imports).
    Returns {sha256: asset_id} for all of them (new and existing).

    Without `object_store` the bytes go into the database (storage "db"), as every existing upload does. With one, the bytes are put
    into that store FIRST under a server-made key (question-images/<provider>/<sha256>), then the row pointing at them is added; a
    retry after a failure puts the same key again, so nothing is duplicated."""
    wanted = {img.sha256: img for img in images}
    if not wanted:
        return {}
    owner = QuestionImageAsset.provider_id == provider_id if provider_id else QuestionImageAsset.provider_id.is_(None)
    found = dict(db.execute(select(QuestionImageAsset.checksum, QuestionImageAsset.asset_id).where(
        owner, QuestionImageAsset.checksum.in_(wanted))).all())
    for sha, img in wanted.items():
        if sha in found:
            continue
        if object_store is None:
            where = {"storage": "db", "image_data": img.data}
        else:
            key = question_image_key(provider_id, sha)
            object_store.put(key, img.data, img.mime)
            where = {"storage": object_store.name, "storage_key": key}
        asset = QuestionImageAsset(provider_id=provider_id, file_name=img.name[:255], mime_type=img.mime, file_size=len(img.data),
                                   width=img.width, height=img.height, checksum=sha, created_by=staff_id, created_ip=ip, **where)
        try:
            with db.begin_nested():                    # a concurrent upload of the same picture wins the unique key: reuse its asset
                db.add(asset)
                db.flush()
            found[sha] = asset.asset_id
        except IntegrityError:
            found[sha] = db.scalar(select(QuestionImageAsset.asset_id).where(owner, QuestionImageAsset.checksum == sha))
    return found


def image_bytes(asset: QuestionImageAsset) -> bytes:
    """The picture's original bytes, from wherever `storage` says they live (database or object store). LookupError when they are
    gone; ObjectStoreError when the object store cannot be reached."""
    try:
        return stored_asset_bytes(asset)
    except ObjectMissing as exc:
        raise LookupError(f"picture {asset.asset_id} has no stored bytes ({asset.storage})") from exc


def referenced_by_question(asset_id) -> "object":
    """SQL condition: some question version shows this picture."""
    return exists().where(or_(*(getattr(QuestionVersion, c) == asset_id for c in (
        "stem_image_id", "option_a_image_id", "option_b_image_id", "option_c_image_id", "option_d_image_id"))))


def sweep_unreferenced(db: Session, dry_run: bool = True, now=None) -> dict:
    """Pictures stored by a preview that no question uses, older than UNREFERENCED_RETENTION: listed, and deleted only when
    dry_run is False (run by an operator: python -m inst_paper.sweep_stored_files --unused-pictures [--execute])."""
    cutoff = (now or utcnow()) - UNREFERENCED_RETENTION
    stale = db.scalars(select(QuestionImageAsset.asset_id).where(
        QuestionImageAsset.created_at < cutoff, ~referenced_by_question(QuestionImageAsset.asset_id))).all()
    deleted, failed = 0, 0
    if not dry_run:
        for asset_id in stale:
            asset = db.get(QuestionImageAsset, asset_id)
            if asset.storage != "db":
                # The object goes first: if that fails the row stays, so the next run retries - never a row-less (orphaned) object.
                try:
                    store_named(asset.storage).delete(asset.storage_key)
                except ObjectStoreError:
                    failed += 1
                    continue
            db.delete(asset)
            deleted += 1
        db.commit()
    return {"unused_pictures": len(stale), "deleted": deleted, "failed": failed, "dry_run": dry_run}
