"""Internal APIs for paper sharing and authorized platform-owned paper copies."""
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from institute_common.core import audit
from institute_common.core.security import api_error
from institute_common.database import get_db, utcnow
from institute_common.internal_http import require_internal_key
from institute_common.models.academics import Subject
from institute_common.models.papers import (
    GlobalPaperCopy,
    Paper,
    PaperShare,
    PaperVersion,
    PaperVersionEvent,
    PaperVersionQuestion,
    Question,
    QuestionVersion,
)

router = APIRouter(
    prefix="/internal/v1",
    tags=["Internal"],
    dependencies=[Depends(require_internal_key)],
)


class ShareGrant(BaseModel):
    paper_id: UUID
    owner_provider_id: UUID
    shared_with_provider_id: UUID
    granted_by: UUID


class GlobalPaperCopyIn(BaseModel):
    contribution_id: UUID
    source_paper_version_id: UUID
    expected_provider_id: UUID
    expected_branch_id: UUID
    expected_subject_id: UUID
    copied_by: UUID
    authorization_reference: str = Field(min_length=1, max_length=200)


def _copy_response(record: GlobalPaperCopy, created: bool) -> dict:
    return {
        "created": created,
        "copy_id": str(record.copy_id),
        "contribution_id": str(record.contribution_id),
        "source_paper_version_id": str(record.source_paper_version_id),
        "global_paper_id": str(record.global_paper_id),
        "global_paper_version_id": str(record.global_paper_version_id),
    }


@router.post("/paper-shares")
def grant_share(body: ShareGrant, db: Session = Depends(get_db)):
    """Grant another provider use of a published paper, never content access."""
    paper = db.get(Paper, body.paper_id)
    if not paper or paper.provider_id != body.owner_provider_id:
        raise api_error(404, "NOT_FOUND", "Paper not found for that owner.")
    if body.shared_with_provider_id == paper.provider_id:
        raise api_error(422, "INVALID_SHARE", "Choose a different provider.")
    if not db.scalar(
        select(PaperVersion).where(
            PaperVersion.paper_id == paper.paper_id,
            PaperVersion.status == "PUBLISHED",
        )
    ):
        raise api_error(
            409, "NOT_PUBLISHED",
            "Only papers with a published version can be shared.",
        )

    share = db.scalar(
        select(PaperShare).where(
            PaperShare.paper_id == paper.paper_id,
            PaperShare.shared_with_provider_id == body.shared_with_provider_id,
        )
    )
    created = share is None or share.status != "ACTIVE"
    if share:
        share.status = "ACTIVE"
    else:
        db.add(
            PaperShare(
                paper_id=paper.paper_id,
                owner_provider_id=paper.provider_id,
                shared_with_provider_id=body.shared_with_provider_id,
                granted_by=body.granted_by,
            )
        )
    db.commit()

    if created:
        audit.record(
            db, "paper.share", "SUCCESS", actor=body.granted_by,
            target_type="paper", target_id=paper.paper_id,
            provider_id=paper.provider_id,
            details={
                "with": str(body.shared_with_provider_id),
                "via": "composite-request",
            },
        )
    return {"created": created, "status": "ACTIVE"}


