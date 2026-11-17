"""Seeds the disposable database of platform_improvements_ui.sh and prints the ids as JSON. Refuses anything but that database."""
import json
import os
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

if "@pi-e2e-pg/" not in os.environ["DATABASE_URL"]:
    raise SystemExit("refusing: disposable database only")

from institute_common import models  # noqa: E402,F401
from institute_common.core.passwords import hash_password  # noqa: E402
from institute_common.database import utcnow  # noqa: E402
from institute_common.models.academics import ProviderSubject, Subject  # noqa: E402
from institute_common.models.org import Branch, Enrollment, Provider  # noqa: E402
from institute_common.models.papers import Paper, PaperVersion, PaperVersionQuestion, Question, QuestionVersion  # noqa: E402
from institute_common.models.staff import Staff, StaffRoleAssignment  # noqa: E402

PW = "Correct-Horse-Battery-9"
with Session(create_engine(os.environ["DATABASE_URL"])) as db:
    apex = Provider(name="Apex Academy", email="apex@example.com")
    core = Provider(name="Core Science Institute", email="core@example.com")
    db.add_all([apex, core])
    db.flush()
    main, north, central = Branch(provider_id=apex.provider_id, name="Apex Main"), Branch(provider_id=apex.provider_id, name="Apex North"), Branch(provider_id=core.provider_id, name="Core Central")
    db.add_all([main, north, central])
    subject = Subject(code="PHYSICS", name="Physics")
    db.add(subject)
    db.flush()
    db.add_all([ProviderSubject(provider_id=p.provider_id, subject_id=subject.subject_id, created_by=uuid4()) for p in (apex, core)])

    def staff(code, role, provider=None, branch=None):
        s = Staff(staff_code=code, email=f"{code.lower()}@example.com", full_name=code, password_hash=hash_password(PW), email_verified_at=utcnow(), status="ACTIVE")
        db.add(s)
        db.flush()
        db.add(StaffRoleAssignment(staff_id=s.staff_id, role=role, provider_id=provider, branch_id=branch))

    staff("STF900001", "SUPER_ADMIN")
    staff("STF900002", "PROVIDER_ADMIN", apex.provider_id)
    staff("STF900003", "BRANCH_ADMIN", apex.provider_id, main.branch_id)
    staff("STF900004", "PROVIDER_ADMIN", core.provider_id)

    versions = {}

    def paper(title, branch, status):
        owner = uuid4()
        x = Paper(scope="PROVIDER", provider_id=branch.provider_id, branch_id=branch.branch_id, subject_id=subject.subject_id, title=title, created_by=owner)
        db.add(x)
        db.flush()
        v = PaperVersion(paper_id=x.paper_id, version_no=1, status=status, question_count=1, total_marks=Decimal("4"), content_hash=uuid4().hex * 2, created_by=owner)
        q = Question(scope="PROVIDER", provider_id=branch.provider_id, branch_id=branch.branch_id, subject_id=subject.subject_id, owner_staff_id=owner)
        db.add_all([v, q])
        db.flush()
        qv = QuestionVersion(question_id=q.question_id, version_no=1, stem=f"Question of {title}", option_a="1", option_b="2", option_c="3", option_d="4",
                             correct_option="A", marks_positive=Decimal("4"), marks_negative=Decimal("1"), created_by=owner)
        db.add(qv)
        db.flush()
        db.add(PaperVersionQuestion(paper_version_id=v.paper_version_id, position=1, question_version_id=qv.question_version_id))
        versions[title] = str(v.paper_version_id)

    paper("Draft Main", main, "VALIDATED")
    paper("Approved Main", main, "APPROVED")
    paper("Published North", north, "PUBLISHED")
    paper("Core Paper", central, "PUBLISHED")
    for i in range(1, 4):
        db.add(Enrollment(enrollment_id=f"ENR{i:05d}", provider_id=apex.provider_id, branch_id=main.branch_id, student_id=f"STU{i:06d}"))
    db.commit()
    print(json.dumps({"password": PW, "versions": versions, "apex": str(apex.provider_id), "core": str(core.provider_id), "main": str(main.branch_id),
                      "north": str(north.branch_id), "central": str(central.branch_id)}))
