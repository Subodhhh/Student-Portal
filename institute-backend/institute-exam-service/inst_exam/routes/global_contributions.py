"""Global Test contributions: provider staff offer one of their approved papers for a published Global Test; the platform reviews it.

  PENDING --approve--> APPROVED   (platform; then copied into an independent GLOBAL paper by the paper service)
  PENDING --reject---> REJECTED   (platform, reason required)
  PENDING --withdraw-> WITHDRAWN  (the submitter or an admin of the contributing provider/branch, reason required)

Who may contribute: anyone who may publish the paper itself (`paper.publish` for the paper's provider, branch and subject), i.e.
provider admins, the paper branch's admins and teachers of that subject in that branch. The browser never names the provider, branch
or subject: they are taken from the paper version, and the version must be APPROVED or PUBLISHED.

Rules that keep a published Global Test unchanged and the platform in control:
  * a contribution never touches the test or its sections. An approved contribution is COPIED (paper service internal API, keyed by
    the contribution so a retry returns the same copy) into a GLOBAL paper whose version starts VALIDATED: it must still be approved
    and published through the normal paper workflow, and only a platform administrator puts it into a DRAFT test (a derived copy of
    a published test) through the existing, validated edit;
  * competing contributions for one subject are a CONFLICT the platform resolves explicitly: at most one APPROVED contribution per
    test and subject (partial unique index, so two simultaneous approvals cannot both succeed); nothing is chosen automatically;
  * one active (PENDING or APPROVED) contribution per test, provider and subject: resubmitting the same version returns the existing
    contribution, a different version must wait until the first is withdrawn or rejected.
Every change is audited; submitters are notified of decisions and platform administrators of new submissions."""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from institute_common.core import audit
from institute_common.core.roles import GLOBAL_ROLES, Role
from institute_common.core.security import Principal, api_error, get_principal
from institute_common.database import get_db, utcnow
from institute_common.internal_http import InternalServiceError, peer
from institute_common.models.academics import ProviderSubject, Subject
from institute_common.models.exams import GlobalTest, GlobalTestContribution, GlobalTestSection
from institute_common.models.org import Branch, Provider
from institute_common.models.papers import Paper, PaperVersion
from institute_common.models.staff import Staff, StaffRoleAssignment
from institute_common.util import Strict, need_reason
from inst_exam.routes.exams import api_error_detail
from inst_exam.routes.global_tests import _is_platform, _require_platform
from inst_exam.services.notify import notify

router = APIRouter(prefix="/api/v1", tags=["Global Test Contributions"])

USABLE = {"APPROVED", "PUBLISHED"}
CONTRIBUTE = "paper.publish"


class ContributionIn(Strict):
    paper_version_id: UUID
    note: str | None = Field(default=None, max_length=1000)


class NoteIn(Strict):
    note: str | None = Field(default=None, max_length=1000)


class ReasonIn(Strict):
    reason: str


class ContributionOut(BaseModel):
    contribution_id: UUID
    test_id: UUID
    test_name: str
    test_status: str
    subject_id: UUID
    subject: str | None
    provider_id: UUID
    provider_name: str | None
    branch_id: UUID
    branch_name: str | None
    paper_id: UUID
    paper_title: str
    paper_version_id: UUID
    version_no: int
    version_status: str
    question_count: int
    total_marks: str
    note: str | None
    status: str
    submitted_by: UUID
    submitted_by_name: str | None
    submitted_at: datetime
    reviewed_by_name: str | None
    reviewed_at: datetime | None
    review_note: str | None
    copy_status: str | None
    copy_error: str | None
    global_paper_id: UUID | None
    global_paper_version_id: UUID | None
    global_version_status: str | None
    # Platform-only context for resolving conflicts (None for provider staff, who never see other providers' submissions).
    competing: int | None = None
    section_exists: bool | None = None
    can_withdraw: bool = False


# ------------------------------------------------------------------ helpers

def _platform_staff(db: Session) -> list[UUID]:
    return list(db.scalars(select(StaffRoleAssignment.staff_id).join(Staff, Staff.staff_id == StaffRoleAssignment.staff_id)
                           .where(StaffRoleAssignment.status == "ACTIVE", Staff.status == "ACTIVE",
                                  StaffRoleAssignment.role.in_([r.value for r in GLOBAL_ROLES]))))


