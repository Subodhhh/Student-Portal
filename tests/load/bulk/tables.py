"""Every table of the dataset as (database, table, columns, row generator), parents before children. Rows mirror what the application
writes (sources named beside each group); values are strings or None, the form COPY takes. Generators hold no rows, so memory stays flat."""
import hashlib
import json
import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo

from .world import SUBJECT_NAMES, World, _unit, question

NAMESPACE = uuid.UUID("5f2a3c0e-2a39-4a5e-9a52-7d1a0c6f2b11")   # student-test-service routes/internal.py: delivered question ids
IST = ZoneInfo("Asia/Kolkata")
POLICIES = {"unanswered": "ZERO", "attempts": "ONE_WITH_RESUME", "timer": "SHARED_FULL_DURATION", "results": "AFTER_EVALUATION",
            "answer_review": "AFTER_WINDOW_CLOSES", "shuffle": "NONE"}   # exam_generation.FIXED_POLICIES


def iso(d):
    return d.isoformat()


def naive(d):
    return d.replace(tzinfo=None).isoformat(sep=" ")


def run_key(start) -> str:
    return "window:" + start.replace(microsecond=0).isoformat()      # exam_generation.canonical_run_key (anchor is UTC)


def hash_questions(items) -> str:
    h = hashlib.sha256()                                              # paper service _hash_questions
    for it in items:
        h.update("\x1f".join(map(str, it)).encode())
        h.update(b"\x1e")
    return h.hexdigest()


def paper_questions(w: World, p):
    return [(p.first_question + k, question(w.config, p.subject, p.first_question + k)) for k in range(w.config["questions_per_paper"])]


def phone(n: int) -> str:
    return f"9{n:09d}"


# ---------------------------------------------------------------- reference data (identical in both databases, like the reference sync)
def providers(w):
    t = naive(w.created)
    for p in w.providers:
        yield (p.id, f"{w.config['marker']} Provider {p.index:05d}", w.email(f"provider{p.index:05d}"), phone(p.index), None, "ACTIVE", t, t)


def branches(w):
    t = naive(w.created)
    for b in w.branches:
        yield (b.id, w.providers[b.provider].id, b.name, None, b.city, "Maharashtra", None, "ACTIVE", t, t)


def batches(w):
    t, c = naive(w.created), w.config
    for b in w.batches:
        yield (b.id, b.name, f"{c['marker']} synthetic batch", c["batch_start"], c["batch_end"], "ACTIVE", t, t, w.branches[b.branch].id)


def enrollments(w):
    t = naive(w.created)
    for m in w.memberships:
        br = w.branches[m.branch]
        yield (m.id, w.providers[br.provider].id, w.student_id(m.student), t, "ACTIVE", t, t, br.id)


def enrollment_batches(w):
    t = naive(w.created)
    for m in w.memberships:
        for b in m.batches:
            yield (w.uid("enrollment-batch", m.index, b), m.id, w.batches[b].id, t, t)


# ---------------------------------------------------------------- Institute: academics and identity
def staff(w):
    t, h = iso(w.created), w.password_hash
    for i, (key, sid, _role, _p, _b) in enumerate(w.staff, start=1):
        yield (sid, f"STF{i:06d}", w.email(key), t, f"{w.config['marker']} {key}", None, h, t if h else None, "ACTIVE", t, t)


def staff_roles(w):
    t = iso(w.created)
    for key, sid, role, p, b in w.staff:
        yield (w.uid("role", key), sid, role, None if p is None else w.providers[p].id, None if b is None else w.branches[b].id, "ACTIVE", t, t)


def provider_subjects(w):
    t = iso(w.created)
    for p in w.providers:
        for s in w.subjects_of_provider(p):
            yield (w.uid("provider-subject", p.index, s), p.id, w.subject_ids[s], t)


def batch_subjects(w):
    t = iso(w.created)
    for b in w.batches:
        for s in w.subjects_of(w.branches[b.branch]):
            yield (w.uid("batch-subject", b.index, s), b.id, w.subject_ids[s], t)


def batch_defaults(w):
    t, year = iso(w.created), w.config["academic_year"]
    for b in w.batches:
        yield (b.id, b.class_level, year, t, t)


