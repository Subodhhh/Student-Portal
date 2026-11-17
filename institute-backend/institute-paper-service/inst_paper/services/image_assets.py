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

from inst_paper.services.question_images import ExtractedImage

UNREFERENCED_RETENTION = timedelta(days=7)        # longer than any open preview; review drafts re-store pictures when reopened


def store_images(db: Session, provider_id: UUID, images: Iterable[ExtractedImage], staff_id: UUID, ip: str | None) -> dict[str, UUID]:
    """Store every picture not yet stored for this provider. Returns {sha256: asset_id} for all of them (new and existing)."""
    wanted = {img.sha256: img for img in images}
    if not wanted:
        return {}
    found = dict(db.execute(select(QuestionImageAsset.checksum, QuestionImageAsset.asset_id).where(
        QuestionImageAsset.provider_id == provider_id, QuestionImageAsset.checksum.in_(wanted))).all())
    for sha, img in wanted.items():
        if sha in found:
            continue
        asset = QuestionImageAsset(provider_id=provider_id, file_name=img.name[:255], mime_type=img.mime, file_size=len(img.data),
                                   width=img.width, height=img.height, checksum=sha, storage="db", image_data=img.data,
                                   created_by=staff_id, created_ip=ip)
        try:
            with db.begin_nested():                    # a concurrent upload of the same picture wins the unique key: reuse its asset
                db.add(asset)
                db.flush()
            found[sha] = asset.asset_id
        except IntegrityError:
            found[sha] = db.scalar(select(QuestionImageAsset.asset_id).where(
                QuestionImageAsset.provider_id == provider_id, QuestionImageAsset.checksum == sha))
    return found


def image_bytes(asset: QuestionImageAsset) -> bytes:
    """The picture's original bytes, from wherever `storage` says they live."""
    if asset.storage == "db" and asset.image_data is not None:
        return asset.image_data
    raise LookupError(f"picture {asset.asset_id} has no stored bytes ({asset.storage})")


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
    if not dry_run:
        for asset_id in stale:
            db.delete(db.get(QuestionImageAsset, asset_id))
        db.commit()
    return {"unused_pictures": len(stale), "deleted": 0 if dry_run else len(stale), "dry_run": dry_run}
