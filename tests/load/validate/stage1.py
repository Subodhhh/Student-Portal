"""Stage 1 validation, READ-ONLY, after the branch guard verified both Neon load-test branches.

  plan vs actual   every planned provider, branch, batch, provider/batch subject, staff role, teaching assignment, student,
                   enrollment (+ batches, chosen subjects, class/year) and paper exists exactly once, and nothing extra with the marker
  business rules   membership batch in the enrollment's branch; one ACTIVE enrollment per student and branch; batch subjects within
                   the provider's subjects; chosen subjects within the batch's; class + academic year on every enrollment; teaching
                   only for ACTIVE TEACHER roles, enabled subjects and batches of that branch; memberships 1-4 at distinct
                   providers; 1,000 approved questions for each of the 8 subjects with 4 distinct options, A-D, marks 4 / 1
  projection       Institute -> Student read projection (ALL rows, not only synthetic): every Institute row exists in Student with
                   identical synced fields (optionally polled until the reference sync caught up); a Student-only row is accepted
                   ONLY if it is one of the pre-load baseline rows recorded for these exact branches (validate/baselines/) AND its
                   synced fields are still the recorded ones; every Student enrollment's student exists; the student-id allocator
                   is above every issued id
Why a baseline: the reference sync upserts Institute rows and never deletes Student-only rows, so rows that were already
Student-only before any load-test write stay that way. They are listed one by one (never "ignore all Student-only rows").
Queries only SELECT; values are compared in memory; nothing is written. Pure check functions are unit-tested with fixtures."""
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

LOAD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOAD / "guard"))
sys.path.insert(0, str(LOAD))
import guard  # noqa: E402
from generator.plan import SUBJECT_CODES, Plan  # noqa: E402

PROJECTION = {   # table -> (key, synced fields), as the Student enrollment service's /reference/sync writes them
    "providers": ("provider_id", ["name", "lower(email) as email", "status::text as status"]),
    "branches": ("branch_id", ["provider_id", "name", "status::text as status"]),
    "batches": ("batch_id", ["branch_id", "name", "status::text as status", "start_date", "end_date"]),
    "enrollments": ("enrollment_id", ["provider_id", "branch_id", "student_id", "status::text as status"]),
    "enrollment_batches": ("enrollment_batch_id", ["enrollment_id", "batch_id"]),
}


BASELINE = LOAD / "validate" / "baselines" / "stage1_student_only.json"


class BaselineError(SystemExit):
    pass


# ------------------------------------------------------------------ baseline of pre-load Student-only rows
def load_baseline(path: Path, branch_ids: dict) -> dict[str, dict[str, dict]]:
    """table -> {id: recorded synced fields}. Refused unless it was recorded on exactly these pinned branches and is well formed."""
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("branch_ids") != branch_ids:
        raise BaselineError(f"baseline {path.name} was recorded on {data.get('branch_ids')}, not on the pinned {branch_ids}: refusing it")
    out = {}
    for table, rows in (data.get("student_only") or {}).items():
        if table not in PROJECTION:
            raise BaselineError(f"baseline {path.name}: unknown table {table!r}")
        fields = {f.split(" as ")[-1] for f in PROJECTION[table][1]}        # the column names projection_rows returns
        for key, row in rows.items():
            if not fields <= set(row):
                raise BaselineError(f"baseline {path.name}: {table} {key} lacks synced fields {sorted(fields - set(row))}")
        out[table] = {key: {f: row[f] for f in fields} for key, row in rows.items()}
    return out