def _may_contribute(p: Principal, paper: Paper) -> bool:
    return paper.scope == "PROVIDER" and p.can(CONTRIBUTE, provider_id=paper.provider_id, branch_id=paper.branch_id, subject_id=paper.subject_id)


def _may_withdraw(p: Principal, c: GlobalTestContribution) -> bool:
    """The submitter, or an admin of the contributing provider/branch (teachers only their own submissions). Withdrawing is the
    provider's decision: the platform rejects a contribution instead."""
    return c.submitted_by == p.staff_id or p.can(CONTRIBUTE, provider_id=c.provider_id, branch_id=c.branch_id,
                                                 exclude_roles=(Role.TEACHER, *GLOBAL_ROLES))


def _may_see(p: Principal, c: GlobalTestContribution) -> bool:
    if _is_platform(p) or c.submitted_by == p.staff_id:
        return True
    if p.can("paper.read", provider_id=c.provider_id, branch_id=c.branch_id, exclude_roles=(Role.TEACHER,)):
        return True
    return p.can("paper.read", provider_id=c.provider_id, branch_id=c.branch_id, subject_id=c.subject_id)


def _published_test(db: Session, p: Principal, test_id: UUID) -> GlobalTest:
    t = db.get(GlobalTest, test_id)
    if not t or (t.status != "PUBLISHED" and not _is_platform(p)):
        raise api_error(404, "NOT_FOUND", "Global test not found.")
    return t


def _out_many(db: Session, p: Principal, rows: list[GlobalTestContribution]) -> list[ContributionOut]:
    if not rows:
        return []
    platform = _is_platform(p)
    versions = {v.paper_version_id: v for v in db.scalars(select(PaperVersion).where(PaperVersion.paper_version_id.in_(
        {c.paper_version_id for c in rows} | {c.global_paper_version_id for c in rows if c.global_paper_version_id})))}
    papers = {x.paper_id: x for x in db.scalars(select(Paper).where(Paper.paper_id.in_({v.paper_id for v in versions.values()})))}
    tests = {t.test_id: t for t in db.scalars(select(GlobalTest).where(GlobalTest.test_id.in_({c.test_id for c in rows})))}
    subjects = dict(db.execute(select(Subject.subject_id, Subject.name).where(Subject.subject_id.in_({c.subject_id for c in rows}))).all())
    providers = dict(db.execute(select(Provider.provider_id, Provider.name).where(Provider.provider_id.in_({c.provider_id for c in rows}))).all())
    branches = dict(db.execute(select(Branch.branch_id, Branch.name).where(Branch.branch_id.in_({c.branch_id for c in rows}))).all())
    people = dict(db.execute(select(Staff.staff_id, Staff.full_name).where(Staff.staff_id.in_(
        {c.submitted_by for c in rows} | {c.reviewed_by for c in rows if c.reviewed_by}))).all())
    competing: dict = {}
    sections: set = set()
    if platform:
        for tid, sid, n in db.execute(select(GlobalTestContribution.test_id, GlobalTestContribution.subject_id, func.count())
                                      .where(GlobalTestContribution.test_id.in_(tests), GlobalTestContribution.status.in_(("PENDING", "APPROVED")))
                                      .group_by(GlobalTestContribution.test_id, GlobalTestContribution.subject_id)):
            competing[(tid, sid)] = n
        sections = set(db.execute(select(GlobalTestSection.test_id, GlobalTestSection.subject_id).where(GlobalTestSection.test_id.in_(tests))).all())
    out = []
    for c in rows:
        v = versions.get(c.paper_version_id)
        paper = papers.get(v.paper_id) if v else None
        gv = versions.get(c.global_paper_version_id) if c.global_paper_version_id else None
        t = tests[c.test_id]
        out.append(ContributionOut(
            contribution_id=c.contribution_id, test_id=c.test_id, test_name=t.name, test_status=t.status, subject_id=c.subject_id,
            subject=subjects.get(c.subject_id), provider_id=c.provider_id, provider_name=providers.get(c.provider_id), branch_id=c.branch_id,
            branch_name=branches.get(c.branch_id), paper_id=paper.paper_id if paper else c.paper_version_id, paper_title=paper.title if paper else "—",
            paper_version_id=c.paper_version_id, version_no=v.version_no if v else 0, version_status=v.status if v else "MISSING",
            question_count=v.question_count if v else 0, total_marks=str(v.total_marks) if v else "0", note=c.note, status=c.status,
            submitted_by=c.submitted_by, submitted_by_name=people.get(c.submitted_by), submitted_at=c.submitted_at,
            reviewed_by_name=people.get(c.reviewed_by) if c.reviewed_by else None, reviewed_at=c.reviewed_at, review_note=c.review_note,
            copy_status=c.copy_status, copy_error=c.copy_error, global_paper_id=c.global_paper_id, global_paper_version_id=c.global_paper_version_id,
            global_version_status=gv.status if gv else None,
            competing=competing.get((c.test_id, c.subject_id), 0) if platform else None,
            section_exists=((c.test_id, c.subject_id) in sections) if platform else None,
            can_withdraw=c.status == "PENDING" and _may_withdraw(p, c)))
    return out


