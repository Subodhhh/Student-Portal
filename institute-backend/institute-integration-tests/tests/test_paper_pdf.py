"""Paper PDFs: the question paper and, separately, the answer key; one version at a time, and only once approved.
The paper service decides, never the page: the same paper.read scope for both documents, and the answer key also needs the
correct answers by the paper page's own rule (AnswerPolicy). A question paper never holds answers or explanations; both have a structured
header (provider, branch, Global Test, paper, subject, totals) and the subject-wise marking scheme that actually applies."""
import hashlib
import io
import re
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pypdf import PdfReader

from institute_common.core.roles import Role
from institute_common.database import utcnow
from inst_paper.services import paper_pdf
from tests.test_exams import API, ctx, make_paper  # noqa: F401

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def text_of(pdf: bytes) -> str:
    """The PDF's text with whitespace collapsed: the checks are about content, not where the PDF library breaks lines."""
    return " ".join(" ".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages).split())


def _staff(make_staff, login, role, provider=None, branch=None):
    return login(make_staff([(role, provider, branch)])[1])


def paper(db_factory, world, ctx, title, questions, provider="p1", branch="p1b1", scope="PROVIDER", status="PUBLISHED"):
    """A published paper written once (question versions are append-only). questions: [(stem, correct, explanation, +marks, -marks)]."""
    from institute_common.models.papers import Paper, PaperVersion, PaperVersionQuestion, Question, QuestionVersion
    prov, br = (None, None) if scope == "GLOBAL" else (world[provider], world[branch])
    with db_factory() as db:
        x = Paper(scope=scope, provider_id=prov, branch_id=br, subject_id=ctx["phy"], title=title, created_by=uuid4())
        db.add(x)
        db.flush()
        v = PaperVersion(paper_id=x.paper_id, version_no=1, status=status, question_count=len(questions),
                         total_marks=sum(Decimal(q[3]) for q in questions), content_hash=hashlib.sha256(uuid4().bytes).hexdigest(), created_by=uuid4())
        db.add(v)
        db.flush()
        for i, (stem, correct, explanation, plus, minus) in enumerate(questions, start=1):
            qq = Question(scope=scope, provider_id=prov, branch_id=br, subject_id=ctx["phy"], owner_staff_id=uuid4())
            db.add(qq)
            db.flush()
            qv = QuestionVersion(question_id=qq.question_id, version_no=1, stem=stem, option_a="Newton (∑F = ma)", option_b="Joule", option_c="Watt",
                                 option_d="Pascal", correct_option=correct, explanation=explanation, marks_positive=Decimal(plus),
                                 marks_negative=Decimal(minus), created_by=uuid4())
            db.add(qv)
            db.flush()
            db.add(PaperVersionQuestion(paper_version_id=v.paper_version_id, position=i, question_version_id=qv.question_version_id))
        db.commit()
        return v.paper_version_id


MIXED = [("What is ∫ F·dx?", "A", "SECRET EXPLANATION one", "4", "1"), ("Unit of energy?", "B", None, "4", "0"),
         ("Unit of power?", "C", "SECRET EXPLANATION three", "2", "0.5")]


def test_the_question_paper_has_the_header_marking_scheme_and_never_the_answers(client, world, ctx, make_staff, login, db_factory):
    from institute_common.models.staff import AuditLog
    pv = paper(db_factory, world, ctx, "Physics Mock", MIXED)
    r = client.get(f"{API}/paper-versions/{pv}/pdf", headers=_staff(make_staff, login, Role.PROVIDER_ADMIN, world["p1"]))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf" and r.headers["content-disposition"] == 'attachment; filename="Physics-Mock-v1.pdf"'
    assert r.headers["cache-control"] == "no-store"
    text = text_of(r.content)
    for expected in ("Provider Provider p1", "Branch p1-b1", "Paper Physics Mock (version 1)", "Subject Physics", "Total questions 3",
                     "Total marks 10", "Marking scheme", "Physics 3 10 +2 to +4 0 to −1 0", "A range means the marks vary by question", "Instructions",
                     "An unanswered question scores 0.", "1. What is ∫ F·dx?", "(A) Newton (∑F = ma)", "Marks: +4 / −1", "Marks: +4 / 0",
                     "Marks: +2 / −0.5", "Question paper", "Page 1 of 1"):
        assert expected in text, (expected, text)
    assert "Global Test" not in text                                                       # not a Global Test paper: no blank field
    assert "SECRET" not in text and "Answer" not in text and "ANSWER KEY" not in text and "✓" not in text
    assert not re.search(r"−0(?![.\d])", text)                                             # zero deductions read 0, never −0
    assert not UUID_RE.search(text)
    with db_factory() as db:
        assert db.query(AuditLog).filter_by(action="paper.download", outcome="SUCCESS").count() == 1
        assert db.query(AuditLog).filter_by(action="paper.answer_key.download").count() == 0


