"""Read-only validation of a loaded dataset: each check is a query counting violations (0 = pass). Foreign keys, unique and check
constraints were enforced by PostgreSQL during COPY; these checks cover what the schema cannot see, above all the links between the two
databases and the application's own rules."""
import json
from pathlib import Path

from .tables import PLAN
from .world import SUBJECT_CODES, World

MD5 = "select md5(coalesce(string_agg(x, '|' order by x collate \"C\"), '')) from ({}) q"
BOTH = {   # projection pushed by reference_sync, then rows linking delivered tests to the exams they came from
    "providers projection": ("select concat_ws(',', provider_id, name, lower(email), status) x from providers",) * 2,
    "branches projection": ("select concat_ws(',', branch_id, provider_id, name, city, state, status) x from branches",) * 2,
    "batches projection": ("select concat_ws(',', batch_id, branch_id, name, status, start_date, end_date) x from batches",) * 2,
    "enrollments projection": ("select concat_ws(',', enrollment_id, provider_id, branch_id, student_id, status) x from enrollments",) * 2,
    "enrollment_batches projection": ("select concat_ws(',', enrollment_batch_id, enrollment_id, batch_id) x from enrollment_batches",) * 2,
    "tests match exam versions": (
        "select concat_ws(',', ev.exam_version_id, ev.total_questions, ev.total_marks::int, ev.duration_minutes, to_char(ev.start_at at time zone 'UTC', 'YYYY-MM-DD HH24:MI:SS'),"
        " case e.status when 'CANCELLED' then 'INACTIVE' else 'ACTIVE' end) x from exam_versions ev join exams e using (exam_id)",
        "select concat_ws(',', test_id, total_questions, total_marks, duration_minutes, to_char(start_at, 'YYYY-MM-DD HH24:MI:SS'), status) x from tests"),
    "audiences match assignments": ("select exam_version_id || student_id x from student_exam_assignments",
                                    "select test_id || student_id x from test_audiences"),
    "attempts match COMPLETED": ("select exam_version_id || student_id x from student_exam_assignments where status = 'COMPLETED'",
                                 "select test_id || student_id x from test_attempts"),
}
STUDENT = {
    "enrollment student exists": "select count(*) from enrollments e left join students s using (student_id) where s.student_id is null",
    "audience test exists": "select count(*) from test_audiences a left join tests t using (test_id) where t.test_id is null",
    "audience are STUDENT rows": "select count(*) from test_audiences where audience_type <> 'STUDENT' or student_id is null",
    "tests hold their questions": "select count(*) from tests t where total_questions <> (select count(*) from questions q where q.test_id = t.test_id)",
    "question orders are 1..n": "select count(*) from (select test_id from questions group by 1 having min(question_order) <> 1 or max(question_order) <> count(*)) x",
    "attempt test exists": "select count(*) from test_attempts a left join tests t using (test_id) where t.test_id is null",
    "attempt has audience": "select count(*) from test_attempts a where not exists (select 1 from test_audiences t where t.test_id = a.test_id and t.student_id = a.student_id)",
    "attempt inside its window": "select count(*) from test_attempts a join tests t using (test_id) where a.started_at < t.start_at at time zone current_setting('TimeZone') or a.started_at > t.end_at at time zone current_setting('TimeZone')",
    "submitted before expiry": "select count(*) from test_attempts where submitted_at is null or submitted_at > expires_at or started_at >= expires_at",
    "no open attempts": "select count(*) from test_attempts where status = 'IN_PROGRESS'",
    "cancelled tests have no attempts": "select count(*) from test_attempts a join tests t using (test_id) where t.status <> 'ACTIVE'",
    "answer question in its test": "select count(*) from attempt_answers x join test_attempts a using (attempt_id) left join questions q on q.question_id = x.question_id and q.test_id = a.test_id where q.question_id is null",
    "answer is A-D and ANSWERED": "select count(*) from attempt_answers where selected_answer not in ('A','B','C','D') or question_state <> 'ANSWERED'",
    "every attempt has a result": "select count(*) from test_attempts a left join test_results r using (attempt_id) where r.result_id is null",
    "result has its attempt": "select count(*) from test_results r left join test_attempts a using (attempt_id) where a.attempt_id is null or a.student_id <> r.student_id or a.test_id <> r.test_id",
    "results equal a rescore": """select count(*) from test_results r join (
        select a.attempt_id, count(x.answer_id) attempted, count(*) filter (where x.selected_answer = q.correct_answer) correct,
               coalesce(sum(case when x.selected_answer = q.correct_answer then q.marks else -q.negative_marks end), 0) marks
        from test_attempts a left join attempt_answers x using (attempt_id) left join questions q on q.question_id = x.question_id group by 1) s using (attempt_id)
        where r.attempted_questions <> s.attempted or r.correct_answers <> s.correct or r.obtained_marks <> s.marks
           or r.incorrect_answers <> s.attempted - s.correct or r.unanswered_questions <> r.total_questions - s.attempted""",
    "notification recipients exist": "select count(*) from notification_recipients r left join students s on s.student_id = r.recipient_id where s.student_id is null",
    "notifications have test metadata": "select count(*) from notifications where metadata->>'test_id' is null or metadata->>'dedupe_key' is null",
    "constraints all validated": "select count(*) from pg_constraint where not convalidated",
}
INSTITUTE = {
    "questions per subject": "select count(*) from (select s.code from subjects s left join bank_questions q using (subject_id) group by 1 having count(q.*) <> {per_subject}) x",
    "8 active subjects": f"select {len(SUBJECT_CODES)} - count(*) from subjects where status = 'ACTIVE'",
    "batch belongs to enrollment branch": "select count(*) from enrollment_batches x join enrollments e using (enrollment_id) join batches b using (batch_id) where b.branch_id <> e.branch_id",
    "one enrollment per student and branch": "select count(*) from (select student_id, branch_id from enrollments group by 1, 2 having count(*) > 1) x",
    "memberships at distinct providers": "select count(*) from (select student_id, provider_id from enrollments group by 1, 2 having count(*) > 1) x",
    "every enrollment has a batch": "select count(*) from enrollments e where not exists (select 1 from enrollment_batches x where x.enrollment_id = e.enrollment_id)",
    "every enrollment has a class": "select count(*) from enrollments e left join enrollment_academic_profiles p using (enrollment_id) where p.enrollment_id is null",
    "batch class matches enrollment": "select count(*) from enrollment_batches x join enrollment_academic_profiles p using (enrollment_id) join batch_academic_defaults d using (batch_id) where d.default_class_level <> p.class_level",
    "batch subjects enabled on provider": "select count(*) from batch_subjects x join batches b using (batch_id) join branches r using (branch_id) where not exists (select 1 from provider_subjects p where p.provider_id = r.provider_id and p.subject_id = x.subject_id)",
    "teaching uses an active teacher role": "select count(*) from teacher_subject_assignments t where not exists (select 1 from staff_role_assignments r where r.staff_id = t.staff_id and r.provider_id = t.provider_id and r.role = 'TEACHER' and r.status = 'ACTIVE')",
    "paper has its question count": "select count(*) from paper_versions v where question_count <> (select count(*) from paper_version_questions q where q.paper_version_id = v.paper_version_id)",
    "paper questions share its subject": "select count(*) from paper_version_questions x join paper_versions v using (paper_version_id) join papers p using (paper_id) join question_versions q using (question_version_id) join bank_questions b using (question_id) where b.subject_id <> p.subject_id",
    "question used by one paper": "select count(*) from (select question_version_id from paper_version_questions group by 1 having count(*) > 1) x",
    "exam papers are authorized": """select count(*) from exam_version_papers x join exam_versions v using (exam_version_id) join exams e using (exam_id)
        join paper_versions pv using (paper_version_id) join papers p using (paper_id)
        where p.provider_id <> e.provider_id and not exists (select 1 from paper_shares s where s.paper_id = p.paper_id and s.shared_with_provider_id = e.provider_id and s.status = 'ACTIVE')""",
    "exam papers are published": "select count(*) from exam_version_papers x join paper_versions v using (paper_version_id) where v.status <> 'PUBLISHED'",
    "exam subject on provider": "select count(*) from exam_plan_subjects x join exam_plans p using (plan_id) where not exists (select 1 from provider_subjects s where s.provider_id = p.provider_id and s.subject_id = x.subject_id)",
    "assignment enrollment matches": "select count(*) from student_exam_assignments a join exams e using (exam_id) left join enrollments n using (enrollment_id) where n.enrollment_id is null or n.student_id <> a.student_id or n.branch_id <> e.branch_id",
    "assignment batch is planned": "select count(*) from student_exam_assignments a join exams e using (exam_id) where not exists (select 1 from exam_plan_batches b where b.plan_id = e.plan_id and b.batch_id = a.batch_id)",
    "plans are not due for generation": "select count(*) from exam_plans where generate_at is not null",
    "no pending outbox rows": "select (select count(*) from exam_outbox where status <> 'SENT') + (select count(*) from student_notification_outbox where status <> 'SENT')",
    "no result notices owed": "select count(*) from student_exam_assignments where status = 'COMPLETED' and result_notified_at is null",
    "past assignments are settled": "select count(*) from student_exam_assignments a join exam_versions v using (exam_version_id) join exams e on e.exam_id = a.exam_id where a.status = 'ASSIGNED' and e.status = 'PUBLISHED' and v.end_at <= '{anchor}'",
    "jobs point at exams": "select count(*) from exam_generation_jobs j left join exams e using (exam_id) where e.exam_id is null",
    "constraints all validated": "select count(*) from pg_constraint where not convalidated",
}