# ------------------------------------------------------------------ pure checks
def diff_projection(institute: dict[str, dict], student: dict[str, dict], baseline: dict[str, dict] | None = None) -> dict:
    """Institute rows must all exist in Student with identical fields. Student-only rows are new discrepancies unless listed in
    `baseline` (id -> recorded fields) with UNCHANGED fields; a changed baseline row is a discrepancy too."""
    baseline = baseline or {}
    only_i = sorted(set(institute) - set(student))
    student_only = set(student) - set(institute)
    known = sorted(k for k in student_only if k in baseline and _norm(student[k]) == _norm(baseline[k]))
    changed = sorted(k for k in student_only if k in baseline and _norm(student[k]) != _norm(baseline[k]))
    new = sorted(student_only - set(baseline))
    absent = sorted(set(baseline) - student_only)          # gone, or now also in Institute: reported, not a failure
    fields = sorted(k for k in set(institute) & set(student) if _norm(institute[k]) != _norm(student[k]))
    return {"only_institute": len(only_i), "only_student": len(new), "baseline_only_student": len(known),
            "baseline_changed": len(changed), "baseline_absent": len(absent), "field_differences": len(fields),
            "examples": {"only_institute": only_i[:5], "only_student": new[:5], "baseline_changed": changed[:5],
                         "field_differences": fields[:5]}}


def projection_ok(d: dict) -> bool:
    return not (d["only_institute"] or d["only_student"] or d["field_differences"] or d.get("baseline_changed"))


def _norm(row: dict) -> dict:
    return {k: (str(v) if v is not None else None) for k, v in row.items()}


def expected_from_plan(plan: Plan) -> dict:
    p = plan
    names = {k: v.name for k, v in p.providers.items()}
    bname = {k: v.name for k, v in p.branches.items()}
    tname = {k: (bname[v.branch], v.name) for k, v in p.batches.items()}
    email = {k: v.email for k, v in p.staff.items()}
    return {
        "providers": {v.name for v in p.providers.values()},
        "branches": {(names[v.provider], v.name) for v in p.branches.values()},
        "batches": {(bname[v.branch], v.name, v.class_level) for v in p.batches.values()},
        "provider_subjects": {(v.name, c) for v in p.providers.values() for c in v.subjects},
        "batch_subjects": {(*tname[k], c) for k, v in p.batches.items() for c in v.subjects},
        "roles": {(v.email, v.role, names[pk]) for v in p.staff.values() for pk in p.roles_of(v)},
        "teaching": {(email[t.staff], bname[t.branch], t.subject, tname[t.batch][1] if t.batch else None) for t in p.teaching.values()},
        "students": {v.email for v in p.students.values()},
        "enrollments": {(p.students[m.student].email, bname[m.branch], frozenset(tname[t][1] for t in m.batches), m.class_level,
                         frozenset((tname[t][1], c) for t, cs in m.chosen_subjects.items() for c in cs))
                        for m in p.memberships.values()},
        "papers": {(v.title, bname[v.branch], v.subject) for v in p.papers.values()},
    }


def compare_sets(expected: dict, actual: dict) -> dict:
    out = {}
    for k, exp in expected.items():
        act = actual.get(k, set())
        out[k] = {"expected": len(exp), "actual": len(act), "missing": len(exp - act), "unexpected": len(act - exp),
                  "examples_missing": sorted(map(str, exp - act))[:3], "examples_unexpected": sorted(map(str, act - exp))[:3]}
    return out


def membership_rules(enrollments: list[dict], expect_average: float | None = 2.0) -> list[str]:
    """enrollments: synthetic rows {student, provider, branch, status} -> rule violations (the 2.0 average applies to the full plan only)."""
    problems = []
    per_student = defaultdict(list)
    for e in enrollments:
        if e["status"] == "ACTIVE":
            per_student[e["student"]].append(e)
    for s, rows in per_student.items():
        provs = [r["provider"] for r in rows]
        if not 1 <= len(rows) <= 4:
            problems.append(f"{s}: {len(rows)} memberships (allowed 1-4)")
        if len(set(provs)) != len(provs):
            problems.append(f"{s}: two memberships at the same provider")
    if per_student and expect_average is not None:
        avg = sum(len(v) for v in per_student.values()) / len(per_student)
        if abs(avg - expect_average) > 0.05:
            problems.append(f"average memberships {avg:.3f}, expected {expect_average}")
    return problems