def _out(db: Session, p: Principal, c: GlobalTestContribution) -> ContributionOut:
    return _out_many(db, p, [c])[0]


def _contribution(db: Session, p: Principal, contribution_id: UUID, *, lock: bool = False) -> GlobalTestContribution:
    stmt = select(GlobalTestContribution).where(GlobalTestContribution.contribution_id == contribution_id)
    c = db.scalar(stmt.with_for_update() if lock else stmt)
    if not c or not _may_see(p, c):
        raise api_error(404, "NOT_FOUND", "Contribution not found.")
    return c


def _decided(db: Session, c: GlobalTestContribution, verb: str) -> None:
    t = db.get(GlobalTest, c.test_id)
    notify(db, [c.submitted_by], kind=f"GLOBAL_CONTRIBUTION_{verb}", title=f"Your contribution to {t.name} was {verb.lower()}",
           body=(f"Your paper for the Global Test '{t.name}' was {verb.lower()} by the platform."
                 + (f"\n\nNote: {c.review_note}" if c.review_note else "")),
           dedupe=f"gtc:{c.contribution_id}:{verb}", provider_id=c.provider_id)


# ------------------------------------------------------------------ provider side

@router.get("/global-tests/{test_id}/contribution-options")
def contribution_options(test_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Approved or published versions of the caller's own provider papers that the caller may contribute to this test (latest usable
    version per paper). Only papers the caller could publish are listed."""
    t = _published_test(db, p, test_id)
    provs = p.scope_provider_ids(CONTRIBUTE)
    if provs is not None and not provs:
        p.require(db, CONTRIBUTE)
    stmt = select(Paper).where(Paper.scope == "PROVIDER")
    if provs is not None:
        stmt = stmt.where(Paper.provider_id.in_(provs))
    items = []
    for paper in db.scalars(stmt.order_by(Paper.title)):
        if not _may_contribute(p, paper):
            continue
        v = db.scalar(select(PaperVersion).where(PaperVersion.paper_id == paper.paper_id, PaperVersion.status.in_(USABLE))
                      .order_by(PaperVersion.version_no.desc()).limit(1))
        if not v or _incompatible(t, paper):
            continue
        items.append({"paper_id": paper.paper_id, "paper_title": paper.title, "subject_id": paper.subject_id, "provider_id": paper.provider_id,
                      "branch_id": paper.branch_id, "paper_version_id": v.paper_version_id, "version_no": v.version_no, "version_status": v.status,
                      "question_count": v.question_count, "total_marks": str(v.total_marks)})
    names = dict(db.execute(select(Subject.subject_id, Subject.name).where(Subject.subject_id.in_({i["subject_id"] for i in items} or {None}))).all())
    for i in items:
        i["subject"] = names.get(i["subject_id"])
    return {"items": items[:300], "total": len(items)}


def _incompatible(t: GlobalTest, paper: Paper) -> str | None:
    """A paper set for a different exam type or class than the test cannot be offered for it."""
    for field, label in (("exam_type", "exam type"), ("target_class", "class")):
        want, have = getattr(t, field), getattr(paper, field)
        if want and have and want.strip().lower() != have.strip().lower():
            return f"The paper is for {label} '{have}', the test is for '{want}'."
    return None


@router.post("/global-tests/{test_id}/contributions", response_model=ContributionOut, status_code=201)
def submit_contribution(test_id: UUID, body: ContributionIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    t = _published_test(db, p, test_id)
    if t.status != "PUBLISHED":
        raise api_error(409, "NOT_PUBLISHED", "Papers can be contributed only to a published Global Test.")
    v = db.get(PaperVersion, body.paper_version_id)
    paper = db.get(Paper, v.paper_id) if v else None
    # Unknown, platform-owned and other providers' papers look the same: nothing about them is revealed.
    if not paper or not _may_contribute(p, paper):
        audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, details={"permission": "global_test.contribute",
                                                                                       "paper_version_id": str(body.paper_version_id)})
        raise api_error(404, "PAPER_NOT_AVAILABLE", "That paper version is not available to you.")
    if v.status not in USABLE:
        raise api_error(409, "VERSION_NOT_APPROVED", f"This version is {v.status}; only an approved or published version can be contributed.")
    subject = db.get(Subject, paper.subject_id)
    if not subject or subject.status != "ACTIVE" or not db.scalar(select(ProviderSubject).where(
            ProviderSubject.provider_id == paper.provider_id, ProviderSubject.subject_id == paper.subject_id)):
        raise api_error(422, "SUBJECT_NOT_AVAILABLE", "Your provider does not offer this subject.")
    problem = _incompatible(t, paper)
    if problem:
        raise api_error(422, "INCOMPATIBLE_PAPER", problem)

    def active():
        return db.scalar(select(GlobalTestContribution).where(
            GlobalTestContribution.test_id == test_id, GlobalTestContribution.provider_id == paper.provider_id,
            GlobalTestContribution.subject_id == paper.subject_id, GlobalTestContribution.status.in_(("PENDING", "APPROVED"))))

    def replay_or_conflict(existing: GlobalTestContribution) -> ContributionOut:
        if existing.paper_version_id == v.paper_version_id:
            return _out(db, p, existing)            # the same submission again: idempotent
        raise api_error_detail(409, "ALREADY_CONTRIBUTED", "Your provider already has an active contribution for this subject. "
                               "Withdraw it before offering another paper.", contribution_id=str(existing.contribution_id))

    existing = active()
    if existing:
        return replay_or_conflict(existing)
    c = GlobalTestContribution(test_id=test_id, subject_id=paper.subject_id, provider_id=paper.provider_id, branch_id=paper.branch_id,
                               paper_version_id=v.paper_version_id, note=(body.note or "").strip() or None, submitted_by=p.staff_id)
    db.add(c)
    try:
        db.commit()
    except IntegrityError:                          # a simultaneous submission won the unique index
        db.rollback()
        existing = active()
        if not existing:
            raise
        return replay_or_conflict(existing)
    audit.record(db, "global_test.contribution.submit", "SUCCESS", actor=p.staff_id, target_type="global_test_contribution",
                 target_id=c.contribution_id, provider_id=c.provider_id, branch_id=c.branch_id, ip=p.ip,
                 details={"test_id": str(test_id), "subject_id": str(c.subject_id), "paper_version_id": str(c.paper_version_id)})
    notify(db, _platform_staff(db), kind="GLOBAL_CONTRIBUTION_SUBMITTED", title=f"New paper offered for {t.name}",
           body=f"A provider offered a {subject.name} paper for the Global Test '{t.name}'. Review it in Test > Global Tests > Contributions.",
           dedupe=f"gtc:{c.contribution_id}:submitted")
    return _out(db, p, c)


@router.post("/global-test-contributions/{contribution_id}/withdraw", response_model=ContributionOut)
def withdraw_contribution(contribution_id: UUID, body: ReasonIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    c = _contribution(db, p, contribution_id, lock=True)
    reason = need_reason(body.reason)
    if not _may_withdraw(p, c):
        raise api_error(403, "FORBIDDEN", "Only the submitter or an administrator of the contributing provider can withdraw it.")
    if c.status != "PENDING":
        raise api_error(409, "INVALID_TRANSITION", f"Only a pending contribution can be withdrawn; this one is {c.status}.")
    c.status, c.review_note, c.reviewed_by, c.reviewed_at = "WITHDRAWN", reason, p.staff_id, utcnow()
    db.commit()
    audit.record(db, "global_test.contribution.withdraw", "SUCCESS", actor=p.staff_id, target_type="global_test_contribution",
                 target_id=c.contribution_id, provider_id=c.provider_id, branch_id=c.branch_id, reason=reason, ip=p.ip)
    return _out(db, p, c)


# ------------------------------------------------------------------ both sides

@router.get("/global-test-contributions")
def list_contributions(test_id: UUID | None = None, status: str | None = Query(default=None, pattern="^(PENDING|APPROVED|REJECTED|WITHDRAWN)$"),
                       page: int = 1, page_size: int = 50, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Platform administrators see every contribution with conflict context; provider staff see only their own provider's (teachers:
    their own submissions and the subjects they teach)."""
    stmt = select(GlobalTestContribution)
    if not _is_platform(p):
        provs = p.scope_provider_ids("paper.read")
        if provs is not None and not provs:
            p.require(db, "paper.read")
        if provs is not None:
            stmt = stmt.where(GlobalTestContribution.provider_id.in_(provs))
    if test_id:
        stmt = stmt.where(GlobalTestContribution.test_id == test_id)
    if status:
        stmt = stmt.where(GlobalTestContribution.status == status)
    rows = [c for c in db.scalars(stmt.order_by(GlobalTestContribution.submitted_at.desc())) if _may_see(p, c)]
    page, page_size = max(page, 1), min(max(page_size, 1), 100)
    return {"items": _out_many(db, p, rows[(page - 1) * page_size: page * page_size]), "total": len(rows), "page": page, "page_size": page_size}