def distribution(conns: dict) -> dict:
    s, i = conns["student"], conns["institute"]
    one = lambda c, q: c.execute(q).fetchall()
    return {"assignments_by_status": dict(one(i, "select status, count(*) from student_exam_assignments group by 1 order by 1")),
            "attempts_by_status": dict((k, v) for k, v in one(s, "select status::text, count(*) from test_attempts group by 1 order by 1")),
            "tests_by_status": dict((k, v) for k, v in one(s, "select status::text, count(*) from tests group by 1 order by 1")),
            "upcoming_tests": one(s, "select count(*) from tests where start_at > now()")[0][0],
            "questions_per_test": [float(x) for x in one(s, "select min(total_questions), avg(total_questions)::numeric(8,1), max(total_questions) from tests")[0]],
            "results_per_student": [float(x or 0) for x in one(s, "select min(c), avg(c)::numeric(8,1), max(c) from (select count(*) c from test_results group by student_id) x")[0]],
            "completed_tests_per_page_student_p95": float(one(s, "select percentile_cont(.95) within group (order by c) from (select count(*) c from test_attempts group by student_id) x")[0][0] or 0),
            "students_per_test": [float(x) for x in one(s, "select min(c), avg(c)::numeric(8,1), max(c) from (select count(*) c from test_audiences group by test_id) x")[0]]}