def question_rules(rows: list[dict], expected: dict[str, int]) -> list[str]:
    """rows: one per question of the synthetic APPROVED papers {subject, stem, options[4], correct, marks, negative};
    expected: approved questions per subject code (all 8 for the full plan, the canary's own for the canary)."""
    problems = []
    counts = Counter(r["subject"] for r in rows)
    for code in sorted(set(SUBJECT_CODES) | set(expected)):
        if counts.get(code, 0) != expected.get(code, 0):
            problems.append(f"{code}: {counts.get(code, 0)} approved questions, expected {expected.get(code, 0)}")
    stems = Counter((r["subject"], r["stem"]) for r in rows)
    dup = sum(1 for n in stems.values() if n > 1)
    if dup:
        problems.append(f"{dup} duplicated question stems within a subject")
    bad = sum(1 for r in rows if len({o.strip().lower() for o in r["options"]}) != 4 or r["correct"] not in {"A", "B", "C", "D"}
              or float(r["marks"]) != 4 or float(r["negative"]) != 1)
    if bad:
        problems.append(f"{bad} questions break the option/answer/marks rules")
    return problems


# ------------------------------------------------------------------ database access (read-only)
def connect(name: str):
    import psycopg
    cfg = guard.load_config()
    url = guard.libpq_url(guard.database_urls(cfg["targets"][name])[0][1], cfg["targets"][name].get("connect_host"))
    return psycopg.connect(url, connect_timeout=15, options="-c default_transaction_read_only=on")


def _rows(conn, sql: str, *args) -> list[dict]:
    cur = conn.execute(sql, args)
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def projection_rows(conn, table: str) -> dict[str, dict]:
    key, fields = PROJECTION[table]
    rows = _rows(conn, f"select {key}::text as k, {', '.join(fields)} from {table}")
    return {r.pop("k"): {k: (str(v) if not isinstance(v, str) else v) for k, v in r.items()} for r in rows}