def test_the_question_paper_data_never_carries_answers():
    """The renderer's input for a question paper has no answer fields at all (routes/papers._pdf_papers with_answers=False)."""
    import inspect
    from inst_paper.routes import papers
    src = inspect.getsource(papers._pdf_papers)
    body = src.split('"""', 2)[2]                                                           # code only, not the docstring
    assert body.count("correct_option") == 1 and body.count(".explanation") == 1 and "if with_answers:" in body


def test_the_answer_key_is_marked_and_has_answers_and_explanations_where_available(client, world, ctx, make_staff, login, db_factory):
    from institute_common.models.staff import AuditLog
    pv = paper(db_factory, world, ctx, "Physics Mock", MIXED)
    r = client.get(f"{API}/paper-versions/{pv}/answer-key", headers=_staff(make_staff, login, Role.PROVIDER_ADMIN, world["p1"]))
    assert r.status_code == 200, r.text
    assert r.headers["content-disposition"] == 'attachment; filename="Physics-Mock-v1-answer-key.pdf"'
    text = text_of(r.content)
    assert text.startswith("ANSWER KEY") and "ANSWER KEY · Physics Mock · v1 · Answer key" in text   # banner and every page's footer
    for expected in ("Answer: (A)", "Answer: (B)", "Answer: (C)", "Explanation: SECRET EXPLANATION one", "Explanation: SECRET EXPLANATION three",
                     "Marks: +4 / 0", "Marking scheme"):
        assert expected in text, (expected, text)
    assert re.search(r"\(A\) Newton \(∑F = ma\)\s+✓ correct", text) and text.count("✓ correct") == 3
    assert text.count("Explanation:") == 2                                                 # the question without one has none
    assert "Instructions" not in text and not UUID_RE.search(text)
    with db_factory() as db:
        assert db.query(AuditLog).filter_by(action="paper.answer_key.download", outcome="SUCCESS").count() == 1
        assert db.query(AuditLog).filter_by(action="paper.download").count() == 0


def test_scope_is_the_same_for_both_documents(client, world, ctx, make_staff, login, db_factory):
    pv = paper(db_factory, world, ctx, "Physics Mock", MIXED)
    for doc in ("pdf", "answer-key"):
        url = f"{API}/paper-versions/{pv}/{doc}"
        for h in (_staff(make_staff, login, Role.PROVIDER_ADMIN, world["p2"]), _staff(make_staff, login, Role.BRANCH_ADMIN, world["p1"], world["p1b2"]),
                  login(ctx["tchem"])):                    # another provider, another branch, a teacher of another subject
            assert client.get(url, headers=h).status_code == 403, doc
        for h in (login(ctx["tphy"]), _staff(make_staff, login, Role.BRANCH_ADMIN, world["p1"], world["p1b1"]), _staff(make_staff, login, Role.ADMIN)):
            assert client.get(url, headers=h).status_code == 200, doc
        client.cookies.clear()
        assert client.get(url, headers={"Origin": "http://localhost:5174"}).status_code == 401


def _reserved_platform_paper(db_factory, world, ctx):
    """A platform paper that is a section of a published Global Test: its answers stay with the platform until an exam closes."""
    from institute_common.models.exams import GlobalTest, GlobalTestSection
    pv = paper(db_factory, world, ctx, "Platform Physics", MIXED, scope="GLOBAL")
    with db_factory() as db:
        t = GlobalTest(name="National Mock", duration_minutes=60, status="DRAFT", kind="SECTIONS", created_by=uuid4())
        db.add(t)
        db.flush()
        db.add(GlobalTestSection(test_id=t.test_id, position=1, subject_id=ctx["phy"], paper_version_id=pv))
        db.flush()
        t.status = "PUBLISHED"                             # sections first: a published test's sections are frozen
        db.commit()
    return pv


