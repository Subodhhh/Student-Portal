"""Question pictures, Institute to Student, end to end on DISPOSABLE infrastructure
(run: tests/e2e/pipeline_disposable.sh pictures_pipeline_disposable.py; PICTURES_FORMAT=xlsx for an Excel paper with the pictures
inserted into its cells, default zip: a CSV + picture files).

The throwaway PostgreSQL / Redis and the real Student services come from pipeline_disposable.sh; the Institute services run in-process
(the integration-test gateway) with REAL HTTP clients to the Student services. Pictures are stored and shown exactly as uploaded:
OCR and picture recognition were removed on 2026-11-16, and this run proves nothing reads them.
Stages:
  1 the paper (a .zip of CSV + pictures, or an .xlsx with embedded pictures: a formula picture as a question, a labelled diagram as a question, a printed word as an option,
    and plain English text) is previewed: every picture is stored once, byte-identical, and shown; plain English needs no decision
  2 nothing reads the pictures: no recognition job is created and there is no recognition endpoint
  3 import, approval, publication; question versions reference their pictures
  4 exam generation and delivery: the pictures reach the Student test service before the exam, byte-identical
  5 the student's attempt carries the pictures and fetches them; an outsider cannot; answers, submission, evaluation
  6 after the window closes the result review shows the pictures with the questions
"""
import csv
import io
import os
import re
import sys
import time
import zipfile
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

DB, KEY, AUTH_LOG = os.environ["PIPELINE_DB"], os.environ["PIPELINE_KEY"], Path(os.environ["PIPELINE_AUTH_LOG"])
RPORT = os.environ["PIPELINE_RPORT"]
if not re.search(r"@127\.0\.0\.1:\d+/pipeline_verify$", DB):
    sys.exit("refusing: disposable pipeline database only")
ROOT = Path(__file__).resolve().parents[2]
STU = {"auth": "http://127.0.0.1:18102", "enroll": "http://127.0.0.1:18104", "test": "http://127.0.0.1:18105",
       "attempt": "http://127.0.0.1:18106", "result": "http://127.0.0.1:18107", "notify": "http://127.0.0.1:18108"}
for name, url in (("STUDENT_SERVICE", STU["auth"]), ("STUDENT_ENROLLMENT_SERVICE", STU["enroll"]), ("STUDENT_TEST_SERVICE", STU["test"]),
                  ("STUDENT_ATTEMPT_SERVICE", STU["attempt"]), ("STUDENT_RESULT_SERVICE", STU["result"]), ("STUDENT_NOTIFICATION_SERVICE", STU["notify"])):
    os.environ[f"{name}_URL"], os.environ[f"{name}_API_KEY"] = url, KEY

sys.path.insert(0, str(ROOT / "institute-backend" / "institute-integration-tests"))
import fakeredis
import httpx
import tests.conftest  # noqa: F401  (service paths + test settings; the database is overridden below)
from fastapi.testclient import TestClient
from inst_enrollment.services import reference_sync
from inst_exam.services import outbox
from institute_common.core import email, redis_store
from institute_common.core.passwords import hash_password
from institute_common.database import get_db
from institute_common.internal_http import set_peer_client
from institute_common.models.staff import Staff, StaffRoleAssignment
from institute_common.ownership import attach_guard
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from tests.gateway import SERVICES, app

ORIGIN, API, PW = {"Origin": "http://localhost:5174"}, "/api/v1", "Pipeline-" + uuid4().hex
ACAD = {"class_level": "11th", "academic_year": "2026-2027"}
results: list[tuple[str, bool]] = []


