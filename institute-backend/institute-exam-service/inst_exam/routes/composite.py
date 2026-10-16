"""Composite tests across providers.

The initiating provider (A) names the subjects and which provider should supply each. Every invited provider (B, C, ...) is notified,
reviews the request and either contributes one of its PUBLISHED papers or declines. A contribution grants the initiator USE of that
paper (a paper share, written by the paper service) but never access to its questions or files. The initiator then builds an
ordinary exam plan from the contributed paper versions; generation records every contributing provider and the exact versions."""
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from inst_exam.services.notify import notify
from institute_common.core import audit
from institute_common.core.roles import GLOBAL_ROLES, Role
from institute_common.core.security import Principal, api_error, get_principal
from institute_common.database import get_db, utcnow
from institute_common.internal_http import InternalServiceError, peer
from institute_common.models.academics import ProviderSubject, Subject
from institute_common.models.exams import CompositeRequest, CompositeRequestSlot
from institute_common.models.org import Branch, Provider
from institute_common.models.papers import Paper, PaperVersion
from institute_common.models.staff import StaffRoleAssignment
from institute_common.schemas.common import Page
from institute_common.util import Strict, escape_like, need_reason, paginate

router = APIRouter(prefix="/api/v1", tags=["Composite tests"])


class SlotIn(Strict):
    subject_id: UUID
    provider_id: UUID


class RequestIn(Strict):
    name: str = Field(min_length=3, max_length=255)
    note: str | None = Field(default=None, max_length=1000)
    slots: list[SlotIn] = Field(min_length=1, max_length=20)
    # Optional: invitations still pending at this instant expire (must include a UTC offset; between 1 hour and 90 days ahead).
    respond_by: datetime | None = None

    @field_validator("respond_by")
    @classmethod
    def deadline_window(cls, v: datetime | None):
        if v is None:
            return v
        if v.tzinfo is None:
            raise ValueError("respond_by must include a UTC offset")
        now = utcnow()
        if not (now + timedelta(hours=1) <= v <= now + timedelta(days=90)):
            raise ValueError("respond_by must be between 1 hour and 90 days from now")
        return v

    @field_validator("slots")
    @classmethod
    def one_slot_per_subject(cls, v: list[SlotIn]):
        if len({s.subject_id for s in v}) != len(v):
            raise ValueError("each subject may appear once in a request")
        return v


class ContributeIn(Strict):
    paper_version_id: UUID


class ReasonIn(Strict):
    reason: str = Field(max_length=1000)


# ------------------------------------------------------------------ helpers

def _key(value: str | None) -> str:
    if not value or not (8 <= len(value.strip()) <= 100):
        raise api_error(422, "IDEMPOTENCY_KEY_REQUIRED", "An Idempotency-Key header of 8-100 characters is required.")
    return value.strip()


def _slot_out(s: CompositeRequestSlot, *, with_paper: bool) -> dict:
    return {"slot_id": str(s.slot_id), "subject_id": str(s.subject_id), "provider_id": str(s.invited_provider_id), "status": s.status,
            "paper_version_id": str(s.paper_version_id) if (with_paper and s.paper_version_id) else None,
            "responded_at": s.responded_at.isoformat() if s.responded_at else None, "decline_reason": s.decline_reason}


def _header(r: CompositeRequest) -> dict:
    return {"request_id": str(r.request_id), "name": r.name, "note": r.note, "status": r.status, "created_at": r.created_at.isoformat(),
            "respond_by": r.respond_by.isoformat() if r.respond_by else None,
            "initiator_provider_id": str(r.initiator_provider_id), "initiator_branch_id": str(r.initiator_branch_id)}


def _slots(db: Session, r: CompositeRequest) -> list[CompositeRequestSlot]:
    return list(db.scalars(select(CompositeRequestSlot).where(CompositeRequestSlot.request_id == r.request_id).order_by(CompositeRequestSlot.position)))


def _full(db: Session, r: CompositeRequest, *, only_provider_ids: set | None = None) -> dict:
    slots = _slots(db, r)
    if only_provider_ids is not None:
        slots = [s for s in slots if s.invited_provider_id in only_provider_ids]
    return {**_header(r), "slots": [_slot_out(s, with_paper=True) for s in slots],
            "missing": [{"slot_id": str(s.slot_id), "subject_id": str(s.subject_id), "provider_id": str(s.invited_provider_id), "status": s.status}
                        for s in slots if s.status != "CONTRIBUTED"]}


def _decider(p: Principal, provider_id: UUID) -> bool:
    """May `p` speak for this provider in cross-provider matters? (provider-level admins and the global roles; never teachers/branch admins)."""
    return p.can("paper.publish", exclude_roles=(Role.TEACHER, Role.BRANCH_ADMIN), provider_id=provider_id)


