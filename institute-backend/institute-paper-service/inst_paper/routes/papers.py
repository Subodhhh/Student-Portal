"""Question bank and the paper workflow: upload -> extraction -> validation -> approval ->
publication, with immutable versions and a full status history."""
import json
import hashlib
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import and_, distinct, false, func, or_, select, true
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
    IMAGE_FIELD_COLUMNS, Question, QuestionImageAsset, QuestionVersion, QuestionVersionAsset, StoredFile,
)
from institute_common.objectstore import ObjectStoreError, store_named
from institute_common.util import escape_like, need_reason, paginate
from institute_common.util import Strict
from institute_common.schemas.common import Page
from institute_common.core import question_types as qtypes
from inst_paper.services import bank_access, extraction, image_assets, isolation, paper_pdf, recognition, taxonomy
from inst_paper.services.math import domain_of_subject_id, markers, recognise
from inst_paper.services.bulk_validation import version_hash, version_key
from inst_paper.services.extraction import same_text_key
from inst_paper.services.filestore import get_store

router = APIRouter(prefix="/api/v1", tags=["Questions & Papers"])

ALLOWED_TYPES = {"text/csv", "text/plain", "application/csv", "application/vnd.ms-excel"}
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
# extension -> (accepted declared content types, stored content type). The content itself is always verified (magic bytes), never the name.
UPLOAD_KINDS = {
    ".csv": (ALLOWED_TYPES, "text/csv"),
    ".xlsx": ({XLSX_TYPE, "application/octet-stream"}, XLSX_TYPE),
    ".zip": ({"application/zip", "application/x-zip-compressed", "application/octet-stream"}, "application/zip"),
    ".docx": ({DOCX_TYPE, "application/octet-stream"}, DOCX_TYPE),
    ".pdf": ({"application/pdf", "application/octet-stream"}, "application/pdf"),
}
# Whole-page picture papers were read by OCR until 2026-11-16 (discontinued); such stored uploads can still be downloaded.
STORED_KINDS = {**UPLOAD_KINDS, ".png": ({"image/png"}, "image/png"), ".jpg": ({"image/jpeg"}, "image/jpeg")}


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


# A question's own marks are optional: marks belong to the test (RULES / UNIFORM marking). When given they are exact decimals with at
# most 2 places, the precision of question_versions.marks_* (NUMERIC(6,2)).
OptionalMarks = Annotated[Decimal | None, Field(lt=10000, decimal_places=2, allow_inf_nan=False)]


def fill_marks_pair(body) -> None:
    """A question has both marks or neither (the database checks the same rule). Giving only the positive marks keeps the earlier
    API behaviour: no negative marking (0)."""
    if body.marks_positive is None and body.marks_negative is not None:
        raise ValueError("marks_negative needs marks_positive (or leave both empty: the test decides the marks)")
    if body.marks_positive is not None and body.marks_negative is None:
        body.marks_negative = Decimal(0)


def _mark_text(value: Decimal | None) -> str:
    """A mark as the validator reads it ("" = no marks of its own)."""
    return "" if value is None else str(value)


def paper_total(positive_marks: list[Decimal | None]) -> Decimal | None:
    """A paper's maximum marks from its questions' own positive marks; None when any question has no marks (the test decides them)."""
    if any(marks is None for marks in positive_marks):
        return None
    return sum((Decimal(marks) for marks in positive_marks), Decimal(0))


class TypedQuestion(Strict):
    """The type of a question and, for MSQ / NAT, its typed fields (institute_common.core.question_types: one rulebook for every
    path). MCQ (the default; every body sent before MSQ/NAT existed): the four options and the correct letter, exactly as before.
    MSQ / NAT: `content` (MSQ options / NAT number format) and `answer_key`; the four options and the letter stay empty."""
    question_type: Literal["MCQ_SINGLE", "MCQ_MULTI", "NUMERICAL"] = "MCQ_SINGLE"
    content: dict | None = None
    answer_key: dict | None = None

    def typed_rule(self, *, option_min: int):
        if self.question_type == qtypes.MCQ:
            if self.content is not None or self.answer_key is not None:
                raise ValueError("an MCQ has options and a correct option, not content or answer_key")
            if self.correct_option is None:
                raise ValueError("an MCQ needs its correct option (A-D)")
            if any(len(getattr(self, f"option_{x}")) < option_min for x in "abcd"):
                raise ValueError("an MCQ needs its four options")
            return self
        if self.correct_option is not None or any(getattr(self, f"option_{x}") for x in "abcd"):
            raise ValueError("MSQ and NAT questions keep their options and key in content / answer_key, not option_a-d / correct_option")
        self.content, self.answer_key = qtypes.validate(self.question_type, self.content, self.answer_key)
        if self.question_type == qtypes.MSQ and self.marks_negative not in (None, 0):
            raise ValueError("MSQ questions are scored all-or-nothing and never lose marks: leave negative marks at 0")
        return self


class QuestionIn(Classification, TypedQuestion):
    scope: Literal["PROVIDER", "GLOBAL"] = "PROVIDER"
    # PROVIDER: branch_id (a branch question) or provider_id alone (provider-wide, Provider Admin); GLOBAL: neither (platform catalogue)
    branch_id: UUID | None = None
    provider_id: UUID | None = None
    subject_id: UUID
    stem: str = Field(min_length=3, max_length=5000)
    option_a: str = Field(default="", max_length=5000)      # MCQ: required (typed_rule); MSQ / NAT: empty
    option_b: str = Field(default="", max_length=5000)
    option_c: str = Field(default="", max_length=5000)
    option_d: str = Field(default="", max_length=5000)
    correct_option: str | None = Field(default=None, pattern="^[ABCD]$")
    marks_positive: OptionalMarks = Field(default=None, gt=0)
    marks_negative: OptionalMarks = Field(default=None, ge=0)
    explanation: str | None = Field(default=None, max_length=5000)
    topic: TopicLabel = None

    @model_validator(mode="after")
    def marks_rule(self):
        fill_marks_pair(self)
        return self

    @model_validator(mode="after")
    def type_rule(self):
        return self.typed_rule(option_min=1)

    @model_validator(mode="after")
    def scope_rule(self):
        if self.scope == "PROVIDER" and not (self.branch_id or self.provider_id):
            raise ValueError("a provider question needs branch_id (branch) or provider_id (provider-wide)")
        if self.scope == "PROVIDER" and self.branch_id and self.provider_id:
            raise ValueError("give branch_id for a branch question or provider_id for a provider-wide question, not both")
        if self.scope == "GLOBAL" and (self.branch_id or self.provider_id):
            raise ValueError("a global question has no provider or branch")
        return self


class QuestionPatch(Classification):
    """Re-classify or archive a question. Content changes are new versions (POST /questions/{id}/versions)."""
    pattern: str | None = Field(default=None, pattern=f"^({'|'.join(PATTERNS)})$")
    topic: TopicLabel = None
    status: str | None = Field(default=None, pattern="^(ACTIVE|ARCHIVED)$")


class QuestionVersionIn(TypedQuestion):
    # a text may be empty only where the question keeps a picture for that field (checked by _picture_rule)
    stem: str = Field(max_length=5000)
    option_a: str = Field(default="", max_length=5000)
    option_b: str = Field(default="", max_length=5000)
    option_c: str = Field(default="", max_length=5000)
    option_d: str = Field(default="", max_length=5000)
    correct_option: str | None = Field(default=None, pattern="^[ABCD]$")
    marks_positive: OptionalMarks = Field(default=None, gt=0)
    marks_negative: OptionalMarks = Field(default=None, ge=0)
    explanation: str | None = Field(default=None, max_length=5000)
    topic: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def marks_rule(self):
        fill_marks_pair(self)
        return self

    @model_validator(mode="after")
    def type_rule(self):
        return self.typed_rule(option_min=0)        # an option text may be empty where the question keeps its picture


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
    correct_option: str | None          # None when withheld (answers_hidden), and for MSQ / NAT (their key: answer_key)
    explanation: str | None
    answers_hidden: bool = False
    question_type: str = "MCQ_SINGLE"
    content: dict | None = None         # MSQ options / NAT number format
    answer_key: dict | None = None      # MSQ correct set / NAT value + tolerance; None when withheld (answers_hidden)
    topic: str | None
    marks_positive: Decimal | None      # None: the question has no marks of its own (the test decides them)
    marks_negative: Decimal | None
    # pictures (GET /question-images/{id}); None: the field has no picture
    stem_image_id: UUID | None = None
    option_a_image_id: UUID | None = None
    option_b_image_id: UUID | None = None
    option_c_image_id: UUID | None = None
    option_d_image_id: UUID | None = None


class BankQuestionOut(BaseModel):
    """A bank question with its latest content version. Staff-facing only: the Student services never call this API."""
    question_id: UUID
    scope: str
    provider_id: UUID | None
    branch_id: UUID | None
    # the name of the branch that created a branch question (shown to everyone who may see the question: its provider's staff and
    # platform roles); None for platform and provider-wide questions
    origin_branch_name: str | None = None
    subject_id: UUID
    status: str
    exam_type: str | None
    target_class: str | None
    chapter: str | None
    topic: str | None
    subtopic: str | None
    # canonical taxonomy records the labels are linked to (None: the label matches no record yet)
    chapter_id: UUID | None = None
    topic_id: UUID | None = None
    subtopic_id: UUID | None = None
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
    total_marks: Decimal | None         # None: some question has no marks of its own
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


