"""Global Test with paper choice, per student. One test serves every paper combination of an occurrence: tests.sections holds the shared
configuration (each subject with its papers), student_paper_eligibility each student's frozen eligibility, and the attempt service's
attempt_selected_papers the papers an attempt was started with (read here, never written). Other tests have no papers in their
sections and keep their existing behaviour."""
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.paper_eligibility import StudentPaperEligibility


def has_paper_choice(sections) -> bool:
    return bool(sections) and all("papers" in s for s in sections)


def rows(db: Session, test_id, student_id: str) -> list[StudentPaperEligibility]:
    return list(db.scalars(select(StudentPaperEligibility).where(StudentPaperEligibility.test_id == test_id,
                                                                 StudentPaperEligibility.student_id == student_id)))


def snapshot(db: Session, test_id, student_id: str) -> dict[str, UUID] | None:
    """subject -> paper of the student's current attempt (the latest: a retake supersedes its original); None without an attempt."""
    attempt_id = db.execute(text("SELECT attempt_id FROM test_attempts WHERE test_id = :t AND student_id = :s ORDER BY started_at DESC LIMIT 1"),
                            {"t": test_id, "s": student_id}).scalar()
    if attempt_id is None:
        return None
    return {subject: UUID(str(paper_id)) for subject, paper_id in db.execute(
        text("SELECT subject, paper_id FROM attempt_selected_papers WHERE attempt_id = :a"), {"a": attempt_id})}


def subjects(sections, eligibility: list[StudentPaperEligibility]) -> list[dict] | None:
    """Every configured subject in order with the student's status, message and eligible papers; None if the student has no
    eligibility in this test (not part of it)."""
    if not eligibility:
        return None
    by_subject: dict[str, list[StudentPaperEligibility]] = {}
    for r in eligibility:
        by_subject.setdefault(r.subject, []).append(r)
    out = []
    for s in sorted(sections, key=lambda s: s["position"]):
        mine = by_subject.get(s["subject"])
        if not mine:
            continue                        # cannot happen: delivery requires one cell per subject for every student
        ids = {str(r.paper_id) for r in mine if r.paper_id}
        out.append({"position": s["position"], "subject": s["subject"], "status": mine[0].status, "message": mine[0].message,
                    "papers": [p for p in s["papers"] if str(p["paper_id"]) in ids]})
    return out


def student_view(sections, eligibility, snap: dict[str, UUID] | None) -> tuple[list[dict], int | None, int | None] | None:
    """The student's sections (SectionResponse shape) and totals. With an attempt, each subject of its snapshot is a PAPER section
    of that paper. Before it, a SELECTABLE subject has no count yet and the totals are unknown (None) until the attempt fixes them."""
    subs = subjects(sections, eligibility)
    if subs is None:
        return None
    chosen = {str(p) for p in snap.values()} if snap is not None else None
    out, total_q, total_m, known = [], 0, 0, True
    for s in subs:
        papers = [p for p in s["papers"] if chosen is None or str(p["paper_id"]) in chosen]
        status = "PAPER" if chosen is not None and papers else s["status"]
        count = papers[0]["question_count"] if status == "PAPER" and papers else 0
        if status == "PAPER" and papers:
            total_q, total_m = total_q + count, total_m + papers[0]["total_marks"]
        elif status == "SELECTABLE":
            known = False
        out.append({"position": s["position"], "subject": s["subject"], "status": status,
                    "message": s["message"] if status not in ("PAPER", "SELECTABLE") else None,
                    "question_count": count, "papers": papers})
    return out, (total_q if known else None), (total_m if known else None)