def _deciders_of(db: Session, provider_id: UUID) -> list[UUID]:
    return list(dict.fromkeys(db.scalars(select(StaffRoleAssignment.staff_id).where(
        StaffRoleAssignment.role == Role.PROVIDER_ADMIN.value, StaffRoleAssignment.provider_id == provider_id, StaffRoleAssignment.status == "ACTIVE"))))


def _initiator_staff(db: Session, r: CompositeRequest) -> list[UUID]:
    return list(dict.fromkeys([*_deciders_of(db, r.initiator_provider_id), r.created_by]))


def _denied(db: Session, p: Principal, what: str, provider_id=None):
    audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, provider_id=provider_id, details={"permission": what})
    return api_error(403, "FORBIDDEN", "You do not have permission for this action.")


def _request(db: Session, request_id: UUID) -> CompositeRequest:
    r = db.get(CompositeRequest, request_id)
    if not r:
        raise api_error(404, "NOT_FOUND", "Composite request not found.")
    return r


def _slot(db: Session, r: CompositeRequest, slot_id: UUID) -> CompositeRequestSlot:
    s = db.get(CompositeRequestSlot, slot_id)
    if not s or s.request_id != r.request_id:
        raise api_error(404, "NOT_FOUND", "Slot not found.")
    return s


def _my_provider_ids(p: Principal) -> set[UUID] | None:
    """Providers this person decides for; None = every provider (global role)."""
    if any(Role(a.role) in GLOBAL_ROLES for a in p.assignments):
        return None
    return {a.provider_id for a in p.assignments if Role(a.role) == Role.PROVIDER_ADMIN}


# ------------------------------------------------------------------ initiator