def check(stage, name, ok, detail=""):
    results.append((f"{stage} {name}", bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {stage} {name}" + ("" if ok else f" - {str(detail)[:600]}"), flush=True)
    if not ok and os.environ.get("STOP_ON_FAIL", "1") == "1":
        summary()


def summary():
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (f"; FAILED: {failed}" if failed else " - every stage proven"))
    sys.exit(1 if failed else 0)


def printed(words: str, size=(360, 90), font_size=40) -> bytes:
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    d.text((20, 18), words, fill="black", font=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def labelled_cell() -> bytes:
    """A small labelled diagram (a picture with words in it: it must reach students exactly as drawn)."""
    img = Image.new("RGB", (420, 260), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
    d.ellipse((40, 40, 260, 220), outline="black", width=3)
    d.ellipse((120, 100, 180, 160), fill="gray", outline="black", width=2)
    d.line((175, 125, 300, 70), fill="black", width=2)
    d.text((305, 55), "Nucleus", fill="black", font=font)
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


engine = create_engine(DB)
factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
sql = lambda q, **a: engine.connect().execute(text(q), a).all()
sender = email.MemoryEmailSender()
email.set_sender(sender)
redis_store.set_client(fakeredis.FakeRedis(decode_responses=True))


def override():
    db = factory()
    attach_guard(db)
    try:
        yield db
    finally:
        db.close()


reference_sync.new_session = factory
for name, service_app in SERVICES.items():
    service_app.dependency_overrides[get_db] = override
    set_peer_client(name, TestClient(service_app, base_url=f"http://{name}"))

with factory() as db:
    sa = Staff(staff_code="STF000001", email="owner@example.com", full_name="Platform Owner", password_hash=hash_password(PW), status="ACTIVE",
               email_verified_at=datetime.now(UTC))
    db.add(sa)
    db.flush()
    db.add(StaffRoleAssignment(staff_id=sa.staff_id, role="SUPER_ADMIN", status="ACTIVE"))
    db.commit()

# ------------------------------------------------------------------ the package: one CSV + pictures
FORMULA = printed("x² + 5x + 6 = 0", (520, 100), 48)
DIAGRAM = labelled_cell()
NEWTON = printed("Newton")
PICTURES = {"pics/formula.png": FORMULA, "pics/cell.png": DIAGRAM, "pics/newton.png": NEWTON}
HEADER = ["question", "question_image", "option_a", "option_a_image", "option_b", "option_b_image", "option_c", "option_c_image",
          "option_d", "option_d_image", "correct_answer", "positive_marks", "negative_marks", "chapter", "topic", "sub_topic", "explanation"]
ROWS = [
    ["Find the roots of the equation in the picture.", "pics/formula.png", "-2 and -3", "", "2 and 3", "", "-1 and -6", "", "1 and 6", "",
     "A", 4, 1, "Quadratic equations", "Roots", "Factorisation", "(x+2)(x+3)=0."],
    ["", "pics/cell.png", "Nucleus", "", "Chlorophyll", "", "Xylem", "", "Stomata", "",
     "A", 4, 1, "Cell", "Organelles", "", "The topmost labelled part."],
    ["Which is the SI unit of force?", "", "Joule", "", "Watt", "", "Pascal", "", "", "pics/newton.png",
     "D", 4, 1, "Units", "Force", "", "Force is measured in newtons."],
]


FORMAT = os.environ.get("PICTURES_FORMAT", "zip")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CELLS = {"pics/formula.png": "B2", "pics/cell.png": "B3", "pics/newton.png": "J4"}   # question_image is column B, option_d_image column J


def workbook() -> bytes:
    """The same paper as an Excel file: each picture inserted over its image cell, the image columns otherwise empty."""
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as XLImage
    wb = Workbook()
    ws = wb.active
    ws.append(HEADER)
    for row in ROWS:
        ws.append([None if (v == "" or str(v).startswith("pics/")) else v for v in row])
    for name, cell in CELLS.items():
        ws.add_image(XLImage(io.BytesIO(PICTURES[name])), cell)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def package() -> bytes:
    buf = io.StringIO()
    csv.writer(buf).writerows([HEADER, *ROWS])
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("paper.csv", buf.getvalue())
        for n, data in PICTURES.items():
            zf.writestr(n, data)
    return out.getvalue()


def upload(c, h, branch, subject, preview: bool):
    return c.post(f"{API}/branches/{branch}/subjects/{subject}/papers/upload", headers=h,
                  files={"file": ("paper.xlsx", workbook(), XLSX) if FORMAT == "xlsx" else ("paper.zip", package(), "application/zip")},
                  data={"title": "Pictures test", **({"preview": "true"} if preview else {})})


def login(c, code, password=PW):
    body = c.post(f"{API}/auth/login", json={"staff_code": code, "password": password}, headers=ORIGIN).json()
    if body.get("status") == "MFA_REQUIRED":
        otp = sender.outbox[-1].body.split("code is ")[1][:6]
        body = c.post(f"{API}/auth/mfa/verify", json={"challenge_id": body["challenge_id"], "otp": otp}, headers=ORIGIN).json()
    return {**ORIGIN, "X-CSRF-Token": body["me"]["csrf_token"]}


def student(email_: str, sid: str):
    s = httpx.Client(timeout=20, headers={"Origin": "http://localhost"})
    s.post(STU["auth"] + "/auth/forgot-password", json={"email": email_})
    tok = None
    for _ in range(20):
        found = re.findall(r"to=" + re.escape(email_) + r"[\s\S]{0,400}?reset-password\?token=([A-Za-z0-9_\-]+)", AUTH_LOG.read_text()) \
            or re.findall(r"reset-password\?token=([A-Za-z0-9_\-]+)", AUTH_LOG.read_text())
        if found:
            tok = found[-1]
            break
        time.sleep(0.5)
    s.post(STU["auth"] + "/auth/reset-password", json={"token": tok, "new_password": PW})
    return s, s.post(STU["auth"] + "/auth/login", json={"login": sid, "password": PW})


with TestClient(app, base_url="http://localhost") as c:
    print("set-up through the Institute API")
    h = login(c, "STF000001")
    P = c.post(f"{API}/providers", json={"name": "Pictures Academy", "email": "office@pictures-academy.example.com"}, headers=h).json()["provider_id"]
    B = c.post(f"{API}/providers/{P}/branches", json={"name": "Main", "city": "Pune", "state": "MH"}, headers=h).json()["branch_id"]
    SJ = c.post(f"{API}/subjects", json={"code": "SCI", "name": "Science"}, headers=h).json()["subject_id"]
    c.put(f"{API}/providers/{P}/subjects/{SJ}", json={}, headers=h)
    BT = c.post(f"{API}/branches/{B}/batches", json={"name": "Foundation 2027"}, headers=h).json()["batch_id"]
    r = c.post(f"{API}/branches/{B}/enrollments", json={"academic": ACAD, "new_student": {"first_name": "Riya", "last_name": "Patil",
                                                                                           "email": "riya@family.example.com"}, "batch_ids": [BT]}, headers=h)
    check("0", "student enrolled through the REAL Student authentication service", r.status_code == 201, r.text)
    SID = r.json()["student_id"]
    OTHER = c.post(f"{API}/branches/{B}/enrollments", json={"academic": ACAD, "new_student": {"first_name": "Aman", "last_name": "Shah",
                                                                                             "email": "aman@family.example.com"}}, headers=h).json()["student_id"]
    with factory() as db:
        reference_sync.run_once(db, full=True)

    print(f"1 preview of the paper ({'an .xlsx with embedded pictures' if FORMAT == 'xlsx' else 'a .zip of CSV + pictures'})")
    if FORMAT == "xlsx":
        with zipfile.ZipFile(io.BytesIO(workbook())) as zf:
            media = [zf.read(n) for n in zf.namelist() if n.startswith("xl/media/")]
        check("1", "the Excel file carries the three original pictures unchanged", sorted(media) == sorted(PICTURES.values()), len(media))
    r = upload(c, h, B, SJ, preview=True)
    check("1", "preview accepted", r.status_code == 200, r.text)
    qs = r.json()["questions"]
    check("1", "every picture is shown on its question/option", [sorted(q.get("images", {})) for q in qs] == [["question"], ["question"], ["D"]],
          [q.get("images") for q in qs])
    assets = {n: qs[i]["images"][f]["asset_id"] for n, i, f in (("formula", 0, "question"), ("cell", 1, "question"), ("newton", 2, "D"))}
    stored = {row[0]: bytes(row[1]) for row in sql("select asset_id::text, image_data from question_image_assets")}
    check("1", "stored once each, byte-identical (BYTEA)", len(stored) == 3 and stored[assets["formula"]] == FORMULA
          and stored[assets["cell"]] == DIAGRAM and stored[assets["newton"]] == NEWTON, sorted(stored))
    pic = c.get(f"{API}/question-images/{assets['cell']}", headers=h)
    check("1", "the picture endpoint serves it to the provider's staff", pic.status_code == 200 and pic.content == DIAGRAM, pic.status_code)
    plain = qs[2]["fields"]["question"]
    check("1", "plain English is kept exactly as typed and needs no decision", (plain["outcome"], plain["text"]) == ("PLAIN_TEXT", ROWS[2][0])
          and r.json()["summary"]["questions_needing_review"] == 0, (plain, r.json()["summary"]))

    print("2 nothing reads the pictures")
    check("2", "no recognition job is created for any picture", sql("select count(*) from picture_recognition_jobs")[0][0] == 0)
    gone = c.get(f"{API}/question-images/{assets['cell']}/recognition", headers=h)
    check("2", "there is no picture-recognition endpoint", gone.status_code == 404, gone.status_code)

    print("3 import, approval, publication")
    r = upload(c, h, B, SJ, preview=False)
    check("3", "import accepted", r.status_code == 201, r.text)
    PV = r.json()["paper_version_id"]
    check("3", "pictures reused, not stored twice", sql("select count(*) from question_image_assets")[0][0] == 3)
    check("3", "still no recognition job after the import", sql("select count(*) from picture_recognition_jobs")[0][0] == 0)
    check("3", "plain English stored verbatim in the Question Bank",
          sql("select stem, option_a from question_versions where option_d_image_id::text = :a", a=assets["newton"]) == [(ROWS[2][0], "Joule")])
    linked = sql("""select qv.stem_image_id::text, qv.option_d_image_id::text, qv.option_d from paper_version_questions pvq
                    join question_versions qv using (question_version_id) where pvq.paper_version_id = :pv order by pvq.position""", pv=PV)
    check("3", "question versions reference their pictures; the picture option has no text",
          [x[0] for x in linked] == [assets["formula"], assets["cell"], None] and linked[2][1] == assets["newton"] and linked[2][2] == "", linked)
    check("3", "the picture-only question keeps an empty text (nothing read from the picture)", "x" not in (sql("select stem from question_versions where stem_image_id::text = :a", a=assets["cell"])[0][0] or ""))
    c.post(f"{API}/paper-versions/{PV}/approve", json={"note": "checked"}, headers=h)
    check("3", "published", c.post(f"{API}/paper-versions/{PV}/publish", json={"note": None}, headers=h).json().get("status") == "PUBLISHED")

    print("4 exam generation and delivery of the pictures")
    start = datetime.now(UTC) + timedelta(seconds=25)
    end = start + timedelta(minutes=2)
    r = c.post(f"{API}/branches/{B}/exam-plans", json={
        "name": "Pictures weekly", "duration_minutes": 1, "start_at": start.isoformat(), "end_at": end.isoformat(),
        "audience_type": "BATCHES", "batch_ids": [BT], "marking_mode": "UNIFORM", "marks_correct": 4, "marks_wrong": 1,
        "slots": [{"subject_id": SJ, "paper_version_id": PV}]}, headers=h)
    check("4", "exam plan created", r.status_code == 201, r.text)
    job = c.post(f"{API}/exam-plans/{r.json()['plan_id']}/generate", json={}, headers=h).json()
    ex = c.get(f"{API}/exams/{job.get('exam_id')}", headers=h).json()
    with factory() as db:
        sent = outbox.deliver_due(db)
    TEST_ID = ex["exam_version_id"]
    check("4", "delivered to the real Student test service", sent.get("sent") == 1, sent)
    sums = {n: sha256(d).hexdigest() for n, d in (("formula", FORMULA), ("cell", DIAGRAM), ("newton", NEWTON))}
    student_pics = {row[0]: bytes(row[1]) for row in sql("select checksum, image_data from question_images")}
    check("4", "the three pictures are in the Student database, byte-identical",
          all(student_pics.get(s) == d for s, d in ((sums["formula"], FORMULA), (sums["cell"], DIAGRAM), (sums["newton"], NEWTON)))
          and len(student_pics) == 3, sorted(student_pics))
    delivered = sql("select question_image, option_d_image, option_d from questions where test_id::text = :t order by question_order", t=TEST_ID)
    check("4", "delivered questions reference the pictures by checksum",
          [x[0] for x in delivered] == [sums["formula"], sums["cell"], None] and delivered[2][1] == sums["newton"], delivered)

    print("5 the student attempts the test with pictures")
    s, r = student("riya@family.example.com", SID)
    check("5", "student signs in", r.status_code == 200, r.text)
    time.sleep(max(0, (start - datetime.now(UTC)).total_seconds() + 1))
    att = s.post(STU["attempt"] + "/attempts/start", json={"test_id": TEST_ID}).json()
    AID = att.get("attempt_id")
    wq = sorted(s.get(f"{STU['attempt']}/attempts/{AID}/workspace").json().get("questions", []), key=lambda q: q["question_order"])
    check("5", "the attempt carries every picture reference",
          [q.get("question_image") for q in wq] == [sums["formula"], sums["cell"], None] and wq[2].get("option_d_image") == sums["newton"],
          [(q.get("question_image"), q.get("option_d_image")) for q in wq])
    got = s.get(f"{STU['test']}/tests/{TEST_ID}/images/{sums['cell']}")
    check("5", "the student's browser can fetch the picture, byte-identical", got.status_code == 200 and got.content == DIAGRAM
          and got.headers.get("content-type") == "image/png", (got.status_code, got.headers.get("content-type")))
    o, _ = student("aman@family.example.com", OTHER)
    check("5", "a student outside the audience cannot fetch it", o.get(f"{STU['test']}/tests/{TEST_ID}/images/{sums['cell']}").status_code in (403, 404))
    check("5", "nobody signed out can fetch it", httpx.get(f"{STU['test']}/tests/{TEST_ID}/images/{sums['cell']}").status_code in (401, 403))
    s.post(f"{STU['attempt']}/attempts/{AID}/answers", json={"question_id": wq[0]["question_id"], "selected_answer": "A"})   # correct
    s.post(f"{STU['attempt']}/attempts/{AID}/answers", json={"question_id": wq[2]["question_id"], "selected_answer": "D"})   # correct (picture option)
    s.post(f"{STU['attempt']}/attempts/{AID}/answers", json={"question_id": wq[1]["question_id"], "selected_answer": "B"})   # wrong
    check("5", "submitted", s.post(f"{STU['attempt']}/attempts/{AID}/submit").json().get("status") == "SUBMITTED")
    res = s.post(f"{STU['result']}/results/attempts/{AID}/evaluate").json()
    check("5", "evaluated: the picture option counts as the correct answer: +4 +4 -1 = 7 of 12", res.get("obtained_marks") == 7
          and res.get("total_marks") == 12, res)

    print("6 result review after the window closes")
    time.sleep(max(0, (end - datetime.now(UTC)).total_seconds() + 2))
    det = s.get(f"{STU['result']}/results/attempts/{AID}/details").json()
    dq = sorted(det.get("questions", []), key=lambda q: q.get("question_order", 0))
    check("6", "the review shows the pictures with the questions", det.get("review_available") is True
          and [q.get("question_image") for q in dq] == [sums["formula"], sums["cell"], None] and dq[2].get("option_d_image") == sums["newton"],
          det)
summary()