@router.post("/papers/copy-global", status_code=201)
def copy_paper_to_global(body: GlobalPaperCopyIn, db: Session = Depends(get_db)):
    """Deep-copy one exact, authorized provider paper version into GLOBAL scope.

    The exam service must call this only after platform approval of the
    contribution. Source records are never modified. The unique contribution
    key makes successful retries return the same GLOBAL paper and version.
    """
    existing = db.scalar(
        select(GlobalPaperCopy).where(
            GlobalPaperCopy.contribution_id == body.contribution_id
        )
    )
    if existing:
        if existing.source_paper_version_id != body.source_paper_version_id:
            raise api_error(
                409, "IDEMPOTENCY_CONFLICT",
                "This contribution was already copied from a different paper version.",
            )
        return _copy_response(existing, created=False)

    source_version = db.get(PaperVersion, body.source_paper_version_id)
    if not source_version:
        raise api_error(404, "SOURCE_VERSION_NOT_FOUND", "Source paper version not found.")

    source_paper = db.get(Paper, source_version.paper_id)
    if (
        not source_paper
        or source_paper.scope != "PROVIDER"
        or source_paper.provider_id != body.expected_provider_id
        or source_paper.branch_id != body.expected_branch_id
        or source_paper.subject_id != body.expected_subject_id
    ):
        raise api_error(
            409, "SOURCE_SCOPE_MISMATCH",
            "Source paper does not match the approved provider, branch and subject.",
        )

    if source_version.status not in {"APPROVED", "PUBLISHED"}:
        raise api_error(
            409, "SOURCE_VERSION_NOT_APPROVED",
            "Only an approved or published source version can be contributed.",
        )

    subject = db.get(Subject, body.expected_subject_id)
    if not subject or subject.status != "ACTIVE":
        raise api_error(409, "SUBJECT_NOT_AVAILABLE", "The subject is not active.")

    source_items = db.execute(
        select(PaperVersionQuestion, QuestionVersion, Question)
        .join(
            QuestionVersion,
            QuestionVersion.question_version_id
            == PaperVersionQuestion.question_version_id,
        )
        .join(Question, Question.question_id == QuestionVersion.question_id)
        .where(
            PaperVersionQuestion.paper_version_id == source_version.paper_version_id
        )
        .order_by(PaperVersionQuestion.position)
    ).all()

    if not source_items or len(source_items) != source_version.question_count:
        raise api_error(
            409, "SOURCE_CONTENT_INCOMPLETE",
            "The source paper version has missing or inconsistent questions.",
        )

    try:
        global_paper = Paper(
            scope="GLOBAL",
            provider_id=None,
            branch_id=None,
            subject_id=source_paper.subject_id,
            title=(source_paper.title[:230] + " (Global Copy)")[:255],
            exam_type=source_paper.exam_type,
            target_class=source_paper.target_class,
            created_by=body.copied_by,
        )
        db.add(global_paper)
        db.flush()

        copied_question_versions = []
        total_marks = 0
        for item, source_qv, source_question in source_items:
            global_question = Question(
                scope="GLOBAL",
                provider_id=None,
                branch_id=None,
                subject_id=source_question.subject_id,
                owner_staff_id=body.copied_by,
                forked_from_question_id=source_question.question_id,
                status="ACTIVE",
                exam_type=source_question.exam_type,
                target_class=source_question.target_class,
                chapter=source_question.chapter,
                topic=source_question.topic,
                subtopic=source_question.subtopic,
                difficulty=source_question.difficulty,
                pattern=source_question.pattern,
                subtype=source_question.subtype,
                question_type=source_question.question_type,
            )
            db.add(global_question)
            db.flush()

            global_qv = QuestionVersion(
                question_id=global_question.question_id,
                version_no=1,
                stem=source_qv.stem,
                option_a=source_qv.option_a,
                option_b=source_qv.option_b,
                option_c=source_qv.option_c,
                option_d=source_qv.option_d,
                correct_option=source_qv.correct_option,
                explanation=source_qv.explanation,
                topic=source_qv.topic,
                marks_positive=source_qv.marks_positive,
                marks_negative=source_qv.marks_negative,
                created_by=body.copied_by,
            )
            db.add(global_qv)
            db.flush()

            copied_question_versions.append((item.position, global_qv))
            total_marks += global_qv.marks_positive

        global_version = PaperVersion(
            paper_id=global_paper.paper_id,
            version_no=1,
            status="VALIDATED",
            question_count=len(copied_question_versions),
            total_marks=total_marks,
            content_hash=source_version.content_hash,
            created_by=body.copied_by,
        )
        db.add(global_version)
        db.flush()

        for position, global_qv in copied_question_versions:
            db.add(
                PaperVersionQuestion(
                    paper_version_id=global_version.paper_version_id,
                    position=position,
                    question_version_id=global_qv.question_version_id,
                )
            )

        db.add(
            PaperVersionEvent(
                paper_version_id=global_version.paper_version_id,
                event_type="GLOBAL_COPY_CREATED",
                from_status=None,
                to_status="VALIDATED",
                actor_staff_id=body.copied_by,
                reason=f"Authorized contribution {body.contribution_id}",
                occurred_at=utcnow(),
            )
        )

        record = GlobalPaperCopy(
            contribution_id=body.contribution_id,
            source_paper_version_id=source_version.paper_version_id,
            global_paper_id=global_paper.paper_id,
            global_paper_version_id=global_version.paper_version_id,
            authorization_reference=body.authorization_reference,
            copied_by=body.copied_by,
        )
        db.add(record)
        db.flush()
        db.commit()
        db.refresh(record)

    except IntegrityError:
        # A concurrent retry may have won the unique contribution key.
        db.rollback()
        record = db.scalar(
            select(GlobalPaperCopy).where(
                GlobalPaperCopy.contribution_id == body.contribution_id
            )
        )
        if record and record.source_paper_version_id == body.source_paper_version_id:
            return _copy_response(record, created=False)
        raise api_error(
            409, "COPY_CONFLICT",
            "The copy could not be completed because of a conflicting record.",
        )

    audit.record(
        db,
        "paper.global_copy",
        "SUCCESS",
        actor=body.copied_by,
        target_type="paper",
        target_id=global_paper.paper_id,
        details={
            "contribution_id": str(body.contribution_id),
            "source_paper_version_id": str(source_version.paper_version_id),
            "global_paper_version_id": str(global_version.paper_version_id),
            "authorization_reference": body.authorization_reference,
        },
    )
    return _copy_response(record, created=True)
