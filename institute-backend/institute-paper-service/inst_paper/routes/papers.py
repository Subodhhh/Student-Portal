"""Question bank and the paper workflow: upload -> extraction -> validation -> approval ->
publication, with immutable versions and a full status history."""
import hashlib
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import and_, distinct, false, func, or_, select
from sqlalchemy.orm import Session

from institute_common.config import settings
from institute_common.core import audit
from institute_common.core.roles import Role
from institute_common.core.security import Principal, api_error, get_principal
from institute_common.core.taxonomy import DIFFICULTIES, PATTERNS, Label, TopicLabel
from institute_common.database import get_db, utcnow
from institute_common.models.academics import ProviderSubject, Subject
from institute_common.models.org import Branch
from institute_common.models.papers import (
    Paper, PaperShare, PaperUpload, PaperVersion, PaperVersionEvent, PaperVersionQuestion,
    Question, QuestionVersion, StoredFile,
)
from institute_common.util import escape_like, need_reason, paginate
from institute_common.util import Strict
from institute_common.schemas.common import Page
from inst_paper.services import documents, extraction
from inst_paper.services.filestore import get_store

router = APIRouter(prefix="/api/v1", tags=["Questions & Papers"])

ALLOWED_TYPES = {"text/csv", "text/plain", "application/csv", "application/vnd.ms-excel"}
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
# extension -> (accepted declared content types, stored content type). The content itself is always verified (magic bytes), never the name.
UPLOAD_KINDS = {
    ".csv": (ALLOWED_TYPES, "text/csv"),
    ".xlsx": ({XLSX_TYPE, "application/octet-stream"}, XLSX_TYPE),
    ".docx": ({DOCX_TYPE, "application/octet-stream"}, DOCX_TYPE),
    ".pdf": ({"application/pdf", "application/octet-stream"}, "application/pdf"),
    ".png": ({"image/png"}, "image/png"),
    ".jpg": ({"image/jpeg"}, "image/jpeg"),
    ".jpeg": ({"image/jpeg"}, "image/jpeg"),
}


# ---------------------------------------------------------------- schemas



class Classification(Strict):
    """Where a question sits in the bank: exam type -> class -> (subject) -> chapter -> topic -> subtopic, plus difficulty/pattern."""
    exam_type: Label = None
    target_class: Label = None
    chapter: Label = None
    subtopic: Label = None
    difficulty: str | None = Field(default=None, pattern=f"^({'|'.join(DIFFICULTIES)})$")
    pattern: str = Field(default="SINGLE_CORRECT", pattern=f"^({'|'.join(PATTERNS)})$")
    subtype: Label = None


class QuestionIn(Classification):
    scope: Literal["PROVIDER", "GLOBAL"] = "PROVIDER"
    branch_id: UUID | None = None       # required for PROVIDER questions; GLOBAL questions belong to the platform catalogue
    subject_id: UUID
    stem: str = Field(min_length=3, max_length=5000)
    option_a: str = Field(min_length=1, max_length=5000)
    option_b: str = Field(min_length=1, max_length=5000)
    option_c: str = Field(min_length=1, max_length=5000)
    option_d: str = Field(min_length=1, max_length=5000)
    correct_option: str = Field(pattern="^[ABCD]$")
    marks_positive: Decimal = Field(gt=0, lt=10000)
    marks_negative: Decimal = Field(ge=0, lt=10000, default=Decimal(0))
    explanation: str | None = Field(default=None, max_length=5000)
    topic: TopicLabel = None

    @model_validator(mode="after")
    def scope_rule(self):
        if self.scope == "PROVIDER" and not self.branch_id:
            raise ValueError("a provider question needs branch_id")
        if self.scope == "GLOBAL" and self.branch_id:
            raise ValueError("a global question has no branch")
        return self


class QuestionPatch(Classification):
    """Re-classify or archive a question. Content changes are new versions (POST /questions/{id}/versions)."""
    pattern: str | None = Field(default=None, pattern=f"^({'|'.join(PATTERNS)})$")
    topic: TopicLabel = None
    status: str | None = Field(default=None, pattern="^(ACTIVE|ARCHIVED)$")


class QuestionVersionIn(Strict):
    stem: str = Field(min_length=3, max_length=5000)
    option_a: str = Field(min_length=1, max_length=5000)
    option_b: str = Field(min_length=1, max_length=5000)
    option_c: str = Field(min_length=1, max_length=5000)
    option_d: str = Field(min_length=1, max_length=5000)
    correct_option: str = Field(pattern="^[ABCD]$")
    marks_positive: Decimal = Field(gt=0, lt=10000)
    marks_negative: Decimal = Field(ge=0, lt=10000, default=Decimal(0))
    explanation: str | None = Field(default=None, max_length=5000)
    topic: str | None = Field(default=None, max_length=200)


class QuestionVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    question_version_id: UUID
    question_id: UUID
    version_no: int
    stem: str
    option_a: str
    option_b: str
    option_c: str
    option_d: str
    correct_option: str | None          # None when withheld (answers_hidden)
    explanation: str | None
    answers_hidden: bool = False
    topic: str | None
    marks_positive: Decimal
    marks_negative: Decimal


class BankQuestionOut(BaseModel):
    """A bank question with its latest content version. Staff-facing only: the Student services never call this API."""
    question_id: UUID
    scope: str
    provider_id: UUID | None
    branch_id: UUID | None
    subject_id: UUID
    status: str
    exam_type: str | None
    target_class: str | None
    chapter: str | None
    topic: str | None
    subtopic: str | None
    difficulty: str | None
    pattern: str
    subtype: str | None
    question_type: str
    owner_staff_id: UUID
    forked_from_question_id: UUID | None = None
    created_at: datetime
    latest: QuestionVersionOut
    usage_count: int
    can_edit: bool
    reserved_for_global_test: bool = False   # part of a Global Test: providers cannot reuse it in their own papers


class PaperVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    paper_version_id: UUID
    paper_id: UUID
    version_no: int
    status: str
    question_count: int
    total_marks: Decimal
    content_hash: str
    created_by: UUID
    created_at: datetime
    published_at: datetime | None


class PaperOut(BaseModel):
    paper_id: UUID
    scope: str = "PROVIDER"
    provider_id: UUID | None
    branch_id: UUID | None
    subject_id: UUID
    title: str
    exam_type: str | None = None
    target_class: str | None = None
    created_by: UUID
    latest_version: PaperVersionOut | None


class PaperBuildIn(Strict):
    """A new paper version assembled from bank questions: their exact latest versions are referenced, never copied."""
    scope: Literal["PROVIDER", "GLOBAL"] = "PROVIDER"
    branch_id: UUID | None = None
    subject_id: UUID
    title: str = Field(min_length=3, max_length=255)
    exam_type: Label = None
    target_class: Label = None
    paper_id: UUID | None = None          # add a new version to this paper instead of creating a paper
    question_ids: list[UUID] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def rules(self):
        if self.scope == "PROVIDER" and not self.branch_id:
            raise ValueError("a provider paper needs branch_id")
        if self.scope == "GLOBAL" and self.branch_id:
            raise ValueError("a global paper has no branch")
        if len(set(self.question_ids)) != len(self.question_ids):
            raise ValueError("a question may appear only once in a paper")
        return self


class PaperDetail(PaperOut):
    questions: list[QuestionVersionOut]
    history: list[dict]


class Note(Strict):
    note: str | None = Field(default=None, max_length=1000)


class ReasonIn(Strict):
    reason: str


class OverrideIn(Strict):
    to_status: str = Field(pattern="^(VALIDATED|APPROVED|PUBLISHED|WITHDRAWN)$")
    reason: str


class ShareIn(Strict):
    provider_id: UUID


class QuestionEdit(Strict):
    """A correction of one question of a paper version; only the supplied fields change."""
    position: int = Field(ge=1, le=10000)
    stem: str | None = Field(default=None, min_length=3, max_length=5000)
    option_a: str | None = Field(default=None, min_length=1, max_length=5000)
    option_b: str | None = Field(default=None, min_length=1, max_length=5000)
    option_c: str | None = Field(default=None, min_length=1, max_length=5000)
    option_d: str | None = Field(default=None, min_length=1, max_length=5000)
    correct_option: str | None = Field(default=None, pattern="^[ABCD]$")
    marks_positive: Decimal | None = Field(default=None, gt=0, lt=10000)
    marks_negative: Decimal | None = Field(default=None, ge=0, lt=10000)
    explanation: str | None = Field(default=None, max_length=5000)
    topic: str | None = Field(default=None, max_length=200)

    def changes(self) -> dict:
        return {k: v for k, v in self.model_dump(exclude={"position"}).items() if v is not None}

    @model_validator(mode="after")
    def must_change_something(self):
        if not self.changes():
            raise ValueError("an edit must change at least one field")
        return self