def test_reading_a_paper_does_not_grant_its_answer_key(client, world, ctx, make_staff, login, db_factory):
    from institute_common.models.staff import AuditLog
    pv = _reserved_platform_paper(db_factory, world, ctx)
    pa = _staff(make_staff, login, Role.PROVIDER_ADMIN, world["p1"])
    qp = client.get(f"{API}/paper-versions/{pv}/pdf", headers=pa)
    assert qp.status_code == 200 and "Global Test National Mock" in text_of(qp.content)
    r = client.get(f"{API}/paper-versions/{pv}/answer-key", headers=pa)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "FORBIDDEN"
    assert client.get(f"{API}/paper-versions/{pv}", headers=pa).json()["questions"][0]["answers_hidden"] is True   # the page agrees
    with db_factory() as db:
        denied = db.query(AuditLog).filter_by(action="authz.denied").all()
        assert any((d.details or {}).get("permission") == "paper.answer_key" for d in denied)
        assert db.query(AuditLog).filter_by(action="paper.answer_key.download").count() == 0
    sa = _staff(make_staff, login, Role.SUPER_ADMIN)
    assert client.get(f"{API}/paper-versions/{pv}/answer-key", headers=sa).status_code == 200


def test_global_test_papers_show_their_own_provider_and_the_tests_uniform_marking(client, world, ctx, make_staff, login, db_factory):
    from institute_common.models.exams import GlobalPaperSubmission, GlobalTest, GlobalTestOccurrence
    p1 = paper(db_factory, world, ctx, "Alpha Physics", MIXED)
    p2 = paper(db_factory, world, ctx, "Beta Physics", MIXED, provider="p2", branch="p2b1")
    start = utcnow() + timedelta(days=3)

    def schedule(name, correct, wrong, submissions):
        with db_factory() as db:
            t = GlobalTest(name=name, duration_minutes=60, status="PUBLISHED", kind="SCHEDULE", marking_mode="UNIFORM",
                           marks_correct=correct, marks_wrong=wrong, created_by=uuid4())
            db.add(t)
            db.flush()
            occ = GlobalTestOccurrence(test_id=t.test_id, start_at=start, end_at=start + timedelta(hours=3), status="PUBLISHED", created_by=uuid4())
            db.add(occ)
            db.flush()
            for pv, prov, br in submissions:              # each provider's own paper for the same date
                db.add(GlobalPaperSubmission(occurrence_id=occ.occurrence_id, subject_id=ctx["phy"], provider_id=world[prov], branch_id=world[br],
                                             paper_version_id=pv, audience={}, client_request_id=f"sub-{name}-{pv}", submitted_by=uuid4()))
            db.commit()

    schedule("National Mock", 4, 1, ((p1, "p1", "p1b1"), (p2, "p2", "p2b1")))
    sa = _staff(make_staff, login, Role.SUPER_ADMIN)
    one = text_of(client.get(f"{API}/paper-versions/{p1}/pdf", headers=sa).content)
    two = text_of(client.get(f"{API}/paper-versions/{p2}/pdf", headers=sa).content)
    assert "Provider Provider p1" in one and "Branch p1-b1" in one and "Global Test National Mock" in one
    assert "Provider Provider p2" in two and "Branch p2-b1" in two and "Provider p1" not in two     # never another provider's name
    # the test's uniform marks apply to every question (not the paper's own +2/−0.5 or +4/0)
    assert "Physics 3 12 +4 −1 0" in one and one.count("Marks: +4 / −1") == 3 and "Total marks 12" in one, one
    assert "Marking of National Mock: the same marks for every question." in one
    schedule("State Mock", 3, 0, ((p1, "p1", "p1b1"),))   # a second test with other uniform marks: each question's own marks, noted
    both = text_of(client.get(f"{API}/paper-versions/{p1}/pdf", headers=sa).content)
    assert "Global Test National Mock" in both and "Global Test State Mock" in both
    assert "In National Mock: every question +4 / −1" in both and "In State Mock: every question +3 / 0" in both
    assert "Marks: +2 / −0.5" in both                                                       # each question's own marks otherwise