def actual_institute(conn, marker: str, student_email: dict[str, str]) -> tuple[dict, dict]:
    like = f"{marker} %"
    a: dict = {}
    a["providers"] = {r["name"] for r in _rows(conn, "select name from providers where name like %s", like)}
    a["branches"] = {(r["p"], r["b"]) for r in _rows(conn, "select p.name p, b.name b from branches b join providers p using(provider_id) where b.name like %s", like)}
    a["batches"] = {(r["br"], r["t"], r["c"]) for r in _rows(conn, """select br.name br, t.name t, d.default_class_level c from batches t
        join branches br using(branch_id) left join batch_academic_defaults d using(batch_id) where t.name like %s""", like)}
    a["provider_subjects"] = {(r["p"], r["c"]) for r in _rows(conn, """select p.name p, s.code c from provider_subjects ps
        join providers p using(provider_id) join subjects s using(subject_id) where p.name like %s""", like)}
    a["batch_subjects"] = {(r["br"], r["t"], r["c"]) for r in _rows(conn, """select br.name br, t.name t, s.code c from batch_subjects bs
        join batches t using(batch_id) join branches br using(branch_id) join subjects s using(subject_id) where t.name like %s""", like)}
    domain = f"{marker.lower()}.%@loadtest.example"
    a["roles"] = {(r["e"], r["role"], r["p"]) for r in _rows(conn, """select s.email e, r.role, p.name p from staff_role_assignments r
        join staff s using(staff_id) join providers p on p.provider_id = r.provider_id where s.email like %s and r.status = 'ACTIVE'""", domain)}
    a["teaching"] = {(r["e"], r["br"], r["c"], r["t"]) for r in _rows(conn, """select s.email e, br.name br, sub.code c, t.name t
        from teacher_subject_assignments x join staff s using(staff_id) join branches br on br.branch_id = x.branch_id
        join subjects sub on sub.subject_id = x.subject_id left join batches t on t.batch_id = x.batch_id
        where s.email like %s and x.status = 'ACTIVE'""", domain)}
    ids = list(student_email)
    enr = _rows(conn, """select e.enrollment_id, e.student_id, p.name p, br.name br, e.status, ap.class_level, ap.academic_year
        from enrollments e join providers p using(provider_id) join branches br on br.branch_id = e.branch_id
        left join enrollment_academic_profiles ap using(enrollment_id) where e.student_id = any(%s)""", ids)
    links = defaultdict(set)
    for r in _rows(conn, "select eb.enrollment_id, t.name t from enrollment_batches eb join batches t using(batch_id) where eb.enrollment_id = any(%s)",
                   [e["enrollment_id"] for e in enr]):
        links[r["enrollment_id"]].add(r["t"])
    chosen = defaultdict(set)
    for r in _rows(conn, """select eb.enrollment_id, t.name t, s.code c from enrollment_batch_subjects x
        join enrollment_batches eb using(enrollment_batch_id) join batches t on t.batch_id = eb.batch_id join subjects s using(subject_id)
        where eb.enrollment_id = any(%s)""", [e["enrollment_id"] for e in enr]):
        chosen[r["enrollment_id"]].add((r["t"], r["c"]))
    a["students"] = set(student_email.values())
    a["enrollments"] = {(student_email[e["student_id"]], e["br"], frozenset(links[e["enrollment_id"]]), e["class_level"],
                         frozenset(chosen[e["enrollment_id"]])) for e in enr if e["status"] == "ACTIVE"}
    papers = _rows(conn, """select pa.title, br.name br, s.code c, v.status, v.paper_version_id from papers pa
        join branches br using(branch_id) join subjects s using(subject_id)
        join lateral (select * from paper_versions pv where pv.paper_id = pa.paper_id order by version_no desc limit 1) v on true
        where pa.title like %s and pa.scope = 'PROVIDER'""", like)
    a["papers"] = {(r["title"], r["br"], r["c"]) for r in papers}
    extra = {
        "enrollment_rows": [{"student": e["student_id"], "provider": e["p"], "branch": e["br"], "status": e["status"],
                             "class_level": e["class_level"], "academic_year": e["academic_year"]} for e in enr],
        "papers_not_approved": sorted(r["title"] for r in papers if r["status"] not in ("APPROVED", "PUBLISHED")),
        "questions": [{"subject": r["c"], "stem": r["stem"], "options": [r["a"], r["b"], r["cc"], r["d"]], "correct": r["correct_option"],
                       "marks": r["marks_positive"], "negative": r["marks_negative"]}
                      for r in _rows(conn, """select s.code c, q.stem, q.option_a a, q.option_b b, q.option_c cc, q.option_d d, q.correct_option,
                            q.marks_positive, q.marks_negative from papers pa join subjects s using(subject_id)
                            join paper_versions v on v.paper_id = pa.paper_id and v.status in ('APPROVED','PUBLISHED')
                            join paper_version_questions x on x.paper_version_id = v.paper_version_id
                            join question_versions q on q.question_version_id = x.question_version_id
                            where pa.title like %s and pa.scope = 'PROVIDER'""", like)],
        "global_rules": {
            "membership_batch_outside_branch": _rows(conn, """select count(*) n from enrollment_batches eb join enrollments e using(enrollment_id)
                join batches t on t.batch_id = eb.batch_id where t.branch_id <> e.branch_id""")[0]["n"],
            "duplicate_active_enrollments": _rows(conn, """select count(*) n from (select student_id, branch_id from enrollments
                where status = 'ACTIVE' group by 1, 2 having count(*) > 1) d""")[0]["n"],
            "batch_subject_not_enabled": _rows(conn, """select count(*) n from batch_subjects bs join batches t using(batch_id)
                join branches br using(branch_id) left join provider_subjects ps on ps.provider_id = br.provider_id and ps.subject_id = bs.subject_id
                where ps.provider_subject_id is null""")[0]["n"],
            "chosen_subject_not_in_batch": _rows(conn, """select count(*) n from enrollment_batch_subjects x join enrollment_batches eb
                using(enrollment_batch_id) left join batch_subjects bs on bs.batch_id = eb.batch_id and bs.subject_id = x.subject_id
                where bs.batch_subject_id is null""")[0]["n"],
            "teaching_without_teacher_role": _rows(conn, """select count(*) n from teacher_subject_assignments x where x.status = 'ACTIVE'
                and not exists (select 1 from staff_role_assignments r where r.staff_id = x.staff_id and r.role = 'TEACHER'
                and r.provider_id = x.provider_id and r.status = 'ACTIVE')""")[0]["n"],
            "teaching_subject_not_enabled": _rows(conn, """select count(*) n from teacher_subject_assignments x left join provider_subjects ps
                on ps.provider_id = x.provider_id and ps.subject_id = x.subject_id where x.status = 'ACTIVE' and ps.provider_subject_id is null""")[0]["n"],
            "teaching_batch_outside_branch": _rows(conn, """select count(*) n from teacher_subject_assignments x join batches t using(batch_id)
                where t.branch_id <> x.branch_id""")[0]["n"],
        },
    }
    return a, extra