class RevisionIn(Strict):
    edits: list[QuestionEdit] = Field(min_length=1, max_length=500)
    reason: str = Field(max_length=1000)

    @model_validator(mode="after")
    def unique_positions(self):
        if len({e.position for e in self.edits}) != len(self.edits):
            raise ValueError("a question may be edited only once per revision")
        return self


# ---------------------------------------------------------------- helpers

def _branch(db: Session, branch_id: UUID) -> Branch:
    b = db.get(Branch, branch_id)
    if not b or b.deleted_at is not None:
        raise api_error(404, "NOT_FOUND", "Branch not found.")
    return b


def _check_subject(db: Session, provider_id: UUID, subject_id: UUID) -> None:
    if not db.scalar(select(ProviderSubject).where(ProviderSubject.provider_id == provider_id,
                                                  ProviderSubject.subject_id == subject_id)):
        raise api_error(422, "SUBJECT_NOT_ENABLED", "Subject is not enabled for this provider.")


def _scope(p: Principal, perm: str, obj, db: Session, *, owner_only_for_teachers: bool = False) -> None:
    """Authorize `perm` on an object with provider/branch/subject. Teachers are further
    restricted to their teaching assignments (via subject) and, for approve/publish,
    to papers they created themselves."""
    if getattr(obj, "scope", "PROVIDER") == "GLOBAL":
        _scope_global(p, perm, db)
        return
    kw = dict(provider_id=obj.provider_id, branch_id=obj.branch_id, subject_id=obj.subject_id)
    if p.can(perm, exclude_roles=(Role.TEACHER,), **kw):
        return
    if p.can(perm, **kw):
        owner = getattr(obj, "created_by", None) or getattr(obj, "owner_staff_id", None)
        if not owner_only_for_teachers or owner == p.staff_id:
            return
    if not p.can(perm, **kw):
        p.require(db, perm, **kw)
    audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, provider_id=obj.provider_id,
                 branch_id=obj.branch_id, details={"permission": perm, "reason": "not_owner"})
    raise api_error(403, "FORBIDDEN", "Teachers may only do this on papers they created.")


READ_PERMS = {"paper.read", "question.read"}


def _scope_global(p: Principal, perm: str, db: Session) -> None:
    """Platform (GLOBAL) questions and papers: every staff member holding the read permission may read and reuse them; only the
    platform roles (Super Admin / Admin) may create, change, approve, publish or withdraw them."""
    if perm in READ_PERMS and p.has_perm(perm):
        return
    if p.is_global and p.has_perm(perm):
        return
    audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, details={"permission": perm, "reason": "global_content"})
    raise api_error(403, "FORBIDDEN", "Only platform administrators may change platform questions and papers.")


def _paper(db: Session, paper_id: UUID) -> Paper:
    p = db.get(Paper, paper_id)
    if not p:
        raise api_error(404, "NOT_FOUND", "Paper not found.")
    return p


def _pv(db: Session, pv_id: UUID) -> tuple[PaperVersion, Paper]:
    v = db.get(PaperVersion, pv_id)
    if not v:
        raise api_error(404, "NOT_FOUND", "Paper version not found.")
    return v, _paper(db, v.paper_id)


def _event(db, v: PaperVersion, kind, actor, from_s=None, to_s=None, reason=None):
    db.add(PaperVersionEvent(paper_version_id=v.paper_version_id, event_type=kind, from_status=from_s,
                             to_status=to_s, actor_staff_id=actor, reason=reason))


def _hash_questions(items: list[tuple]) -> str:
    h = hashlib.sha256()
    for it in items:
        # Decimals are normalised so 4, 4.0 and 4.00 hash alike (database values come back as 4.00).
        h.update("\x1f".join(format(x.normalize(), "f") if isinstance(x, Decimal) else str(x) for x in it).encode())
        h.update(b"\x1e")
    return h.hexdigest()


def _latest(db: Session, paper_id: UUID) -> PaperVersion | None:
    return db.scalar(select(PaperVersion).where(PaperVersion.paper_id == paper_id).order_by(PaperVersion.version_no.desc()).limit(1))


# ---------------------------------------------------------------- question bank

CLASSIFICATION = ("exam_type", "target_class", "chapter", "topic", "subtopic", "difficulty", "pattern", "subtype")