def teaching(w):
    t = iso(w.created)
    for b in w.branches:
        for s in w.subjects_of(b):
            yield (w.uid("teaching", b.index, s), w.teacher(b.provider, s), w.providers[b.provider].id, b.id, w.subject_ids[s], "ACTIVE", t, t)


def academic_profiles(w):
    t, year = iso(w.created), w.config["academic_year"]
    for m in w.memberships:
        yield (m.id, m.class_level, year, t, t)


def enrollment_events(w):
    t = iso(w.created)                                        # enrollments.enroll: BATCH_ADDED per batch, then ENROLLED
    for m in w.memberships:
        br = w.branches[m.branch]
        pid, actor = w.providers[br.provider].id, w.actor(br.provider)
        for b in m.batches:
            yield (w.uid("event", m.index, b), m.id, pid, br.id, w.student_id(m.student), "BATCH_ADDED", w.batches[b].id, actor, t)
        yield (w.uid("event", m.index), m.id, pid, br.id, w.student_id(m.student), "ENROLLED", None, actor, t)


# ---------------------------------------------------------------- Institute: question bank and papers (persist_validated_paper shape)
def _paper_owner(w, p):
    br = w.branches[p.branch]
    return br, w.providers[br.provider], w.teacher(br.provider, p.subject)


def bank_questions(w):
    t = iso(w.created)
    for p in w.papers:
        br, pr, owner = _paper_owner(w, p)
        for n, q in paper_questions(w, p):
            difficulty = ("EASY", "MEDIUM", "MEDIUM", "HARD")[n % 4]
            yield (w.uid("question", p.subject, n), "PROVIDER", pr.id, br.id, w.subject_ids[p.subject], owner, "ACTIVE", q["topic"], difficulty,
                   "SINGLE_CORRECT", "MCQ_SINGLE", t, t)


def question_versions(w):
    t = iso(w.created)
    for p in w.papers:
        _br, _pr, owner = _paper_owner(w, p)
        for n, q in paper_questions(w, p):
            a, b, c, d = q["options"]
            yield (w.uid("question-version", p.subject, n), w.uid("question", p.subject, n), "1", q["stem"], a, b, c, d, q["correct"],
                   q["explanation"], q["topic"], "4", "1", owner, t)


def papers(w):
    t = iso(w.created)
    for p in w.papers:
        br, pr, owner = _paper_owner(w, p)
        yield (p.id, "PROVIDER", pr.id, br.id, w.subject_ids[p.subject], f"{w.config['marker']} {p.subject} Paper {p.number:05d}", owner, t)


def paper_versions(w):
    t, per = iso(w.created), w.config["questions_per_paper"]
    for p in w.papers:
        _br, _pr, owner = _paper_owner(w, p)
        digest = hash_questions((q["stem"], *q["options"], q["correct"], "4", "1") for _n, q in paper_questions(w, p))
        yield (p.version_id, p.id, "1", "PUBLISHED", str(per), str(per * 4), digest, owner, t, t)


def paper_version_questions(w):
    for p in w.papers:
        for pos, (n, _q) in enumerate(paper_questions(w, p), start=1):
            yield (w.uid("pvq", p.index, pos), p.version_id, str(pos), w.uid("question-version", p.subject, n))


def paper_version_events(w):
    t = iso(w.created)
    for p in w.papers:
        _br, _pr, owner = _paper_owner(w, p)
        for kind, a, b in (("VALIDATED", None, "VALIDATED"), ("APPROVED", "VALIDATED", "APPROVED"), ("PUBLISHED", "APPROVED", "PUBLISHED")):
            yield (w.uid("paper-event", p.index, kind), p.version_id, kind, a, b, owner, t)


def paper_shares(w):
    t = iso(w.created)
    for paper, provider in w.shares:
        p = w.papers[paper]
        yield (w.uid("share", paper, provider), p.id, w.providers[w.branches[p.branch].provider].id, w.providers[provider].id, "ACTIVE",
               w.actor(w.branches[p.branch].provider), t)


# ---------------------------------------------------------------- Institute: exams (exam_generation.generate + outbox shape)
def _exam_type(w, e):
    return "NEET" if "NEET" in w.providers[w.branches[e.branch].provider].profile else "JEE Main"


def _totals(w, e):
    n = len(e.papers) * w.config["questions_per_paper"]
    return n, n * 4


