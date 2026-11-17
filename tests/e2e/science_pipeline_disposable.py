"""Mathematics, Physics and Chemistry papers end to end, in a REAL browser, on DISPOSABLE infrastructure
(run: tests/e2e/pipeline_disposable.sh science_pipeline_disposable.py).

Recognition Cycle 2.3 E2E. The throwaway PostgreSQL / Redis and the six real Student services come from pipeline_disposable.sh; the
Institute services run in-process (the integration-test gateway) with REAL HTTP clients to the Student services. Stages:
  0 isolation evidence: the database is the newly created throwaway container on 127.0.0.1, nothing points at Neon, the paper chain
    (incl. 0006_upload_recognition) exists only here
  1 set-up through the APIs; the subject categories configured explicitly by subject_id (Biology = OTHER)
  2 per subject: provider preview -> recognition outcomes (automatic conversions + fields needing review) -> the import is refused
    while a flagged field is unresolved -> provider decisions (edit / keep as typed) -> import -> stored LaTeX + recognition record
  3 approval, publishing, one exam with the three papers, delivery to the real Student test service (text identical to the bank)
  4 Chromium (inside the Playwright container): the Student portal production build, served with the production
    Content-Security-Policy, proxied same-origin to the Student services: sign-in, the attempt, rendering of every question, no raw
    LaTeX, no console errors, no CSP violations, answers, submission, the result
Evidence (screenshots, browser report, isolation report) is copied to $E2E_EVIDENCE (default: the run's work folder).
Needs: Docker with the image mcr.microsoft.com/playwright/python:v1.48.0-noble (browsers), a Python with playwright==1.48 (PW_PYTHON,
default python3; its package is mounted into the image) and npm/node (the Student portal build). Nothing is built into an image.
"""
import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

DB, KEY, AUTH_LOG = os.environ["PIPELINE_DB"], os.environ["PIPELINE_KEY"], Path(os.environ["PIPELINE_AUTH_LOG"])
if not re.search(r"@127\.0\.0\.1:\d+/pipeline_verify$", DB):
    sys.exit("refusing: disposable pipeline database only")
ROOT = Path(__file__).resolve().parents[2]
WORK = AUTH_LOG.parent
EVID = Path(os.environ.get("E2E_EVIDENCE") or WORK / "evidence")
EVID.mkdir(parents=True, exist_ok=True)
SPORT, PW_IMAGE, PW_NAME = 18191, "mcr.microsoft.com/playwright/python:v1.48.0-noble", "sci-pw"
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
from inst_enrollment.services import reference_sync
from inst_exam.services import outbox
from institute_common.config import settings
from institute_common.core import email, redis_store
from institute_common.core.passwords import hash_password
from institute_common.database import get_db
from institute_common.internal_http import set_peer_client
from institute_common.models.staff import Staff, StaffRoleAssignment
from institute_common.ownership import attach_guard
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from tests.gateway import SERVICES, app
from tests.test_paper_recognition import word

ORIGIN, API, PW = {"Origin": "http://localhost:5174"}, "/api/v1", "Science-" + uuid4().hex
ACAD = {"class_level": "11th", "academic_year": "2026-2027"}
results: list[tuple[str, bool]] = []
evidence: dict = {"started": datetime.now(UTC).isoformat()}


def check(stage, name, ok, detail=""):
    results.append((f"{stage} {name}", bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {stage} {name}" + ("" if ok else f" - {str(detail)[:600]}"), flush=True)
    if not ok and os.environ.get("STOP_ON_FAIL", "1") == "1":
        summary()


def summary():
    failed = [n for n, ok in results if not ok]
    evidence["checks"] = [{"check": n, "passed": ok} for n, ok in results]
    (EVID / "e2e_evidence.json").write_text(json.dumps(evidence, indent=1, ensure_ascii=False, default=str))
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (f"; FAILED: {failed}" if failed else " - every stage proven"))
    sys.exit(1 if failed else 0)


def docker(*args, check_=False):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check_)