def test_a_long_paper_paginates_readably():
    q = {"stem": "A particle moves with v = 3t² − 2t m/s; find a at t = 2 s (a = dv/dt). " + "Context. " * 12,
         "options": ["10 m/s²", "√2 m/s²", "π m/s²", "∞"], "marks_positive": Decimal(4), "marks_negative": Decimal(1)}
    doc = {"title": "Full Mock", "version_no": 1, "subject": "Physics", "provider": "Alpha", "branch": "Pune", "global_tests": [],
           "exam_type": None, "target_class": None, "total_questions": 120, "total_marks": Decimal(480),
           "marking": [{"subject": "Physics", "questions": 120, "max_marks": Decimal(480), "correct": (Decimal(4), Decimal(4)),
                        "incorrect": (Decimal(1), Decimal(1))}], "marking_notes": [], "questions": [dict(q) for _ in range(120)]}
    pages = [p.extract_text() for p in PdfReader(io.BytesIO(paper_pdf.render(doc))).pages]
    assert len(pages) > 10 and all(f"Page {i} of {len(pages)}" in t for i, t in enumerate(pages, start=1))
    text = "\n".join(pages)
    assert all(f"{n}. A particle" in text for n in (1, 60, 120)) and "√2" in text and "π" in text and "∞" in text


@pytest.mark.parametrize(("status", "allowed"), [("VALIDATED", False), ("WITHDRAWN", False), ("APPROVED", True), ("PUBLISHED", True), ("SUPERSEDED", True)])
def test_only_approved_papers_can_be_downloaded_even_by_the_api(status, allowed, client, world, ctx, make_staff, login, db_factory):
    pv = paper(db_factory, world, ctx, f"Paper {status}", MIXED, status=status)
    for h in (_staff(make_staff, login, Role.PROVIDER_ADMIN, world["p1"]), _staff(make_staff, login, Role.SUPER_ADMIN)):
        for doc in ("pdf", "answer-key"):
            r = client.get(f"{API}/paper-versions/{pv}/{doc}", headers=h)
            assert r.status_code == (200 if allowed else 403), (status, doc)
            if not allowed:
                assert r.json()["detail"]["code"] == "PAPER_NOT_APPROVED"


def test_zip_downloads_no_longer_exist(client, world, ctx, make_staff, login, db_factory):
    paper(db_factory, world, ctx, "Physics Mock", MIXED)
    h = _staff(make_staff, login, Role.SUPER_ADMIN)
    for path in ("papers/pdf", "papers/answer-keys"):
        assert client.get(f"{API}/{path}", params={"provider_id": str(world["p1"])}, headers=h).status_code in (404, 405)


def test_all_branches_lists_every_readable_branch_and_nothing_else(client, world, ctx, make_staff, login, db_factory):
    paper(db_factory, world, ctx, "Branch one", MIXED)
    paper(db_factory, world, ctx, "Branch two", MIXED, branch="p1b2")
    paper(db_factory, world, ctx, "Branch of another provider", MIXED, provider="p2", branch="p2b1")

    def titles(h, **params):
        r = client.get(f"{API}/papers", params={"provider_id": str(world["p1"]), "q": "Branch", **params}, headers=h)       # not the fixture's own papers
        assert r.status_code == 200, r.text
        return sorted(x["title"] for x in r.json()["items"])

    pa = _staff(make_staff, login, Role.PROVIDER_ADMIN, world["p1"])
    assert titles(pa) == ["Branch one", "Branch two"]                                 # no duplicates, no other provider
    assert titles(pa, q="two") == ["Branch two"]                                      # with the other filters
    assert titles(pa, status="PUBLISHED", subject_id=str(ctx["phy"])) == ["Branch one", "Branch two"]
    assert titles(pa, subject_id=str(uuid4())) == []
    assert titles(pa, branch_id=str(world["p1b1"])) == ["Branch one"]                 # a named branch still wins
    ba = _staff(make_staff, login, Role.BRANCH_ADMIN, world["p1"], world["p1b1"])
    assert titles(ba) == ["Branch one"]                                               # never a sibling branch
    assert client.get(f"{API}/papers", params={"branch_id": str(world["p1b2"])}, headers=ba).status_code == 403
    assert titles(login(ctx["tphy"])) == ["Branch one"]                               # teaches this subject in that branch only
    assert titles(login(ctx["tchem"])) == []                                          # teaches another subject
    outsider = _staff(make_staff, login, Role.PROVIDER_ADMIN, world["p2"])
    assert client.get(f"{API}/papers", params={"provider_id": str(world["p1"])}, headers=outsider).status_code == 403