def exam_plans(w):
    for e in w.exams():
        b = w.branches[e.branch]
        p = w.providers[b.provider]
        t = iso(e.start - timedelta(days=7))
        yield (e.plan_id, p.id, b.id, e.name, None, str(e.minutes), iso(e.start), iso(e.end), None, "t", "ACTIVE", "BATCHES", "PER_QUESTION", "f",
               _exam_type(w, e), e.class_level, w.actor(b.provider), t, t)


def exam_plan_batches(w):
    for e in w.exams():
        for t in w.branches[e.branch].batches:
            if w.batches[t].class_level == e.class_level:
                yield (w.uid("plan-batch", e.key, t), e.plan_id, w.batches[t].id)


def exam_plan_subjects(w):
    for e in w.exams():
        for pos, p in enumerate(e.papers, start=1):
            yield (w.uid("slot", e.key, pos), e.plan_id, str(pos), w.subject_ids[w.papers[p].subject], w.papers[p].version_id)


def generation_jobs(w):
    for e in w.exams():
        t = iso(e.start - timedelta(days=7))
        yield (e.job_id, e.plan_id, run_key(e.start), "SUCCEEDED", "1", e.exam_id, w.actor(w.branches[e.branch].provider), t, t)


def generation_events(w):
    for e in w.exams():
        t = iso(e.start - timedelta(days=7))
        yield (w.uid("job-event", e.key), e.job_id, "GENERATED",
               json.dumps({"exam_id": e.exam_id, "students": len(w.by_class[(e.branch, e.class_level)]), "subjects": len(e.papers)}), t)


def exams(w):
    for e in w.exams():
        b = w.branches[e.branch]
        yield (e.exam_id, e.plan_id, run_key(e.start), w.providers[b.provider].id, b.id, f"{e.name} ({e.start.date().isoformat()})",
               "CANCELLED" if e.cancelled else "PUBLISHED", "Synthetic cancellation" if e.cancelled else None, w.actor(b.provider),
               iso(e.start - timedelta(days=7)))


def exam_versions(w):
    settings_base = {"marking": {"mode": "PER_QUESTION"}, "policies": POLICIES, "shuffle_questions": False, "audience_type": "BATCHES",
                     "global_test_id": None, "skipped_duplicates": 0}
    for e in w.exams():
        total_q, marks = _totals(w, e)
        t = iso(e.start - timedelta(days=7))
        yield (e.version_id, e.exam_id, "1", str(e.minutes), iso(e.start), iso(e.end), str(total_q), str(marks), e.job_id,
               json.dumps({**settings_base, "exam_type": _exam_type(w, e), "target_class": e.class_level}), t, t)


def exam_version_papers(w):
    per = w.config["questions_per_paper"]
    for e in w.exams():
        for pos, p in enumerate(e.papers, start=1):
            paper = w.papers[p]
            yield (w.uid("evp", e.key, pos), e.version_id, str(pos), w.subject_ids[paper.subject], paper.version_id,
                   w.providers[w.branches[paper.branch].provider].id, str(per), str(per * 4))


def _past(w, e):
    return e.end <= w.anchor and not e.cancelled


def assignments(w):
    for e in w.exams():
        t = iso(e.start - timedelta(days=7))
        past = _past(w, e)
        for student, enrollment, batch in w.audience(e):
            result = w.outcome(e, student) if past else None
            status = "ASSIGNED" if not past else "COMPLETED" if result else "MISSED"
            done = iso(result[1]) if result else iso(e.end) if past else None
            yield (w.uid("assignment", e.key, student), e.exam_id, e.version_id, w.student_id(student), enrollment, batch, status, done,
                   done if result else None, t)


def exam_outbox(w):
    for e in w.exams():
        t = iso(e.start - timedelta(days=7))
        yield (w.uid("outbox", e.key), e.version_id, "PUBLISH", "SENT", "1", t, t, t)
        if e.cancelled:
            yield (w.uid("outbox-cancel", e.key), e.version_id, "CANCEL", "SENT", "1", t, t, t)