# ------------------------------------------------------------------ platform side

@router.post("/global-test-contributions/{contribution_id}/reject", response_model=ContributionOut)
def reject_contribution(contribution_id: UUID, body: ReasonIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    _require_platform(db, p)
    reason = need_reason(body.reason)
    c = _contribution(db, p, contribution_id, lock=True)
    if c.status != "PENDING":
        raise api_error(409, "INVALID_TRANSITION", f"Only a pending contribution can be rejected; this one is {c.status}.")
    c.status, c.review_note, c.reviewed_by, c.reviewed_at = "REJECTED", reason, p.staff_id, utcnow()
    db.commit()
    audit.record(db, "global_test.contribution.reject", "SUCCESS", actor=p.staff_id, target_type="global_test_contribution",
                 target_id=c.contribution_id, provider_id=c.provider_id, branch_id=c.branch_id, reason=reason, ip=p.ip)
    _decided(db, c, "REJECTED")
    return _out(db, p, c)


@router.post("/global-test-contributions/{contribution_id}/approve", response_model=ContributionOut)
def approve_contribution(contribution_id: UUID, body: NoteIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Approve (resolving any conflict in favour of this contribution must be explicit: an already APPROVED contribution for the same
    subject blocks it) and copy the paper into an independent GLOBAL paper. The copy is retried by approving again or with
    /retry-copy; the approval itself is recorded first and never undone by a copy failure."""
    _require_platform(db, p)
    c = _contribution(db, p, contribution_id, lock=True)
    if c.status == "APPROVED" and c.copy_status != "COPIED":
        return _copy(db, p, c)                     # an approval whose copy failed: retry the copy
    if c.status != "PENDING":
        raise api_error(409, "INVALID_TRANSITION", f"Only a pending contribution can be approved; this one is {c.status}.")
    t = db.get(GlobalTest, c.test_id)
    if t.status != "PUBLISHED":
        raise api_error(409, "NOT_PUBLISHED", f"The Global Test is {t.status}; reject the contribution instead.")
    v = db.get(PaperVersion, c.paper_version_id)
    if not v or v.status not in USABLE:
        raise api_error(409, "VERSION_NOT_APPROVED", "The contributed version is no longer approved by its provider; reject it.")
    c.status, c.reviewed_by, c.reviewed_at = "APPROVED", p.staff_id, utcnow()
    c.review_note = (body.note or "").strip() or None
    c.copy_status, c.copy_error = "PENDING", None
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        winner = db.scalar(select(GlobalTestContribution).where(GlobalTestContribution.test_id == c.test_id,
                                                                GlobalTestContribution.subject_id == c.subject_id,
                                                                GlobalTestContribution.status == "APPROVED"))
        raise api_error_detail(409, "SUBJECT_CONFLICT", "Another contribution for this subject is already approved. Resolve the conflict "
                               "explicitly: keep that one, or create a new test version without it.",
                               approved_contribution_id=str(winner.contribution_id) if winner else None)
    audit.record(db, "global_test.contribution.approve", "SUCCESS", actor=p.staff_id, target_type="global_test_contribution",
                 target_id=c.contribution_id, provider_id=c.provider_id, branch_id=c.branch_id, ip=p.ip,
                 details={"test_id": str(c.test_id), "subject_id": str(c.subject_id), "paper_version_id": str(c.paper_version_id)})
    _decided(db, c, "APPROVED")
    return _copy(db, p, c)


@router.post("/global-test-contributions/{contribution_id}/retry-copy", response_model=ContributionOut)
def retry_copy(contribution_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    _require_platform(db, p)
    c = _contribution(db, p, contribution_id, lock=True)
    if c.status != "APPROVED":
        raise api_error(409, "INVALID_TRANSITION", "Only an approved contribution is copied.")
    return _copy(db, p, c)


def _copy(db: Session, p: Principal, c: GlobalTestContribution) -> ContributionOut:
    """Ask the paper service (owner of papers) for the GLOBAL copy. Idempotent there (unique contribution key), so retries are safe.
    The outcome is always recorded on the contribution: COPIED with the new ids, or FAILED with the reason."""
    if c.copy_status == "COPIED":
        return _out(db, p, c)
    try:
        resp = peer("paper").request("POST", "/papers/copy-global", json={
            "contribution_id": str(c.contribution_id), "source_paper_version_id": str(c.paper_version_id),
            "expected_provider_id": str(c.provider_id), "expected_branch_id": str(c.branch_id), "expected_subject_id": str(c.subject_id),
            "copied_by": str(p.staff_id), "authorization_reference": f"global-test-contribution:{c.contribution_id}"})
    except InternalServiceError:
        return _copy_failed(db, p, c, 503, "PAPER_SERVICE_UNAVAILABLE", "The paper service is unavailable. The approval is saved; retry the copy.")
    ids = _copy_ids(resp)
    if resp.status_code not in (200, 201) or not ids:
        code = _error_code(resp) or "COPY_REJECTED"
        return _copy_failed(db, p, c, 409 if resp.status_code < 500 else 502, code,
                            "The paper service refused the copy" + (f" ({code})" if code else "") + ". The approval is saved.")
    c.global_paper_id, c.global_paper_version_id = ids
    c.copy_status, c.copy_error = "COPIED", None
    db.commit()
    audit.record(db, "global_test.contribution.copy", "SUCCESS", actor=p.staff_id, target_type="global_test_contribution",
                 target_id=c.contribution_id, provider_id=c.provider_id, branch_id=c.branch_id, ip=p.ip,
                 details={"global_paper_id": str(ids[0]), "global_paper_version_id": str(ids[1])})
    return _out(db, p, c)


def _copy_ids(resp) -> tuple[UUID, UUID] | None:
    """The two ids of a successful copy response, validated (an unexpected body is treated as a failure, never trusted)."""
    try:
        body = resp.json()
        return UUID(str(body["global_paper_id"])), UUID(str(body["global_paper_version_id"]))
    except (ValueError, KeyError, TypeError):
        return None


def _error_code(resp) -> str | None:
    try:
        detail = resp.json().get("detail")
        code = detail.get("code") if isinstance(detail, dict) else None
        return str(code)[:60] if code else None
    except (ValueError, AttributeError):
        return None


def _copy_failed(db: Session, p: Principal, c: GlobalTestContribution, status: int, code: str, message: str):
    c.copy_status, c.copy_error = "FAILED", f"{code}: {message}"[:500]
    db.commit()
    audit.record(db, "global_test.contribution.copy", "FAILURE", actor=p.staff_id, target_type="global_test_contribution",
                 target_id=c.contribution_id, provider_id=c.provider_id, branch_id=c.branch_id, ip=p.ip, details={"error": code})
    raise api_error_detail(status, "COPY_FAILED", message, reason=code, contribution=_out(db, p, c).model_dump(mode="json"))
