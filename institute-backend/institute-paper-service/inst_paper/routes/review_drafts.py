"""Recognition review drafts: an import review saved as it is made, so a reload (or another day) never loses the decisions.

    POST /branches/{b}/subjects/{s}/review-drafts   preview a file AND keep it (with its answer key) as an OPEN draft; the same person
                                                    previewing the same file again resumes that draft
    GET  /review-drafts?branch_id=...               open drafts the caller may continue (teachers: their own)
    GET  /review-drafts/{id}                        the draft and its preview, recognised again from the stored file
    PATCH /review-drafts/{id}                       save changed decisions; a field changed by someone else since `base_revision` is
                                                    never overwritten (409 DRAFT_CONFLICT with the current value)
    POST /review-drafts/{id}/import                 import with the saved decisions - the same path as an upload with resolutions
    POST /review-drafts/{id}/discard

Authorization is the upload's: paper.upload on the draft's provider, branch and subject; a teacher only on drafts they started."""
import json
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from institute_common.config import settings
from institute_common.core import audit
from institute_common.core.roles import Role
from institute_common.core.security import Principal, api_error, get_principal
from institute_common.database import get_db, utcnow
from institute_common.models.academics import Subject
from institute_common.models.papers import PaperReviewDraft, StoredFile
from institute_common.util import Strict
from inst_paper.routes import papers
from inst_paper.services import recognition
from inst_paper.services.filestore import get_store
from inst_paper.services.math import domain_of_subject_id

router = APIRouter(prefix="/api/v1", tags=["review-drafts"])
PERM = "paper.upload"
MAX_LISTED = 50


class Decision(Strict):
    action: str = Field(pattern="^(keep_original|edit)$")
    text: str | None = None


class DraftChanges(Strict):
    base_revision: int = Field(ge=1)
    changes: dict[str, Decision | None] = Field(min_length=1, max_length=600)   # "<question>:<field>": a decision, or null to clear


class DraftImport(Strict):
    revision: int = Field(ge=1)          # the revision the importer has seen: a newer save by someone else must be seen first


def _ip(p: Principal) -> str | None:
    return p.ip if p.ip != "unknown" else None


def _out(d: PaperReviewDraft) -> dict:
    return {"draft_id": str(d.draft_id), "kind": d.kind, "status": d.status, "title": d.title, "file_name": d.file_name,
            "branch_id": str(d.branch_id), "subject_id": str(d.subject_id), "paper_id": str(d.paper_id) if d.paper_id else None,
            "has_answer_key": d.answer_key_file_id is not None, "engine_version": d.engine_version, "revision": d.revision,
            "decisions": d.decisions or {}, "created_by": str(d.created_by), "created_at": d.created_at.isoformat(),
            "updated_by": str(d.updated_by), "updated_at": d.updated_at.isoformat(),
            "result_paper_version_id": str(d.result_paper_version_id) if d.result_paper_version_id else None}


def _visible(p: Principal, d: PaperReviewDraft) -> bool:
    kw = dict(provider_id=d.provider_id, branch_id=d.branch_id, subject_id=d.subject_id)
    return p.can(PERM, exclude_roles=(Role.TEACHER,), **kw) or (p.can(PERM, **kw) and d.created_by == p.staff_id)


def _draft(db: Session, p: Principal, draft_id: UUID, *, lock: bool = False) -> PaperReviewDraft:
    q = select(PaperReviewDraft).where(PaperReviewDraft.draft_id == draft_id)
    d = db.scalar(q.with_for_update() if lock else q)
    providers = p.scope_provider_ids(PERM)                                    # None: a platform role
    if d is None or (providers is not None and d.provider_id not in providers):
        raise api_error(404, "NOT_FOUND", "Review draft not found.")          # another provider's draft does not exist for the caller
    papers._scope(p, PERM, d, db, owner_only_for_teachers=True)
    return d


def _open(d: PaperReviewDraft) -> None:
    if d.status != "OPEN":
        raise api_error(409, "DRAFT_CLOSED", "This review was already imported or discarded.")


def _files(db: Session, d: PaperReviewDraft) -> tuple[bytes, bytes | None, StoredFile, StoredFile | None]:
    sf = db.get(StoredFile, d.file_id)
    kf = db.get(StoredFile, d.answer_key_file_id) if d.answer_key_file_id else None
    return get_store().read(sf.storage_key), (get_store().read(kf.storage_key) if kf else None), sf, kf


def _stale(d: PaperReviewDraft) -> list[str]:
    """Decisions made with another engine version than the current one: the fields may now read differently, so each must be
    confirmed again before the paper can be imported."""
    return sorted(k for k, e in (d.decisions or {}).items() if e.get("decision") and e.get("engine") != recognition.ENGINE_VERSION)


def _domain(d: PaperReviewDraft) -> str | None:
    return domain_of_subject_id(d.subject_id, settings.recognition_subject_categories)