@router.get("/composite-providers")
def find_providers(q: str = Query(min_length=2, max_length=100), p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Name lookup of ACTIVE providers that can be invited to a composite test (id and name only; at most 20 matches, 2+ characters)."""
    if not p.has_perm("examplan.manage"):
        raise _denied(db, p, "examplan.manage")
    rows = db.execute(select(Provider.provider_id, Provider.name).where(
        Provider.status == "ACTIVE", Provider.deleted_at.is_(None), Provider.name.ilike(f"%{escape_like(q)}%", escape="\\"))
        .order_by(Provider.name).limit(20)).all()
    return [{"provider_id": str(pid), "name": name} for pid, name in rows]


@router.post("/branches/{branch_id}/composite-requests", status_code=201)
def create_request(branch_id: UUID, body: RequestIn, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                   p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    br = db.get(Branch, branch_id)
    if not br or br.deleted_at is not None:
        raise api_error(404, "NOT_FOUND", "Branch not found.")
    p.require(db, "examplan.manage", provider_id=br.provider_id, branch_id=branch_id)
    key = _key(idempotency_key)

    wanted = sorted((str(s.subject_id), str(s.provider_id)) for s in body.slots)
    previous = db.scalar(select(CompositeRequest).where(CompositeRequest.created_by == p.staff_id, CompositeRequest.idempotency_key == key))
    if previous:
        same = previous.name == body.name and sorted((str(s.subject_id), str(s.invited_provider_id)) for s in _slots(db, previous)) == wanted
        if not same:
            raise api_error(409, "IDEMPOTENCY_CONFLICT", "This Idempotency-Key was already used for a different request.")
        return JSONResponse(status_code=200, content=_full(db, previous))

    for s in body.slots:
        prov = db.get(Provider, s.provider_id)
        if not prov or prov.deleted_at is not None or prov.status != "ACTIVE":
            raise api_error(422, "INVALID_PROVIDER", "An invited provider does not exist or is inactive.")
        if not db.get(Subject, s.subject_id):
            raise api_error(422, "INVALID_SUBJECT", "A subject does not exist.")
        for provider_id in {s.provider_id, br.provider_id}:      # the initiator runs the exam, the contributor supplies the paper
            if not db.scalar(select(ProviderSubject).where(ProviderSubject.provider_id == provider_id, ProviderSubject.subject_id == s.subject_id)):
                raise api_error(422, "SUBJECT_NOT_ENABLED", "A subject is not enabled for the initiating or the invited provider.")
    if db.scalar(select(CompositeRequest.request_id).where(CompositeRequest.initiator_branch_id == branch_id, CompositeRequest.name == body.name,
                                                           CompositeRequest.status == "OPEN")):
        raise api_error(409, "DUPLICATE_REQUEST", "An open request with this name already exists for this branch.")

    r = CompositeRequest(name=body.name, note=body.note, initiator_provider_id=br.provider_id, initiator_branch_id=branch_id,
                         idempotency_key=key, created_by=p.staff_id, respond_by=body.respond_by)
    db.add(r)
    try:
        db.flush()
        for pos, s in enumerate(body.slots, start=1):
            db.add(CompositeRequestSlot(request_id=r.request_id, position=pos, subject_id=s.subject_id, invited_provider_id=s.provider_id))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise api_error(409, "DUPLICATE_REQUEST", "An open request with this name already exists for this branch.")
    audit.record(db, "composite.request", "SUCCESS", actor=p.staff_id, target_type="composite_request", target_id=r.request_id,
                 provider_id=br.provider_id, branch_id=branch_id, ip=p.ip,
                 details={"slots": [{"subject_id": s, "provider_id": pr} for s, pr in wanted]})
    for provider_id in dict.fromkeys(s.provider_id for s in body.slots):
        if provider_id == br.provider_id:
            continue                                            # the initiator needs no invitation for its own slots
        staff = _deciders_of(db, provider_id)
        notify(db, staff, kind="COMPOSITE_INVITATION", title=f"Paper requested for a joint test: {r.name}",
               body=f"{db.get(Provider, br.provider_id).name} asks you to contribute an approved paper to the composite test '{r.name}'. "
                    "Open Composite invitations to review and respond.",
               dedupe=f"composite:{r.request_id}:{provider_id}:invited", provider_id=provider_id)
    return JSONResponse(status_code=201, content=_full(db, r))


@router.get("/branches/{branch_id}/composite-requests", response_model=Page[dict])
def list_requests(branch_id: UUID, status: str | None = Query(default=None, pattern="^(OPEN|COMPLETE|CANCELLED|EXPIRED)$"), page: int = 1, page_size: int = 25,
                  p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    br = db.get(Branch, branch_id)
    if not br:
        raise api_error(404, "NOT_FOUND", "Branch not found.")
    p.require(db, "exam.read", provider_id=br.provider_id, branch_id=branch_id)
    stmt = select(CompositeRequest).where(CompositeRequest.initiator_branch_id == branch_id)
    if status:
        stmt = stmt.where(CompositeRequest.status == status)
    res = paginate(db, stmt.order_by(CompositeRequest.created_at.desc()), page, page_size)
    res["items"] = [_full(db, r) for r in res["items"]]
    return res


@router.post("/composite-requests/{request_id}/cancel")
def cancel_request(request_id: UUID, body: ReasonIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    r = _request(db, request_id)
    if not p.can("examplan.manage", provider_id=r.initiator_provider_id, branch_id=r.initiator_branch_id):
        raise _denied(db, p, "examplan.manage", r.initiator_provider_id)
    reason = need_reason(body.reason)
    if r.status != "OPEN":
        raise api_error(409, "INVALID_STATE", f"The request is {r.status}.")
    r.status, r.closed_at, r.cancel_reason = "CANCELLED", utcnow(), reason
    db.commit()
    audit.record(db, "composite.cancel", "SUCCESS", actor=p.staff_id, target_type="composite_request", target_id=r.request_id,
                 provider_id=r.initiator_provider_id, branch_id=r.initiator_branch_id, reason=reason, ip=p.ip)
    for s in _slots(db, r):
        if s.status == "PENDING" and s.invited_provider_id != r.initiator_provider_id:
            notify(db, _deciders_of(db, s.invited_provider_id), kind="COMPOSITE_CANCELLED", title=f"Joint test cancelled: {r.name}",
                   body=f"The composite test '{r.name}' was cancelled by the initiating provider; no paper is needed any more.",
                   dedupe=f"composite:{r.request_id}:{s.slot_id}:cancelled", provider_id=s.invited_provider_id)
    return _full(db, r)


# ------------------------------------------------------------------ invited provider

@router.get("/composite-invitations")
def my_invitations(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Slots the caller's provider(s) are asked to fill, newest first. Another provider's slots and papers are never shown."""
    mine = _my_provider_ids(p)
    if mine is not None and not mine:
        return []
    stmt = select(CompositeRequestSlot, CompositeRequest).join(CompositeRequest, CompositeRequest.request_id == CompositeRequestSlot.request_id)
    if mine is not None:
        stmt = stmt.where(CompositeRequestSlot.invited_provider_id.in_(mine))
    out = []
    for slot, req in db.execute(stmt.order_by(CompositeRequest.created_at.desc()).limit(200)).all():
        init = db.get(Provider, req.initiator_provider_id)
        out.append({"request": {**_header(req), "initiator_provider_name": init.name if init else None}, "slot": _slot_out(slot, with_paper=True)})
    return out


@router.get("/composite-requests/{request_id}")
def get_request(request_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    r = _request(db, request_id)
    if p.can("exam.read", provider_id=r.initiator_provider_id, branch_id=r.initiator_branch_id):
        return _full(db, r)
    mine = {s.invited_provider_id for s in _slots(db, r) if _decider(p, s.invited_provider_id)}
    if not mine:
        raise _denied(db, p, "composite.read", r.initiator_provider_id)
    return _full(db, r, only_provider_ids=mine)


@router.post("/composite-requests/{request_id}/slots/{slot_id}/contribute")
def contribute(request_id: UUID, slot_id: UUID, body: ContributeIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    r = _request(db, request_id)
    s = _slot(db, r, slot_id)
    pv = db.get(PaperVersion, body.paper_version_id)
    paper = db.get(Paper, pv.paper_id) if pv else None
    allowed = _decider(p, s.invited_provider_id) or (
        paper is not None and paper.provider_id == s.invited_provider_id
        and p.can("paper.publish", exclude_roles=(Role.TEACHER,), provider_id=paper.provider_id, branch_id=paper.branch_id))
    if not allowed:
        raise _denied(db, p, "composite.contribute", s.invited_provider_id)
    if r.status != "OPEN" or s.status != "PENDING":
        raise api_error(409, "INVALID_STATE", "This slot can no longer be filled.")
    if r.respond_by is not None and utcnow() > r.respond_by:
        raise api_error(409, "EXPIRED", "The response deadline for this composite request has passed.")
    # Same message for "missing" and "not yours": other providers' content is never revealed.
    if not pv or not paper or paper.provider_id != s.invited_provider_id:
        raise api_error(422, "PAPER_NOT_AVAILABLE", "The paper does not exist or does not belong to the invited provider.")
    if paper.subject_id != s.subject_id:
        raise api_error(422, "SUBJECT_MISMATCH", "The paper is for a different subject.")
    if pv.status != "PUBLISHED":
        raise api_error(409, "NOT_PUBLISHED", "Only an approved, published paper version can be contributed.")

    try:
        resp = peer("paper").request("POST", "/paper-shares", json={
            "paper_id": str(paper.paper_id), "owner_provider_id": str(paper.provider_id),
            "shared_with_provider_id": str(r.initiator_provider_id), "granted_by": str(p.staff_id)})
    except InternalServiceError:
        raise api_error(503, "PAPER_SERVICE_UNAVAILABLE", "The paper service is unavailable; nothing was changed. Please retry.")
    if resp.status_code != 200:
        raise api_error(422, "SHARE_REJECTED", "The paper could not be shared with the initiating provider.")

    s.status, s.paper_id, s.paper_version_id, s.responded_by, s.responded_at = "CONTRIBUTED", paper.paper_id, pv.paper_version_id, p.staff_id, utcnow()
    if all(x.status == "CONTRIBUTED" for x in _slots(db, r) if x.slot_id != s.slot_id):
        r.status, r.closed_at = "COMPLETE", utcnow()
    db.commit()
    audit.record(db, "composite.contribute", "SUCCESS", actor=p.staff_id, target_type="composite_request", target_id=r.request_id,
                 provider_id=s.invited_provider_id, ip=p.ip, details={"slot_id": str(s.slot_id), "paper_version_id": str(pv.paper_version_id),
                                                                      "initiator_provider_id": str(r.initiator_provider_id)})
    notify(db, _initiator_staff(db, r), kind="COMPOSITE_CONTRIBUTION", title=f"Paper contributed for {r.name}",
           body=f"A paper for one subject of the composite test '{r.name}' was contributed. You may now use it in an exam plan.",
           dedupe=f"composite:{r.request_id}:{s.slot_id}:contributed", provider_id=r.initiator_provider_id)
    return {**_full(db, r), "slot": _slot_out(s, with_paper=True)}


@router.post("/composite-requests/{request_id}/slots/{slot_id}/decline")
def decline(request_id: UUID, slot_id: UUID, body: ReasonIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    r = _request(db, request_id)
    s = _slot(db, r, slot_id)
    if not _decider(p, s.invited_provider_id):
        raise _denied(db, p, "composite.decline", s.invited_provider_id)
    reason = need_reason(body.reason)
    if r.status != "OPEN" or s.status != "PENDING":
        raise api_error(409, "INVALID_STATE", "This slot can no longer be declined.")
    s.status, s.responded_by, s.responded_at, s.decline_reason = "DECLINED", p.staff_id, utcnow(), reason
    db.commit()
    audit.record(db, "composite.decline", "SUCCESS", actor=p.staff_id, target_type="composite_request", target_id=r.request_id,
                 provider_id=s.invited_provider_id, reason=reason, ip=p.ip, details={"slot_id": str(s.slot_id)})
    notify(db, _initiator_staff(db, r), kind="COMPOSITE_DECLINED", title=f"Paper declined for {r.name}",
           body=f"A provider declined to supply a paper for the composite test '{r.name}'. The exam can still be generated without that subject.",
           dedupe=f"composite:{r.request_id}:{s.slot_id}:declined", provider_id=r.initiator_provider_id)
    return {**_full(db, r), "slot": _slot_out(s, with_paper=True)}