def validate(plan: Plan, wait_sync_minutes: float = 0, report=print, poll_seconds: float = 60) -> dict:
    cfg = plan.config
    marker, domain = cfg["marker"], f"{cfg['marker'].lower()}.%@loadtest.example"
    gcfg = guard.load_config()
    if guard.cmd_check(gcfg, ["neon"]) != 0:
        raise SystemExit("branch guard refused: nothing validated")
    baseline = load_baseline(BASELINE, {n: gcfg["targets"][n].get("branch_id") for n in ("neon-student", "neon-institute")})
    with connect("neon-institute") as inst, connect("neon-student") as stu:
        students = _rows(stu, "select student_id, email, status::text as status from students where email like %s", domain)
        student_email = {r["student_id"]: r["email"] for r in students}
        actual, extra = actual_institute(inst, marker, student_email)
        sets = compare_sets(expected_from_plan(plan), actual)
        rules = membership_rules(extra["enrollment_rows"], None if plan.full is not None else 2.0)
        rules += [f"{n} enrollment(s) without class/academic year" for n in
                  [sum(1 for e in extra["enrollment_rows"] if not e["class_level"] or not re.match(r"^\d{4}-\d{4}$", e["academic_year"] or ""))] if n]
        rules += [f"{k}: {v}" for k, v in extra["global_rules"].items() if v]
        rules += [f"papers not approved: {', '.join(extra['papers_not_approved'][:5])}"] if extra["papers_not_approved"] else []
        rules += question_rules(extra["questions"], plan.summary()["questions_per_subject"])
        rules += [f"{sum(1 for s in students if s['status'] != 'ACTIVE')} synthetic students not ACTIVE"] if any(s["status"] != "ACTIVE" for s in students) else []
        deadline = time.monotonic() + wait_sync_minutes * 60
        while True:
            projection = {t: diff_projection(projection_rows(inst, t), projection_rows(stu, t), baseline.get(t)) for t in PROJECTION}
            in_sync = all(projection_ok(d) for d in projection.values())
            if in_sync or time.monotonic() >= deadline:
                break
            report("projection not in sync yet: " + ", ".join(f"{t} {d['only_institute']}/{d['only_student']}/{d['field_differences']}"
                                                              for t, d in projection.items()) + f"; waiting {poll_seconds:.0f} s")
            time.sleep(poll_seconds)
        orphans = _rows(stu, "select count(*) n from enrollments e where not exists (select 1 from students s where s.student_id = e.student_id)")[0]["n"]
        alloc = _rows(stu, "select last_value from student_id_allocator where name = 'STU'")
        top = _rows(stu, r"select max(substring(student_id from 4)::bigint) m from students where student_id ~ '^STU\d+$'")[0]["m"] or 0
        sizes = {"institute": _rows(inst, "select pg_database_size(current_database()) n")[0]["n"],
                 "student": _rows(stu, "select pg_database_size(current_database()) n")[0]["n"]}
    allocator_ok = bool(alloc) and alloc[0]["last_value"] >= top
    ok = (all(v["missing"] == 0 and v["unexpected"] == 0 for v in sets.values()) and not rules and in_sync and orphans == 0 and allocator_ok)
    return {"ok": ok, "scope": "canary" if plan.full is not None else "full", "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "plan_vs_actual": sets,
            "rule_violations": rules, "projection": projection, "projection_in_sync": in_sync,
            "baseline": {"file": BASELINE.name if baseline else None, "rows": {t: len(v) for t, v in baseline.items()}},
            "student_enrollments_without_student": orphans, "allocator_ok": allocator_ok, "database_bytes": sizes}