@router.post("/branches/{branch_id}/subjects/{subject_id}/review-drafts", status_code=201)
def start_draft(branch_id: UUID, subject_id: UUID, title: str = Form(min_length=3, max_length=255),
                paper_id: UUID | None = Form(default=None), file: UploadFile = File(...), answer_key: UploadFile | None = File(default=None),
                p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    br = papers._branch(db, branch_id)
    p.require(db, PERM, provider_id=br.provider_id, branch_id=branch_id, subject_id=subject_id)
    papers._check_subject(db, br.provider_id, subject_id)
    data, key_data, ext, stored_type = papers._read_upload(file, answer_key)
    papers._target_paper(db, p, paper_id, br, branch_id, subject_id)
    domain = domain_of_subject_id(subject_id, settings.recognition_subject_categories)
    token = recognition.review_token(data, key_data, domain)
    preview = papers._preview(data, key_data, ext, stored_type, domain, token,         # a file that cannot be read is never kept
                              papers.image_saver(db, p, br.provider_id))
    scope = dict(provider_id=br.provider_id, branch_id=branch_id, subject_id=subject_id)
    mine = select(PaperReviewDraft).where(*(getattr(PaperReviewDraft, k) == v for k, v in scope.items()),
                                          PaperReviewDraft.kind == "IMPORT", PaperReviewDraft.status == "OPEN",
                                          PaperReviewDraft.review_token == token, PaperReviewDraft.created_by == p.staff_id
                                          ).with_for_update()                  # never resumed while the cleanup removes it
    d = db.scalar(mine)
    resumed = d is not None
    if d is None:
        sf = papers._store_file(db, p, br, data, file.filename or "upload", stored_type)
        kf = papers._store_file(db, p, br, key_data, answer_key.filename or "answer-key", "text/csv") if key_data is not None else None
        d = PaperReviewDraft(kind="IMPORT", **scope, paper_id=paper_id, title=title, file_id=sf.file_id, file_name=sf.original_name,
                             file_ext=ext, answer_key_file_id=kf.file_id if kf else None, review_token=token,
                             engine_version=recognition.ENGINE_VERSION, decisions={}, created_by=p.staff_id, created_ip=_ip(p),
                             updated_by=p.staff_id, updated_ip=_ip(p), last_opened_at=utcnow())
        db.add(d)
        try:
            db.commit()
        except IntegrityError:                     # the same person started the same review twice at once: continue the first
            db.rollback()
            d, resumed = db.scalar(mine), True
    if resumed:
        d.title, d.paper_id = title, paper_id      # what to import it as may change between previews; the decisions stay
        d.last_opened_at = utcnow()
        db.commit()
    audit.record(db, "paper.review_draft.resume" if resumed else "paper.review_draft.create", "SUCCESS", actor=p.staff_id,
                 target_type="review_draft", target_id=d.draft_id, provider_id=br.provider_id, branch_id=branch_id, ip=p.ip,
                 details={"draft_id": str(d.draft_id), "questions": len(preview["questions"])})
    return {"draft": _out(d), "resumed": resumed, "preview": preview, "stale_keys": _stale(d), "engine_changed": bool(_stale(d))}


@router.get("/review-drafts")
def list_drafts(branch_id: UUID | None = None, provider_id: UUID | None = None, subject_id: UUID | None = None,
                p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Open import reviews of one branch, or with ?provider_id= alone ("All branches") of every branch of the provider; each is
    listed only when the caller may see it (_visible)."""
    if branch_id:
        br = papers._branch(db, branch_id)
        p.require(db, PERM, provider_id=br.provider_id, branch_id=branch_id)
        where = PaperReviewDraft.branch_id == branch_id
    elif provider_id:
        if not any(a.provider_id == provider_id for a in p.assignments):     # staff of the provider; _visible filters each draft
            p.require(db, PERM, provider_id=provider_id)
        where = PaperReviewDraft.provider_id == provider_id
    else:
        raise api_error(422, "BRANCH_REQUIRED", "Choose a branch or the provider.")
    q = select(PaperReviewDraft).where(where, PaperReviewDraft.status == "OPEN", PaperReviewDraft.kind == "IMPORT")
    if subject_id:
        q = q.where(PaperReviewDraft.subject_id == subject_id)
    drafts = [d for d in db.scalars(q.order_by(PaperReviewDraft.updated_at.desc()).limit(MAX_LISTED * 4)) if _visible(p, d)]
    return {"items": [{k: v for k, v in _out(d).items() if k != "decisions"} | {"decided": sum(1 for e in (d.decisions or {}).values()
                                                                                              if e.get("decision"))}
                      for d in drafts[:MAX_LISTED]]}


@router.get("/review-drafts/{draft_id}")
def get_draft(draft_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    d = _draft(db, p, draft_id)
    out = {"draft": _out(d)}
    if d.status == "OPEN":
        data, key_data, sf, _ = _files(db, d)
        token = recognition.review_token(data, key_data, _domain(d))
        out["preview"] = papers._preview(data, key_data, d.file_ext, sf.content_type, _domain(d), token,
                                         papers.image_saver(db, p, d.provider_id))
        # decisions made with an older engine are kept but must each be confirmed again (the import refuses them until then)
        out["stale_keys"] = _stale(d)
        out["engine_changed"] = bool(out["stale_keys"])
        d.last_opened_at = utcnow()                # opened: not abandoned (draft_retention)
        db.commit()
    return out


@router.patch("/review-drafts/{draft_id}")
def save_draft(draft_id: UUID, body: DraftChanges, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    d = _draft(db, p, draft_id, lock=True)        # row lock: two saves of one draft are applied one after the other
    _open(d)
    try:                                          # the same rules as decisions sent with an upload
        recognition.parse_resolutions(json.dumps({k: v.model_dump() for k, v in body.changes.items() if v is not None}))
        for key in body.changes:
            number, _, field = key.partition(":")
            if not number.isdigit() or field not in recognition.FIELDS:
                raise recognition.ResolutionError(f"Unknown review decision {key!r}.")
    except recognition.ResolutionError as exc:
        raise api_error(422, "INVALID_RESOLUTIONS", str(exc))
    current = dict(d.decisions or {})
    conflicts = {k: current[k] for k in body.changes if k in current and current[k]["rev"] > body.base_revision}
    if conflicts:
        db.rollback()
        audit.record(db, "paper.review_draft.save", "FAILURE", actor=p.staff_id, target_type="review_draft", target_id=d.draft_id,
                     provider_id=d.provider_id, branch_id=d.branch_id, ip=p.ip,
                     details={"draft_id": str(d.draft_id), "conflict": sorted(conflicts)[:50]})
        raise papers.api_error_detail(409, "DRAFT_CONFLICT", "Someone else changed these fields after you opened the review. "
                                      "Check their version, then keep yours or theirs.", conflicts=conflicts, revision=d.revision,
                                      decisions=current)
    d.revision += 1
    now = utcnow()
    for key, value in body.changes.items():
        current[key] = {"decision": value.model_dump() if value else None, "rev": d.revision, "by": str(p.staff_id), "at": now.isoformat(),
                        "engine": recognition.ENGINE_VERSION}       # the engine the decision was made with
    d.decisions, d.updated_by, d.updated_at, d.updated_ip = current, p.staff_id, now, _ip(p)
    db.commit()
    audit.record(db, "paper.review_draft.save", "SUCCESS", actor=p.staff_id, target_type="review_draft", target_id=d.draft_id,
                 provider_id=d.provider_id, branch_id=d.branch_id, ip=p.ip,
                 details={"draft_id": str(d.draft_id), "fields": sorted(body.changes)[:50], "revision": d.revision})   # never the text
    return {"revision": d.revision, "decisions": current, "updated_at": now.isoformat(), "updated_by": str(p.staff_id)}


@router.post("/review-drafts/{draft_id}/import", status_code=201)
def import_draft(draft_id: UUID, body: DraftImport, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    d = _draft(db, p, draft_id, lock=True)
    _open(d)
    if body.revision != d.revision:
        raise papers.api_error_detail(409, "DRAFT_CONFLICT", "The review was changed after you last saw it. Check the latest decisions "
                                      "before importing.", revision=d.revision, decisions=d.decisions or {})
    if stale := _stale(d):
        raise papers.api_error_detail(409, "REVIEW_STALE", "The recognition was improved since some decisions were made. Check and "
                                      "confirm each highlighted field again, then import.", stale_keys=stale)
    br = papers._branch(db, d.branch_id)
    existing = papers._target_paper(db, p, d.paper_id, br, d.branch_id, d.subject_id)
    data, key_data, sf, kf = _files(db, d)
    domain = _domain(d)
    decisions = {k: e["decision"] for k, e in (d.decisions or {}).items() if e.get("decision")}
    result, upload = papers.import_reviewed(db, p, br, d.subject_id, d.title, existing, sf, kf, data, key_data, d.file_ext,
                                            sf.content_type, decisions, recognition.review_token(data, key_data, domain), domain,
                                            db.get(Subject, d.subject_id))
    d = db.get(PaperReviewDraft, draft_id)
    d.status, d.result_upload_id, d.result_paper_version_id = "IMPORTED", upload.upload_id, UUID(result["paper_version_id"])
    d.updated_by, d.updated_at, d.updated_ip = p.staff_id, utcnow(), _ip(p)
    db.commit()
    audit.record(db, "paper.review_draft.import", "SUCCESS", actor=p.staff_id, target_type="review_draft", target_id=d.draft_id,
                 provider_id=d.provider_id, branch_id=d.branch_id, ip=p.ip,
                 details={"draft_id": str(d.draft_id), "paper_version_id": result["paper_version_id"], "revision": d.revision})
    return result


@router.post("/review-drafts/{draft_id}/discard")
def discard_draft(draft_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    d = _draft(db, p, draft_id, lock=True)
    _open(d)
    d.status, d.updated_by, d.updated_at, d.updated_ip = "DISCARDED", p.staff_id, utcnow(), _ip(p)
    db.commit()
    audit.record(db, "paper.review_draft.discard", "SUCCESS", actor=p.staff_id, target_type="review_draft", target_id=d.draft_id,
                 provider_id=d.provider_id, branch_id=d.branch_id, ip=p.ip, details={"draft_id": str(d.draft_id)})
    return {"draft": _out(d)}