class RecognitionPreviewIn(Strict):
    """One field as a reviewer is typing it: recognised exactly as an import or a correction would recognise it; nothing is stored."""
    text: str = Field(min_length=1, max_length=5000)
    field: Literal["question", "option_a", "option_b", "option_c", "option_d", "explanation"]


def _recognition_preview(text: str, field: str, domain: str | None) -> dict:
    rec = recognition.field_result(text, domain, field)
    return {"original": rec["original"], "text": rec["text"], "outcome": rec["outcome"], "reasons": rec["reasons"],
            "spans": [{k: x[k] for k in ("original", "status", "code", "reason", "start", "end", "explanation")} for x in rec["spans"]],
            "suggestions": rec["suggestions"], "domain": domain}


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


def _typed_parts(qtype: str | None, content, key) -> tuple:
    """What an MSQ / NAT question adds to a paper's identity (its content and key); nothing for an MCQ, so MCQ paper hashes stay
    exactly as they were."""
    if (qtype or qtypes.MCQ) == qtypes.MCQ:
        return ()
    return (qtype, json.dumps(content, sort_keys=True), json.dumps(key, sort_keys=True))


def _latest(db: Session, paper_id: UUID) -> PaperVersion | None:
    return db.scalar(select(PaperVersion).where(PaperVersion.paper_id == paper_id).order_by(PaperVersion.version_no.desc()).limit(1))


# ---------------------------------------------------------------- question bank

CLASSIFICATION = ("exam_type", "target_class", "chapter", "topic", "subtopic", "difficulty", "pattern", "subtype")


