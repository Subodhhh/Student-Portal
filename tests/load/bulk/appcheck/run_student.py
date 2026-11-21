"""Orders the Student route checks: picks the busiest student, opens one of their upcoming tests in the DISPOSABLE fixture, then runs each
service in its own virtualenv. python run_student.py <dbname> <scratch dir>"""
import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[4]
dbname, scratch = sys.argv[1], Path(sys.argv[2])
PORT = 55439
conn = psycopg.connect(f"host=127.0.0.1 port={PORT} user=lt dbname={dbname}", autocommit=True)
student = conn.execute("""select a.student_id from test_attempts a join test_audiences f on f.student_id=a.student_id join tests t on t.test_id=f.test_id
                          where t.start_at > now() and t.status='ACTIVE' group by 1 order by count(*) desc limit 1""").fetchone()[0]
test, count = conn.execute("""select t.test_id::text, t.total_questions from test_audiences f join tests t using (test_id)
                              where f.student_id=%s and t.start_at > now() and t.status='ACTIVE' order by t.start_at limit 1""", (student,)).fetchone()
conn.execute("update tests set start_at = now() - interval '5 minutes', end_at = now() + interval '3 hours' where test_id = %s", (test,))
state = scratch / "appcheck-student.json"
state.write_text(json.dumps({"student": student, "live_test": test, "live_questions": count}))
env = dict(os.environ, DATABASE_URL=f"postgresql+psycopg://lt@127.0.0.1:{PORT}/{dbname}", REDIS_URL="redis://127.0.0.1:1/0", CORS_ORIGINS="http://x",
           SESSION_COOKIE_SECURE="false", RESEND_API_KEY="", EMAIL_FROM="", FRONTEND_RESET_URL="http://x/r", PYTHONPATH=".",
           ATTEMPT_EXPIRY_SWEEP_SECONDS="0", AUTO_EVALUATION_INTERVAL_SECONDS="0")
failed = 0
for service in ("student-authentication-service", "student-profile-service", "student-enrollment-service", "student-notification-service",
                "student-test-service", "student-attempt-service", "student-result-service", "student-ranking-service"):
    py = Path.home() / ".cache/student-service-test-venvs" / service / "bin/python"
    r = subprocess.run([str(py), str(Path(__file__).with_name("student_routes.py")), service, str(state)], cwd=ROOT / "student-backend" / service, env=env,
                       capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip()[-1500:])
    failed += r.returncode != 0
sys.exit(1 if failed else 0)