def sizes(conns: dict) -> dict:
    out = {}
    for db, conn in conns.items():
        rows = conn.execute("""select relname, reltuples::bigint, pg_relation_size(c.oid), pg_total_relation_size(c.oid) from pg_class c
                               join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'public' and c.relkind = 'r' order by 4 desc""").fetchall()
        out[db] = {"database_bytes": conn.execute("select pg_database_size(current_database())").fetchone()[0],
                   "tables": {r[0]: {"rows": r[1], "heap": r[2], "total": r[3]} for r in rows if r[1] > 0}}
    return out


def run(conns: dict, world: World, state_file: Path | None, *, report=print) -> list[str]:
    c = world.config
    failures, checks = [], 0

    def verdict(name, bad):
        nonlocal checks
        checks += 1
        report(f"{'PASS' if not bad else 'FAIL'}  {name}" + (f": {bad}" if bad else ""))
        if bad:
            failures.append(f"{name}: {bad}")

    for conn in conns.values():
        conn.rollback()
        conn.execute("set default_transaction_read_only = on")
        conn.commit()
    if state_file and state_file.exists():
        state = json.loads(state_file.read_text())
        for db, table, _columns, _make in PLAN:
            got = conns[db].execute(f'select count(*) from "{table}"').fetchone()[0]
            want = state["tables"].get(f"{db}.{table}", {}).get("rows")
            verdict(f"rows {db}.{table} = {want}", None if got == want else f"found {got}")
    for name, (inst, stud) in BOTH.items():
        a, b = conns["institute"].execute(MD5.format(inst)).fetchone()[0], conns["student"].execute(MD5.format(stud)).fetchone()[0]
        verdict(name, None if a == b else "digests differ")
    for name, sql in STUDENT.items():
        n = conns["student"].execute(sql).fetchone()[0]
        verdict(f"student: {name}", n or None)
    top = conns["student"].execute("select last_value from student_id_allocator where name = 'STU'").fetchone()[0]
    verdict("student: allocator past every id", None if top >= c["student_base"] + world.student_count else f"allocator {top}")
    for name, sql in INSTITUTE.items():
        n = conns["institute"].execute(sql.format(per_subject=c["questions_per_subject"], anchor=world.anchor.isoformat())).fetchone()[0]
        verdict(f"institute: {name}", n or None)
    for conn in conns.values():
        conn.rollback()
        conn.execute("set default_transaction_read_only = off")
        conn.commit()
    summary = {"checks": checks, "failed": len(failures), "failures": failures, "distribution": distribution(conns), "sizes": sizes(conns)}
    if state_file:
        state_file.with_name(state_file.stem + "-validation.json").write_text(json.dumps(summary, indent=1, default=str))
    report(f"{checks - len(failures)}/{checks} checks passed")
    return failures