def _notices(w, e):
    """(key, event, title, message, students, when), as outbox._queue_student_notice and assignment_sync.notify_results queue them."""
    total_q, _ = _totals(w, e)
    ist = "%d %b %Y, %H:%M IST"
    name = f"{e.name} ({e.start.date().isoformat()})"
    everyone = sorted(w.student_id(s) for s, _e, _b in w.audience(e))
    created = e.start - timedelta(days=7)
    yield (f"exam-published:{e.version_id}", "TEST_SCHEDULED", f"New test scheduled: {name}",
           f"You can start it from {e.start.astimezone(IST).strftime(ist)} until {e.end.astimezone(IST).strftime(ist)}. Once started you have "
           f"{e.minutes} minutes ({total_q} questions).", everyone, created)
    if e.cancelled:
        yield (f"exam-cancelled:{e.version_id}", "SYSTEM_ANNOUNCEMENT", f"Test cancelled: {name}",
               "This test has been cancelled and can no longer be started.", everyone, created)
    elif _past(w, e):
        done = sorted(w.student_id(s) for s, _e, _b in w.audience(e) if w.outcome(e, s))
        if done:
            key = f"result:{e.version_id}:{hashlib.sha1(','.join(done).encode(), usedforsecurity=False).hexdigest()[:20]}"
            yield (key, "RESULT_PUBLISHED", f"Your result is ready: {name}",
                   "Your test has been evaluated. Open Results to see your score and review your answers.", done, e.end)


def notification_outbox(w):
    for e in w.exams():
        b = w.branches[e.branch]
        details = json.dumps({"exam_version_id": e.version_id, "test_id": e.version_id})
        for key, event, title, message, students, when in _notices(w, e):
            t = iso(when)
            yield (w.uid("notice-out", key), key, event, title[:255], message, details, json.dumps(students), w.providers[b.provider].id, b.id, "SENT",
                   "1", t, t, t)


# ---------------------------------------------------------------- Student: identity, delivered tests, attempts, results, inbox
def students(w):
    t = naive(w.created)
    first = ("Aarav", "Diya", "Kabir", "Ishita", "Rohan", "Meera", "Vivaan", "Anaya")
    for s in range(1, w.student_count + 1):
        yield (w.student_id(s), first[s % 8], f"LT2 {s:06d}", w.email(f"student{s:06d}"), phone(100000000 + s), "ACTIVE", t, t, w.password_hash)


def tests(w):
    for e in w.exams():
        b = w.branches[e.branch]
        total_q, marks = _totals(w, e)
        t = naive(e.start - timedelta(days=7))
        yield (e.version_id, w.providers[b.provider].id, b.id, f"{e.name} ({e.start.date().isoformat()})", None, str(e.minutes), str(total_q), str(marks),
               naive(e.start), naive(e.end), "INACTIVE" if e.cancelled else "ACTIVE", t, t, "f")


def delivered_questions(w):
    for e in w.exams():
        t = naive(e.start - timedelta(days=7))
        for order, paper, number in w.exam_questions(e):
            q = question(w.config, paper.subject, number)
            a, b, c, d = q["options"]
            yield (str(uuid.uuid5(NAMESPACE, f"{e.version_id}:{order}")), e.version_id, SUBJECT_NAMES[paper.subject], str(order), q["stem"], "MCQ",
                   a, b, c, d, q["correct"], "4", "1", t, t)


def audiences(w):
    for e in w.exams():
        t = naive(e.start - timedelta(days=7))
        b = w.branches[e.branch]
        for student, _enrollment, _batch in w.audience(e):
            yield (w.uid("audience", e.key, student), e.version_id, "STUDENT", w.providers[b.provider].id, b.id, w.student_id(student), t)


def _attempted(w):
    for e in w.exams():
        if _past(w, e):
            for student, _enrollment, _batch in w.audience(e):
                result = w.outcome(e, student)
                if result:
                    yield e, student, result


def attempts(w):
    for e, student, (started, submitted, expires, status, reason, _answers) in _attempted(w):
        yield (w.uid("attempt", e.key, student), e.version_id, w.student_id(student), iso(started), iso(expires), iso(submitted), status, reason)


def answers(w):
    for e, student, (started, submitted, _expires, _status, _reason, rows) in _attempted(w):
        attempt = w.uid("attempt", e.key, student)
        step = (submitted - started) / (len(rows) + 1)
        for i, (order, letter, _right) in enumerate(rows, start=1):
            at = started + step * i
            yield (w.uid("answer", attempt, order), attempt, str(uuid.uuid5(NAMESPACE, f"{e.version_id}:{order}")), letter, naive(at), naive(at), "ANSWERED", iso(at))


