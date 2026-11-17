"""The Institute portal's paper Review (import preview) in a REAL browser, on DISPOSABLE infrastructure
(run: tests/e2e/pipeline_disposable.sh review_ui_disposable.py).

A teacher imports realistic Mathematics, Physics and Chemistry papers through Question Bank & Papers > Import, and the Review screen
must show every converted expression typeset by KaTeX (fractions, roots, limits under "lim", sums with bounds above/below, matrices,
bold vectors, chemical formulae, reaction arrows, units) - never raw LaTeX or internal markers - with every uncertain field marked
and blocking the import until the teacher decides. Then the imported paper is checked in the database.

Optional genuine Microsoft Office files (never committed): OFFICE_XLSX (a complete paper workbook, imported as Chemistry) and
OFFICE_DOCX (a Word for the web document; imported if complete, otherwise its refusal is shown).
The Institute portal is the production build of this working tree, served with its production Content-Security-Policy.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

DB, KEY, AUTH_LOG = os.environ["PIPELINE_DB"], os.environ["PIPELINE_KEY"], Path(os.environ["PIPELINE_AUTH_LOG"])
if not re.search(r"@127\.0\.0\.1:\d+/pipeline_verify$", DB):
    sys.exit("refusing: disposable pipeline database only")
ROOT = Path(__file__).resolve().parents[2]
WORK = AUTH_LOG.parent
PORT, PW_PORT, PW_IMAGE = 18190, int(os.environ.get("UI_PW_PORT", "3011")), "mcr.microsoft.com/playwright/python:v1.48.0-noble"
STU = {"auth": "http://127.0.0.1:18102", "enroll": "http://127.0.0.1:18104", "test": "http://127.0.0.1:18105",
       "attempt": "http://127.0.0.1:18106", "result": "http://127.0.0.1:18107", "notify": "http://127.0.0.1:18108"}
for name, url in (("STUDENT_SERVICE", STU["auth"]), ("STUDENT_ENROLLMENT_SERVICE", STU["enroll"]), ("STUDENT_TEST_SERVICE", STU["test"]),
                  ("STUDENT_ATTEMPT_SERVICE", STU["attempt"]), ("STUDENT_RESULT_SERVICE", STU["result"]), ("STUDENT_NOTIFICATION_SERVICE", STU["notify"])):
    os.environ[f"{name}_URL"], os.environ[f"{name}_API_KEY"] = url, KEY

sys.path.insert(0, str(ROOT / "institute-backend" / "institute-integration-tests"))
import fakeredis
import httpx
import tests.conftest  # noqa: F401  (service paths + test settings; the database is overridden below)
import uvicorn
from fastapi.testclient import TestClient
from institute_common.config import settings
from institute_common.core import email, redis_store
from institute_common.core.passwords import hash_password
from institute_common.database import get_db
from institute_common.internal_http import set_peer_client
from institute_common.models.staff import Staff, StaffRoleAssignment
from institute_common.ownership import attach_guard
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from tests.gateway import SERVICES
from tests.gateway import app as gateway

ORIGIN, API, PW = {"Origin": "http://localhost:5174"}, "/api/v1", "Review-" + uuid4().hex
results: list[tuple[str, bool]] = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" - {str(detail)[:600]}"), flush=True)
    if not ok:
        summary()


def summary():
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} driver checks passed" + (f"; FAILED: {failed}" if failed else ""), flush=True)
    subprocess.run(["docker", "rm", "-f", "rv-pw"], capture_output=True, check=False)
    os._exit(1 if failed else 0)


engine = create_engine(DB)
factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def sql(q, **a):
    with engine.connect() as conn:
        return conn.execute(text(q), a).all()


class FileSender(email.MemoryEmailSender):
    """Keeps mail in memory and appends it to a file the browser script reads (sign-in verification codes)."""

    def send(self, mail):
        super().send(mail)
        with open(WORK / "mail.log", "a") as f:
            f.write(json.dumps({"to": mail.to, "body": mail.body}) + "\n")


sender = FileSender()
email.set_sender(sender)
redis_store.set_client(fakeredis.FakeRedis(decode_responses=True))


def override():
    db = factory()
    attach_guard(db)
    try:
        yield db
    finally:
        db.close()


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


def login(c, code):
    body = c.post(f"{API}/auth/login", json={"staff_code": code, "password": PW}, headers=ORIGIN).json()
    if body.get("status") == "MFA_REQUIRED":
        otp = sender.outbox[-1].body.split("code is ")[1][:6]
        body = c.post(f"{API}/auth/mfa/verify", json={"challenge_id": body["challenge_id"], "otp": otp}, headers=ORIGIN).json()
    return {**ORIGIN, "X-CSRF-Token": body["me"]["csrf_token"]}


# ------------------------------------------------------------------ realistic papers, typed as teachers type them (Unicode / ASCII)
HEAD = "question,option_a,option_b,option_c,option_d,correct,marks,negative_marks,explanation,topic"
MATH = [
    ("Evaluate lim_(x→0) (sin x)/x.", "1", "0", "∞", "does not exist", "A", "Standard limit: lim_(x→0) (sin x)/x = 1.", "Limits"),
    ("The value of ∫₀^π sin x dx is", "2", "0", "1", "π", "A", "", "Integrals"),
    ("∑_(i=1)^n i equals", "n(n+1)/2", "n²", "n(n−1)/2", "2n", "A", "", "Series"),
    ("Simplify √((a+b)/c) for a = 3, b = 1 and c = 1.", "2", "4", "√2", "1", "A", "", "Roots"),
    ("If A = [1 2; 3 4], then det(A) is", "−2", "2", "10", "−10", "A", "det(A) = 1·4 − 2·3 = −2.", "Matrices"),
    ("The derivative dy/dx of y = x³ + 2x is", "3x² + 2", "3x²", "x² + 2", "3x + 2", "A", "", "Derivatives"),
    ("Solve |x − 2| < 3.", "−1 < x < 5", "x < 5", "x > −1", "−5 < x < 1", "A", "", "Inequalities"),
    ("The value of 1/(1 + 1/x) at x = 1 is", "1/2", "1", "2", "0", "A", "", "Fractions"),
    ("sin²θ + cos²θ equals", "1", "0", "2", "sin 2θ", "A", "", "Trigonometry"),
    ("If x ∈ ℝ and x² = 4, the values of x are", "2 and −2", "2", "−2", "4", "A", "", "Equations"),
    ("Compute e^3t when t = 0.", "1", "0", "3", "e", "A", "", "Exponentials"),          # e^(3t) or (e^3)t: the teacher decides
]
PHYS = [
    ("A body starts from rest with a = 3 m/s². By v = u + at, after t = 2 s, v equals", "6 m/s", "3 m/s", "5 m/s", "1.5 m/s", "A", "", "Kinematics"),
    ("By F = G m₁m₂/r², if r is doubled the force becomes", "F/4", "F/2", "2F", "4F", "A", "", "Gravitation"),
    ("The dimensional formula of energy is", "[M L² T⁻²]", "[M L T⁻²]", "[M L² T⁻¹]", "[M L T⁻¹]", "A", "", "Units"),
    ("The momentum of 2 kg moving at 3 m s⁻¹ is", "6 kg m s⁻¹", "5 kg m s⁻¹", "1.5 kg m s⁻¹", "9 kg m s⁻¹", "A", "", "Momentum"),
    ("In vector form Newton's second law is 𝐅 = m𝐚. Here 𝐚 is the", "acceleration", "area", "amplitude", "angle", "A", "", "Laws of motion"),
    ("For a photon λ = h/p. If p doubles, λ", "halves", "doubles", "is unchanged", "becomes four times", "A", "", "Modern physics"),
    ("With ω = 2πf and f = 50 Hz, ω equals", "100π rad/s", "50π rad/s", "25π rad/s", "200π rad/s", "A", "", "Oscillations"),
    ("Gauss's law in differential form is ∇ · E = ρ/ε₀. Here ε₀ is the", "permittivity of free space", "permeability", "charge density",
     "electric field", "A", "", "Electrostatics"),
    ("The relation J s⁻¹ = W defines the", "watt", "joule", "newton", "pascal", "A", "", "Units"),      # W: watt or work? reviewed
]
CHEM = [
    ("The pH of 10⁻³ M HCl at 25 °C is", "3", "11", "7", "1", "A", "", "Acids"),
    ("The charge on the sulfate ion SO₄²⁻ is", "−2", "+2", "−1", "0", "A", "", "Ions"),
    ("In 2H₂ + O₂ → 2H₂O the number of O atoms on each side is", "2", "1", "4", "3", "A", "", "Equations"),
    ("For N₂ + 3H₂ ⇌ 2NH₃ the forward reaction is", "exothermic", "endothermic", "neither", "nuclear", "A", "", "Equilibrium"),
    ("The formula of calcium hydroxide is", "Ca(OH)₂", "CaOH", "Ca₂OH", "Ca(OH)₃", "A", "", "Formulae"),
    ("The ammonium ion is", "NH₄⁺", "NH₃⁻", "NH₄⁻", "NH₂⁺", "A", "", "Ions"),
    ("Fe³⁺ has how many more protons than electrons?", "3", "2", "1", "0", "A", "", "Ions"),
    ("¹⁴C is used for", "radiocarbon dating", "fertiliser", "welding", "bleaching", "A", "", "Isotopes"),
    ("Acetic acid is", "CH₃COOH", "CH₃OH", "HCOOH", "C₂H₅OH", "A", "", "Organic"),
    ("The electron configuration of Ne is 1s² 2s² 2p⁶. Its number of electrons is", "10", "8", "6", "12", "A", "", "Structure"),
]


def csv_paper(rows) -> bytes:
    def cell(x):
        return '"' + str(x).replace('"', '""') + '"'
    return (HEAD + "\n" + "\n".join(",".join(cell(x) for x in (*r[:6], 4, 1, r[6], r[7])) for r in rows) + "\n").encode()


FILES = WORK / "papers"
FILES.mkdir(exist_ok=True)
(FILES / "mathematics.csv").write_bytes(csv_paper(MATH))
(FILES / "physics.csv").write_bytes(csv_paper(PHYS))
(FILES / "chemistry.csv").write_bytes(csv_paper(CHEM))
papers = [
    {"subject": "Mathematics", "title": "Review E2E Mathematics", "file": str(FILES / "mathematics.csv"), "questions": len(MATH),
     # the one ambiguous power, corrected by the teacher; every other flagged field (if any) kept exactly as in the file
     "edits": {"11:question": r"Compute \(e^{3t}\) when \(t = 0\)."},
     "structures": {"mfrac": 4, "sqrt": 1, "op-limits": 2, "mtable": 1, "msupsub": 3}},
    {"subject": "Physics", "title": "Review E2E Physics", "file": str(FILES / "physics.csv"), "questions": len(PHYS), "edits": {},
     "structures": {"mfrac": 2, "msupsub": 3, "mathbf": 1}},
    {"subject": "Chemistry", "title": "Review E2E Chemistry", "file": str(FILES / "chemistry.csv"), "questions": len(CHEM), "edits": {},
     "structures": {"msupsub": 5}},
]
# complete papers supplied for acceptance (ACCEPT_DIR: the institute's own files; read here, never committed), with the rendered
# structures their content must show
ACCEPT = [("Mathematics_Provider_Paper.docx", "Mathematics", {"mfrac": 2, "msupsub": 5, "op-limits": 1}),
          ("Physics_Provider_Paper.xlsx", "Physics", {"mfrac": 3, "msupsub": 3}),
          ("Chemistry_Provider_Paper.docx", "Chemistry", {"msupsub": 5})]
if os.environ.get("ACCEPT_DIR"):
    for name, subject, structures in ACCEPT:
        src = Path(os.environ["ACCEPT_DIR"]) / name
        if src.is_file():
            dst = FILES / name
            dst.write_bytes(src.read_bytes())
            papers.append({"subject": subject, "title": f"Acceptance {subject} ({name})", "file": str(dst), "questions": None, "edits": {},
                           "structures": structures, "accept": True, "approve": True,
                           "sha256": __import__("hashlib").sha256(dst.read_bytes()).hexdigest()})
for p in papers[:3]:
    p["approve"] = True                                    # the realistic papers go all the way to approval too
for env, subject, title in (("OFFICE_XLSX", "Chemistry", "Review E2E genuine Excel workbook"), ("OFFICE_DOCX", "Physics", "Review E2E genuine Word file")):
    src = os.environ.get(env)
    if src and Path(src).is_file():
        dst = FILES / Path(src).name
        dst.write_bytes(Path(src).read_bytes())
        papers.append({"subject": subject, "title": title, "file": str(dst), "questions": None, "edits": {}, "structures": {},
                       "genuine": True, "sha256": __import__("hashlib").sha256(dst.read_bytes()).hexdigest()})

with TestClient(gateway, base_url="http://localhost") as c:
    print("set-up through the Institute API")
    h = login(c, "STF000001")
    P = c.post(f"{API}/providers", json={"name": "Review Academy", "email": "office@review-academy.example.com"}, headers=h).json()["provider_id"]
    B = c.post(f"{API}/providers/{P}/branches", json={"name": "Main", "city": "Pune", "state": "MH"}, headers=h).json()["branch_id"]
    SUBJ = {name: c.post(f"{API}/subjects", json={"code": code, "name": name}, headers=h).json()["subject_id"]
            for code, name in (("MATHS", "Mathematics"), ("PHYS", "Physics"), ("CHEM", "Chemistry"))}
    for s in SUBJ.values():
        c.put(f"{API}/providers/{P}/subjects/{s}", json={}, headers=h)
    # the explicit subject_id -> category mapping (RECOGNITION_SUBJECT_CATEGORIES), as the development configuration has it
    settings.recognition_subject_categories = {SUBJ["Mathematics"]: "MATHEMATICS", SUBJ["Physics"]: "PHYSICS", SUBJ["Chemistry"]: "CHEMISTRY"}
    BT = c.post(f"{API}/branches/{B}/batches", json={"name": "JEE 2027"}, headers=h).json()["batch_id"]
    r = c.post(f"{API}/staff", json={"full_name": "Meera Teacher", "email": "meera@review-academy.example.com",
                                     "assignment": {"role": "TEACHER", "provider_id": P}}, headers=h)
    T = r.json()
    token = re.search(r"token=([A-Za-z0-9_\-]+)", sender.outbox[-1].body).group(1)
    ok = c.post(f"{API}/auth/verify-email", json={"token": token, "new_password": PW}, headers=ORIGIN).status_code == 200
    h = login(c, "STF000001")
    for s in SUBJ.values():
        c.post(f"{API}/teaching", json={"staff_id": T["staff_id"], "branch_id": B, "subject_id": s, "batch_id": BT}, headers=h)
    check("set-up: provider, branch, Mathematics/Physics/Chemistry (recognition categories by subject_id), a teacher of all three", ok, r.text)
    # saved reviews (review drafts): a second editor, a paper with two uncertain fields, and a review saved before an engine update
    r = c.post(f"{API}/staff", json={"full_name": "Asha Admin", "email": "asha@review-academy.example.com",
                                     "assignment": {"role": "PROVIDER_ADMIN", "provider_id": P}}, headers=h)
    A = r.json()
    token = re.search(r"token=([A-Za-z0-9_\-]+)", sender.outbox[-1].body).group(1)
    ok = c.post(f"{API}/auth/verify-email", json={"token": token, "new_password": PW}, headers=ORIGIN).status_code == 200
    DRAFT_ROWS = [("Evaluate ∫₀π sin x dx.", "2", "0", "1", "π", "A", "", "Integrals"), ("Simplify sin x/x at x = 1.", "1", "0", "2", "3", "A", "", "Limits")]
    (FILES / "drafts.csv").write_bytes(csv_paper(DRAFT_ROWS))
    (FILES / "stale.csv").write_bytes(csv_paper(DRAFT_ROWS[:1] + [("What is 2 + 2?", "4", "3", "5", "6", "A", "", "Arithmetic")]))
    ht = login(c, T["staff_code"])
    r = c.post(f"{API}/branches/{B}/subjects/{SUBJ['Mathematics']}/review-drafts", headers=ht, data={"title": "Draft E2E saved before an update"},
               files={"file": ("stale.csv", (FILES / "stale.csv").read_bytes(), "text/csv")})
    STALE = r.json()["draft"]["draft_id"]
    c.patch(f"{API}/review-drafts/{STALE}", headers=ht, json={"base_revision": 1, "changes": {"1:question": {"action": "keep_original"}}})
    from institute_common.models.papers import PaperReviewDraft
    with factory() as db:                                    # as a review saved with the previous engine (2.3.0) would look
        d = db.get(PaperReviewDraft, __import__("uuid").UUID(STALE))
        d.decisions = {k: {**e, "engine": "2.3.0"} for k, e in d.decisions.items()}
        d.engine_version = "2.3.0"
        db.commit()
    check("set-up: a provider admin (second editor), a two-question draft paper, a review saved before an engine update", ok and r.status_code == 201, r.text)

# ------------------------------------------------------------------ the Institute portal: production build, production CSP, one origin
DIST = WORK / "dist"
build = subprocess.run(["npx", "vite", "build", "--outDir", str(DIST), "--emptyOutDir"], cwd=ROOT / "institute-frontend",
                       env={**os.environ, "VITE_INSTITUTE_API_URL": ""}, capture_output=True, text=True, check=False)
check("production build of the Institute portal (this working tree, same-origin API)", build.returncode == 0 and (DIST / "index.html").exists(),
      build.stderr[-500:])
CSP = subprocess.run(["node", "--input-type=module", "-e",
                      "import { contentSecurityPolicy } from './scripts/render-nginx-conf.mjs'; console.log(contentSecurityPolicy([]))"],
                     cwd=ROOT / "institute-frontend", capture_output=True, text=True, check=True).stdout.strip()
check("the Institute portal's production Content-Security-Policy (no unsafe-eval, scripts from self only)",
      "script-src 'self'" in CSP and "unsafe-eval" not in CSP, CSP)
KINDS = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".html": "text/html", ".png": "image/png", ".ico": "image/x-icon",
         ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf"}


async def site(scope, receive, send):
    """As production serves it: /api/* to the gateway, everything else the SPA with the production security headers."""
    if scope["type"] != "http" or scope["path"].startswith("/api/"):
        return await gateway(scope, receive, send)
    rel = scope["path"].lstrip("/")
    target = DIST / rel if rel and (DIST / rel).is_file() else DIST / "index.html"
    body = target.read_bytes()
    await send({"type": "http.response.start", "status": 200, "headers": [
        (b"content-type", KINDS.get(target.suffix, "application/octet-stream").encode()), (b"content-length", str(len(body)).encode()),
        (b"content-security-policy", CSP.encode()), (b"x-content-type-options", b"nosniff")]})
    await send({"type": "http.response.body", "body": body})


server = uvicorn.Server(uvicorn.Config(site, host="0.0.0.0", port=PORT, log_level="warning", lifespan="off"))
threading.Thread(target=server.run, daemon=True).start()
for _ in range(40):
    try:
        if httpx.get(f"http://127.0.0.1:{PORT}/login", timeout=2).status_code == 200:
            break
    except httpx.HTTPError:
        time.sleep(0.25)

# ------------------------------------------------------------------ Chromium in the Playwright container (its localhost:5174 -> this server)
PW_PYTHON = os.environ.get("PW_PYTHON", "python3")
pkgs = subprocess.run([PW_PYTHON, "-c", "import os, playwright; print(os.path.dirname(os.path.dirname(playwright.__file__)))"],
                      capture_output=True, text=True, check=True).stdout.strip()
subprocess.run(["docker", "rm", "-f", "rv-pw"], capture_output=True, check=False)
subprocess.run(["docker", "run", "-d", "--name", "rv-pw", "--add-host", "host.docker.internal:host-gateway", "-p", f"127.0.0.1:{PW_PORT}:3000",
                "-v", f"{pkgs}:/pwpkgs:ro", "-e", "PYTHONPATH=/pwpkgs", PW_IMAGE, "python3", "-m", "playwright", "run-server", "--port", "3000",
                "--host", "0.0.0.0"], check=True, capture_output=True)
for _ in range(60):
    try:
        if httpx.get(f"http://127.0.0.1:{PW_PORT}/", timeout=2).status_code == 200:
            break
    except httpx.HTTPError:
        time.sleep(0.5)
FWD = ("import asyncio\nasync def pipe(r, w):\n    try:\n        while data := await r.read(65536):\n            w.write(data); await w.drain()\n"
       "    finally:\n        w.close()\nasync def h(r, w):\n    ur, uw = await asyncio.open_connection('host.docker.internal', " + str(PORT) + ")\n"
       "    await asyncio.gather(pipe(r, uw), pipe(ur, w), return_exceptions=True)\n"
       "async def main():\n    a = await asyncio.start_server(h, '127.0.0.1', 5174)\n    await a.serve_forever()\nasyncio.run(main())\n")
subprocess.run(["docker", "exec", "-d", "rv-pw", "python3", "-c", FWD], check=True)
time.sleep(2)

params = {"ui": "http://localhost:5174", "ws": f"ws://127.0.0.1:{PW_PORT}/", "mail_log": str(WORK / "mail.log"), "password": PW,
          "teacher": T["staff_code"], "shots": str(Path(os.environ.get("UI_SHOTS", WORK / "shots"))), "papers": papers,
          "report": str(WORK / "review_report.json")}
(WORK / "review_params.json").write_text(json.dumps(params))
print("\nbrowser journey (Chromium)")
browser = subprocess.run([PW_PYTHON, str(ROOT / "tests" / "e2e" / "review_ui_browser.py"), str(WORK / "review_params.json")], check=False)
check("every browser check of the Review journey passed (see the list above)", browser.returncode == 0)

print("\nsaved reviews (drafts) in two browsers")
params.update({"admin": A["staff_code"], "drafts_report": str(WORK / "drafts_report.json"), "drafts": {
    "subject": "Mathematics", "title": "Draft E2E two editors", "file": str(FILES / "drafts.csv"), "stale_draft": STALE,
    "teacher_text": r"Simplify \(\frac{\sin x}{x}\) at x = 1.", "admin_text": r"Simplify \(\sin\frac{x}{x}\) at x = 1.",
    "admin_q1": r"Evaluate \(\int_{0}^{\pi}\sin x\,dx\)."}})
(WORK / "review_params.json").write_text(json.dumps(params))
drafts_run = subprocess.run([PW_PYTHON, str(ROOT / "tests" / "e2e" / "review_drafts_ui_browser.py"), str(WORK / "review_params.json")], check=False)
check("every browser check of the saved-review journey passed (see the list above)", drafts_run.returncode == 0)
DR = json.loads((WORK / "drafts_report.json").read_text()) if (WORK / "drafts_report.json").exists() else {}
rows = {str(i): (st, dec) for i, st, dec in sql("select draft_id, status, decisions from paper_review_drafts")}
two = rows.get(DR.get("draft_id"), (None, {}))
check("the two-editor review was discarded, with the admin's kept version of question 2 and the admin's change to question 1",
      two[0] == "DISCARDED" and two[1].get("2:question", {}).get("by") == A["staff_id"]
      and "sin" in two[1]["2:question"]["decision"]["text"] and two[1].get("1:question", {}).get("by") == A["staff_id"], two)
check("the review saved before the engine update was imported after its decision was confirmed with the current engine",
      rows.get(STALE, (None,))[0] == "IMPORTED" and rows[STALE][1]["1:question"]["engine"] != "2.3.0", rows.get(STALE))

# ------------------------------------------------------------------ what was stored
report = json.loads((WORK / "review_report.json").read_text())
imported = {t: n for t, n in sql("select p.title, count(q.question_version_id) from papers p join paper_versions v on v.paper_id = p.paper_id "
                                 "join paper_version_questions pq on pq.paper_version_id = v.paper_version_id "
                                 "join question_versions q on q.question_version_id = pq.question_version_id group by p.title")}
for p in papers:
    if p.get("genuine") and not report.get(p["title"], {}).get("imported"):
        continue
    want = p["questions"] if p["questions"] is not None else report[p["title"]]["questions"]
    check(f"'{p['title']}' stored as a draft paper with all {want} questions", imported.get(p["title"]) == want, imported)
stems = {s for (s,) in sql("select q.stem from question_versions q")}
check("stored stems keep typeset LaTeX: the limit, the sum, the matrix, the bold vector, the reaction arrow",
      any(r"\lim\limits_{" in s for s in stems) and any(r"\sum\limits_{" in s for s in stems) and any("bmatrix" in s for s in stems)
      and any(r"\mathbf{F}" in s for s in stems) and any(r"\rightleftharpoons" in s or "⇌" in s for s in stems), sorted(stems)[:8])
check("the teacher's correction is what was stored (e^(3t), not a guess)", any(r"e^{3t}" in s for s in stems), [s for s in stems if "Compute" in s])
status = dict(sql("select p.title, v.status from papers p join paper_versions v on v.paper_id = p.paper_id"))
for p in papers:
    if p.get("approve") and report.get(p["title"], {}).get("imported"):
        check(f"'{p['title']}' approved through the portal (status APPROVED)", status.get(p["title"]) == "APPROVED", status.get(p["title"]))
check("every complete acceptance paper supplied was imported and approved",
      all(report.get(p["title"], {}).get("approved") for p in papers if p.get("accept")), {p["title"]: report.get(p["title"]) for p in papers if p.get("accept")})
summary()
