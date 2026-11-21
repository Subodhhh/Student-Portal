"""Runs REAL Institute code on a dataset in a DISPOSABLE PostgreSQL: public routes through each service's FastAPI app with real principals,
then the exam functions the dataset must satisfy (build_payload, audience resolution, slot resolution, generation replay, worker steps)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
inst_db, stu_db = sys.argv[1], sys.argv[2]
PORT = 55439
os.environ.update(DATABASE_URL=f"postgresql+psycopg://lt@127.0.0.1:{PORT}/{inst_db}", CORS_ORIGINS="http://x", REDIS_URL="redis://127.0.0.1:1/0",
                  REFERENCE_SYNC_INTERVAL_SECONDS="0", AUDIT_FORWARD_INTERVAL_SECONDS="0", SESSION_COOKIE_SECURE="false")
sys.path[:0] = [str(ROOT / "institute-backend/institute-common")] + [str(ROOT / f"institute-backend/institute-{s}-service") for s in
                                                                      ("organization", "academics", "enrollment", "paper", "exam", "operations")]
import psycopg  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from institute_common.context import service_context  # noqa: E402
from institute_common.core import security  # noqa: E402
from institute_common.database import new_session  # noqa: E402
from institute_common.models.exams import ExamPlan  # noqa: E402

stu = psycopg.connect(f"host=127.0.0.1 port={PORT} user=lt dbname={stu_db}", autocommit=True)
ins = psycopg.connect(f"host=127.0.0.1 port={PORT} user=lt dbname={inst_db}", autocommit=True)
results = []


def same(label, got, want):
    results.append((label, got == want, got, want))


def principal(staff_id):
    with service_context("identity"), new_session() as db:
        staff, assignments, teaching = security.load_principal(db, staff_id)
        return security.Principal(staff, assignments, teaching, "sid", "csrf", "127.0.0.1")


def app_of(module):
    return __import__(module, fromlist=["app"]).app


platform = ins.execute("select staff_id from staff_role_assignments where role='SUPER_ADMIN'").fetchone()[0]
branch, provider = ins.execute("""select e.branch_id, e.provider_id from enrollments e join branches b using (branch_id) group by 1, 2 order by count(*) desc limit 1""").fetchone()
admin = ins.execute("select staff_id from staff_role_assignments where role='PROVIDER_ADMIN' and provider_id=%s", (provider,)).fetchone()[0]
paper_version, paper = ins.execute("select v.paper_version_id, p.paper_id from paper_versions v join papers p using (paper_id) where p.provider_id=%s limit 1", (provider,)).fetchone()
exam = ins.execute("select exam_id from exams where status='PUBLISHED' and branch_id=%s limit 1", (branch,)).fetchone()[0]
ROUTES = {
    "inst_organization.main": ["/api/v1/providers?page=1&page_size=20", f"/api/v1/providers/{provider}", f"/api/v1/providers/{provider}/branches?page_size=100", f"/api/v1/branches/{branch}"],
    "inst_academics.main": ["/api/v1/subjects", f"/api/v1/branches/{branch}/batches?page_size=100", f"/api/v1/providers/{provider}/subjects", "/api/v1/teaching"],
    "inst_enrollment.main": [f"/api/v1/branches/{branch}/enrollments?page=1&page_size=25", f"/api/v1/providers/{provider}/transfers?status=PENDING"],
    "inst_paper.main": [f"/api/v1/paper-versions/{paper_version}", f"/api/v1/papers/{paper}/versions", f"/api/v1/papers?branch_id={branch}&page=1&page_size=20", "/api/v1/questions?page=1&page_size=20",
                        f"/api/v1/branches/{branch}/papers?page=1&page_size=20"],
    "inst_exam.main": ["/api/v1/tests?page=1&page_size=25", "/api/v1/exams?status=PUBLISHED&page=1&page_size=20", f"/api/v1/exams/{exam}", f"/api/v1/exams/{exam}/assignments",
                       f"/api/v1/branches/{branch}/exams?page=1&page_size=20", f"/api/v1/branches/{branch}/exam-plans"],
    "inst_operations.main": ["/api/v1/dashboard"],
}
for who, staff_id in (("super admin", platform), ("provider admin", admin)):
    p = principal(staff_id)
    for module, paths in ROUTES.items():
        app = app_of(module)
        app.dependency_overrides[security.get_principal] = lambda p=p: p
        with TestClient(app) as client:
            for path in paths:
                r = client.get(path)
                same(f"{who} GET {path.split('?')[0].replace(str(provider), '{p}').replace(str(branch), '{b}').replace(str(exam), '{e}')}", r.status_code, 200)
                if r.status_code != 200:
                    print(r.text[:300])

from inst_exam.services import assignment_sync, exam_generation as gen, notify, outbox, student_notifications  # noqa: E402

with service_context("exam"), new_session() as db:
    plans = ins.execute("""select plan_id, exam_version_id, exam_id from exams join exam_versions using (exam_id) where status='PUBLISHED'""").fetchall()
    payload_ok = audience_ok = slots_ok = replay_ok = 0
    for plan_id, version, exam_id in plans:
        payload = outbox.build_payload(db, version)
        rows = stu.execute("""select question_order, subject, question_text, option_a, option_b, option_c, option_d, correct_answer, marks, negative_marks
                              from questions where test_id=%s order by question_order""", (version,)).fetchall()
        built = [(q["order"], q["subject"], q["text"], q["options"]["A"], q["options"]["B"], q["options"]["C"], q["options"]["D"], q["correct"], q["marks"], q["negative_marks"]) for q in payload["questions"]]
        delivered = {r[0] for r in stu.execute("select student_id from test_audiences where test_id=%s", (version,)).fetchall()}
        payload_ok += built == rows and set(payload["student_ids"]) == delivered
        plan = db.get(ExamPlan, plan_id)
        audience_ok += {s for s, _e, _b in gen.eligible_students(db, plan)} == delivered
        res = gen.resolve_slots(db, plan)
        slots_ok += not res.missing and len(res.resolved) > 0
        job = gen.generate(db, plan_id)                 # a replay: same plan and window must return the existing job, nothing new
        replay_ok += str(job.exam_id) == str(exam_id)
    same(f"build_payload equals delivered test ({len(plans)} exams)", payload_ok, len(plans))
    same(f"real audience resolver equals assignments ({len(plans)} exams)", audience_ok, len(plans))
    same(f"resolve_slots finds every paper ({len(plans)} exams)", slots_ok, len(plans))
    same(f"generate replay returns the existing exam ({len(plans)} exams)", replay_ok, len(plans))
    before = ins.execute("select count(*) from exams").fetchone()[0]
    same("no plan is due for generation", len(gen.run_due_plans(db)), 0)
    same("no exam delivery is due", outbox.deliver_due(db, publisher=object())["sent"], 0)
    same("no student notice is due", student_notifications.deliver_due(db)["sent"], 0)
    same("no result notice is owed", assignment_sync.notify_results(db), 0)
    same("worker steps created no exams", ins.execute("select count(*) from exams").fetchone()[0], before)

from inst_enrollment.services import reference_sync  # noqa: E402

with service_context("enrollment"), new_session() as db:
    snap = reference_sync.snapshot(db)
    same("reference snapshot rows", {k: len(v) for k, v in snap.items()},
         {k: ins.execute(f"select count(*) from {k}").fetchone()[0] for k in ("providers", "branches", "batches", "enrollments")})
bad = [r for r in results if not r[1]]
print(f"institute: {len(results) - len(bad)}/{len(results)} passed")
for r in bad:
    print("  FAIL", r[0], "got", r[2], "want", r[3])
sys.exit(1 if bad else 0)