def results(w):
    for e, student, (_started, submitted, _expires, _status, _reason, rows) in _attempted(w):
        s = w.score(_totals(w, e)[0], rows)
        yield (w.uid("result", e.key, student), w.uid("attempt", e.key, student), e.version_id, w.student_id(student), str(s["total_questions"]),
               str(s["attempted"]), str(s["correct"]), str(s["incorrect"]), str(s["unanswered"]), str(s["total_marks"]), str(s["obtained"]),
               str(s["accuracy"]), iso(submitted))


def notifications(w):
    for e in w.exams():
        details = {"exam_version_id": e.version_id, "test_id": e.version_id}
        for key, event, title, message, _students, when in _notices(w, e):
            yield (w.uid("notification", key), event, title[:255], message, json.dumps({**details, "dedupe_key": key, "source": "institute"}), iso(when))


def recipients(w):
    fresh = w.anchor - timedelta(days=7)
    for e in w.exams():
        for key, _event, _title, _message, students_, when in _notices(w, e):
            notification = w.uid("notification", key)
            for sid in students_:
                read = when < fresh or _unit("read", key, sid) < 0.5
                yield (w.uid("recipient", key, sid), notification, "STUDENT", sid, "t" if read else "f", iso(when + timedelta(hours=6)) if read else None)