@router.post("/questions", response_model=QuestionVersionOut, status_code=201)
def create_question(body: QuestionIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    content = body.model_dump(include={"stem", "option_a", "option_b", "option_c", "option_d", "correct_option", "marks_positive",
                                       "marks_negative", "explanation", "topic"})
    meta = body.model_dump(include=set(CLASSIFICATION))
    if body.scope == "GLOBAL":
        _scope_global(p, "question.manage", db)
        _active_subject(db, body.subject_id)
        q = Question(scope="GLOBAL", subject_id=body.subject_id, owner_staff_id=p.staff_id, **meta)
        provider_id = branch_id = None
    else:
        br = _branch(db, body.branch_id)
        p.require(db, "question.manage", provider_id=br.provider_id, branch_id=br.branch_id, subject_id=body.subject_id)
        _check_subject(db, br.provider_id, body.subject_id)
        q = Question(provider_id=br.provider_id, branch_id=br.branch_id, subject_id=body.subject_id, owner_staff_id=p.staff_id, **meta)
        provider_id, branch_id = br.provider_id, br.branch_id
    db.add(q)
    db.flush()
    v = QuestionVersion(question_id=q.question_id, version_no=1, created_by=p.staff_id, **content)
    db.add(v)
    db.commit()
    audit.record(db, "question.create", "SUCCESS", actor=p.staff_id, target_type="question", target_id=q.question_id,
                 provider_id=provider_id, branch_id=branch_id, ip=p.ip, details={"scope": q.scope})
    return v


def _active_subject(db: Session, subject_id: UUID) -> None:
    subj = db.get(Subject, subject_id)
    if not subj or subj.status != "ACTIVE":
        raise api_error(422, "SUBJECT_NOT_AVAILABLE", "The subject is not in the active platform catalogue.")


@router.post("/questions/{question_id}/versions", response_model=QuestionVersionOut, status_code=201)
def new_question_version(question_id: UUID, body: QuestionVersionIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    q = db.get(Question, question_id)
    if not q:
        raise api_error(404, "NOT_FOUND", "Question not found.")
    _scope(p, "question.manage", q, db, owner_only_for_teachers=True)
    last = db.scalar(select(func.max(QuestionVersion.version_no)).where(QuestionVersion.question_id == question_id)) or 0
    v = QuestionVersion(question_id=question_id, version_no=last + 1, created_by=p.staff_id, **body.model_dump())
    db.add(v)
    db.commit()  # earlier versions (and any paper that used them) are untouched
    audit.record(db, "question.new_version", "SUCCESS", actor=p.staff_id, target_type="question", target_id=question_id,
                 provider_id=q.provider_id, branch_id=q.branch_id, ip=p.ip)
    return v


@router.get("/branches/{branch_id}/questions", response_model=Page[QuestionVersionOut])
def list_questions(branch_id: UUID, subject_id: UUID | None = None, page: int = 1, page_size: int = 25,
                   p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    br = _branch(db, branch_id)
    p.require(db, "question.read", provider_id=br.provider_id, branch_id=branch_id, subject_id=subject_id)
    latest = select(QuestionVersion.question_id, func.max(QuestionVersion.version_no).label("n")).group_by(QuestionVersion.question_id).subquery()
    stmt = (select(QuestionVersion).join(latest, (latest.c.question_id == QuestionVersion.question_id) & (latest.c.n == QuestionVersion.version_no))
            .join(Question, Question.question_id == QuestionVersion.question_id)
            .where(Question.branch_id == branch_id, Question.status == "ACTIVE"))
    if subject_id:
        stmt = stmt.where(Question.subject_id == subject_id)
    elif not p.can("question.read", exclude_roles=(Role.TEACHER,), provider_id=br.provider_id, branch_id=branch_id):
        taught = {t.subject_id for t in p.teaching if t.branch_id == branch_id}
        stmt = stmt.where(Question.subject_id.in_(taught)) if taught else stmt.where(False)
    return paginate(db, stmt.order_by(QuestionVersion.created_at.desc()), page, page_size)


def _latest_versions():
    return (select(QuestionVersion.question_id, func.max(QuestionVersion.version_no).label("n"))
            .group_by(QuestionVersion.question_id).subquery())


def _usage_counts():
    """Paper versions using any version of each question (a question is reused by reference, never duplicated)."""
    return (select(QuestionVersion.question_id, func.count(distinct(PaperVersionQuestion.paper_version_id)).label("uses"))
            .join(PaperVersionQuestion, PaperVersionQuestion.question_version_id == QuestionVersion.question_version_id)
            .group_by(QuestionVersion.question_id).subquery())


def _question_visibility(p: Principal, db: Session, scope: str, provider_id: UUID | None, branch_id: UUID | None):
    """SQL condition for the questions `p` may read. GLOBAL questions: anyone holding question.read. PROVIDER questions: a platform
    role may look at any provider (optionally narrowed by provider/branch); everyone else must name a branch they can read, and
    teachers then see only the subjects they teach there."""
    conds = []
    if scope in ("GLOBAL", "ALL") and p.has_perm("question.read"):
        conds.append(Question.scope == "GLOBAL")
    if scope in ("PROVIDER", "ALL"):
        if p.is_global and p.has_perm("question.read") and not branch_id:
            conds.append(and_(Question.scope == "PROVIDER", Question.provider_id == provider_id) if provider_id else Question.scope == "PROVIDER")
        elif branch_id:
            br = _branch(db, branch_id)
            p.require(db, "question.read", provider_id=br.provider_id, branch_id=branch_id)
            cond = and_(Question.scope == "PROVIDER", Question.branch_id == branch_id)
            if not p.can("question.read", exclude_roles=(Role.TEACHER,), provider_id=br.provider_id, branch_id=branch_id):
                taught = {t.subject_id for t in p.teaching if t.branch_id == branch_id}
                cond = and_(cond, Question.subject_id.in_(taught)) if taught else false()
            conds.append(cond)
        elif scope == "PROVIDER":
            raise api_error(422, "BRANCH_REQUIRED", "Choose a branch to list its questions.")
    if not conds:
        p.require(db, "question.read")   # nothing visible at all: same answer as any other missing permission
    return or_(*conds)


def _can_edit_question(p: Principal, q: Question) -> bool:
    if q.scope == "GLOBAL":
        return p.is_global and p.has_perm("question.manage")
    kw = dict(provider_id=q.provider_id, branch_id=q.branch_id, subject_id=q.subject_id)
    return p.can("question.manage", exclude_roles=(Role.TEACHER,), **kw) or (p.can("question.manage", **kw) and q.owner_staff_id == p.staff_id)


# ---------------------------------------------------------------- Global Test answer protection
# Platform content that belongs to a Global Test (any version of it appears in a global_test_sections paper version) is RESERVED:
#   * platform roles see everything;
#   * a provider's staff see its questions but not the correct answers or explanations, until the review point for their provider:
#     an exam of their provider that used that paper version and whose window has closed (the moment students may review answers);
#   * providers cannot build reserved questions into their own papers (their paper pages would otherwise reveal the answers).
# Other platform questions keep their answers visible to staff (they are meant to be reused in providers' own papers).

def _reserved_versions(db: Session):
    from institute_common.models.exams import GlobalTestSection
    return select(GlobalTestSection.paper_version_id)


def reserved_question_ids(db: Session, question_ids) -> set[UUID]:
    ids = list(question_ids)
    if not ids:
        return set()
    return {qid for (qid,) in db.execute(
        select(QuestionVersion.question_id).join(PaperVersionQuestion, PaperVersionQuestion.question_version_id == QuestionVersion.question_version_id)
        .where(PaperVersionQuestion.paper_version_id.in_(_reserved_versions(db)), QuestionVersion.question_id.in_(ids)).distinct())}


def released_question_ids(db: Session, p: Principal, question_ids) -> set[UUID]:
    """Reserved questions whose answers the caller's providers may now see (an exam of theirs using the version has closed)."""
    from institute_common.models.exams import Exam, ExamVersion, ExamVersionPaper
    ids = list(question_ids)
    providers = p.scope_provider_ids("paper.read")
    if not ids or providers is None or not providers:
        return set()
    closed = (select(ExamVersionPaper.paper_version_id)
              .join(ExamVersion, ExamVersion.exam_version_id == ExamVersionPaper.exam_version_id)
              .join(Exam, Exam.exam_id == ExamVersion.exam_id)
              .where(Exam.provider_id.in_(providers), Exam.status != "CANCELLED", ExamVersion.end_at < utcnow(),
                     ExamVersionPaper.paper_version_id.in_(_reserved_versions(db))))
    return {qid for (qid,) in db.execute(
        select(QuestionVersion.question_id).join(PaperVersionQuestion, PaperVersionQuestion.question_version_id == QuestionVersion.question_version_id)
        .where(PaperVersionQuestion.paper_version_id.in_(closed), QuestionVersion.question_id.in_(ids)).distinct())}


class AnswerPolicy:
    """Decides, for one request, which questions' answers are withheld from the caller."""

    def __init__(self, db: Session, p: Principal, questions):
        self.platform = p.is_global
        # Decided by the reserved paper versions alone (not by the question's own scope), so it holds even for unusual data.
        self.reserved = reserved_question_ids(db, {q.question_id for q in questions})
        self.hidden = set() if self.platform else self.reserved - released_question_ids(db, p, self.reserved)

    def version_out(self, v: QuestionVersion) -> QuestionVersionOut:
        out = QuestionVersionOut.model_validate(v)
        if v.question_id in self.hidden:
            out = out.model_copy(update={"correct_option": None, "explanation": None, "answers_hidden": True})
        return out


def _bank_out(p: Principal, q: Question, v: QuestionVersion, uses: int, policy: AnswerPolicy) -> BankQuestionOut:
    return BankQuestionOut(**{c: getattr(q, c) for c in ("question_id", "scope", "provider_id", "branch_id", "subject_id", "status", "exam_type",
                                                         "target_class", "chapter", "topic", "subtopic", "difficulty", "pattern", "subtype",
                                                         "question_type", "owner_staff_id", "forked_from_question_id", "created_at")},
                           latest=policy.version_out(v), usage_count=uses or 0, can_edit=_can_edit_question(p, q),
                           reserved_for_global_test=q.question_id in policy.reserved)


@router.get("/questions", response_model=Page[BankQuestionOut])
def search_questions(
    scope: str = Query(default="ALL", pattern="^(ALL|GLOBAL|PROVIDER)$"),
    provider_id: UUID | None = None, branch_id: UUID | None = None,
    subject_id: list[UUID] | None = Query(default=None, max_length=20),
    exam_type: str | None = Query(default=None, max_length=80), target_class: str | None = Query(default=None, max_length=80),
    chapter: list[str] | None = Query(default=None, max_length=30), topic: str | None = Query(default=None, max_length=80),
    subtopic: str | None = Query(default=None, max_length=80),
    difficulty: list[str] | None = Query(default=None, max_length=3), pattern: str | None = Query(default=None, max_length=30),
    include_used: bool = True, status: str = Query(default="ACTIVE", pattern="^(ACTIVE|ARCHIVED|ALL)$"),
    q: str | None = Query(default=None, max_length=200), page: int = 1, page_size: int = 25,
    p: Principal = Depends(get_principal), db: Session = Depends(get_db),
):
    """The question bank: reusable questions with their latest version, filtered along the taxonomy. Only staff use this API; the
    Student services receive questions solely through exam delivery, and answers there are revealed only at result review."""
    latest, uses = _latest_versions(), _usage_counts()
    stmt = (select(Question, QuestionVersion, uses.c.uses)
            .join(latest, latest.c.question_id == Question.question_id)
            .join(QuestionVersion, (QuestionVersion.question_id == Question.question_id) & (QuestionVersion.version_no == latest.c.n))
            .outerjoin(uses, uses.c.question_id == Question.question_id)
            .where(_question_visibility(p, db, scope, provider_id, branch_id)))
    if status != "ALL":
        stmt = stmt.where(Question.status == status)
    if subject_id:
        stmt = stmt.where(Question.subject_id.in_(subject_id))
    for col, val in ((Question.exam_type, exam_type), (Question.target_class, target_class), (Question.topic, topic),
                     (Question.subtopic, subtopic), (Question.pattern, pattern)):
        if val:
            stmt = stmt.where(col == val)
    if chapter:
        stmt = stmt.where(Question.chapter.in_(chapter))
    if difficulty:
        stmt = stmt.where(Question.difficulty.in_(difficulty))
    if not include_used:
        stmt = stmt.where(uses.c.uses.is_(None))
    if q:
        stmt = stmt.where(QuestionVersion.stem.ilike(f"%{escape_like(q)}%", escape="\\"))
    page_size = min(max(page_size, 1), 100)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.execute(stmt.order_by(Question.created_at.desc(), Question.question_id).limit(page_size).offset((max(page, 1) - 1) * page_size)).all()
    policy = AnswerPolicy(db, p, [qq for qq, _, _ in rows])
    return {"items": [_bank_out(p, qq, v, n, policy) for qq, v, n in rows], "total": total, "page": max(page, 1), "page_size": page_size}


def _question(db: Session, question_id: UUID) -> Question:
    q = db.get(Question, question_id)
    if not q:
        raise api_error(404, "NOT_FOUND", "Question not found.")
    return q


@router.get("/questions/{question_id}")
def get_question(question_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """One question: every content version (immutable) and the papers that use it, limited to papers the caller may read."""
    q = _question(db, question_id)
    _scope(p, "question.read", q, db)
    versions = db.scalars(select(QuestionVersion).where(QuestionVersion.question_id == question_id).order_by(QuestionVersion.version_no.desc())).all()
    used = db.execute(
        select(Paper, PaperVersion, QuestionVersion.version_no)
        .join(PaperVersion, PaperVersion.paper_id == Paper.paper_id)
        .join(PaperVersionQuestion, PaperVersionQuestion.paper_version_id == PaperVersion.paper_version_id)
        .join(QuestionVersion, QuestionVersion.question_version_id == PaperVersionQuestion.question_version_id)
        .where(QuestionVersion.question_id == question_id).order_by(PaperVersion.created_at.desc()).limit(200)).all()
    usage, hidden = [], 0
    for paper, pv, qv_no in used:
        visible = (paper.scope == "GLOBAL" and p.has_perm("paper.read")) or (
            paper.scope == "PROVIDER" and p.can("paper.read", provider_id=paper.provider_id, branch_id=paper.branch_id, subject_id=paper.subject_id))
        if not visible:
            hidden += 1   # used by another provider: counted, never named
            continue
        usage.append({"paper_id": str(paper.paper_id), "title": paper.title, "paper_version_id": str(pv.paper_version_id),
                      "version_no": pv.version_no, "status": pv.status, "question_version_no": qv_no})
    latest_uses = db.scalar(select(func.count(distinct(PaperVersionQuestion.paper_version_id))).join(
        QuestionVersion, QuestionVersion.question_version_id == PaperVersionQuestion.question_version_id).where(QuestionVersion.question_id == question_id)) or 0
    policy = AnswerPolicy(db, p, [q])
    return {"question": _bank_out(p, q, versions[0], latest_uses, policy), "versions": [policy.version_out(v) for v in versions],
            "usage": usage, "usage_elsewhere": hidden}


@router.patch("/questions/{question_id}", response_model=BankQuestionOut)
def update_question(question_id: UUID, body: QuestionPatch, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Re-classify or archive/restore. Archiving hides the question from the bank; papers and exams that already use one of its
    versions are unaffected (they reference the exact version)."""
    q = _question(db, question_id)
    _scope(p, "question.manage", q, db, owner_only_for_teachers=True)
    changes = body.model_dump(exclude_unset=True)
    if "pattern" in changes and changes["pattern"] is None:
        changes.pop("pattern")
    for k, v in changes.items():
        setattr(q, k, v)
    db.commit()
    audit.record(db, "question.archive" if changes.get("status") == "ARCHIVED" else "question.update", "SUCCESS", actor=p.staff_id,
                 target_type="question", target_id=question_id, provider_id=q.provider_id, branch_id=q.branch_id, ip=p.ip,
                 details={"fields": sorted(changes)})
    v = db.scalar(select(QuestionVersion).where(QuestionVersion.question_id == question_id).order_by(QuestionVersion.version_no.desc()).limit(1))
    uses = db.scalar(select(func.count(distinct(PaperVersionQuestion.paper_version_id))).join(
        QuestionVersion, QuestionVersion.question_version_id == PaperVersionQuestion.question_version_id).where(QuestionVersion.question_id == question_id)) or 0
    return _bank_out(p, q, v, uses, AnswerPolicy(db, p, [q]))


@router.get("/question-taxonomy")
def question_taxonomy(scope: str = Query(default="ALL", pattern="^(ALL|GLOBAL|PROVIDER)$"), provider_id: UUID | None = None,
                      branch_id: UUID | None = None, subject_id: list[UUID] | None = Query(default=None, max_length=20),
                      p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Values in use at each taxonomy level among the questions the caller can see (drives the bank's filter dropdowns)."""
    base = select(Question).where(_question_visibility(p, db, scope, provider_id, branch_id), Question.status == "ACTIVE")
    if subject_id:
        base = base.where(Question.subject_id.in_(subject_id))
    sub = base.subquery()
    out = {}
    for col in ("exam_type", "target_class", "chapter", "topic", "subtopic"):
        c = getattr(sub.c, col)
        out[col] = [v for (v,) in db.execute(select(c).where(c.is_not(None)).distinct().order_by(c).limit(500))]
    out["difficulty"], out["pattern"] = list(DIFFICULTIES), list(PATTERNS)
    return out


# ---------------------------------------------------------------- upload & extraction

@router.post("/branches/{branch_id}/subjects/{subject_id}/papers/upload", status_code=201)
def upload_paper(branch_id: UUID, subject_id: UUID, title: str = Form(min_length=3, max_length=255),
                 paper_id: UUID | None = Form(default=None), file: UploadFile = File(...),
                 answer_key: UploadFile | None = File(default=None),
                 p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    br = _branch(db, branch_id)
    p.require(db, "paper.upload", provider_id=br.provider_id, branch_id=branch_id, subject_id=subject_id)
    _check_subject(db, br.provider_id, subject_id)
    name_l = (file.filename or "").lower()
    ext = next((e for e in UPLOAD_KINDS if name_l.endswith(e)), None)
    if ext is None or (file.content_type or "").split(";")[0].strip().lower() not in UPLOAD_KINDS[ext][0]:
        raise api_error(415, "UNSUPPORTED_FILE", "Upload a .csv, one-sheet .xlsx, .docx or text .pdf file (images only where image extraction is enabled).")
    stored_type = UPLOAD_KINDS[ext][1]
    data = file.file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise api_error(413, "FILE_TOO_LARGE", f"File exceeds {settings.max_upload_bytes // 1024} KB.")
    if not data:
        raise api_error(422, "EMPTY_FILE", "The file is empty.")

    key_data = None
    if answer_key is not None and (answer_key.filename or answer_key.size):
        key_name = (answer_key.filename or "").lower()
        if not key_name.endswith((".csv", ".txt")) or (answer_key.content_type or "").split(";")[0].strip().lower() not in ALLOWED_TYPES:
            raise api_error(415, "UNSUPPORTED_ANSWER_KEY", "Upload the answer key as a .csv or .txt file (one 'number,answer' per line).")
        key_data = answer_key.file.read(settings.max_upload_bytes + 1)
        if len(key_data) > settings.max_upload_bytes:
            raise api_error(413, "FILE_TOO_LARGE", f"Answer key exceeds {settings.max_upload_bytes // 1024} KB.")
        if not key_data:
            raise api_error(422, "EMPTY_FILE", "The answer key is empty.")

    existing = None
    if paper_id:
        existing = _paper(db, paper_id)
        if (existing.provider_id, existing.branch_id, existing.subject_id) != (br.provider_id, branch_id, subject_id):
            raise api_error(422, "PAPER_MISMATCH", "Paper belongs to a different branch or subject.")
        _scope(p, "paper.upload", existing, db, owner_only_for_teachers=True)

    key, sha = get_store().save(data)
    sf = StoredFile(provider_id=br.provider_id, storage_key=key, original_name=(file.filename or "upload")[:255],
                    content_type=stored_type, size_bytes=len(data), sha256=sha, uploaded_by=p.staff_id)
    db.add(sf)
    db.flush()
    key_file, mapping = None, None
    key_errors: list[dict] = []
    if key_data is not None:
        k_key, k_sha = get_store().save(key_data)
        key_file = StoredFile(provider_id=br.provider_id, storage_key=k_key, original_name=(answer_key.filename or "answer-key")[:255],
                              content_type="text/csv", size_bytes=len(key_data), sha256=k_sha, uploaded_by=p.staff_id)
        db.add(key_file)
        db.flush()
        mapping, key_errors = extraction.parse_answer_key(key_data, settings.max_paper_questions)
    with extraction.answer_key(mapping if key_data is not None and not key_errors else None):
        if key_errors:
            result = extraction.ExtractionResult(errors=key_errors)     # an unreadable key rejects the paper before extraction
        elif ext == ".csv":
            result = extraction.extract_csv(data, settings.max_paper_questions)
        elif ext == ".xlsx":
            result = extraction.extract_xlsx(data, settings.max_paper_questions)
        elif ext == ".docx":
            result = documents.extract_docx(data, settings.max_paper_questions)
        elif ext == ".pdf":
            result = documents.extract_pdf(data, settings.max_paper_questions)
        else:
            result = extraction.extract_image(data, stored_type, extraction.get_ocr_backend(), settings.max_paper_questions, settings.ocr_min_confidence)
    upload = PaperUpload(provider_id=br.provider_id, branch_id=branch_id, subject_id=subject_id, file_id=sf.file_id, title=title,
                         answer_key_file_id=key_file.file_id if key_file else None,
                         uploaded_by=p.staff_id, status="VALIDATED" if result.ok else "REJECTED",
                         question_count=len(result.questions), errors=result.errors or None,
                         uploaded_ip=p.ip if p.ip != "unknown" else None)
    db.add(upload)
    if not result.ok:
        db.commit()  # keep the rejected upload and its precise errors for the uploader
        audit.record(db, "paper.upload", "FAILURE", actor=p.staff_id, target_type="upload", target_id=upload.upload_id,
                     provider_id=br.provider_id, branch_id=branch_id, ip=p.ip, details={"errors": len(result.errors)})
        summary = validation_summary(result)
        message = ("The paper was rejected as a whole: more than five questions failed validation. Correct the files and upload them again."
                   if summary["threshold_exceeded"] else
                   "The paper was rejected: the files cannot be imported reliably. Correct the problems below and upload again."
                   if summary["critical"] else
                   f"The paper was rejected: {summary['failed_questions']} question(s) need correcting. Nothing was imported; fix them and upload again.")
        raise api_error_detail(422, "EXTRACTION_FAILED", message, upload_id=str(upload.upload_id), errors=result.errors[:200], summary=summary)

    content_hash = _hash_questions([(q.stem, *q.options.values(), q.correct, q.marks, q.negative) for q in result.questions])
    dup = db.scalar(select(PaperVersion).join(Paper, Paper.paper_id == PaperVersion.paper_id).where(
        Paper.branch_id == branch_id, Paper.subject_id == subject_id, PaperVersion.content_hash == content_hash,
        PaperVersion.status != "WITHDRAWN"))
    if dup:
        upload.status, upload.errors = "REJECTED", [{"row": None, "field": None, "message": "Identical paper already exists."}]
        db.commit()
        raise api_error_detail(409, "DUPLICATE_PAPER", "An identical paper already exists in this branch and subject.",
                               paper_version_id=str(dup.paper_version_id))

    paper = existing or Paper(provider_id=br.provider_id, branch_id=branch_id, subject_id=subject_id, title=title, created_by=p.staff_id)
    if not existing:
        db.add(paper)
        db.flush()
    version_no = (db.scalar(select(func.max(PaperVersion.version_no)).where(PaperVersion.paper_id == paper.paper_id)) or 0) + 1
    total = sum((q.marks for q in result.questions), Decimal(0))
    pv = PaperVersion(paper_id=paper.paper_id, version_no=version_no, status="VALIDATED", created_by=p.staff_id,
                      question_count=len(result.questions), total_marks=total, content_hash=content_hash)
    db.add(pv)
    db.flush()
    for pos, q in enumerate(result.questions, start=1):
        bank = Question(provider_id=br.provider_id, branch_id=branch_id, subject_id=subject_id, owner_staff_id=p.staff_id)
        db.add(bank)
        db.flush()
        bank.topic = (q.topic or None) and q.topic[:200]
        qv = QuestionVersion(question_id=bank.question_id, version_no=1, stem=q.stem, option_a=q.options["A"], option_b=q.options["B"],
                             option_c=q.options["C"], option_d=q.options["D"], correct_option=q.correct, explanation=q.explanation,
                             topic=q.topic, marks_positive=q.marks, marks_negative=q.negative, created_by=p.staff_id)
        db.add(qv)
        db.flush()
        db.add(PaperVersionQuestion(paper_version_id=pv.paper_version_id, position=pos, question_version_id=qv.question_version_id))
    upload.paper_version_id = pv.paper_version_id
    _event(db, pv, "VALIDATED", p.staff_id, None, "VALIDATED")
    db.commit()
    audit.record(db, "paper.upload", "SUCCESS", actor=p.staff_id, target_type="paper_version", target_id=pv.paper_version_id,
                 provider_id=br.provider_id, branch_id=branch_id, ip=p.ip, details={"questions": len(result.questions)})
    return {"upload_id": str(upload.upload_id), "paper_id": str(paper.paper_id), "paper_version_id": str(pv.paper_version_id),
            "version_no": version_no, "status": pv.status, "question_count": pv.question_count, "total_marks": f"{total:.2f}"}


def api_error_detail(status: int, code: str, message: str, **extra):
    from fastapi import HTTPException
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


@router.get("/uploads/{upload_id}")
def get_upload(upload_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    u = db.get(PaperUpload, upload_id)
    if not u:
        raise api_error(404, "NOT_FOUND", "Upload not found.")
    _scope(p, "paper.read", u, db)
    return {"upload_id": str(u.upload_id), "status": u.status, "question_count": u.question_count, "errors": u.errors or [],
            "paper_version_id": str(u.paper_version_id) if u.paper_version_id else None, "created_at": u.created_at.isoformat()}


@router.get("/uploads/{upload_id}/file")
def download_upload(upload_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    u = db.get(PaperUpload, upload_id)
    if not u:
        raise api_error(404, "NOT_FOUND", "Upload not found.")
    _scope(p, "paper.read", u, db)
    sf = db.get(StoredFile, u.file_id)
    try:
        body = get_store().read(sf.storage_key)
    except FileNotFoundError:
        raise api_error(404, "FILE_MISSING", "The stored file is unavailable.")
    audit.record(db, "paper.download", "SUCCESS", actor=p.staff_id, target_type="upload", target_id=upload_id,
                 provider_id=u.provider_id, branch_id=u.branch_id, ip=p.ip)
    ext = next((e for e, (_, t) in UPLOAD_KINDS.items() if t == sf.content_type), ".csv")
    return Response(body, media_type=sf.content_type if sf.content_type in {t for _, t in UPLOAD_KINDS.values()} else "application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="paper{ext}"', "X-Content-Type-Options": "nosniff"})


# ---------------------------------------------------------------- papers

def _paper_out(db: Session, paper: Paper) -> PaperOut:
    lv = _latest(db, paper.paper_id)
    return PaperOut(paper_id=paper.paper_id, scope=paper.scope, provider_id=paper.provider_id, branch_id=paper.branch_id,
                    subject_id=paper.subject_id, title=paper.title, exam_type=paper.exam_type, target_class=paper.target_class,
                    created_by=paper.created_by, latest_version=PaperVersionOut.model_validate(lv) if lv else None)


@router.post("/papers", status_code=201)
def build_paper(body: PaperBuildIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Assemble a paper version from bank questions. Each question contributes its exact latest version by reference, so nothing
    is duplicated and later edits to a question (new versions) never change this paper. The version starts as VALIDATED and goes
    through the normal approve -> publish workflow; earlier versions of the paper are untouched."""
    provider_id, branch_id, existing, versions, content_hash = _check_build(body, p, db)
    paper = existing or Paper(scope=body.scope, provider_id=provider_id, branch_id=branch_id, subject_id=body.subject_id, title=body.title,
                              exam_type=body.exam_type, target_class=body.target_class, created_by=p.staff_id)
    if not existing:
        db.add(paper)
        db.flush()
    version_no = (db.scalar(select(func.max(PaperVersion.version_no)).where(PaperVersion.paper_id == paper.paper_id)) or 0) + 1
    total = sum((Decimal(v.marks_positive) for v in versions), Decimal(0))
    pv = PaperVersion(paper_id=paper.paper_id, version_no=version_no, status="VALIDATED", created_by=p.staff_id,
                      question_count=len(versions), total_marks=total, content_hash=content_hash)
    db.add(pv)
    db.flush()
    for pos, v in enumerate(versions, start=1):
        db.add(PaperVersionQuestion(paper_version_id=pv.paper_version_id, position=pos, question_version_id=v.question_version_id))
    _event(db, pv, "VALIDATED", p.staff_id, None, "VALIDATED", "Built from the question bank")
    db.commit()
    audit.record(db, "paper.build", "SUCCESS", actor=p.staff_id, target_type="paper_version", target_id=pv.paper_version_id,
                 provider_id=provider_id, branch_id=branch_id, ip=p.ip, details={"questions": len(versions), "scope": body.scope})
    return {"paper_id": str(paper.paper_id), "paper_version_id": str(pv.paper_version_id), "version_no": version_no, "status": pv.status,
            "question_count": pv.question_count, "total_marks": f"{total:.2f}"}


@router.post("/papers/check")
def check_paper_build(body: PaperBuildIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Dry run of POST /papers: the same authorisation and validation, nothing is written (used before multi-paper workflows)."""
    _, _, _, versions, _ = _check_build(body, p, db)
    return {"ok": True, "question_count": len(versions), "total_marks": str(sum((Decimal(v.marks_positive) for v in versions), Decimal(0)))}


def _check_build(body: PaperBuildIn, p: Principal, db: Session):
    if body.scope == "GLOBAL":
        _scope_global(p, "paper.create", db)
        _active_subject(db, body.subject_id)
        provider_id = branch_id = None
    else:
        br = _branch(db, body.branch_id)
        provider_id, branch_id = br.provider_id, br.branch_id
        p.require(db, "paper.create", provider_id=provider_id, branch_id=branch_id, subject_id=body.subject_id)
        _check_subject(db, provider_id, body.subject_id)
    existing = None
    if body.paper_id:
        existing = _paper(db, body.paper_id)
        if (existing.scope, existing.provider_id, existing.branch_id, existing.subject_id) != (body.scope, provider_id, branch_id, body.subject_id):
            raise api_error(422, "PAPER_MISMATCH", "Paper belongs to a different scope, branch or subject.")
        _scope(p, "paper.create", existing, db, owner_only_for_teachers=True)

    latest = _latest_versions()
    rows = {qq.question_id: (qq, v) for qq, v in db.execute(
        select(Question, QuestionVersion).join(latest, latest.c.question_id == Question.question_id)
        .join(QuestionVersion, (QuestionVersion.question_id == Question.question_id) & (QuestionVersion.version_no == latest.c.n))
        .where(Question.question_id.in_(body.question_ids))).all()}
    problems = []
    reserved = reserved_question_ids(db, [qq.question_id for qq, _ in rows.values()])
    for pos, qid in enumerate(body.question_ids, start=1):
        hit = rows.get(qid)
        if not hit:
            problems.append({"position": pos, "message": "Question not found."})
            continue
        qq, v = hit
        if body.scope == "GLOBAL":
            usable = qq.scope == "GLOBAL"                 # platform papers use platform questions only
        elif qq.scope == "GLOBAL":
            usable = p.has_perm("question.read")          # any provider may reuse platform questions
        else:                                             # own provider's questions, in branches the caller can read
            usable = qq.provider_id == provider_id and p.can("question.read", provider_id=qq.provider_id, branch_id=qq.branch_id,
                                                             subject_id=qq.subject_id)
        if not usable:
            problems.append({"position": pos, "message": "Question not found."})   # same answer as missing: no cross-provider probing
        elif body.scope == "PROVIDER" and qq.question_id in reserved:
            problems.append({"position": pos, "message": "This platform question belongs to a Global Test and cannot be reused."})
        elif qq.status != "ACTIVE":
            problems.append({"position": pos, "message": "Question is archived."})
        elif qq.subject_id != body.subject_id:
            problems.append({"position": pos, "message": "Question belongs to a different subject."})
        elif Decimal(v.marks_positive) != Decimal(v.marks_positive).to_integral_value() or Decimal(v.marks_negative) != Decimal(v.marks_negative).to_integral_value():
            problems.append({"position": pos, "message": "Student delivery needs whole-number marks; correct this question first."})
    if problems:
        # All or nothing, like uploads: one bad question rejects the whole paper.
        raise api_error_detail(422, "INVALID_QUESTIONS", "The paper was not created: fix the listed questions.", errors=problems[:200])
    versions = [rows[qid][1] for qid in body.question_ids]
    if len(versions) > settings.max_paper_questions:
        raise api_error(422, "TOO_MANY_QUESTIONS", f"A paper may have at most {settings.max_paper_questions} questions.")
    content_hash = _hash_questions([(v.stem, v.option_a, v.option_b, v.option_c, v.option_d, v.correct_option, v.marks_positive, v.marks_negative)
                                    for v in versions])
    dup = db.scalar(select(PaperVersion).join(Paper, Paper.paper_id == PaperVersion.paper_id).where(
        Paper.scope == body.scope, Paper.branch_id == branch_id if branch_id else Paper.branch_id.is_(None),
        Paper.subject_id == body.subject_id, PaperVersion.content_hash == content_hash, PaperVersion.status != "WITHDRAWN"))
    if dup:
        raise api_error_detail(409, "DUPLICATE_PAPER", "An identical paper already exists for this subject.", paper_version_id=str(dup.paper_version_id))
    return provider_id, branch_id, existing, versions, content_hash



@router.get("/papers", response_model=Page[PaperOut])
def paper_catalogue(scope: str = Query(default="PROVIDER", pattern="^(GLOBAL|PROVIDER)$"), provider_id: UUID | None = None,
                    branch_id: UUID | None = None, subject_id: UUID | None = None,
                    status: str | None = Query(default=None, pattern="^(VALIDATED|APPROVED|PUBLISHED|SUPERSEDED|WITHDRAWN)$"),
                    q: str | None = Query(default=None, max_length=100), page: int = 1, page_size: int = 25,
                    p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Platform papers (scope=GLOBAL, readable by all staff with paper.read) or provider papers (platform roles: any provider,
    optionally narrowed; everyone else: one branch they can read, teachers only their subjects)."""
    stmt = select(Paper).where(Paper.scope == scope)
    if scope == "GLOBAL":
        if not p.has_perm("paper.read"):
            p.require(db, "paper.read")
    elif p.is_global and p.has_perm("paper.read") and not branch_id:
        if provider_id:
            stmt = stmt.where(Paper.provider_id == provider_id)
    else:
        if not branch_id:
            raise api_error(422, "BRANCH_REQUIRED", "Choose a branch to list its papers.")
        br = _branch(db, branch_id)
        p.require(db, "paper.read", provider_id=br.provider_id, branch_id=branch_id, subject_id=subject_id)
        stmt = stmt.where(Paper.branch_id == branch_id)
        if not subject_id and not p.can("paper.read", exclude_roles=(Role.TEACHER,), provider_id=br.provider_id, branch_id=branch_id):
            taught = {t.subject_id for t in p.teaching if t.branch_id == branch_id}
            stmt = stmt.where(Paper.subject_id.in_(taught)) if taught else stmt.where(false())
    if subject_id:
        stmt = stmt.where(Paper.subject_id == subject_id)
    if q:
        stmt = stmt.where(Paper.title.ilike(f"%{escape_like(q)}%", escape="\\"))
    if status:
        stmt = stmt.where(Paper.paper_id.in_(select(PaperVersion.paper_id).where(PaperVersion.status == status)))
    res = paginate(db, stmt.order_by(Paper.created_at.desc()), page, page_size)
    res["items"] = [_paper_out(db, x) for x in res["items"]]
    return res


@router.get("/papers/{paper_id}/versions", response_model=list[PaperVersionOut])
def paper_versions(paper_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    paper = _paper(db, paper_id)
    _scope(p, "paper.read", paper, db)
    return db.scalars(select(PaperVersion).where(PaperVersion.paper_id == paper_id).order_by(PaperVersion.version_no.desc())).all()


@router.get("/uploads")
def list_uploads(branch_id: UUID | None = None, status: str | None = Query(default=None, pattern="^(VALIDATED|REJECTED)$"),
                 page: int = 1, page_size: int = 25, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Import history. Uploads are processed synchronously, so an upload is either VALIDATED (it became a paper version, whose own
    status shows the review/publication state) or REJECTED (nothing was imported; the errors say what to fix)."""
    stmt = select(PaperUpload)
    if p.is_global and p.has_perm("paper.read") and not branch_id:
        pass
    else:
        if not branch_id:
            raise api_error(422, "BRANCH_REQUIRED", "Choose a branch to list its uploads.")
        br = _branch(db, branch_id)
        p.require(db, "paper.read", provider_id=br.provider_id, branch_id=branch_id)
        stmt = stmt.where(PaperUpload.branch_id == branch_id)
        if not p.can("paper.read", exclude_roles=(Role.TEACHER,), provider_id=br.provider_id, branch_id=branch_id):
            stmt = stmt.where(PaperUpload.uploaded_by == p.staff_id)     # teachers: their own uploads
    if status:
        stmt = stmt.where(PaperUpload.status == status)
    res = paginate(db, stmt.order_by(PaperUpload.created_at.desc()), page, page_size)
    items = []
    for u in res["items"]:
        pv = db.get(PaperVersion, u.paper_version_id) if u.paper_version_id else None
        items.append({"upload_id": str(u.upload_id), "title": u.title, "branch_id": str(u.branch_id), "subject_id": str(u.subject_id),
                      "status": u.status, "question_count": u.question_count, "errors": (u.errors or [])[:50], "error_count": len(u.errors or []),
                      "has_answer_key": u.answer_key_file_id is not None, "paper_version_id": str(pv.paper_version_id) if pv else None,
                      "paper_version_status": pv.status if pv else None, "version_no": pv.version_no if pv else None,
                      "uploaded_by": str(u.uploaded_by), "created_at": u.created_at.isoformat()})
    res["items"] = items
    return res


@router.get("/branches/{branch_id}/papers", response_model=Page[PaperOut])
def list_papers(branch_id: UUID, subject_id: UUID | None = None, status: str | None = Query(default=None, max_length=20),
                page: int = 1, page_size: int = 25, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    br = _branch(db, branch_id)
    p.require(db, "paper.read", provider_id=br.provider_id, branch_id=branch_id, subject_id=subject_id)
    stmt = select(Paper).where(Paper.branch_id == branch_id)
    if subject_id:
        stmt = stmt.where(Paper.subject_id == subject_id)
    elif not p.can("paper.read", exclude_roles=(Role.TEACHER,), provider_id=br.provider_id, branch_id=branch_id):
        taught = {t.subject_id for t in p.teaching if t.branch_id == branch_id}
        stmt = stmt.where(Paper.subject_id.in_(taught)) if taught else stmt.where(False)
    if status:
        stmt = stmt.where(Paper.paper_id.in_(select(PaperVersion.paper_id).where(PaperVersion.status == status)))
    res = paginate(db, stmt.order_by(Paper.created_at.desc()), page, page_size)
    res["items"] = [_paper_out(db, x) for x in res["items"]]
    return res


@router.get("/paper-versions/{pv_id}", response_model=PaperDetail)
def get_paper_version(pv_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    v, paper = _pv(db, pv_id)
    _scope(p, "paper.read", paper, db)
    qs = db.scalars(select(QuestionVersion).join(PaperVersionQuestion, PaperVersionQuestion.question_version_id == QuestionVersion.question_version_id)
                    .where(PaperVersionQuestion.paper_version_id == pv_id).order_by(PaperVersionQuestion.position)).all()
    hist = [{"event": e.event_type, "from": e.from_status, "to": e.to_status, "actor": str(e.actor_staff_id),
             "reason": e.reason, "at": e.occurred_at.isoformat()}
            for e in db.scalars(select(PaperVersionEvent).where(PaperVersionEvent.paper_version_id == pv_id).order_by(PaperVersionEvent.occurred_at))]
    base = _paper_out(db, paper).model_dump()
    base["latest_version"] = PaperVersionOut.model_validate(v)
    policy = AnswerPolicy(db, p, db.scalars(select(Question).where(Question.question_id.in_({q.question_id for q in qs}))).all())
    return PaperDetail(**base, questions=[policy.version_out(q) for q in qs], history=hist)


@router.post("/paper-versions/{pv_id}/revisions", response_model=PaperVersionOut, status_code=201)
def revise_paper(pv_id: UUID, body: RevisionIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Correct questions of a version (preview -> correct -> approve/publish). Versions are immutable, so a correction creates the NEXT
    version: untouched questions are shared with the source version. A corrected question becomes a new version of the same bank
    question only when that question belongs to this paper's own scope and the caller may manage it; a platform question, another
    branch's question or another teacher's question is never re-versioned from someone else's paper - the correction becomes a NEW
    question owned by this paper's scope, linked to the original (forked_from_question_id), and the original stays exactly as it is.
    The whole resulting paper is validated again; any problem rejects the correction and nothing is created."""
    reason = need_reason(body.reason)
    v, paper = _pv(db, pv_id)
    _scope(p, "paper.create", paper, db, owner_only_for_teachers=True)
    if v.status == "WITHDRAWN":
        raise api_error(409, "INVALID_TRANSITION", "A withdrawn version cannot be revised.")
    rows = db.execute(select(PaperVersionQuestion.position, QuestionVersion).join(
        QuestionVersion, QuestionVersion.question_version_id == PaperVersionQuestion.question_version_id)
        .where(PaperVersionQuestion.paper_version_id == pv_id).order_by(PaperVersionQuestion.position)).all()
    current = {pos: qv for pos, qv in rows}
    edits = {e.position: e for e in body.edits}
    unknown = sorted(set(edits) - set(current))
    if unknown:
        raise api_error_detail(422, "INVALID_POSITION", f"This paper has no question {unknown[0]}.", positions=unknown)

    merged: dict[int, dict] = {}
    changed: set[int] = set()
    for pos, qv in current.items():
        m = {"stem": qv.stem, "option_a": qv.option_a, "option_b": qv.option_b, "option_c": qv.option_c, "option_d": qv.option_d,
             "correct_option": qv.correct_option, "marks_positive": qv.marks_positive, "marks_negative": qv.marks_negative,
             "explanation": qv.explanation, "topic": qv.topic}
        if pos in edits:
            new = {**m, **edits[pos].changes()}
            if any(new[k] != m[k] for k in m):
                changed.add(pos)
            m = new
        merged[pos] = m
    if not changed:
        raise api_error(409, "NO_CHANGE", "These corrections do not change anything.")

    check = extraction.validate_paper(
        [(pos + 1, {"question": m["stem"], "option_a": m["option_a"], "option_b": m["option_b"], "option_c": m["option_c"], "option_d": m["option_d"],
                    "correct": m["correct_option"], "marks": str(m["marks_positive"]), "negative_marks": str(m["marks_negative"]),
                    "explanation": m["explanation"] or "", "topic": m["topic"] or ""}) for pos, m in merged.items()],
        settings.max_paper_questions)
    if not check.ok:
        # error rows are spreadsheet-style (position + 1); report the question position
        errors = [{**e, "row": (e["row"] - 1) if e.get("row") else None} for e in check.errors]
        raise api_error_detail(422, "REVISION_INVALID", "The corrected paper is not valid: nothing was created.", errors=errors[:200])

    content_hash = _hash_questions([(m["stem"], m["option_a"], m["option_b"], m["option_c"], m["option_d"], m["correct_option"],
                                     m["marks_positive"], m["marks_negative"]) for m in merged.values()])
    dup = db.scalar(select(PaperVersion).join(Paper, Paper.paper_id == PaperVersion.paper_id).where(
        Paper.scope == paper.scope, Paper.branch_id == paper.branch_id if paper.branch_id else Paper.branch_id.is_(None),
        Paper.subject_id == paper.subject_id, PaperVersion.content_hash == content_hash, PaperVersion.status != "WITHDRAWN"))
    if dup:
        raise api_error_detail(409, "DUPLICATE_PAPER", "An identical paper already exists in this branch and subject.",
                               paper_version_id=str(dup.paper_version_id))

    version_no = (db.scalar(select(func.max(PaperVersion.version_no)).where(PaperVersion.paper_id == paper.paper_id)) or 0) + 1
    total = sum((m["marks_positive"] for m in merged.values()), Decimal(0))
    nv = PaperVersion(paper_id=paper.paper_id, version_no=version_no, status="VALIDATED", created_by=p.staff_id,
                      question_count=len(merged), total_marks=total, content_hash=content_hash)
    db.add(nv)
    db.flush()
    forked: list[int] = []
    for pos, old in current.items():
        if pos in changed:
            m = merged[pos]
            source = db.get(Question, old.question_id)
            if owns_for_versioning(p, source, paper):
                next_no = (db.scalar(select(func.max(QuestionVersion.version_no)).where(QuestionVersion.question_id == old.question_id)) or 0) + 1
                qv = QuestionVersion(question_id=old.question_id, version_no=next_no, created_by=p.staff_id, **m)
            else:
                copy = Question(scope=paper.scope, provider_id=paper.provider_id, branch_id=paper.branch_id, subject_id=paper.subject_id,
                                owner_staff_id=p.staff_id, forked_from_question_id=source.question_id,
                                **{k: getattr(source, k) for k in CLASSIFICATION})
                db.add(copy)
                db.flush()
                qv = QuestionVersion(question_id=copy.question_id, version_no=1, created_by=p.staff_id, **m)
                forked.append(pos)
            db.add(qv)
            db.flush()
            qvid = qv.question_version_id
        else:
            qvid = old.question_version_id            # shared, never copied: history stays one-to-one with the bank
        db.add(PaperVersionQuestion(paper_version_id=nv.paper_version_id, position=pos, question_version_id=qvid))
    _event(db, nv, "REVISED", p.staff_id, None, "VALIDATED", reason)
    _event(db, v, "REVISION_CREATED", p.staff_id, v.status, v.status, f"version {version_no} created: {reason}")
    db.commit()
    audit.record(db, "paper.revise", "SUCCESS", actor=p.staff_id, target_type="paper_version", target_id=nv.paper_version_id,
                 provider_id=paper.provider_id, branch_id=paper.branch_id, reason=reason, ip=p.ip,
                 details={"from_version": str(pv_id), "positions": sorted(changed), "copied_positions": forked})
    return nv


def owns_for_versioning(p: Principal, q: Question, paper: Paper) -> bool:
    """May a correction made in `paper` become the next version of bank question `q`? Only if `q` belongs to the paper's own scope
    (same platform/provider and branch) and the caller may manage `q` itself (teachers: their own questions)."""
    same_scope = (q.scope, q.provider_id, q.branch_id) == (paper.scope, paper.provider_id, paper.branch_id)
    return same_scope and _can_edit_question(p, q)


def validation_summary(result) -> dict:
    """Counts for the validation report. A paper is imported only when NOTHING failed (all or nothing); `threshold_exceeded`
    (more than five failed questions) and `critical` (file-level or answer-key problems that make the question set unreliable)
    explain why it was rejected as a whole."""
    failed = {e.get("question") for e in result.errors if e.get("question")}
    detected = max(result.detected, len(failed))
    return {"questions_detected": detected, "questions_failed": len(failed), "failed_questions": len(failed),
            "questions_valid": max(detected - len(failed), 0), "threshold_exceeded": len(failed) > 5,
            "critical": any(not e.get("question") for e in result.errors), "error_count": len(result.errors)}


def _transition(db: Session, p: Principal, pv_id: UUID, perm: str, expect: str, to: str, kind: str, reason: str | None):
    v, paper = _pv(db, pv_id)
    _scope(p, perm, paper, db, owner_only_for_teachers=True)
    if v.status != expect:
        raise api_error(409, "INVALID_TRANSITION", f"Paper version is {v.status}; expected {expect}.")
    old = v.status
    v.status = to
    if to == "PUBLISHED":
        v.published_at = utcnow()
        for other in db.scalars(select(PaperVersion).where(PaperVersion.paper_id == v.paper_id, PaperVersion.status == "PUBLISHED",
                                                          PaperVersion.paper_version_id != v.paper_version_id)):
            other.status = "SUPERSEDED"  # already-scheduled exams keep pointing at their exact version
            _event(db, other, "SUPERSEDED", p.staff_id, "PUBLISHED", "SUPERSEDED", "newer version published")
    _event(db, v, kind, p.staff_id, old, to, reason)
    db.commit()
    audit.record(db, f"paper.{kind.lower()}", "SUCCESS", actor=p.staff_id, target_type="paper_version", target_id=pv_id,
                 provider_id=paper.provider_id, branch_id=paper.branch_id, reason=reason, ip=p.ip)
    return v


@router.post("/paper-versions/{pv_id}/approve", response_model=PaperVersionOut)
def approve(pv_id: UUID, body: Note, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return _transition(db, p, pv_id, "paper.approve", "VALIDATED", "APPROVED", "APPROVED", body.note)


@router.post("/paper-versions/{pv_id}/publish", response_model=PaperVersionOut)
def publish(pv_id: UUID, body: Note, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return _transition(db, p, pv_id, "paper.publish", "APPROVED", "PUBLISHED", "PUBLISHED", body.note)


@router.post("/paper-versions/{pv_id}/withdraw", response_model=PaperVersionOut)
def withdraw(pv_id: UUID, body: ReasonIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    reason = need_reason(body.reason)
    v, paper = _pv(db, pv_id)
    if v.status not in {"PUBLISHED", "APPROVED", "VALIDATED"}:
        raise api_error(409, "INVALID_TRANSITION", "Only draft, approved or published versions can be withdrawn.")
    _refuse_if_in_published_test(db, pv_id)
    return _transition(db, p, pv_id, "paper.publish", v.status, "WITHDRAWN", "WITHDRAWN", reason)


def _refuse_if_in_published_test(db: Session, pv_id: UUID) -> None:
    """A paper version that is a section of a PUBLISHED Global Test stays usable: withdraw the test first (its scheduled
    plans then stop), so a published test never points at a withdrawn paper."""
    from institute_common.models.exams import GlobalTest, GlobalTestSection
    names = db.scalars(select(GlobalTest.name).join(GlobalTestSection, GlobalTestSection.test_id == GlobalTest.test_id)
                       .where(GlobalTestSection.paper_version_id == pv_id, GlobalTest.status == "PUBLISHED")).all()
    if names:
        raise api_error_detail(409, "PAPER_IN_GLOBAL_TEST", "This paper version is part of a published Global Test. "
                               "Withdraw the test first.", tests=sorted(names))


@router.post("/paper-versions/{pv_id}/override-status", response_model=PaperVersionOut)
def override_status(pv_id: UUID, body: OverrideIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Super Admin only. Always records actor, reason, before/after in the paper history and audit log."""
    v, paper = _pv(db, pv_id)
    p.require(db, "paper.override_status", provider_id=paper.provider_id, branch_id=paper.branch_id)
    reason = need_reason(body.reason)
    if body.to_status == "WITHDRAWN":
        _refuse_if_in_published_test(db, pv_id)
    old, v.status = v.status, body.to_status
    if body.to_status == "PUBLISHED" and v.published_at is None:
        v.published_at = utcnow()
    _event(db, v, "OVERRIDE", p.staff_id, old, body.to_status, reason)
    db.commit()
    audit.record(db, "paper.override_status", "SUCCESS", actor=p.staff_id, target_type="paper_version", target_id=pv_id,
                 provider_id=paper.provider_id, branch_id=paper.branch_id, reason=reason, ip=p.ip,
                 details={"from": old, "to": body.to_status})
    return v


# ---------------------------------------------------------------- cross-provider sharing

@router.post("/papers/{paper_id}/shares", status_code=201)
def share_paper(paper_id: UUID, body: ShareIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    paper = _paper(db, paper_id)
    # Releasing content to another provider is a management decision, never a teacher's.
    if not p.can("paper.publish", exclude_roles=(Role.TEACHER,), provider_id=paper.provider_id, branch_id=paper.branch_id):
        p.require(db, "paper.share", provider_id=paper.provider_id, branch_id=paper.branch_id)
    if paper.scope == "GLOBAL":
        raise api_error(422, "INVALID_SHARE", "Platform papers are already available to every provider.")
    if body.provider_id == paper.provider_id:
        raise api_error(422, "INVALID_SHARE", "Choose a different provider.")
    if not db.scalar(select(PaperVersion).where(PaperVersion.paper_id == paper_id, PaperVersion.status == "PUBLISHED")):
        raise api_error(409, "NOT_PUBLISHED", "Only papers with a published version can be shared.")
    share = db.scalar(select(PaperShare).where(PaperShare.paper_id == paper_id, PaperShare.shared_with_provider_id == body.provider_id))
    if share:
        share.status = "ACTIVE"
    else:
        db.add(PaperShare(paper_id=paper_id, owner_provider_id=paper.provider_id, shared_with_provider_id=body.provider_id, granted_by=p.staff_id))
    db.commit()
    audit.record(db, "paper.share", "SUCCESS", actor=p.staff_id, target_type="paper", target_id=paper_id,
                 provider_id=paper.provider_id, ip=p.ip, details={"with": str(body.provider_id)})
    return {"message": "Shared."}


@router.delete("/papers/{paper_id}/shares/{provider_id}", status_code=204)
def unshare_paper(paper_id: UUID, provider_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    paper = _paper(db, paper_id)
    if not p.can("paper.publish", exclude_roles=(Role.TEACHER,), provider_id=paper.provider_id, branch_id=paper.branch_id):
        p.require(db, "paper.share", provider_id=paper.provider_id, branch_id=paper.branch_id)
    share = db.scalar(select(PaperShare).where(PaperShare.paper_id == paper_id, PaperShare.shared_with_provider_id == provider_id))
    if share:
        share.status = "REVOKED"  # exams already built from it keep their immutable manifest
        db.commit()
        audit.record(db, "paper.unshare", "SUCCESS", actor=p.staff_id, target_type="paper", target_id=paper_id,
                     provider_id=paper.provider_id, ip=p.ip, details={"with": str(provider_id)})