def main():
    # Everything that RUNS lives here: the paper service extracts uploads in an isolated forkserver process (Workstream S), which
    # imports this file again as __mp_main__ - module level must only define things.
    atexit.register(lambda: docker("rm", "-f", PW_NAME))           # the browser container never outlives the run
    # ======================================================================================================== 0 isolation evidence
    print("0 isolation of the disposable database")
    u = urlsplit(DB.replace("postgresql+psycopg", "postgresql"))
    live_before = docker("ps", "--format", "{{.Names}} {{.ID}}").stdout.split("\n")
    inspect = json.loads(docker("inspect", "pipeline-pg").stdout or "[]")
    created = datetime.fromisoformat(inspect[0]["Created"][:26] + "+00:00") if inspect else None    # Docker: ...ss.nnnnnnnnnZ
    ports = inspect[0]["HostConfig"]["PortBindings"] if inspect else {}
    engine = create_engine(DB)
    with engine.connect() as x:
        server = x.execute(text("select current_database(), inet_server_port(), split_part(version(), ' ', 2)")).one()
        extensions = [r[0] for r in x.execute(text("select extname from pg_extension"))]
        foreign = x.execute(text("select count(*) from pg_foreign_server")).scalar()
        versions = {t: x.execute(text(f'select version_num from "{t}"')).scalars().all()
                    for t in x.execute(text("select table_name from information_schema.tables where table_schema = 'public' and table_name like '%alembic_version'")).scalars()}
        recognition_col = x.execute(text("select data_type from information_schema.columns where table_name = 'paper_uploads' "
                                         "and column_name = 'recognition'")).scalar()
        dob_col = x.execute(text("select data_type, is_nullable, column_default from information_schema.columns "
                                 "where table_name = 'students' and column_name = 'date_of_birth'")).one_or_none()
    student_hosts = {}
    # only the real service processes ("<python> -m uvicorn app.main:app ... --port 181NN"), never a shell whose text mentions them
    for pid in subprocess.run(["pgrep", "-f", r"python[0-9.]* -m uvicorn app\.main:app --host 127\.0\.0\.1 --port 181[0-9][0-9]"],
                              capture_output=True, text=True,
                              check=False).stdout.split():
        try:
            env = dict(kv.split("=", 1) for kv in Path(f"/proc/{pid}/environ").read_bytes().decode(errors="ignore").split("\0") if "=" in kv)
            port = re.search(r"--port (\d+)", Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()).group(1)
            student_hosts[port] = urlsplit(env.get("DATABASE_URL", "").replace("postgresql+psycopg", "postgresql")).hostname
        except (OSError, AttributeError, ValueError):
            pass
    neon_in_env = sorted(k for k, v in os.environ.items() if "neon.tech" in v)
    iso = {
        "database_url_host_port_db": [u.hostname, u.port, u.path.lstrip("/")], "container": "pipeline-pg",
        "container_image": inspect[0]["Config"]["Image"] if inspect else None, "container_created_utc": created.isoformat() if created else None,
        "container_age_seconds": (datetime.now(UTC) - created).total_seconds() if created else None,
        "container_port_binding": ports, "container_tmpfs": inspect[0]["HostConfig"].get("Tmpfs") if inspect else None,
        "server": {"database": server[0], "port_inside_container": server[1], "postgres_version": server[2]},
        "extensions": extensions, "foreign_servers": foreign, "alembic_versions": versions,
        "paper_uploads.recognition (migration 0006)": recognition_col, "student_service_db_hosts": student_hosts,
        "students.date_of_birth (auth migration 0003)": list(dob_col) if dob_col else None,
        "env_vars_mentioning_neon_in_this_process": neon_in_env,
    }
    evidence["isolation"] = iso
    print(json.dumps(iso, indent=1, default=str))
    check("0", "the database is the throwaway container created for this run (127.0.0.1, tmpfs, < 15 min old, postgres:18)",
          u.hostname == "127.0.0.1" and u.path == "/pipeline_verify" and inspect and iso["container_age_seconds"] < 900
          and "/var/lib/postgresql" in (iso["container_tmpfs"] or {}) and all(b["HostIp"] == "127.0.0.1" for v in ports.values() for b in v)
          and iso["container_image"] == "postgres:18", iso)
    check("0", "no link to Neon or any other database: no foreign servers, no dblink/postgres_fdw, no Neon URL in the environment",
          foreign == 0 and not {"dblink", "postgres_fdw"} & set(extensions) and not neon_in_env, iso)
    check("0", "the six Student services use the same local database",
          len(student_hosts) == 6 and set(student_hosts.values()) == {"127.0.0.1"}, student_hosts)
    check("0", "migration 0006 (paper_uploads.recognition) exists only in this disposable database",
          recognition_col == "json" or recognition_col == "jsonb", recognition_col)
    heads = {"alembic_version": ["0003_students_date_of_birth"], "inst_paper_alembic_version": ["0006_upload_recognition"]}
    institute = {f"inst_{s}_alembic_version" for s in ("identity", "organization", "academics", "enrollment", "paper", "exam", "operations")}
    student = {"m3_alembic_version", "m4_alembic_version", "m5_alembic_version", "m6_alembic_version", "m9_alembic_version"}
    check("0", "A: every migration chain applied - 7 Institute chains, 5 Student chains, and the authentication chain at 0003 "
               "(paper at 0006)", institute | student | {"alembic_version"} <= set(versions)
          and all(len(versions[v]) == 1 for v in institute | student) and all(versions[k] == v for k, v in heads.items()), versions)
    check("0", "A: students.date_of_birth exists as a nullable DATE with no default (migration 0003_students_date_of_birth)",
          dob_col is not None and tuple(dob_col) == ("date", "YES", None), dob_col)

    # ======================================================================================================== in-process Institute
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


    def login(c, code, password=PW):
        r = c.post(f"{API}/auth/login", json={"staff_code": code, "password": password}, headers=ORIGIN)
        body = r.json()
        if body.get("status") == "MFA_REQUIRED":
            otp = sender.outbox[-1].body.split("code is ")[1][:6]
            body = c.post(f"{API}/auth/mfa/verify", json={"challenge_id": body["challenge_id"], "otp": otp}, headers=ORIGIN).json()
        return {**ORIGIN, "X-CSRF-Token": body["me"]["csrf_token"]}


    def upload(c, h, branch, subject, data, name, ctype, key=None, title="Paper", **form):
        files = {"file": (name, data, ctype)}
        if key is not None:
            files["answer_key"] = ("key.csv", key.encode(), "text/csv")
        fields = {"title": title, **{k: (json.dumps(v) if isinstance(v, dict) else v) for k, v in form.items()}}
        return c.post(f"{API}/branches/{branch}/subjects/{subject}/papers/upload", files=files, data=fields, headers=h)


    # ======================================================================================================== test papers
    HEAD = "question,option_a,option_b,option_c,option_d,marks,negative_marks,explanation,topic\n"


    def csv_paper(rows):
        lines = [",".join('"' + str(x).replace('"', '""') + '"' for x in (*r[:5], 4, 1, "", r[5])) for r in rows]
        return (HEAD + "\n".join(lines) + "\n").encode()


    MATH = [  # stem, A, B, C, D, topic          (answer key below; every correct option is plain text so the browser can pick it)
        ("Simplify (x² − 1)/(x − 1) for x ≠ 1.", "x + 1", "x − 1", "x²", "1", "Algebra"),
        ("If A = [1 2; 3 4], find det(A).", "−2", "2", "10", "−10", "Matrices"),
        ("The derivative of x³ is", "3x²", "x²", "3x", "x³/3", "Calculus"),
        ("Find the area: evaluate ∫₀^π sin x dx.", "2", "0", "1", "π", "Calculus"),
        ("Compute e^3t when t = 0.", "1", "0", "3", "e", "Exponentials"),                 # ambiguous power -> provider review
    ]
    MATH_KEY = "question,answer\n1,A\n2,A\n3,A\n4,A\n5,A\n"
    CHEM = [
        ("The pH of 10⁻³ M HCl at 25 °C is", "3", "11", "7", "1", "Acids"),
        ("The charge on the sulfate ion SO₄²⁻ is", "−2", "+2", "−1", "0", "Ions"),
        ("In 2H₂ + O₂ → 2H₂O the number of O atoms on each side is", "2", "1", "4", "3", "Equations"),
        ("A rate constant k = 5 M⁻¹ s⁻¹ indicates a reaction of order", "2", "1", "0", "3", "Kinetics"),
        ("If c = 0.1M, the pOH of the NaOH solution is", "1", "13", "0.1", "7", "Bases"),    # glued molar M -> provider review
    ]
    CHEM_KEY = "question,answer\n1,A\n2,A\n3,A\n4,A\n5,A\n"
    MW = 'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'


    def wr(t):
        return f'<w:r><w:t xml:space="preserve">{t}</w:t></w:r>'


    def mr(t, sty=None):
        pr = f'<m:rPr><m:sty m:val="{sty}"/></m:rPr>' if sty else ""
        return f"<m:r>{pr}<m:t>{t}</m:t></m:r>"


    BOLD_LAW = f"<m:oMath {MW}>" + mr("F", "bi") + mr("=m") + mr("a", "bi") + "</m:oMath>"   # Word: bold italic F and a (vectors)
    PHYS = [  # (stem paragraph runs, options, topic); answers in the paper
        (wr("Newton's second law in vector form is ") + BOLD_LAW + wr(". Here m is the"), ["mass", "momentum", "metre", "mole"]),
        (wr("The dimensional formula of force is"), ["[M L T⁻²]", "[M L² T⁻²]", "[M L T⁻¹]", "[M L⁻¹ T⁻²]"]),
        (wr("Light of wavelength 5 × 10⁻⁷ m has frequency (c = 3 × 10⁸ m s⁻¹)"), ["6 × 10¹⁴ Hz", "1.5 × 10¹⁵ Hz", "6 × 10¹⁵ Hz", "1.5 × 10¹⁴ Hz"]),
        (wr("If R = 4 kΩ and I = 2 mA, then V = IR equals"), ["8 V", "2 V", "0.5 V", "16 V"]),
        (wr("The relation J s⁻¹ = W defines the"), ["watt", "joule", "newton", "pascal"]),       # W: watt or work? -> provider review
    ]
    body = ""
    for n, (runs, opts) in enumerate(PHYS, start=1):
        body += f"<w:p>{wr(f'{n}. ')}{runs}</w:p>" + "".join(f"<w:p>{wr(f'{L}) {o}')}</w:p>" for L, o in zip("ABCD", opts))
        body += "".join(f"<w:p>{wr(x)}</w:p>" for x in ("Answer: A", "Marks: 4", "Negative: 1"))
    PHYS_DOCX = word(body)
    DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    evidence["papers"] = {"Mathematics (CSV)": [list(r) for r in MATH], "Physics (DOCX, native equation)": [
        [re.sub(r"<[^>]+>", "", r).replace("F=ma", "𝑭=m𝒂 [Word equation, bold italic]"), o] for r, o in PHYS],
        "Chemistry (CSV)": [list(r) for r in CHEM]}

    # what recognition must decide (field -> outcome), and the provider's decisions for the flagged fields
    EXPECT = {
        "math": {"auto": {"1:question": r"\frac{x^{2} - 1}{x - 1}", "2:question": r"\begin{bmatrix}1 & 2 \\ 3 & 4\end{bmatrix}",
                          "3:question": r"x^{3}", "3:option_a": r"3x^{2}", "3:option_d": r"\frac{x^{3}}{3}",
                          "4:question": r"\int_{0}^{\pi} \sin x\,dx", "4:option_d": r"\pi"},
                 "review": {"5:question": "AMBIGUOUS_POWER"},
                 "decisions": {"5:question": {"action": "edit", "text": "Compute e^(3t) when t = 0."}},
                 "after": {"5:question": r"e^{3t}"}},
        "phys": {"auto": {"1:question": r"\boldsymbol{F}=m\boldsymbol{a}", "2:option_a": r"[\mathrm{M}\,\mathrm{L}\,\mathrm{T}^{-2}]",
                          "3:question": r"5 \times {10}^{-7}\,\mathrm{m}", "3:option_a": r"6 \times {10}^{14}\,\mathrm{Hz}",
                          "4:question": r"R = 4\,\mathrm{k}\mathrm{\Omega}", "4:option_a": r"8\,\mathrm{V}"},
                 "review": {"5:question": "NOT_INDEPENDENTLY_VERIFIED"},
                 "decisions": {"5:question": {"action": "keep_original"}},
                 "after": {"5:question": None}},
        "chem": {"auto": {"1:question": r"{10}^{-3}\,\mathrm{M}", "2:question": r"\mathrm{SO_{4}^{2-}}",
                          "3:question": r"2\mathrm{H_{2}} + \mathrm{O_{2}} \rightarrow 2\mathrm{H_{2}O}",
                          "4:question": r"k = 5\,\mathrm{M}^{-1}\,\mathrm{s}^{-1}"},
                 "review": {"5:question": "NOT_INDEPENDENTLY_VERIFIED"},
                 "decisions": {"5:question": {"action": "edit", "text": "If c = 0.1 M, the pOH of the NaOH solution is"}},
                 "after": {"5:question": r"c = 0.1\,\mathrm{M}"}},
    }


    def fields_of(preview):
        return {f"{i}:{f}": v for i, q in enumerate(preview["questions"], start=1) for f, v in q["fields"].items()}


    with TestClient(app, base_url="http://localhost") as c:
        # ==================================================================================================== 1 set-up
        print("1 set-up through the Institute API")
        h = login(c, "STF000001")
        P = c.post(f"{API}/providers", json={"name": "Science Academy", "email": "office@science-academy.example.com"}, headers=h).json()["provider_id"]
        B = c.post(f"{API}/providers/{P}/branches", json={"name": "Main", "city": "Pune", "state": "MH"}, headers=h).json()["branch_id"]
        SUBJ = {k: c.post(f"{API}/subjects", json={"code": code, "name": name}, headers=h).json()["subject_id"]
                for k, code, name in (("math", "MATHS", "Mathematics"), ("phys", "PHYS", "Physics"), ("chem", "CHEM", "Chemistry"),
                                      ("bio", "BIO", "Biology"))}
        for s in SUBJ.values():
            c.put(f"{API}/providers/{P}/subjects/{s}", json={}, headers=h)
        # the explicit subject_id -> category mapping (RECOGNITION_SUBJECT_CATEGORIES), as the development configuration now has it
        settings.recognition_subject_categories = {SUBJ["math"]: "MATHEMATICS", SUBJ["phys"]: "PHYSICS", SUBJ["chem"]: "CHEMISTRY",
                                                   SUBJ["bio"]: "OTHER"}
        BT = c.post(f"{API}/branches/{B}/batches", json={"name": "JEE 2027"}, headers=h).json()["batch_id"]
        r = c.post(f"{API}/staff", json={"full_name": "Meera Teacher", "email": "meera@science-academy.example.com",
                                         "assignment": {"role": "TEACHER", "provider_id": P}}, headers=h)
        T = r.json()
        token = re.search(r"token=([A-Za-z0-9_\-]+)", sender.outbox[-1].body).group(1)
        check("1", "teacher invited and verified", c.post(f"{API}/auth/verify-email", json={"token": token, "new_password": PW}, headers=ORIGIN).status_code == 200, r.text)
        h = login(c, "STF000001")
        for s in SUBJ.values():
            c.post(f"{API}/teaching", json={"staff_id": T["staff_id"], "branch_id": B, "subject_id": s, "batch_id": BT}, headers=h)
        r = c.post(f"{API}/branches/{B}/enrollments", json={"academic": ACAD, "new_student": {"first_name": "Kavya", "last_name": "Rao",
                                                                                               "email": "kavya@family.example.com"}, "batch_ids": [BT]}, headers=h)
        check("1", "student identity created by the REAL Student service and enrolled in the batch", r.status_code == 201, r.text)
        SID = r.json()["student_id"]
        with factory() as db:
            sync = reference_sync.run_once(db, full=True)
        check("1", "enrollment projected to the Student database", not sync.get("rejected"), sync)

        # ==================================================================================================== 2 recognition + review
        ht = login(c, T["staff_code"])
        PV, stats = {}, {}
        for key, subject, data, name, ctype, akey, title in (
                ("math", SUBJ["math"], csv_paper(MATH), "maths.csv", "text/csv", MATH_KEY, "Science E2E Mathematics"),
                ("phys", SUBJ["phys"], PHYS_DOCX, "physics.docx", DOCX, None, "Science E2E Physics"),
                ("chem", SUBJ["chem"], csv_paper(CHEM), "chemistry.csv", "text/csv", CHEM_KEY, "Science E2E Chemistry")):
            print(f"2 {key}: preview, review, import")
            e = EXPECT[key]
            r = upload(c, ht, B, subject, data, name, ctype, akey, title, preview="true")
            check("2", f"{key}: provider preview (200, nothing created)", r.status_code == 200, r.text)
            pre = r.json()
            f = fields_of(pre)
            outcomes = {k: v["outcome"] for k, v in f.items()}
            stats[key] = {o: list(outcomes.values()).count(o) for o in ("CONVERTED", "PLAIN_TEXT", "NEEDS_REVIEW", "EXTRACTION_ERROR")}
            evidence.setdefault("preview", {})[key] = {k: {"original": v["original"], "outcome": v["outcome"], "reasons": v["reasons"],
                                                            "text": v["text"]} for k, v in f.items()}
            check("2", f"{key}: automatic conversions as expected ({len(e['auto'])} fields)",
                  all(outcomes.get(k) == "CONVERTED" and want in f[k]["text"] for k, want in e["auto"].items()),
                  {k: (outcomes.get(k), f.get(k, {}).get("text")) for k in e["auto"]})
            check("2", f"{key}: ambiguous field sent to provider review, nothing else blocks",
                  {k: v for k, v in outcomes.items() if v in ("NEEDS_REVIEW", "EXTRACTION_ERROR")} == {k: "NEEDS_REVIEW" for k in e["review"]}
                  and all(code in f[k]["reasons"] for k, code in e["review"].items()), {k: (v, f[k]["reasons"]) for k, v in outcomes.items() if v != "CONVERTED"})
            before = sql("select count(*) from paper_versions")[0][0]
            r = upload(c, ht, B, subject, data, name, ctype, akey, title)
            check("2", f"{key}: the import is refused while the flagged field is unresolved; nothing stored",
                  r.status_code >= 400 and sql("select count(*) from paper_versions")[0][0] == before, r.text)
            r = upload(c, ht, B, subject, data, name, ctype, akey, title, resolutions=e["decisions"], review_token=pre["review_token"])
            check("2", f"{key}: provider decisions applied ({', '.join(d['action'] for d in e['decisions'].values())}) and the paper imported",
                  r.status_code == 201, r.text)
            PV[key] = r.json()["paper_version_id"]
            qs = c.get(f"{API}/paper-versions/{PV[key]}", headers=ht).json()["questions"]
            stored = {f"{i}:{fld}": q["stem" if fld == "question" else fld] for i, q in enumerate(qs, start=1)
                      for fld in ("question", "option_a", "option_d")}
            check("2", f"{key}: the stored questions carry the accepted LaTeX",
                  all(want in (stored.get(k) or "") for k, want in e["auto"].items() if k in stored), stored)
            for k, want in e["after"].items():
                if want is None:
                    check("2", f"{key}: '{k}' kept exactly as typed (provider decision)", stored[k] == f[k]["original"], (stored[k], f[k]["original"]))
                else:
                    check("2", f"{key}: the provider's correction of '{k}' was recognised and stored", want in stored[k], stored[k])
            rec = sql("select recognition from paper_uploads where paper_version_id::text = :v", v=PV[key])
            rec = rec[0][0] if rec else None
            rec = json.loads(rec) if isinstance(rec, str) else rec
            check("2", f"{key}: recognition record stored (engine 2.3.0, subject category, provider decision with who made it)",
                  rec and rec.get("engine_version") == "2.3.0" and rec.get("domain") == {"math": "mathematics", "phys": "physics", "chem": "chemistry"}[key]
                  and ('"decision": "keep_original"' if key == "phys" else '"decision": "edit"') in json.dumps(rec) and rec.get("decided_by"),
                  rec and {k: rec.get(k) for k in ("engine_version", "domain", "decided_by")})
        evidence["recognition_counts"] = stats

        print("2 Biology (OTHER): the same molar text is not given chemistry conventions")
        r = upload(c, ht, B, SUBJ["bio"], csv_paper([CHEM[0]]), "bio.csv", "text/csv", "question,answer\n1,A\n", "Biology probe", preview="true")
        bio = fields_of(r.json())["1:question"] if r.status_code == 200 else {}
        check("2", "Biology (OTHER): '10⁻³ M' is NOT converted as the molar unit - provider review instead",
              bio.get("outcome") == "NEEDS_REVIEW" and "mathrm{M}" not in bio.get("text", ""), bio)

        # ==================================================================================================== 3 approval, exam, delivery
        print("3 approval, exam, delivery")
        for key, version in PV.items():
            check("3", f"{key}: approved and published", c.post(f"{API}/paper-versions/{version}/approve", json={"note": "checked"}, headers=ht).status_code == 200
                  and c.post(f"{API}/paper-versions/{version}/publish", json={"note": None}, headers=ht).json().get("status") == "PUBLISHED")
        h = login(c, "STF000001")
        start = datetime.now(UTC) + timedelta(seconds=20)
        r = c.post(f"{API}/branches/{B}/exam-plans", json={
            "name": "Science E2E", "duration_minutes": 30, "start_at": start.isoformat(), "end_at": (start + timedelta(minutes=45)).isoformat(),
            "audience_type": "BATCHES", "batch_ids": [BT], "marking_mode": "UNIFORM", "marks_correct": 4, "marks_wrong": 1,
            "slots": [{"subject_id": SUBJ[k], "paper_version_id": PV[k]} for k in ("math", "phys", "chem")]}, headers=h)
        check("3", "one exam plan with the three subject papers (+4 / -1)", r.status_code == 201, r.text)
        job = c.post(f"{API}/exam-plans/{r.json()['plan_id']}/generate", json={}, headers=h).json()
        ex = c.get(f"{API}/exams/{job.get('exam_id')}", headers=h).json()
        check("3", "exam generated and published to the student", job.get("status") == "SUCCEEDED" and ex["status"] == "PUBLISHED"
              and ex["assigned_students"] == 1, (job, ex))
        with factory() as db:
            sent = outbox.deliver_due(db)
        TEST_ID = ex["exam_version_id"]
        bank = sql("""select qv.stem, qv.option_a, qv.option_b, qv.option_c, qv.option_d from paper_version_questions pvq
                      join question_versions qv using (question_version_id) where pvq.paper_version_id::text in (:a, :b, :c)""",
                   a=PV["math"], b=PV["phys"], c=PV["chem"])
        delivered = sql("select question_text, option_a, option_b, option_c, option_d from questions where test_id::text = :t", t=TEST_ID)
        check("3", "delivered over HTTP to the real Student test service: 15 questions, text identical to the bank (LaTeX intact)",
              sent.get("sent") == 1 and len(delivered) == 15 and sorted(map(tuple, delivered)) == sorted(map(tuple, bank)), (sent, len(delivered)))
        evidence["delivered"] = [list(r) for r in delivered]

        # ==================================================================================================== 4 Student portal in Chromium
        print("4 the Student portal in a real browser")
        s = httpx.Client(timeout=20, headers={"Origin": "http://localhost"})
        s.post(STU["auth"] + "/auth/forgot-password", json={"email": "kavya@family.example.com"})
        for _ in range(20):
            found = re.findall(r"reset-password\?token=([A-Za-z0-9_\-]+)", AUTH_LOG.read_text())
            if found:
                break
            time.sleep(0.5)
        check("4", "student password set via the (dev console) reset link", s.post(STU["auth"] + "/auth/reset-password",
                                                                                   json={"token": found[-1], "new_password": PW}).status_code == 200)

        # the portal also calls the profile and ranking services, which the harness does not start: run them here on the same
        # disposable database and Redis (they have no migrations of their own; they read existing tables)
        extra = {}
        for svc, port in (("student-profile-service", 18103), ("student-ranking-service", 18109)):
            env = {"PATH": os.environ["PATH"], "DATABASE_URL": DB, "REDIS_URL": f"redis://127.0.0.1:{os.environ.get('PIPELINE_RPORT', '56380')}",
                   "CORS_ORIGINS": "http://localhost", "APP_ENV": "dev", "SESSION_COOKIE_SECURE": "false"}
            proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                                    cwd=ROOT / "student-backend" / svc, env=env, stderr=subprocess.STDOUT,
                                    stdout=open(WORK / f"{svc}.log", "w"))  # noqa: SIM115 - the log stays open while the service runs
            atexit.register(proc.terminate)
            extra[svc] = (proc, port, urlsplit(env["DATABASE_URL"].replace("postgresql+psycopg", "postgresql")).hostname)
        for svc, (proc, port, _) in extra.items():
            for _ in range(40):
                try:
                    if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).status_code < 500:
                        break
                except httpx.HTTPError:
                    time.sleep(0.5)
        evidence["isolation"]["profile_ranking_db_hosts"] = {s: v[2] for s, v in extra.items()}
        check("4", "profile and ranking services started on the same disposable database (127.0.0.1)",
              all(v[0].poll() is None and v[2] == "127.0.0.1" for v in extra.values()), {s: (v[0].poll(), v[2]) for s, v in extra.items()})
        SDIST = WORK / "sdist"
        SVC = {"auth": STU["auth"], "enrollment": STU["enroll"], "test": STU["test"], "attempt": STU["attempt"], "result": STU["result"],
               "notification": STU["notify"], "profile": "http://127.0.0.1:18103", "ranking": "http://127.0.0.1:18109"}
        senv = {**os.environ, **{f"VITE_{k.upper()}_API_URL": f"/s/{k}" for k in SVC}}
        sbuild = subprocess.run(["npx", "vite", "build", "--outDir", str(SDIST), "--emptyOutDir"], cwd=ROOT / "student-frontend", env=senv,
                                capture_output=True, text=True, check=False)
        check("4", "production build of the Student portal (this working tree; proxied same-origin APIs)",
              sbuild.returncode == 0 and (SDIST / "index.html").exists(), sbuild.stderr[-500:])
        # the production Content-Security-Policy, from the same function the image build uses (same-origin APIs: no extra origins)
        CSP = subprocess.run(["node", "--input-type=module", "-e",
                              "import { contentSecurityPolicy } from './scripts/render-nginx-conf.mjs'; console.log(contentSecurityPolicy([]))"],
                             cwd=ROOT / "student-frontend", capture_output=True, text=True, check=True).stdout.strip()
        evidence["csp"] = CSP
        check("4", "production CSP obtained (no unsafe-eval, no inline scripts)", "script-src 'self'" in CSP and "unsafe-eval" not in CSP, CSP)

        KINDS = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".html": "text/html", ".png": "image/png",
                 ".ico": "image/x-icon", ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf"}
        SEC = [(b"content-security-policy", CSP.encode()), (b"x-content-type-options", b"nosniff")]

        async def static(scope, send):
            rel = scope["path"].lstrip("/")
            target = SDIST / rel if rel and (SDIST / rel).is_file() else SDIST / "index.html"
            body_ = target.read_bytes()
            await send({"type": "http.response.start", "status": 200, "headers": [
                (b"content-type", KINDS.get(target.suffix, "application/octet-stream").encode()), (b"content-length", str(len(body_)).encode()), *SEC]})
            await send({"type": "http.response.body", "body": body_})

        proxy_client = httpx.AsyncClient(timeout=60)

        async def student_site(scope, receive, send):
            if scope["type"] != "http":
                return
            m = re.match(r"^/s/([a-z]+)(/.*)?$", scope["path"])
            if not m:
                return await static(scope, send)
            payload = b""
            while True:
                msg = await receive()
                payload += msg.get("body", b"")
                if not msg.get("more_body"):
                    break
            hdrs = [(k.decode(), v.decode()) for k, v in scope["headers"] if k.lower() not in (b"host", b"origin", b"content-length", b"referer")]
            url = SVC[m.group(1)] + (m.group(2) or "/") + (("?" + scope["query_string"].decode()) if scope["query_string"] else "")
            rr = await proxy_client.request(scope["method"], url, headers=hdrs + [("origin", "http://localhost")], content=payload)
            out = [(k.encode(), v.encode()) for k, v in rr.headers.multi_items() if k.lower() not in (
                "content-length", "transfer-encoding", "content-encoding", "access-control-allow-origin", "access-control-allow-credentials")]
            await send({"type": "http.response.start", "status": rr.status_code, "headers": out + [(b"content-length", str(len(rr.content)).encode())]})
            await send({"type": "http.response.body", "body": rr.content})

        # Docker Desktop: the browser container reaches this process through host-gateway, so it listens on all interfaces for the
        # duration of the run only (test data only), as the other disposable browser drivers do
        bridge = "127.0.0.1"
        srv = uvicorn.Server(uvicorn.Config(student_site, host="0.0.0.0", port=SPORT, log_level="warning", lifespan="off"))
        threading.Thread(target=srv.run, daemon=True).start()
        for _ in range(40):
            try:
                if httpx.get(f"http://{bridge}:{SPORT}/login", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.25)
        head = httpx.get(f"http://{bridge}:{SPORT}/login", timeout=5)
        check("4", "the portal is served with the production CSP header", head.headers.get("content-security-policy") == CSP, dict(head.headers))

        shots = WORK / "browser"
        shots.mkdir(exist_ok=True)
        docker("rm", "-f", PW_NAME)
        # the image ships the browsers, not the Python package: it comes from a host Python with playwright==1.48 (PW_PYTHON), mounted
        # read-only - the same arrangement as the other disposable browser drivers
        pkgs = subprocess.run([os.environ.get("PW_PYTHON", "python3"), "-c",
                               "import os, playwright; print(os.path.dirname(os.path.dirname(playwright.__file__)))"],
                              capture_output=True, text=True, check=False).stdout.strip()
        check("4", "playwright==1.48 package available for the browser script (PW_PYTHON)", bool(pkgs), "set PW_PYTHON")
        run = docker("run", "-d", "--name", PW_NAME, "--add-host", "portal.host:host-gateway", "-v", f"{ROOT / 'tests' / 'e2e'}:/e2e:ro",
                     "-v", f"{pkgs}:/pwpkgs:ro", "-e", "PYTHONPATH=/pwpkgs",
                     "-v", f"{shots}:/work", "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp", PW_IMAGE, "sleep", "1800")
        check("4", "browser container started from the existing Playwright image (no build)", run.returncode == 0, run.stderr)
        FWD = ("import asyncio\nasync def pipe(r, w):\n    try:\n        while data := await r.read(65536):\n            w.write(data); await w.drain()\n"
               "    finally:\n        w.close()\nasync def h(r, w):\n    ur, uw = await asyncio.open_connection('portal.host', %d)\n"
               "    await asyncio.gather(pipe(r, uw), pipe(ur, w), return_exceptions=True)\n"
               "async def main():\n    s = await asyncio.start_server(h, '127.0.0.1', 5175)\n    await s.serve_forever()\nasyncio.run(main())\n"
               ).replace("%d", str(SPORT))
        docker("exec", "-d", PW_NAME, "python3", "-c", FWD, check_=True)          # the portal at http://localhost:5175 inside the container
        time.sleep(2)
        params = {"sui": "http://localhost:5175", "student": SID, "password": PW, "shots": "/work", "exam": "Science E2E", "test_id": TEST_ID,
                  # keyword in the question's plain text -> the option to choose; others are left unanswered on purpose
                  "answers": {"Simplify": "A", ", find": "A", "Find the area": "A", "Compute": "A", "vector form": "A", "pOH": "A",
                              "pH of": "A", "charge on the sulfate": "B"},          # the sulfate answer is deliberately wrong
                  "expect_total": 15}
        (shots / "params.json").write_text(json.dumps(params))
        br = subprocess.run(["docker", "exec", PW_NAME, "python3", "/e2e/science_ui_browser.py", "/work/params.json"], capture_output=True, text=True,
                            timeout=900, check=False)
        print(br.stdout[-6000:], br.stderr[-3000:])
        report = json.loads((shots / "browser_report.json").read_text()) if (shots / "browser_report.json").exists() else {}
        evidence["browser"] = report
        for f_ in shots.glob("*"):                                       # evidence first, whatever the outcome
            shutil.copy2(f_, EVID / f_.name)
        check("4", "browser journey: sign-in, attempt, rendering, answers, submission, result", br.returncode == 0, br.stdout[-1500:])

        # ==================================================================================================== results agree
        res = sql("select obtained_marks, correct_answers, incorrect_answers, unanswered_questions from test_results "
                  "where test_id::text = :t and student_id = :s", t=TEST_ID, s=SID)
        evidence["result_row"] = [list(r) for r in res]
        check("4", "the stored result: 7 correct, 1 wrong, 7 unanswered = 7×4 − 1 = 27 of 60",
              res and float(res[0][0]) == 27 and tuple(res[0][1:]) == (7, 1, 7), res)
        h = login(c, "STF000001")
        c.post(f"{API}/exams/{ex['exam_id']}/sync", headers=h)
        out = c.get(f"{API}/exams/{ex['exam_id']}/results", headers=h).json()
        check("4", "Institute staff see the same result, read from the Student result service",
              [(r["student_id"], float(r["obtained_marks"])) for r in out.get("results", [])] == [(SID, 27.0)], out)

    # copy the evidence out of the work folder (which the harness deletes)
    for f in shots.glob("*"):
        shutil.copy2(f, EVID / f.name)
    live_after = docker("ps", "--format", "{{.Names}} {{.ID}}").stdout.split("\n")
    evidence["live_stack_untouched"] = sorted(x for x in live_before if x and not x.startswith(("pipeline-", PW_NAME)))
    check("5", "the running development stack was not touched (same containers before and after, excluding this run's own)",
          sorted(x for x in live_before if x and not x.startswith(("pipeline-", PW_NAME)))
          == sorted(x for x in live_after if x and not x.startswith(("pipeline-", PW_NAME))))
    summary()


if __name__ == "__main__":
    main()