@router.post("/questions", response_model=QuestionVersionOut, status_code=201)
def create_question(body: QuestionIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    content = body.model_dump(include={"stem", "option_a", "option_b", "option_c", "option_d", "correct_option", "marks_positive",
                                       "marks_negative", "explanation", "topic", "question_type", "content", "answer_key"})
    if body.question_type != qtypes.MCQ:
        if any(o.get("image") for o in (body.content or {}).get("options", [])):
            raise api_error(422, "PICTURE_NOT_ALLOWED", "Option pictures come with a bulk import; a question added here has text options.")
        content["schema_version"] = qtypes.SCHEMA_VERSION
    meta = {**body.model_dump(include=set(CLASSIFICATION)), "question_type": body.question_type}
    if body.scope == "GLOBAL":
        _scope_global(p, "question.manage", db)
        _active_subject(db, body.subject_id)
        q = Question(scope="GLOBAL", subject_id=body.subject_id, owner_staff_id=p.staff_id, **meta)
        taxonomy.link_question(db, q)        # the platform list
        provider_id = branch_id = None
    elif body.provider_id:                  # provider-wide: the provider's own Provider Admin only (not platform roles)
        if not bank_access.is_provider_admin(p, body.provider_id):
            bank_access.deny(db, p, "question.manage", body.provider_id, reason="provider_wide_needs_provider_admin")
        _check_subject(db, body.provider_id, body.subject_id)
        q = Question(provider_id=body.provider_id, branch_id=None, subject_id=body.subject_id, owner_staff_id=p.staff_id, **meta)
        taxonomy.link_question(db, q)
        provider_id, branch_id = body.provider_id, None
    else:
        br = _branch(db, body.branch_id)
        p.require(db, "question.manage", provider_id=br.provider_id, branch_id=br.branch_id, subject_id=body.subject_id)
        _check_subject(db, br.provider_id, body.subject_id)
        q = Question(provider_id=br.provider_id, branch_id=br.branch_id, subject_id=body.subject_id, owner_staff_id=p.staff_id, **meta)
        taxonomy.link_question(db, q)        # labels that name a canonical record get linked to it (nothing is created)
        provider_id, branch_id = br.provider_id, br.branch_id
    _refuse_unmatched(db, q)
    db.add(q)
    db.flush()
    v = QuestionVersion(question_id=q.question_id, version_no=1, created_by=p.staff_id, **content)
    db.add(v)
    db.commit()
    audit.record(db, "question.create", "SUCCESS", actor=p.staff_id, target_type="question", target_id=q.question_id,
                 provider_id=provider_id, branch_id=branch_id, ip=p.ip, details={"scope": q.scope, "question_type": q.question_type})
    return v


def _pictures_of(qv: QuestionVersion | None) -> dict:
    """The picture columns of a question version that are set ({column: asset id})."""
    if qv is None:
        return {}
    return {c: getattr(qv, c) for c in IMAGE_FIELD_COLUMNS.values() if getattr(qv, c) is not None}


def _picture_rule(content: dict, pictures: dict) -> None:
    """The template rule for every way a question version is written: the question has its text, its picture or both (a text
    question needs at least 3 characters); each option has its text or its picture, never both and never neither."""
    stem = (content.get("stem") or "").strip()
    if not stem and "stem_image_id" not in pictures:
        raise api_error(422, "QUESTION_EMPTY", "The question needs its text or its picture.")
    if stem and len(stem) < 3 and "stem_image_id" not in pictures:
        raise api_error(422, "QUESTION_TOO_SHORT", "The question text is too short.")
    for k in "abcd":
        text, picture = (content.get(f"option_{k}") or "").strip(), f"option_{k}_image_id" in pictures
        if text and picture:
            raise api_error(422, "OPTION_TEXT_AND_PICTURE", f"Option {k.upper()} has a picture, so leave its text empty.")
        if not text and not picture:
            raise api_error(422, "OPTION_EMPTY", f"Option {k.upper()} needs its text or its picture.")


def _active_subject(db: Session, subject_id: UUID) -> None:
    subj = db.get(Subject, subject_id)
    if not subj or subj.status != "ACTIVE":
        raise api_error(422, "SUBJECT_NOT_AVAILABLE", "The subject is not in the active platform catalogue.")


@router.post("/questions/{question_id}/versions", response_model=QuestionVersionOut, status_code=201)
def new_question_version(question_id: UUID, body: QuestionVersionIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    q = db.get(Question, question_id)
    if not q:
        raise api_error(404, "NOT_FOUND", "Question not found.")
    bank_access.require_manage_question(db, p, q)
    last = db.scalar(select(func.max(QuestionVersion.version_no)).where(QuestionVersion.question_id == question_id)) or 0
    previous = db.scalar(select(QuestionVersion).where(QuestionVersion.question_id == question_id, QuestionVersion.version_no == last))
    if body.question_type != q.question_type:
        raise api_error(409, "TYPE_LOCKED", "A question keeps its type: create a new question for another type.")
    pictures = _pictures_of(previous)                       # a new version keeps the question's pictures
    content = body.model_dump()
    links = {}
    if q.question_type == qtypes.MCQ:
        _picture_rule(content, pictures)
    else:
        content["schema_version"] = qtypes.SCHEMA_VERSION
        # an MSQ option picture can only be one the question already has (no other provider's picture can be pulled in)
        kept = dict(db.execute(select(QuestionVersionAsset.asset_id, QuestionVersionAsset.role)
                               .where(QuestionVersionAsset.question_version_id == previous.question_version_id)).all()) if previous else {}
        for option in (content["content"] or {}).get("options", []):
            if option.get("image"):
                try:
                    asset = UUID(str(option["image"]))
                except ValueError:
                    asset = None
                if asset not in kept:
                    raise api_error(422, "PICTURE_NOT_ALLOWED", f"Option {option['id']} names a picture this question does not have.")
                links[f"option_{option['id'].lower()}"] = asset
                option["image"] = str(asset)
        if not (content["stem"] or "").strip() and "stem_image_id" not in pictures:
            raise api_error(422, "QUESTION_EMPTY", "The question needs its text or its picture.")
    v = QuestionVersion(question_id=question_id, version_no=last + 1, created_by=p.staff_id, **content, **pictures)
    db.add(v)
    db.flush()
    db.add_all([QuestionVersionAsset(question_version_id=v.question_version_id, role=role, asset_id=asset) for role, asset in links.items()])
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


def _usage_counts(db: Session, question_ids: list[UUID]) -> dict[UUID, int]:
    """Paper versions using any version of each question (a question is reused by reference, never duplicated). Counted only for
    the given questions (one page), never the whole bank."""
    if not question_ids:
        return {}
    return dict(db.execute(
        select(QuestionVersion.question_id, func.count(distinct(PaperVersionQuestion.paper_version_id)))
        .join(PaperVersionQuestion, PaperVersionQuestion.question_version_id == QuestionVersion.question_version_id)
        .where(QuestionVersion.question_id.in_(question_ids)).group_by(QuestionVersion.question_id)).all())


def _is_used():
    """SQL condition: some paper version uses a version of the question."""
    return (select(PaperVersionQuestion.paper_version_id)
            .join(QuestionVersion, PaperVersionQuestion.question_version_id == QuestionVersion.question_version_id)
            .where(QuestionVersion.question_id == Question.question_id).exists())


def _question_visibility(p: Principal, db: Session, scope: str, provider_id: UUID | None, branch_id: UUID | None):
    """SQL condition for the questions `p` may read. GLOBAL questions: anyone holding question.read. PROVIDER questions: one
    provider's whole bank (every branch's questions and the provider-wide ones are shared with all its branches; services/bank_access.py):
    `branch_id` only names the branch the caller works in, i.e. its provider. A platform role without provider/branch sees every
    provider; everyone else sees their provider (named, or their only one), teachers only the subjects they teach."""
    conds = []
    if scope in ("GLOBAL", "ALL") and p.has_perm("question.read"):
        conds.append(Question.scope == "GLOBAL")
    if scope in ("PROVIDER", "ALL"):
        if branch_id:
            br = _branch(db, branch_id)
            if provider_id and provider_id != br.provider_id:
                raise api_error(422, "BRANCH_MISMATCH", "The branch does not belong to this provider.")
            provider_id = br.provider_id
        if provider_id:                                    # asked for explicitly: refused (403, audited) when not allowed
            conds.append(bank_access.provider_condition(provider_id, bank_access.require_provider_read(db, p, provider_id)))
        elif p.is_global and p.has_perm("question.read"):
            conds.append(Question.scope == "PROVIDER")
        elif (own := bank_access.single_provider(p)) is not None:
            subjects = bank_access.readable_subjects(p, own)
            if subjects is None or subjects:               # implied: a teacher who teaches nothing simply sees no provider questions
                conds.append(bank_access.provider_condition(own, subjects))
        elif scope == "PROVIDER":
            raise api_error(422, "BRANCH_REQUIRED", "Choose a branch or provider to list its questions.")
    if not conds:
        p.require(db, "question.read")   # nothing visible at all: same answer as any other missing permission
    return or_(*conds)


_can_edit_question = bank_access.can_manage_question


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
            out = out.model_copy(update={"correct_option": None, "explanation": None, "answer_key": None, "answers_hidden": True})
        return out


def _branch_names(db: Session, questions) -> dict:
    ids = {q.branch_id for q in questions if q.branch_id}
    return dict(db.execute(select(Branch.branch_id, Branch.name).where(Branch.branch_id.in_(ids))).all()) if ids else {}


def _bank_out(p: Principal, q: Question, v: QuestionVersion, uses: int, policy: AnswerPolicy, names: dict | None = None) -> BankQuestionOut:
    return BankQuestionOut(**{c: getattr(q, c) for c in ("question_id", "scope", "provider_id", "branch_id", "subject_id", "status", "exam_type",
                                                         "target_class", "chapter", "topic", "subtopic", "chapter_id", "topic_id", "subtopic_id",
                                                         "difficulty", "pattern", "subtype",
                                                         "question_type", "owner_staff_id", "forked_from_question_id", "created_at")},
                           latest=policy.version_out(v), usage_count=uses or 0, can_edit=_can_edit_question(p, q),
                           reserved_for_global_test=q.question_id in policy.reserved,
                           origin_branch_name=(names or {}).get(q.branch_id))


@router.get("/questions", response_model=Page[BankQuestionOut])
def search_questions(
    scope: str = Query(default="ALL", pattern="^(ALL|GLOBAL|PROVIDER)$"),
    provider_id: UUID | None = None, branch_id: UUID | None = None,
    subject_id: list[UUID] | None = Query(default=None, max_length=20),
    exam_type: str | None = Query(default=None, max_length=80), target_class: str | None = Query(default=None, max_length=80),
    chapter: list[str] | None = Query(default=None, max_length=30), topic: str | None = Query(default=None, max_length=80),
    subtopic: str | None = Query(default=None, max_length=80),
    difficulty: list[str] | None = Query(default=None, max_length=3), pattern: str | None = Query(default=None, max_length=30),
    question_type: str | None = Query(default=None, pattern="^(MCQ_SINGLE|MCQ_MULTI|NUMERICAL)$"),
    include_used: bool = True, status: str = Query(default="ACTIVE", pattern="^(ACTIVE|ARCHIVED|ALL)$"),
    q: str | None = Query(default=None, max_length=200), page: int = 1, page_size: int = 25,
    origin: str | None = Query(default=None, pattern="^(PROVIDER_WIDE|BRANCH)$"), origin_branch_id: UUID | None = None,
    p: Principal = Depends(get_principal), db: Session = Depends(get_db),
):
    """The question bank: reusable questions with their latest version, filtered along the taxonomy. Only staff use this API; the
    Student services receive questions solely through exam delivery, and answers there are revealed only at result review.
    `origin` / `origin_branch_id` narrow PROVIDER questions by where they were created (they never widen what is visible)."""
    latest = _latest_versions()
    stmt = (select(Question, QuestionVersion)
            .join(latest, latest.c.question_id == Question.question_id)
            .join(QuestionVersion, (QuestionVersion.question_id == Question.question_id) & (QuestionVersion.version_no == latest.c.n))
            .where(_question_visibility(p, db, scope, provider_id, branch_id)))
    if status != "ALL":
        stmt = stmt.where(Question.status == status)
    if origin == "PROVIDER_WIDE":
        stmt = stmt.where(Question.scope == "PROVIDER", Question.branch_id.is_(None))
    elif origin == "BRANCH":
        stmt = stmt.where(Question.scope == "PROVIDER", Question.branch_id.is_not(None))
    if origin_branch_id:
        stmt = stmt.where(Question.branch_id == origin_branch_id)
    if subject_id:
        stmt = stmt.where(Question.subject_id.in_(subject_id))
    for col, val in ((Question.exam_type, exam_type), (Question.target_class, target_class), (Question.topic, topic),
                     (Question.subtopic, subtopic), (Question.pattern, pattern), (Question.question_type, question_type)):
        if val:
            stmt = stmt.where(col == val)
    if chapter:
        stmt = stmt.where(Question.chapter.in_(chapter))
    if difficulty:
        stmt = stmt.where(Question.difficulty.in_(difficulty))
    if not include_used:
        stmt = stmt.where(~_is_used())
    if q:
        stmt = stmt.where(QuestionVersion.stem.ilike(f"%{escape_like(q)}%", escape="\\"))
    page_size = min(max(page_size, 1), 100)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.execute(stmt.order_by(Question.created_at.desc(), Question.question_id).limit(page_size).offset((max(page, 1) - 1) * page_size)).all()
    uses = _usage_counts(db, [qq.question_id for qq, _ in rows])
    policy = AnswerPolicy(db, p, [qq for qq, _ in rows])
    names = _branch_names(db, [qq for qq, _ in rows])
    return {"items": [_bank_out(p, qq, v, uses.get(qq.question_id, 0), policy, names) for qq, v in rows], "total": total, "page": max(page, 1), "page_size": page_size}


def _question(db: Session, question_id: UUID) -> Question:
    q = db.get(Question, question_id)
    if not q:
        raise api_error(404, "NOT_FOUND", "Question not found.")
    return q


@router.get("/questions/{question_id}")
def get_question(question_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """One question: every content version (immutable) and the papers that use it, limited to papers the caller may read."""
    q = _question(db, question_id)
    bank_access.require_read_question(db, p, q)
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
    return {"question": _bank_out(p, q, versions[0], latest_uses, policy, _branch_names(db, [q])), "versions": [policy.version_out(v) for v in versions],
            "usage": usage, "usage_elsewhere": hidden}


def _refuse_unmatched(db: Session, q: Question) -> None:
    """A typed Chapter / Topic / Subtopic must be one of the records where records exist (TAXONOMY_ALIASES_PLAN §4.2): refused with
    the same "Did you mean …?" choices as Bulk Upload. Nothing is saved."""
    items = taxonomy.unmatched(db, q)
    if items:
        db.rollback()
        names = ", ".join(f"{i['level'].lower()} “{i['value']}”" for i in items)
        raise HTTPException(422, {"code": "TAXONOMY_UNMATCHED", "message": f"Choose the official name for the {names}.", "items": items})


@router.patch("/questions/{question_id}", response_model=BankQuestionOut)
def update_question(question_id: UUID, body: QuestionPatch, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Re-classify or archive/restore. Archiving hides the question from the bank; papers and exams that already use one of its
    versions are unaffected (they reference the exact version)."""
    q = _question(db, question_id)
    bank_access.require_manage_question(db, p, q)
    changes = body.model_dump(exclude_unset=True)
    if "pattern" in changes and changes["pattern"] is None:
        changes.pop("pattern")
    for k, v in changes.items():
        setattr(q, k, v)
    if {"chapter", "topic", "subtopic"} & changes.keys():
        taxonomy.link_question(db, q)        # re-classified: follow the new labels to their canonical records
        _refuse_unmatched(db, q)
    db.commit()
    audit.record(db, "question.archive" if changes.get("status") == "ARCHIVED" else "question.update", "SUCCESS", actor=p.staff_id,
                 target_type="question", target_id=question_id, provider_id=q.provider_id, branch_id=q.branch_id, ip=p.ip,
                 details={"fields": sorted(changes)})
    v = db.scalar(select(QuestionVersion).where(QuestionVersion.question_id == question_id).order_by(QuestionVersion.version_no.desc()).limit(1))
    uses = db.scalar(select(func.count(distinct(PaperVersionQuestion.paper_version_id))).join(
        QuestionVersion, QuestionVersion.question_version_id == PaperVersionQuestion.question_version_id).where(QuestionVersion.question_id == question_id)) or 0
    return _bank_out(p, q, v, uses, AnswerPolicy(db, p, [q]), _branch_names(db, [q]))


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
                 preview: bool = Form(default=False), resolutions: str | None = Form(default=None),
                 review_token: str | None = Form(default=None),
                 p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Upload a paper. `preview=true` returns every extracted question with its scientific-notation recognition (nothing is stored).
    An import stores the paper only when no field needs review, or when `resolutions` (the provider's decision for each flagged
    field, made on the preview identified by `review_token`) resolve every one; otherwise it is rejected with REVIEW_REQUIRED."""
    br = _branch(db, branch_id)
    p.require(db, "paper.upload", provider_id=br.provider_id, branch_id=branch_id, subject_id=subject_id)
    _check_subject(db, br.provider_id, subject_id)
    data, key_data, ext, stored_type = _read_upload(file, answer_key)
    existing = _target_paper(db, p, paper_id, br, branch_id, subject_id)

    try:
        decisions = recognition.parse_resolutions(resolutions)
    except recognition.ResolutionError as exc:
        raise api_error(422, "INVALID_RESOLUTIONS", str(exc))
    subject = db.get(Subject, subject_id)                               # its name is only recorded with the recognition audit
    # the authoritative subject_id, through the explicit configured mapping; never the subject's name, a file's topic or its text
    domain = domain_of_subject_id(subject_id, settings.recognition_subject_categories)
    token = recognition.review_token(data, key_data, domain)
    if decisions and review_token != token:
        raise api_error(409, "REVIEW_STALE", "The file, answer key or subject changed since the preview; preview the paper again.")
    if preview:
        from fastapi.responses import JSONResponse
        # 200: no paper, upload or question is created; only the pictures are stored, so the preview can show them
        return JSONResponse(_preview(data, key_data, ext, stored_type, domain, token, image_saver(db, p, br.provider_id)))

    sf = _store_file(db, p, br, data, file.filename or "upload", stored_type)
    key_file = _store_file(db, p, br, key_data, answer_key.filename or "answer-key", "text/csv") if key_data is not None else None
    return import_reviewed(db, p, br, subject_id, title, existing, sf, key_file, data, key_data, ext, stored_type, decisions, token,
                           domain, subject)[0]


def _read_upload(file: UploadFile, answer_key: UploadFile | None) -> tuple[bytes, bytes | None, str, str]:
    """The question file and the optional answer key, checked (type, size, not empty): (data, key data, extension, stored type)."""
    name_l = (file.filename or "").lower()
    ext = next((e for e in UPLOAD_KINDS if name_l.endswith(e)), None)
    if ext is None or (file.content_type or "").split(";")[0].strip().lower() not in UPLOAD_KINDS[ext][0]:
        raise api_error(415, "UNSUPPORTED_FILE", "Upload a .csv, one-sheet .xlsx, .zip package (CSV + images), .docx or text .pdf file.")
    stored_type = UPLOAD_KINDS[ext][1]
    # a workbook or .zip package carries the question pictures, so it may be larger than a text-only paper
    limit = settings.max_package_upload_bytes if ext in (".xlsx", ".zip") else settings.max_upload_bytes
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise api_error(413, "FILE_TOO_LARGE", f"File exceeds {limit // (1024 * 1024)} MB." if limit >= 1024 * 1024
                        else f"File exceeds {limit // 1024} KB.")
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
    return data, key_data, ext, stored_type


def _target_paper(db: Session, p: Principal, paper_id: UUID | None, br: Branch, branch_id: UUID, subject_id: UUID) -> Paper | None:
    """The paper an upload becomes the next version of, checked against the branch, the subject and the uploader's scope."""
    existing = None
    if paper_id:
        existing = _paper(db, paper_id)
        if (existing.provider_id, existing.branch_id, existing.subject_id) != (br.provider_id, branch_id, subject_id):
            raise api_error(422, "PAPER_MISMATCH", "Paper belongs to a different branch or subject.")
        _scope(p, "paper.upload", existing, db, owner_only_for_teachers=True)
    return existing


def _store_file(db: Session, p: Principal, br: Branch, data: bytes, name: str, content_type: str) -> StoredFile:
    key, sha = get_store().save(data)
    sf = StoredFile(provider_id=br.provider_id, storage_key=key, original_name=name[:255], content_type=content_type,
                    size_bytes=len(data), sha256=sha, uploaded_by=p.staff_id)
    db.add(sf)
    db.flush()
    return sf


def import_reviewed(db: Session, p: Principal, br: Branch, subject_id: UUID, title: str, existing: Paper | None, sf: StoredFile,
                    key_file: StoredFile | None, data: bytes, key_data: bytes | None, ext: str, stored_type: str, decisions: dict,
                    token: str, domain: str | None, subject) -> tuple[dict, PaperUpload]:
    """Extract, apply the reviewer's decisions and store the paper - or keep a REJECTED upload with its reasons and raise. Used by
    the upload (file sent again with its decisions) and by a saved review draft (files already stored)."""
    branch_id = br.branch_id
    mapping = None
    key_errors: list[dict] = []
    if key_data is not None:
        mapping, key_errors = extraction.parse_answer_key(key_data, settings.max_paper_questions)
    result, review = _extract_and_review(data, ext, stored_type, mapping, key_errors, key_data is not None, domain)
    rec_problems = []
    if result.ok:
        questions, review, rec_problems = recognition.apply_resolutions(result.questions, review, decisions, domain)
        if rec_problems:
            result = extraction.ExtractionResult(errors=rec_problems, detected=len(result.questions))
        else:
            result.questions = questions
    upload = PaperUpload(provider_id=br.provider_id, branch_id=branch_id, subject_id=subject_id, file_id=sf.file_id, title=title,
                         answer_key_file_id=key_file.file_id if key_file else None,
                         uploaded_by=p.staff_id, status="VALIDATED" if result.ok else "REJECTED",
                         question_count=len(result.questions), errors=result.errors or None,
                         uploaded_ip=p.ip if p.ip != "unknown" else None)
    if review is not None:
        upload.recognition = recognition.record(review, domain, subject.name if subject else None, token, p.staff_id)
    db.add(upload)
    if rec_problems:
        db.commit()
        audit.record(db, "paper.upload", "FAILURE", actor=p.staff_id, target_type="upload", target_id=upload.upload_id,
                     provider_id=br.provider_id, branch_id=branch_id, ip=p.ip, details={"review_required": len(rec_problems)})
        raise api_error_detail(422, "REVIEW_REQUIRED", f"Nothing was imported: {len(rec_problems)} field(s) with mathematical or scientific "
                               "notation need your review. Upload the file from Question Bank > Import Processing (Validate & preview) to "
                               "decide on each flagged field, then import.",
                               upload_id=str(upload.upload_id), errors=rec_problems[:200], review_token=token,
                               questions=review, recognition_summary=recognition.summary(review))
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

    return persist_validated_paper(db, p, br, title, existing, result, upload), upload


def _extract_and_review(data: bytes, ext: str, stored_type: str, mapping, key_errors: list[dict], has_key: bool, domain: str | None):
    """Read the paper and check the notation of every text field. Files are processed in an isolated process with a timeout and
    memory limit (services/isolation.py); a failure there is an explicit, whole-paper extraction error, never a partial result.
    Pictures are stored and shown exactly as uploaded: nothing reads their contents."""
    if settings.extraction_isolated:
        limits = isolation.Limits(settings.extraction_timeout_seconds, settings.extraction_memory_mb)
        try:
            return isolation.run_isolated(isolation.extract_and_review, data, ext, stored_type, mapping, key_errors, has_key,
                                          settings.max_paper_questions, domain, limits=limits)
        except isolation.IsolationError as exc:
            message = isolation.FAILURE_MESSAGES[exc.kind].format(detail=exc.detail)
            return extraction.ExtractionResult(errors=[{"row": None, "field": None, "message": message,
                                                         "code": f"PROCESSING_{exc.kind.upper()}"}]), None
    result = isolation.extract_upload(data, ext, stored_type, mapping, key_errors, has_key, settings.max_paper_questions)
    return result, (recognition.review_paper(result.questions, domain) if result.ok else None)


def image_saver(db: Session, p: Principal, provider_id: UUID):
    """Stores a preview's pictures for this provider (see image_assets.store_images) and commits them."""
    def save(images) -> dict[str, UUID]:
        stored = image_assets.store_images(db, provider_id, images, p.staff_id, p.ip if p.ip != "unknown" else None)
        db.commit()
        return stored
    return save


def _preview(data: bytes, key_data: bytes | None, ext: str, stored_type: str, domain: str | None, token: str, save_images=None) -> dict:
    """Extraction and recognition only: a preview never creates an upload, a paper or a question. `save_images` stores the paper's
    pictures (so the preview can display them) and returns {sha256: asset_id}; each picture in the preview then carries its asset_id."""
    mapping, key_errors = (None, [])
    if key_data is not None:
        mapping, key_errors = extraction.parse_answer_key(key_data, settings.max_paper_questions)
    result, review = _extract_and_review(data, ext, stored_type, mapping, key_errors, key_data is not None, domain)
    if not result.ok:
        raise api_error_detail(422, "EXTRACTION_FAILED", "The paper cannot be imported: correct the problems below and upload again.",
                               errors=result.errors[:200], summary=validation_summary(result))
    if save_images is not None:
        stored = save_images(img for q in result.questions for img in q.images.values())
        for entry in review:
            for image in entry.get("images", {}).values():
                image["asset_id"] = str(stored[image["sha256"]])
    return {"preview": True, "review_token": token, "engine_version": recognition.ENGINE_VERSION, "domain": domain,
            "summary": recognition.summary(review), "questions": review}


def persist_validated_paper(db: Session, p: Principal, br: Branch, title: str, existing: Paper | None, result, upload: PaperUpload) -> dict:
    """Store a fully validated extraction as a new VALIDATED paper version (or refuse an identical paper). Shared by file uploads and
    accepted photo drafts, so every import path creates papers, questions and history the same way."""
    branch_id, subject_id = br.branch_id, upload.subject_id
    content_hash = _hash_questions([(q.stem, *q.options.values(), q.correct, q.marks, q.negative,
                                     *sorted(f"{f}:{img.sha256}" for f, img in q.images.items())) for q in result.questions])
    stored = image_assets.store_images(db, br.provider_id, (img for q in result.questions for img in q.images.values()),
                                       p.staff_id, p.ip if p.ip != "unknown" else None)
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
        bank.chapter = (q.chapter or None) and q.chapter[:80]
        bank.subtopic = (q.sub_topic or None) and q.sub_topic[:80]
        taxonomy.link_question(db, bank)
        qv = QuestionVersion(question_id=bank.question_id, version_no=1, stem=q.stem, option_a=q.options["A"], option_b=q.options["B"],
                             option_c=q.options["C"], option_d=q.options["D"], correct_option=q.correct, explanation=q.explanation,
                             topic=q.topic, marks_positive=q.marks, marks_negative=q.negative, created_by=p.staff_id,
                             **{IMAGE_FIELD_COLUMNS[f]: stored[img.sha256] for f, img in q.images.items()})
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
            "paper_version_id": str(u.paper_version_id) if u.paper_version_id else None, "created_at": u.created_at.isoformat(),
            "recognition": u.recognition}


@router.get("/uploads/{upload_id}/file")
def download_upload(upload_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    u = db.get(PaperUpload, upload_id)
    if not u:
        raise api_error(404, "NOT_FOUND", "Upload not found.")
    _scope(p, "paper.read", u, db)
    # the source file holds the same questions as the paper, so it follows the same rule (a rejected upload has no version)
    version = db.get(PaperVersion, u.paper_version_id) if u.paper_version_id else None
    _require_downloadable(db, p, version.status if version else None, u)
    sf = db.get(StoredFile, u.file_id)
    try:
        body = get_store().read(sf.storage_key)
    except FileNotFoundError:
        raise api_error(404, "FILE_MISSING", "The stored file is unavailable.")
    audit.record(db, "paper.download", "SUCCESS", actor=p.staff_id, target_type="upload", target_id=upload_id,
                 provider_id=u.provider_id, branch_id=u.branch_id, ip=p.ip)
    ext = next((e for e, (_, t) in STORED_KINDS.items() if t == sf.content_type), ".csv")
    return Response(body, media_type=sf.content_type if sf.content_type in {t for _, t in STORED_KINDS.values()} else "application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="paper{ext}"', "X-Content-Type-Options": "nosniff"})


# ---------------------------------------------------------------- question pictures

IMAGE_HEADERS = {
    "X-Content-Type-Options": "nosniff",                  # the stored MIME type was sniffed from the bytes at upload; never re-sniffed
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cache-Control": "private, max-age=86400, immutable",  # an asset's bytes never change; private: only this signed-in browser
    "Content-Disposition": "inline",
}


def _used_by_platform_question(db: Session, asset_id: UUID) -> bool:
    in_columns = db.scalar(select(QuestionVersion.question_version_id).join(Question, Question.question_id == QuestionVersion.question_id)
                           .where(Question.scope == "GLOBAL", or_(*(getattr(QuestionVersion, c) == asset_id for c in IMAGE_FIELD_COLUMNS.values())))
                           .limit(1)) is not None
    # an MSQ option picture (question_version_assets) of a platform question is platform content too
    return in_columns or db.scalar(select(QuestionVersionAsset.question_version_id)
                                   .join(QuestionVersion, QuestionVersion.question_version_id == QuestionVersionAsset.question_version_id)
                                   .join(Question, Question.question_id == QuestionVersion.question_id)
                                   .where(Question.scope == "GLOBAL", QuestionVersionAsset.asset_id == asset_id).limit(1)) is not None


@router.get("/question-images/{asset_id}")
def question_image(asset_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """A question or option picture: a provider's picture for that provider's staff (all its branches), a platform picture
    (provider_id NULL) for all staff, like the platform questions it belongs to. A provider picture that a PLATFORM question uses
    (a provider paper copied into the platform keeps its pictures) is platform content too. Any other request gets the same 404 as a
    missing picture, so asset ids of other providers cannot be probed."""
    from institute_common.core.security import can_see_provider
    asset = db.get(QuestionImageAsset, asset_id)
    if asset is None or not p.has_perm("paper.read") or not (
            asset.provider_id is None or can_see_provider(p, asset.provider_id) or _used_by_platform_question(db, asset_id)):
        raise api_error(404, "NOT_FOUND", "Picture not found.")
    try:
        if asset.storage != "db":
            # Object-stored picture: a store that can sign (S3/R2) gets a short-lived URL, AFTER the authorisation above; the bucket
            # itself stays private. The redirect is not cached, so an expired URL is never reused.
            url = store_named(asset.storage).signed_url(asset.storage_key, asset.mime_type)
            if url:
                return RedirectResponse(url, status_code=307, headers={"Cache-Control": "private, no-store"})
        body = image_assets.image_bytes(asset)
    except LookupError:
        raise api_error(404, "FILE_MISSING", "The stored picture is unavailable.")
    except ObjectStoreError:
        raise api_error(503, "STORE_UNAVAILABLE", "Pictures are temporarily unavailable. Try again shortly.")
    return Response(body, media_type=asset.mime_type, headers=IMAGE_HEADERS)


# ---------------------------------------------------------------- papers

def _stored_pictures(m: dict) -> dict:
    """A stored question's pictures as the validator's image columns. Stored pictures are compared by asset id (an asset is one
    provider's one picture), so two options showing the same picture are still 'identical options'."""
    from inst_paper.services.question_images import ExtractedImage
    columns = {"stem_image_id": "question_image", "option_a_image_id": "option_a_image", "option_b_image_id": "option_b_image",
               "option_c_image_id": "option_c_image", "option_d_image_id": "option_d_image"}
    return {columns[c]: ExtractedImage(name="stored picture", mime="", sha256=str(m[c]), width=0, height=0, data=b"")
            for c in columns if m.get(c)}


def _paper_out(db: Session, paper: Paper, latest: dict | None = None) -> PaperOut:
    lv = _latest(db, paper.paper_id) if latest is None else latest.get(paper.paper_id)
    return PaperOut(paper_id=paper.paper_id, scope=paper.scope, provider_id=paper.provider_id, branch_id=paper.branch_id,
                    subject_id=paper.subject_id, title=paper.title, exam_type=paper.exam_type, target_class=paper.target_class,
                    created_by=paper.created_by, latest_version=PaperVersionOut.model_validate(lv) if lv else None)


def _paper_page(db: Session, papers: list[Paper]) -> list[PaperOut]:
    """PaperOut for a page of papers, with every latest version read in one statement."""
    latest: dict = {}
    for v in db.scalars(select(PaperVersion).where(PaperVersion.paper_id.in_([x.paper_id for x in papers] or [None])).order_by(PaperVersion.version_no)):
        latest[v.paper_id] = v
    return [_paper_out(db, x, latest) for x in papers]


@router.post("/papers", status_code=201)
def build_paper(body: PaperBuildIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Assemble a paper version from bank questions. Each question contributes its exact latest version by reference, so nothing
    is duplicated and later edits to a question (new versions) never change this paper. The version starts as VALIDATED and goes
    through the normal approve -> publish workflow; earlier versions of the paper are untouched."""
    provider_id, branch_id, existing, versions, content_hash, review = _check_build(body, p, db)
    paper = existing or Paper(scope=body.scope, provider_id=provider_id, branch_id=branch_id, subject_id=body.subject_id, title=body.title,
                              exam_type=body.exam_type, target_class=body.target_class, created_by=p.staff_id)
    if not existing:
        db.add(paper)
        db.flush()
    version_no = (db.scalar(select(func.max(PaperVersion.version_no)).where(PaperVersion.paper_id == paper.paper_id)) or 0) + 1
    total = paper_total([v.marks_positive for v in versions])
    pv = PaperVersion(paper_id=paper.paper_id, version_no=version_no, status="VALIDATED", created_by=p.staff_id,
                      question_count=len(versions), total_marks=total, content_hash=content_hash)
    db.add(pv)
    db.flush()
    for pos, v in enumerate(versions, start=1):
        db.add(PaperVersionQuestion(paper_version_id=pv.paper_version_id, position=pos, question_version_id=v.question_version_id))
    _event(db, pv, "VALIDATED", p.staff_id, None, "VALIDATED", "Built from the question bank")
    db.commit()
    audit.record(db, "paper.build", "SUCCESS", actor=p.staff_id, target_type="paper_version", target_id=pv.paper_version_id,
                 provider_id=provider_id, branch_id=branch_id, ip=p.ip, details={"questions": len(versions), "scope": body.scope,
                                                                                **({"review": review} if review else {})})
    return {"paper_id": str(paper.paper_id), "paper_version_id": str(pv.paper_version_id), "version_no": version_no, "status": pv.status,
            "question_count": pv.question_count, "total_marks": None if total is None else f"{total:.2f}", "review": review}


@router.post("/papers/check")
def check_paper_build(body: PaperBuildIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Dry run of POST /papers: the same authorisation and validation, nothing is written (used before multi-paper workflows).
    `review` lists possible duplicates worth a look (never blocking; see _content_review)."""
    _, _, _, versions, _, review = _check_build(body, p, db)
    total = paper_total([v.marks_positive for v in versions])
    return {"ok": True, "question_count": len(versions), "total_marks": None if total is None else str(total), "review": review}


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
        else:                                             # own provider's questions: shared by all its branches (bank_access)
            usable = qq.provider_id == provider_id and bank_access.can_read_question(p, qq)
        if not usable:
            problems.append({"position": pos, "message": "Question not found."})   # same answer as missing: no cross-provider probing
        elif body.scope == "PROVIDER" and qq.question_id in reserved:
            problems.append({"position": pos, "message": "This platform question belongs to a Global Test and cannot be reused."})
        elif qq.status != "ACTIVE":
            problems.append({"position": pos, "message": "Question is archived."})
        elif qq.subject_id != body.subject_id:
            problems.append({"position": pos, "message": "Question belongs to a different subject."})
        elif v.marks_positive is not None and (Decimal(v.marks_positive) != Decimal(v.marks_positive).to_integral_value()
                                                or Decimal(v.marks_negative) != Decimal(v.marks_negative).to_integral_value()):
            problems.append({"position": pos, "message": "Student delivery needs whole-number marks; correct this question first."})
    if problems:
        # All or nothing, like uploads: one bad question rejects the whole paper.
        raise api_error_detail(422, "INVALID_QUESTIONS", "The paper was not created: fix the listed questions.", errors=problems[:200])
    versions = [rows[qid][1] for qid in body.question_ids]
    if len(versions) > settings.max_paper_questions:
        raise api_error(422, "TOO_MANY_QUESTIONS", f"A paper may have at most {settings.max_paper_questions} questions.")
    duplicates, review = _content_review(db, versions)
    if duplicates:
        raise api_error_detail(422, "INVALID_QUESTIONS", "The paper was not created: fix the listed questions.", errors=duplicates[:200])
    content_hash = _hash_questions([(v.stem, v.option_a, v.option_b, v.option_c, v.option_d, v.correct_option, v.marks_positive, v.marks_negative,
                                     *_typed_parts(v.question_type, v.content, v.answer_key)) for v in versions])
    dup = db.scalar(select(PaperVersion).join(Paper, Paper.paper_id == PaperVersion.paper_id).where(
        Paper.scope == body.scope, Paper.branch_id == branch_id if branch_id else Paper.branch_id.is_(None),
        Paper.subject_id == body.subject_id, PaperVersion.content_hash == content_hash, PaperVersion.status != "WITHDRAWN"))
    if dup:
        raise api_error_detail(409, "DUPLICATE_PAPER", "An identical paper already exists for this subject.", paper_version_id=str(dup.paper_version_id))
    return provider_id, branch_id, existing, versions, content_hash, review


REVIEW_MIN_TEXT = 25    # shorter question texts without a picture ("Find x.") are too generic to flag as possibly the same question


def _content_review(db: Session, versions: list[QuestionVersion]) -> tuple[list[dict], list[dict]]:
    """Duplicates by CONTENT among the chosen questions (different bank questions can hold the same content, e.g. a platform and a
    provider copy). Content = normalised text + picture checksums of the question and each option, in order (the bulk import's
    content_hash), never the text alone:
      * identical content            -> a problem (blocks the build): "Same question as position N.";
      * same question text/picture with the same options in another order, or (text >= REVIEW_MIN_TEXT or a picture) with other
        options -> a review flag (never blocks)."""
    asset_ids = {getattr(v, c) for v in versions for c in IMAGE_FIELD_COLUMNS.values() if getattr(v, c)}
    asset_ids |= {UUID(str(o["image"])) for v in versions for o in (v.content or {}).get("options", []) if o.get("image")}
    checksums = dict(db.execute(select(QuestionImageAsset.asset_id, QuestionImageAsset.checksum)
                                .where(QuestionImageAsset.asset_id.in_(asset_ids))).all()) if asset_ids else {}
    pic = lambda v, field: checksums.get(getattr(v, IMAGE_FIELD_COLUMNS[field]))     # noqa: E731
    first: dict[str, tuple[int, str]] = {}
    by_stem: dict[tuple, list[tuple[int, str, tuple]]] = {}
    problems, review = [], []
    for pos, v in enumerate(versions, start=1):
        qtype = v.question_type or qtypes.MCQ
        h, key = version_hash(v, checksums.get), version_key(v)        # type-aware content identity and the key (D11)
        if h in first:
            if first[h][1] == key:
                problems.append({"position": pos, "message": f"Same question as position {first[h][0]}."})
            else:
                review.append({"positions": [first[h][0], pos], "reason": "Same question with a different answer key."})
            continue
        first[h] = (pos, key)
        if qtype == qtypes.MCQ:
            options = [(getattr(v, f"option_{x}"), pic(v, x.upper())) for x in "abcd"]
        else:
            options = [(o.get("text"), checksums.get(UUID(str(o["image"]))) if o.get("image") else None)
                       for o in (v.content or {}).get("options", [])]
        stem = (qtype, same_text_key(v.stem or ""), pic(v, "question"))
        by_stem.setdefault(stem, []).append((pos, h, tuple(sorted((same_text_key(t or ""), c or "") for t, c in options))))
    for (_qtype, stem_text, stem_pic), group in by_stem.items():
        if len(group) < 2:
            continue
        positions = [pos for pos, _, _ in group]
        if len({opts for _, _, opts in group}) == 1 and any(opts for _, _, opts in group):
            review.append({"positions": positions, "reason": "Same question with its options in a different order."})
        elif stem_pic or len(stem_text) >= REVIEW_MIN_TEXT:
            review.append({"positions": positions, "reason": "Same question text with different options." if group[0][2] else
                           "Same question text with a different answer format or unit."})
    return problems, review



def _live_branches(provider_id: UUID):
    return select(Branch.branch_id).where(Branch.provider_id == provider_id, Branch.deleted_at.is_(None))


def _readable_in_provider(db: Session, p: Principal, provider_id: UUID):
    """The papers of one provider `p` may read, whatever their branch: the list form of `_scope(p, "paper.read", ...)`. A Provider Admin
    reads every branch, a Branch Admin their own branches, a Teacher the subjects they teach in each branch."""
    if p.can("paper.read", exclude_roles=(Role.TEACHER,), provider_id=provider_id):
        return true()
    if not any(a.provider_id == provider_id for a in p.assignments):
        p.require(db, "paper.read", provider_id=provider_id)
    branches = {a.branch_id for a in p.assignments if a.role == Role.BRANCH_ADMIN and a.provider_id == provider_id}
    taught = {(t.branch_id, t.subject_id) for t in p.teaching if t.provider_id == provider_id}
    return or_(Paper.branch_id.in_(branches or [None]), *(and_(Paper.branch_id == b, Paper.subject_id == s) for b, s in taught))


@router.get("/papers", response_model=Page[PaperOut])
def paper_catalogue(scope: str = Query(default="PROVIDER", pattern="^(GLOBAL|PROVIDER)$"), provider_id: UUID | None = None,
                    branch_id: UUID | None = None, subject_id: UUID | None = None,
                    status: str | None = Query(default=None, pattern="^(VALIDATED|APPROVED|PUBLISHED|SUPERSEDED|WITHDRAWN)$"),
                    q: str | None = Query(default=None, max_length=100), page: int = 1, page_size: int = 25,
                    p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Platform papers (scope=GLOBAL, readable by all staff with paper.read) or provider papers (platform roles: any provider,
    optionally narrowed; everyone else: one branch they can read, or with provider_id alone every branch of that provider they can read;
    teachers only their subjects). A provider outside the caller's scope is refused, never silently emptied."""
    stmt = select(Paper).where(Paper.scope == scope)
    if scope == "GLOBAL":
        if not p.has_perm("paper.read"):
            p.require(db, "paper.read")
    elif p.is_global and p.has_perm("paper.read") and not branch_id:
        if provider_id:
            stmt = stmt.where(Paper.provider_id == provider_id, Paper.branch_id.in_(_live_branches(provider_id)))
    elif provider_id and not branch_id:
        stmt = stmt.where(Paper.provider_id == provider_id, Paper.branch_id.in_(_live_branches(provider_id)), _readable_in_provider(db, p, provider_id))
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
    res = paginate(db, stmt.order_by(Paper.created_at.desc(), Paper.paper_id), page, page_size)
    res["items"] = _paper_page(db, res["items"])
    return res


@router.get("/papers/{paper_id}/versions", response_model=list[PaperVersionOut])
def paper_versions(paper_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    paper = _paper(db, paper_id)
    _scope(p, "paper.read", paper, db)
    return db.scalars(select(PaperVersion).where(PaperVersion.paper_id == paper_id).order_by(PaperVersion.version_no.desc())).all()


@router.get("/uploads")
def list_uploads(branch_id: UUID | None = None, provider_id: UUID | None = None,
                 status: str | None = Query(default=None, pattern="^(VALIDATED|REJECTED)$"),
                 page: int = 1, page_size: int = 25, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Import history. Uploads are processed synchronously, so an upload is either VALIDATED (it became a paper version, whose own
    status shows the review/publication state) or REJECTED (nothing was imported; the errors say what to fix). ?provider_id= alone
    ("All branches"): every live branch of the provider the caller reads - a Provider Admin all, a Branch Admin their branches, and
    everyone their own uploads."""
    stmt = select(PaperUpload)
    if p.is_global and p.has_perm("paper.read") and not branch_id:
        if provider_id:
            stmt = stmt.where(PaperUpload.provider_id == provider_id)
    elif provider_id and not branch_id:
        if not any(a.provider_id == provider_id for a in p.assignments):     # the paper list's rule (_readable_in_provider)
            p.require(db, "paper.read", provider_id=provider_id)
        stmt = stmt.where(PaperUpload.provider_id == provider_id, PaperUpload.branch_id.in_(_live_branches(provider_id)))
        if not p.can("paper.read", exclude_roles=(Role.TEACHER, Role.BRANCH_ADMIN), provider_id=provider_id):
            mine = [a.branch_id for a in p.assignments if Role(a.role) == Role.BRANCH_ADMIN and a.provider_id == provider_id]
            stmt = stmt.where(or_(PaperUpload.branch_id.in_(mine or [None]), PaperUpload.uploaded_by == p.staff_id))
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
    res = paginate(db, stmt.order_by(Paper.created_at.desc(), Paper.paper_id), page, page_size)
    res["items"] = _paper_page(db, res["items"])
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


# ---------------------------------------------------------------- PDF downloads: question paper (never the answers) and answer key

DOWNLOADABLE = {"APPROVED", "PUBLISHED", "SUPERSEDED"}     # never a draft or a withdrawn version; superseded versions stay in use by past exams


def _require_downloadable(db: Session, p: Principal, status: str | None, paper) -> None:
    """Paper files leave the platform only once approved: the single check behind every download route (the UI only mirrors it)."""
    if status not in DOWNLOADABLE:
        audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, provider_id=paper.provider_id, branch_id=paper.branch_id,
                     details={"permission": "paper.download", "reason": "not_approved", "status": status})
        raise api_error(403, "PAPER_NOT_APPROVED", "This paper can be downloaded once it has been approved.")


def _versions_questions(db: Session, version_ids) -> list[tuple[UUID, QuestionVersion, UUID]]:
    """(paper_version_id, question version, subject_id) of every question of these versions, in paper order."""
    return list(db.execute(
        select(PaperVersionQuestion.paper_version_id, QuestionVersion, Question.subject_id)
        .join(QuestionVersion, QuestionVersion.question_version_id == PaperVersionQuestion.question_version_id)
        .join(Question, Question.question_id == QuestionVersion.question_id)
        .where(PaperVersionQuestion.paper_version_id.in_(list(version_ids) or [None]))
        .order_by(PaperVersionQuestion.paper_version_id, PaperVersionQuestion.position)).tuples())


def _require_answer_key(db: Session, p: Principal, version_ids, rows) -> None:
    """The answer key needs the paper (checked by the caller) AND the correct answers, by the same rule as the paper's detail page
    (AnswerPolicy: a platform paper reserved for a Global Test keeps its answers from non-platform staff until an exam of theirs using it
    has closed)."""
    hidden = AnswerPolicy(db, p, [qv for _vid, qv, _s in rows]).hidden
    if hidden:
        audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip,
                     details={"permission": "paper.answer_key", "reason": "answers_withheld", "paper_versions": len(set(version_ids))})
        raise api_error(403, "FORBIDDEN", "The answer key is not available to you for this paper.")


def _global_tests(db: Session, p: Principal, version_ids) -> dict:
    """Global Tests each version belongs to: a platform test's own sections, or a provider paper submitted for a date (withdrawn
    submissions excluded). Draft tests only for the platform."""
    from institute_common.models.exams import GlobalPaperSubmission, GlobalTest, GlobalTestOccurrence, GlobalTestSection
    ids = list(version_ids) or [None]
    rows = list(db.execute(select(GlobalTestSection.paper_version_id, GlobalTest).join(GlobalTest, GlobalTest.test_id == GlobalTestSection.test_id)
                           .where(GlobalTestSection.paper_version_id.in_(ids))).tuples())
    rows += list(db.execute(select(GlobalPaperSubmission.paper_version_id, GlobalTest)
                            .join(GlobalTestOccurrence, GlobalTestOccurrence.occurrence_id == GlobalPaperSubmission.occurrence_id)
                            .join(GlobalTest, GlobalTest.test_id == GlobalTestOccurrence.test_id)
                            .where(GlobalPaperSubmission.paper_version_id.in_(ids), GlobalPaperSubmission.status == "ACTIVE")).tuples())
    out: dict = {}
    for vid, test in rows:
        if (test.status != "DRAFT" or p.is_global) and test not in out.setdefault(vid, []):
            out[vid].append(test)
    return {vid: sorted(tests, key=lambda t: t.name) for vid, tests in out.items()}


def _marking(tests) -> tuple[tuple[Decimal, Decimal] | None, list[str]]:
    """The marks that apply: one uniform scheme when every Global Test of the paper uses the same uniform marks; otherwise each
    question's own marks, with a note for each Global Test that applies uniform marks of its own."""
    uniform = [t for t in tests if t.marking_mode == "UNIFORM"]
    schemes = {(t.marking_mode, t.marks_correct, t.marks_wrong) for t in tests}
    if uniform and len(schemes) == 1:
        t = uniform[0]
        return (Decimal(t.marks_correct), Decimal(t.marks_wrong)), [
            f"Marking of {', '.join(x.name for x in tests)}: the same marks for every question."]
    return None, [f"In {t.name}: every question {paper_pdf.plus_marks(t.marks_correct)} / {paper_pdf.minus_marks(t.marks_wrong)} "
                  "(uniform marking of that test)." for t in uniform]


def _subject_marking(name: str, marks: list[tuple]) -> dict:
    """One row of a PDF's marking scheme from the (positive, negative) marks of a subject's questions. When any question has no marks
    of its own, the subject's marks are decided by the test (None: the PDF prints "Set by the test")."""
    if any(positive is None for positive, _negative in marks):
        return {"subject": name, "questions": len(marks), "max_marks": None, "correct": None, "incorrect": None}
    positives, negatives = [x for x, _y in marks], [y for _x, y in marks]
    return {"subject": name, "questions": len(marks), "max_marks": sum(positives), "correct": (min(positives), max(positives)),
            "incorrect": (min(negatives), max(negatives))}


def _pdf_papers(db: Session, p: Principal, items: list[tuple[Paper, PaperVersion]], rows, with_answers: bool) -> list[dict]:
    """What a PDF shows of each (paper, version): names instead of ids, each paper's OWN provider and branch (never the provider that
    used it in a test), its Global Tests, the subject-wise marking scheme and its questions in paper order. Correct answers and
    explanations are read only when with_answers (the answer key); a question paper's data never holds them."""
    from institute_common.models.org import Provider
    providers = dict(db.execute(select(Provider.provider_id, Provider.name).where(Provider.provider_id.in_({x.provider_id for x, _v in items} or {None}))).all())
    branches = dict(db.execute(select(Branch.branch_id, Branch.name).where(Branch.branch_id.in_({x.branch_id for x, _v in items} or {None}))).all())
    subjects = dict(db.execute(select(Subject.subject_id, Subject.name).where(
        Subject.subject_id.in_({x.subject_id for x, _v in items} | {s for _v, _q, s in rows} or {None}))).all())
    tests = _global_tests(db, p, [v.paper_version_id for _x, v in items])
    by_version: dict = {}
    for vid, qv, subject_id in rows:
        by_version.setdefault(vid, []).append((qv, subject_id))
    out = []
    for paper, v in items:
        linked = tests.get(v.paper_version_id, [])
        uniform, notes = _marking(linked)
        questions, per_subject = [], {}
        for qv, subject_id in by_version.get(v.paper_version_id, []):
            pos, neg = uniform or (qv.marks_positive, qv.marks_negative)
            qtype = qv.question_type or qtypes.MCQ
            if qtype == qtypes.MCQ:
                q = {"type": qtype, "stem": qv.stem, "options": [qv.option_a, qv.option_b, qv.option_c, qv.option_d],
                     "letters": "ABCD", "marks_positive": pos, "marks_negative": neg}
            else:
                options = (qv.content or {}).get("options", [])
                q = {"type": qtype, "stem": qv.stem, "options": [o.get("text") or "(picture)" for o in options],
                     "letters": "".join(o["id"] for o in options), "unit": (qv.content or {}).get("unit"),
                     "marks_positive": pos, "marks_negative": Decimal(0) if qtype == qtypes.MSQ and pos is not None else neg}
            if with_answers:
                key = qv.correct_option if qtype == qtypes.MCQ else qv.answer_key
                q.update(answer=qtypes.describe_key(qtype, key), correct=set(key["correct"]) if qtype == qtypes.MSQ else {key} if qtype == qtypes.MCQ else set(),
                         explanation=qv.explanation)
            questions.append(q)
            per_subject.setdefault(subjects.get(subject_id) or subjects.get(paper.subject_id, ""), []).append((pos, neg))
        marking = [_subject_marking(name, m) for name, m in per_subject.items()]
        out.append({"title": paper.title, "version_no": v.version_no, "subject": subjects.get(paper.subject_id, ""),
                    "provider": "Platform paper" if paper.scope == "GLOBAL" else providers.get(paper.provider_id),
                    "branch": None if paper.scope == "GLOBAL" else branches.get(paper.branch_id),
                    "global_tests": [t.name for t in linked], "exam_type": paper.exam_type, "target_class": paper.target_class,
                    "total_questions": len(questions),
                    "total_marks": None if any(m["max_marks"] is None for m in marking) else sum(m["max_marks"] for m in marking),
                    "marking": marking,
                    "marking_notes": notes, "questions": questions})
    return out


def _download(content: bytes, media_type: str, name: str) -> Response:
    return Response(content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


def _one(db: Session, p: Principal, pv_id: UUID, answer_key: bool) -> Response:
    v, paper = _pv(db, pv_id)
    _scope(p, "paper.read", paper, db)
    _require_downloadable(db, p, v.status, paper)
    rows = _versions_questions(db, [pv_id])
    if answer_key:
        _require_answer_key(db, p, [pv_id], rows)
    [doc] = _pdf_papers(db, p, [(paper, v)], rows, with_answers=answer_key)
    audit.record(db, "paper.answer_key.download" if answer_key else "paper.download", "SUCCESS", actor=p.staff_id, target_type="paper_version",
                 target_id=pv_id, provider_id=paper.provider_id, branch_id=paper.branch_id, ip=p.ip, details={"format": "pdf", "papers": 1})
    return _download(paper_pdf.render(doc, answer_key), "application/pdf", paper_pdf.filename(doc, answer_key))


@router.get("/paper-versions/{pv_id}/pdf")
def paper_version_pdf(pv_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """The question paper as a PDF (questions, options, marks, marking scheme and instructions; never the answers), for exactly the
    staff who may open the paper (GET /paper-versions/{id})."""
    return _one(db, p, pv_id, answer_key=False)


@router.get("/paper-versions/{pv_id}/answer-key")
def paper_version_answer_key(pv_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """The answer key as a PDF: the paper's questions with each correct option and explanation. Needs the paper AND its answers
    (see _require_answer_key); refused with 403 otherwise."""
    return _one(db, p, pv_id, answer_key=True)


@router.post("/branches/{branch_id}/subjects/{subject_id}/recognition-preview")
def recognition_preview_for_import(branch_id: UUID, subject_id: UUID, body: RecognitionPreviewIn, p: Principal = Depends(get_principal),
                                   db: Session = Depends(get_db)):
    """Live preview of a correction typed in the import Review: the same recognition and the same subject domain as the import."""
    br = _branch(db, branch_id)
    p.require(db, "paper.upload", provider_id=br.provider_id, branch_id=branch_id, subject_id=subject_id)
    _check_subject(db, br.provider_id, subject_id)
    return _recognition_preview(body.text, body.field, domain_of_subject_id(subject_id, settings.recognition_subject_categories))


@router.post("/paper-versions/{pv_id}/recognition-preview")
def recognition_preview_for_correction(pv_id: UUID, body: RecognitionPreviewIn, p: Principal = Depends(get_principal),
                                       db: Session = Depends(get_db)):
    """Live preview of a correction typed in the Correct dialog: exactly what saving the correction would store (see revise_paper)."""
    _, paper = _pv(db, pv_id)
    _scope(p, "paper.create", paper, db, owner_only_for_teachers=True)
    return _recognition_preview(body.text, body.field, domain_of_subject_id(paper.subject_id, settings.recognition_subject_categories))


@router.post("/paper-versions/{pv_id}/revisions", response_model=PaperVersionOut, status_code=201)
def revise_paper(pv_id: UUID, body: RevisionIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Correct questions of a version (preview -> correct -> approve/publish). Versions are immutable, so a correction creates the NEXT
    version: untouched questions are shared with the source version. A corrected question becomes a new version of the same bank
    question only when that question belongs to this paper's own platform/provider and the caller may change it (owns_for_versioning); a
    platform question, or a question of the provider the caller may only use (another branch's, the provider's, another teacher's) is
    never re-versioned from someone else's paper - the correction becomes a NEW
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
    typed = sorted(pos for pos in edits if (current[pos].question_type or qtypes.MCQ) != qtypes.MCQ)
    if typed:
        raise api_error_detail(422, "TYPED_QUESTION_EDIT", f"Question {typed[0]} is an MSQ or NAT question: correct it in the Question "
                               "Bank (a new version), then build the paper again.", positions=typed)

    merged: dict[int, dict] = {}
    typed_of: dict[int, tuple] = {}          # what an MSQ / NAT position adds to the paper's identity (never edited here)
    changed: set[int] = set()
    for pos, qv in current.items():
        m = {"stem": qv.stem, "option_a": qv.option_a, "option_b": qv.option_b, "option_c": qv.option_c, "option_d": qv.option_d,
             "correct_option": qv.correct_option, "marks_positive": qv.marks_positive, "marks_negative": qv.marks_negative,
             "explanation": qv.explanation, "topic": qv.topic,
             **{c: getattr(qv, c) for c in IMAGE_FIELD_COLUMNS.values()}}      # pictures are kept by a correction
        typed_of[pos] = _typed_parts(qv.question_type, qv.content, qv.answer_key)
        if pos in edits:
            new = {**m, **edits[pos].changes()}
            if new["marks_positive"] is not None and new["marks_negative"] is None:
                new["marks_negative"] = Decimal(0)        # marks given to a question that had none: no negative marking unless set
            if any(new[k] != m[k] for k in m):
                changed.add(pos)
            m = new
        merged[pos] = m
    if not changed:
        raise api_error(409, "NO_CHANGE", "These corrections do not change anything.")

    # a corrected text goes through the same recognition as an edit in the import review: its maths is stored typeset (\( \)),
    # and a text the engine cannot read with certainty is refused with the reason (nothing is created)
    domain = domain_of_subject_id(paper.subject_id, settings.recognition_subject_categories)
    unreadable = []
    for pos in sorted(changed):
        for col, f in (("stem", "question"), ("option_a", "option_a"), ("option_b", "option_b"), ("option_c", "option_c"),
                       ("option_d", "option_d"), ("explanation", "explanation")):
            text = merged[pos][col]
            if text and text != getattr(current[pos], col):
                r = recognise(markers.strip_private(text), subject=domain, formula_field=f.startswith("option_"))
                if r.outcome in recognition.BLOCKING:
                    unreadable.append({"row": pos, "field": f, "message": "Could not be formatted with certainty: "
                                       + " ".join(s.reason or "" for s in r.flags) + " Write it as LaTeX in \\( … \\) or add brackets."})
                merged[pos][col] = r.text
    if unreadable:
        raise api_error_detail(422, "REVISION_INVALID", "The corrected paper is not valid: nothing was created.", errors=unreadable[:200])

    check = extraction.validate_paper(
        [(pos + 1, {"question": m["stem"], "option_a": m["option_a"], "option_b": m["option_b"], "option_c": m["option_c"], "option_d": m["option_d"],
                    "correct": m["correct_option"], "marks": _mark_text(m["marks_positive"]), "negative_marks": _mark_text(m["marks_negative"]),
                    "explanation": m["explanation"] or "", "topic": m["topic"] or ""}) for pos, m in merged.items() if not typed_of[pos]],
        settings.max_paper_questions, {pos + 1: _stored_pictures(m) for pos, m in merged.items() if not typed_of[pos]}, marks_optional=True)
    if not check.ok:
        # error rows are spreadsheet-style (position + 1); report the question position
        errors = [{**e, "row": (e["row"] - 1) if e.get("row") else None} for e in check.errors]
        raise api_error_detail(422, "REVISION_INVALID", "The corrected paper is not valid: nothing was created.", errors=errors[:200])

    content_hash = _hash_questions([(m["stem"], m["option_a"], m["option_b"], m["option_c"], m["option_d"], m["correct_option"],
                                     m["marks_positive"], m["marks_negative"],
                                     *(str(m[c]) for c in IMAGE_FIELD_COLUMNS.values() if m[c]), *typed_of[pos]) for pos, m in merged.items()])
    same = db.scalars(select(PaperVersion).join(Paper, Paper.paper_id == PaperVersion.paper_id).where(
        Paper.scope == paper.scope, Paper.branch_id == paper.branch_id if paper.branch_id else Paper.branch_id.is_(None),
        Paper.subject_id == paper.subject_id, PaperVersion.content_hash == content_hash, PaperVersion.status != "WITHDRAWN")).all()
    # the explanation and topic are not part of a paper's identity (its hash), so a correction of only those is the next version of
    # the SAME paper; it duplicates a version of this paper only when that version's explanations and topics are identical too
    notes = [(m["explanation"] or "", m["topic"] or "") for m in merged.values()]

    def identical(v: PaperVersion) -> bool:
        if v.paper_id != paper.paper_id:
            return True
        rows = db.execute(select(QuestionVersion.explanation, QuestionVersion.topic).join(
            PaperVersionQuestion, PaperVersionQuestion.question_version_id == QuestionVersion.question_version_id)
            .where(PaperVersionQuestion.paper_version_id == v.paper_version_id).order_by(PaperVersionQuestion.position)).all()
        return [(e or "", t or "") for e, t in rows] == notes

    dup = next((v for v in same if identical(v)), None)
    if dup:
        raise api_error_detail(409, "DUPLICATE_PAPER", "An identical paper already exists in this branch and subject.",
                               paper_version_id=str(dup.paper_version_id))

    version_no = (db.scalar(select(func.max(PaperVersion.version_no)).where(PaperVersion.paper_id == paper.paper_id)) or 0) + 1
    total = paper_total([m["marks_positive"] for m in merged.values()])
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
                taxonomy.link_question(db, copy)
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
                 details={"from_version": str(pv_id), "positions": sorted(changed), "copied_positions": forked,
                         # every corrected field: the value before and the value stored (the old version itself is never changed)
                         "fields": [{"position": pos, "field": k, "before": str(getattr(current[pos], k)) if getattr(current[pos], k) is not None else None,
                                     "after": str(merged[pos][k]) if merged[pos][k] is not None else None}
                                    for pos in sorted(changed) for k in merged[pos] if merged[pos][k] != getattr(current[pos], k)]})
    return nv


def owns_for_versioning(p: Principal, q: Question, paper: Paper) -> bool:
    """May a correction made in `paper` become the next version of bank question `q`? Only if `q` belongs to the paper's own platform
    or provider - a provider's questions are shared by all its branches, so the branch does not matter here - and the caller may
    change `q` itself (bank_access.can_manage_question: the Provider Admin, the origin branch's admin, the owning teacher). Anyone
    else's correction becomes a new question linked to the original (revise_paper), which stays exactly as it is."""
    same_owner = (q.scope, q.provider_id) == (paper.scope, paper.provider_id)
    return same_owner and _can_edit_question(p, q)


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