REF = ["provider_id", "name", "email", "phone", "address", "status", "created_at", "updated_at"]
BRANCH = ["branch_id", "provider_id", "name", "address", "city", "state", "pincode", "status", "created_at", "updated_at"]
BATCH = ["batch_id", "name", "description", "start_date", "end_date", "status", "created_at", "updated_at", "branch_id"]
ENR = ["enrollment_id", "provider_id", "student_id", "enrolled_at", "status", "created_at", "updated_at", "branch_id"]
EB = ["enrollment_batch_id", "enrollment_id", "batch_id", "created_at", "updated_at"]
I, S = "institute", "student"
PLAN = [
    (I, "providers", REF, providers), (I, "branches", BRANCH, branches), (I, "batches", BATCH, batches),
    (I, "staff", ["staff_id", "staff_code", "email", "email_verified_at", "full_name", "phone", "password_hash", "password_changed_at", "status",
                  "created_at", "updated_at"], staff),
    (I, "staff_role_assignments", ["assignment_id", "staff_id", "role", "provider_id", "branch_id", "status", "created_at", "updated_at"], staff_roles),
    (I, "provider_subjects", ["provider_subject_id", "provider_id", "subject_id", "created_at"], provider_subjects),
    (I, "batch_subjects", ["batch_subject_id", "batch_id", "subject_id", "created_at"], batch_subjects),
    (I, "batch_academic_defaults", ["batch_id", "default_class_level", "default_academic_year", "created_at", "updated_at"], batch_defaults),
    (I, "teacher_subject_assignments", ["teaching_id", "staff_id", "provider_id", "branch_id", "subject_id", "status", "created_at", "updated_at"], teaching),
    (I, "enrollments", ENR, enrollments), (I, "enrollment_batches", EB, enrollment_batches),
    (I, "enrollment_academic_profiles", ["enrollment_id", "class_level", "academic_year", "created_at", "updated_at"], academic_profiles),
    (I, "enrollment_events", ["event_id", "enrollment_id", "provider_id", "branch_id", "student_id", "event_type", "batch_id", "actor_staff_id", "occurred_at"], enrollment_events),
    (I, "bank_questions", ["question_id", "scope", "provider_id", "branch_id", "subject_id", "owner_staff_id", "status", "topic", "difficulty", "pattern",
                           "question_type", "created_at", "updated_at"], bank_questions),
    (I, "question_versions", ["question_version_id", "question_id", "version_no", "stem", "option_a", "option_b", "option_c", "option_d", "correct_option",
                              "explanation", "topic", "marks_positive", "marks_negative", "created_by", "created_at"], question_versions),
    (I, "papers", ["paper_id", "scope", "provider_id", "branch_id", "subject_id", "title", "created_by", "created_at"], papers),
    (I, "paper_versions", ["paper_version_id", "paper_id", "version_no", "status", "question_count", "total_marks", "content_hash", "created_by",
                           "created_at", "published_at"], paper_versions),
    (I, "paper_version_questions", ["pvq_id", "paper_version_id", "position", "question_version_id"], paper_version_questions),
    (I, "paper_version_events", ["event_id", "paper_version_id", "event_type", "from_status", "to_status", "actor_staff_id", "occurred_at"], paper_version_events),
    (I, "paper_shares", ["share_id", "paper_id", "owner_provider_id", "shared_with_provider_id", "status", "granted_by", "created_at"], paper_shares),
    (I, "exam_plans", ["plan_id", "provider_id", "branch_id", "name", "description", "duration_minutes", "start_at", "end_at", "generate_at", "auto_publish",
                       "status", "audience_type", "marking_mode", "shuffle_questions", "exam_type", "target_class", "created_by", "created_at", "updated_at"], exam_plans),
    (I, "exam_plan_batches", ["plan_batch_id", "plan_id", "batch_id"], exam_plan_batches),
    (I, "exam_plan_subjects", ["slot_id", "plan_id", "position", "subject_id", "paper_version_id"], exam_plan_subjects),
    (I, "exam_generation_jobs", ["job_id", "plan_id", "run_key", "status", "attempts", "exam_id", "requested_by", "started_at", "finished_at"], generation_jobs),
    (I, "exam_generation_events", ["event_id", "job_id", "event_type", "details", "occurred_at"], generation_events),
    (I, "exams", ["exam_id", "plan_id", "run_key", "provider_id", "branch_id", "name", "status", "cancelled_reason", "created_by", "created_at"], exams),
    (I, "exam_versions", ["exam_version_id", "exam_id", "version_no", "duration_minutes", "start_at", "end_at", "total_questions", "total_marks",
                          "generation_job_id", "settings", "created_at", "published_at"], exam_versions),
    (I, "exam_version_papers", ["evp_id", "exam_version_id", "position", "subject_id", "paper_version_id", "source_provider_id", "question_count", "total_marks"], exam_version_papers),
    (I, "student_exam_assignments", ["assignment_id", "exam_id", "exam_version_id", "student_id", "enrollment_id", "batch_id", "status", "status_updated_at",
                                     "result_notified_at", "created_at"], assignments),
    (I, "exam_outbox", ["outbox_id", "exam_version_id", "operation", "status", "attempts", "next_attempt_at", "created_at", "sent_at"], exam_outbox),
    (I, "student_notification_outbox", ["outbox_id", "dedupe_key", "event_type", "title", "message", "details", "student_ids", "provider_id", "branch_id",
                                        "status", "attempts", "next_attempt_at", "created_at", "sent_at"], notification_outbox),
    (S, "students", ["student_id", "first_name", "last_name", "email", "phone", "status", "created_at", "updated_at", "password_hash"], students),
    (S, "providers", REF, providers), (S, "branches", BRANCH, branches), (S, "batches", BATCH, batches),
    (S, "enrollments", ENR, enrollments), (S, "enrollment_batches", EB, enrollment_batches),
    (S, "tests", ["test_id", "provider_id", "branch_id", "name", "description", "duration_minutes", "total_questions", "total_marks", "start_at", "end_at",
                  "status", "created_at", "updated_at", "shuffle_questions"], tests),
    (S, "questions", ["question_id", "test_id", "subject", "question_order", "question_text", "question_type", "option_a", "option_b", "option_c", "option_d",
                      "correct_answer", "marks", "negative_marks", "created_at", "updated_at"], delivered_questions),
    (S, "test_audiences", ["test_audience_id", "test_id", "audience_type", "provider_id", "branch_id", "student_id", "created_at"], audiences),
    (S, "test_attempts", ["attempt_id", "test_id", "student_id", "started_at", "expires_at", "submitted_at", "status", "submit_reason"], attempts),
    (S, "attempt_answers", ["answer_id", "attempt_id", "question_id", "selected_answer", "answered_at", "updated_at", "question_state", "visited_at"], answers),
    (S, "test_results", ["result_id", "attempt_id", "test_id", "student_id", "total_questions", "attempted_questions", "correct_answers", "incorrect_answers",
                         "unanswered_questions", "total_marks", "obtained_marks", "accuracy", "created_at"], results),
    (S, "notifications", ["notification_id", "event_type", "title", "message", "metadata", "created_at"], notifications),
    (S, "notification_recipients", ["notification_recipient_id", "notification_id", "recipient_type", "recipient_id", "is_read", "read_at"], recipients),
]
