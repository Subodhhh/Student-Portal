"""The Stage 1 loader against the in-process fake API (no network, no database, no containers): complete creation, idempotent
re-runs, lost responses, gateway 502/504 and 500 before/after the server committed, slow commits, interrupted uploads, canary
reuse, journal protection and staleness, MFA + CSRF, coordinated re-authentication, stop rules and secret hygiene."""
import json
import logging
import os
import stat
import sys
from collections import Counter
from pathlib import Path

import pytest

LOAD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOAD))
from generator import plan as P  # noqa: E402
from generator.api import Api, ApiError, SessionExpired  # noqa: E402
from generator.fake_api import OTP, PASSWORD, FakeInstitute  # noqa: E402
from generator.journal import Journal, JournalError  # noqa: E402
from generator.loader import AbortRun, Loader  # noqa: E402

MINI = {**P.STAGE1, "seed": 11, "providers": 10, "branches_per_provider": {1: 7, 2: 2, 0: 1}, "students": 40,
        "memberships": {1: 14, 2: 16, 3: 6, 4: 4}, "questions_per_subject": 20, "questions_per_paper": 10}
BRANCH_IDS = {"neon-student": "br-load-student", "neon-institute": "br-load-institute"}
GATED = {"branch": "branches", "batch": "batches", "enrollment": "enrollments", "paper": "papers"}


@pytest.fixture()
def plan():
    return P.build(MINI)


def ident(plan):
    return {"stage": plan.config["stage"], "seed": plan.config["seed"], "plan": plan.fingerprint(), "branch_ids": BRANCH_IDS}


def run(fake: FakeInstitute, path: Path, plan, *, adopt=False, workers=3, **kw):
    """One loader process: login, run, always close the journal (releases its lock)."""
    api = Api("http://127.0.0.1:18010", transport=fake.transport(), sleep=lambda s: None)
    api.login("STF000001", lambda: PASSWORD, lambda: OTP)
    out: list[str] = []
    journal = Journal(path, ident(plan))
    loader = Loader(api, plan, journal, workers=workers, report=out.append, **kw)
    try:
        return loader.run(adopt_existing=adopt), loader, out
    finally:
        journal.close()


def expected_counts(plan) -> dict:
    s = plan.summary()
    return {"providers": s["providers"], "branches": s["branches"], "batches": s["batches"], "provider_subjects": s["provider_subjects"],
            "batch_subjects": s["batch_subjects"], "staff": s["staff_identities"], "roles": s["role_assignments"],
            "teaching": s["teaching_assignments"], "students": s["students"], "enrollments": s["enrollments"], "papers": s["papers"]}


def actual_counts(fake) -> dict:
    return {"providers": len(fake.providers), "branches": len(fake.branches), "batches": len(fake.batches),
            "provider_subjects": len(fake.provider_subjects), "batch_subjects": len(fake.batch_subjects), "staff": len(fake.staff),
            "roles": len(fake.roles), "teaching": len(fake.teaching), "students": len(fake.students),
            "enrollments": len(fake.enrollments), "papers": len(fake.papers)}


def assert_state_matches_plan(fake: FakeInstitute, plan):
    assert actual_counts(fake) == expected_counts(plan)
    assert all(p["status"] == "APPROVED" for p in fake.papers.values())
    assert max(Counter(p["name"] for p in fake.providers.values()).values()) == 1
    assert max(Counter((b["provider_id"], b["name"]) for b in fake.branches.values()).values()) == 1
    assert max(Counter((t["branch_id"], t["name"]) for t in fake.batches.values()).values()) == 1
    assert max(Counter((e["student_id"], e["branch_id"]) for e in fake.enrollments.values()).values()) == 1
    assert max(Counter((p["branch"], p["subject"], p["title"]) for p in fake.papers.values()).values()) == 1
    per_student = Counter(e["student_id"] for e in fake.enrollments.values())
    assert set(per_student.values()) <= {1, 2, 3, 4}
    providers_per_student = Counter((e["student_id"], e["provider_id"]) for e in fake.enrollments.values())
    assert max(providers_per_student.values()) == 1, "distinct providers per student"
    sid_code = {s["subject_id"]: c for c, s in fake.subjects.items()}
    questions = Counter()
    for p in fake.papers.values():
        questions[sid_code[p["subject"]]] += len(p["questions"])
    assert dict(questions) == {c: n for c, n in plan.summary()["questions_per_subject"].items() if n}


# ------------------------------------------------------------------ complete runs
def test_full_run_creates_exactly_the_plan_and_records_timings_and_phases(plan, tmp_path):
    fake = FakeInstitute()
    result, _, out = run(fake, tmp_path / "j.json", plan)
    assert result["failed"] == {} and result["uncertain"] == {}
    assert_state_matches_plan(fake, plan)
    assert fake.unsafe_without_csrf == 0
    assert result["timings"]["POST /providers"]["count"] == len(plan.providers)
    assert result["timings"]["POST /branches/{id}/subjects/{id}/papers/upload"]["count"] == len(plan.papers)
    assert {"p50_s", "p95_s", "max_s", "statuses"} <= set(result["timings"]["POST /providers"])
    phases = json.loads((tmp_path / "j.json").read_text())["phases"]["full"]
    assert set(phases) == {"providers", "provider_subjects", "branches", "batches", "batch_subjects", "staff", "teaching", "students", "papers"}


def test_no_duplicate_listing_of_branches_and_batches(plan, tmp_path):
    fake = FakeInstitute()
    result, _, _ = run(fake, tmp_path / "j.json", plan)
    lists = result["timings"]
    assert lists["GET /providers/{id}/branches"]["count"] == sum(1 for p in plan.providers.values() if p.branches)
    assert lists["GET /branches/{id}/batches"]["count"] == len(plan.branches)


def test_second_run_is_idempotent_and_sends_no_create(plan, tmp_path):
    fake = FakeInstitute()
    run(fake, tmp_path / "j.json", plan)
    before, attempts = dict(fake.creates), dict(fake.attempts)
    result, _, _ = run(fake, tmp_path / "j.json", plan)
    assert dict(fake.creates) == before and dict(fake.attempts) == attempts
    assert result["failed"] == {}
    assert_state_matches_plan(fake, plan)


def test_lost_journal_needs_explicit_adoption_and_then_creates_nothing(plan, tmp_path):
    fake = FakeInstitute()
    run(fake, tmp_path / "j.json", plan)
    before = dict(fake.creates)
    with pytest.raises(AbortRun, match="does not know"):
        run(fake, tmp_path / "other.json", plan)
    run(fake, tmp_path / "other.json", plan, adopt=True)
    assert dict(fake.creates) == before
    assert_state_matches_plan(fake, plan)


# ------------------------------------------------------------------ lost responses and gateway faults
@pytest.mark.parametrize("kind", ["provider", "branch", "batch", "staff", "teaching", "enrollment", "paper", "approve"])
def test_lost_response_after_commit_never_duplicates(plan, tmp_path, kind):
    fake = FakeInstitute()
    fake.lose_response_after[kind] = 2                       # the 2nd create of this kind commits, its response is lost
    result, _, _ = run(fake, tmp_path / "j.json", plan)
    assert result["failed"] == {}, result["failed"]
    assert_state_matches_plan(fake, plan)


@pytest.mark.parametrize("kind", sorted(GATED))
@pytest.mark.parametrize("status", [504, 502, 500])
def test_gateway_error_after_commit_is_found_not_resent(plan, tmp_path, kind, status):
    fake = FakeInstitute()
    fake.gateway[kind] = {"at": 2, "mode": "after_commit", "status": status}
    result, _, _ = run(fake, tmp_path / "j.json", plan)
    assert result["failed"] == {}
    assert fake.attempts[kind] == expected_counts(plan)[GATED[kind]], "no create was sent twice"
    assert_state_matches_plan(fake, plan)


@pytest.mark.parametrize("kind", sorted(GATED))
def test_slow_upstream_commit_within_the_polling_window_is_waited_for(plan, tmp_path, kind):
    fake = FakeInstitute()
    fake.gateway[kind] = {"at": 1, "mode": "before_commit", "visible_after": 5}       # appears after 5 more GETs
    result, _, _ = run(fake, tmp_path / "j.json", plan, workers=1)
    assert result["failed"] == {}
    assert fake.attempts[kind] == expected_counts(plan)[GATED[kind]]
    assert_state_matches_plan(fake, plan)


@pytest.mark.parametrize("kind", sorted(GATED))
def test_commit_slower_than_the_polling_window_is_never_resent_in_the_same_run(plan, tmp_path, kind):
    fake = FakeInstitute()
    fake.gateway[kind] = {"at": 1, "mode": "before_commit", "visible_after": 10_000}  # still running when polling gives up
    result, _, _ = run(fake, tmp_path / "j.json", plan, workers=1)
    assert result["uncertain"], "the item is recorded as uncertain"
    assert fake.attempts[kind] <= expected_counts(plan)[GATED[kind]], "nothing was re-sent while the first request may still run"
    # the slow request finally commits; the next run must find it, not create a second one
    for item in fake.pending:
        item[0] = 1
    fake._tick_pending()
    result, _, _ = run(fake, tmp_path / "j.json", plan, workers=1)
    assert result["failed"] == {} and result["uncertain"] == {}
    assert fake.attempts[kind] == expected_counts(plan)[GATED[kind]], "the slow create was found, never sent twice"
    assert_state_matches_plan(fake, plan)


@pytest.mark.parametrize("kind", sorted(GATED))
def test_gateway_error_without_commit_is_created_by_the_next_run_exactly_once(plan, tmp_path, kind):
    fake = FakeInstitute()
    fake.gateway[kind] = {"at": 1, "mode": "no_commit", "status": 504}
    first, _, _ = run(fake, tmp_path / "j.json", plan)
    assert first["failed"]                                   # uncertain item (+ anything depending on it)
    result, _, _ = run(fake, tmp_path / "j.json", plan)
    assert result["failed"] == {}
    assert fake.attempts[kind] == expected_counts(plan)[GATED[kind]] + 1, "exactly one extra attempt, after the server check"
    assert_state_matches_plan(fake, plan)


def test_interrupted_upload_resumes_without_a_second_paper(plan, tmp_path):
    fake = FakeInstitute()
    fake.gateway["paper"] = {"at": 1, "mode": "before_commit", "visible_after": 10_000}
    with pytest.raises(AbortRun, match="max-failures"):
        run(fake, tmp_path / "j.json", plan, workers=1, max_failures=1)              # the run stops on the uncertain upload
    j = json.loads((tmp_path / "j.json").read_text())
    assert j["uncertain"]["papers"] and "papers" not in j["phases"].get("full", {})
    for item in fake.pending:                                # the interrupted upload completes on the server afterwards
        item[0] = 1
    fake._tick_pending()
    run(fake, tmp_path / "j.json", plan)
    assert fake.attempts["paper"] == len(plan.papers)
    assert_state_matches_plan(fake, plan)


# ------------------------------------------------------------------ canary
def test_canary_is_a_deterministic_subset_reused_by_the_full_run(plan, tmp_path):
    canary = P.canary(plan)
    assert P.canary(P.build(MINI)).summary() == canary.summary()
    fake = FakeInstitute()
    first, _, out = run(fake, tmp_path / "j.json", canary, workers=1, max_failures=1)
    assert first["scope"] == "canary" and first["failed"] == {}
    assert_state_matches_plan(fake, canary)
    canary_rows = (set(fake.providers), set(fake.branches), set(fake.batches), set(fake.staff), set(fake.enrollments), set(fake.papers))
    again, _, _ = run(fake, tmp_path / "j.json", canary, workers=1, max_failures=1)      # repeated canary: nothing new
    assert (set(fake.providers), set(fake.branches), set(fake.batches), set(fake.staff), set(fake.enrollments), set(fake.papers)) == canary_rows
    full, _, _ = run(fake, tmp_path / "j.json", plan)
    assert full["failed"] == {}
    assert_state_matches_plan(fake, plan)                     # nothing duplicated
    assert all(rows <= new for rows, new in zip(canary_rows, (set(fake.providers), set(fake.branches), set(fake.batches),
                                                              set(fake.staff), set(fake.enrollments), set(fake.papers))))
    assert fake.attempts["paper"] == len(plan.papers) and fake.attempts["enrollment"] == len(plan.memberships)
    phases = json.loads((tmp_path / "j.json").read_text())["phases"]
    assert set(phases) == {"canary", "full"}


def test_canary_staff_get_their_other_roles_in_the_full_run(plan, tmp_path):
    canary = P.canary(plan)
    shared = [s for s in canary.staff.values() if len(s.providers) > 1]
    assert shared, "the test plan must include a teacher who also works outside the canary"
    fake = FakeInstitute()
    run(fake, tmp_path / "j.json", canary, workers=1)
    assert len(fake.roles) == canary.summary()["role_assignments"]
    run(fake, tmp_path / "j.json", plan)
    assert len(fake.roles) == plan.summary()["role_assignments"]


def test_failed_validation_never_opens_the_full_run_gate(plan, tmp_path):
    j = Journal(tmp_path / "j.json", ident(plan))
    try:
        assert j.record_canary_validation({"ok": False, "rule_violations": ["x"]}) is False and not j.canary_validated()
        assert j.record_canary_validation({}) is False and not j.canary_validated()
        assert j.record_canary_validation({"ok": True}) is True and j.canary_validated()
        assert j.record_canary_validation({"ok": False}) is False and not j.canary_validated(), "a later failure closes it again"
    finally:
        j.close()


def test_a_phase_with_a_failed_item_is_never_marked_complete(plan, tmp_path):
    fake = FakeInstitute()
    fake.fail["enrollment"] = (422, "INVALID_BATCH")
    result, _, out = run(fake, tmp_path / "j.json", plan)
    phases = json.loads((tmp_path / "j.json").read_text())["phases"]["full"]
    assert "students" not in phases and {"providers", "branches", "papers"} <= set(phases)
    assert any("NOT complete" in line for line in out)


# ------------------------------------------------------------------ journal protection and staleness
def test_journal_is_locked_private_backed_up_and_tied_to_its_identity(plan, tmp_path):
    path = tmp_path / "results" / "j.json"
    j = Journal(path, ident(plan))
    with pytest.raises(JournalError, match="in use"):
        Journal(path, ident(plan))
    j.close()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    Journal(path, ident(plan)).close()
    backups = list(path.parent.glob("j.json.bak-*"))
    assert backups and all(stat.S_IMODE(os.stat(b).st_mode) == 0o600 for b in backups)
    for other in ({**ident(plan), "branch_ids": {**BRANCH_IDS, "neon-institute": "br-other"}},
                  {**ident(plan), "plan": "another-paper-size"}, {**ident(plan), "seed": 1}):
        with pytest.raises(JournalError, match="refusing to mix"):
            Journal(path, other)


def test_paper_size_is_part_of_the_plan_identity(plan):
    assert P.build(P.with_paper_size(MINI, 20)).fingerprint() != plan.fingerprint()
    with pytest.raises(ValueError):
        P.with_paper_size(MINI, 7)


def test_reset_branch_makes_the_journal_stale_and_nothing_is_written(plan, tmp_path):
    run(FakeInstitute(), tmp_path / "j.json", plan)
    fresh = FakeInstitute()                                   # the same branch after "Reset from parent": synthetic rows gone
    with pytest.raises(AbortRun, match="stale journal"):
        run(fresh, tmp_path / "j.json", plan)
    assert sum(fresh.attempts.values()) == 0 and sum(fresh.creates.values()) == 0


def test_stack_binding_is_rechecked_before_every_phase(plan, tmp_path):
    calls = []

    def check():
        calls.append(1)
        return ["lt-institute-identity-service: DATABASE_URL differs"] if len(calls) == 3 else []
    fake = FakeInstitute()
    with pytest.raises(AbortRun, match="stack binding changed before phase branches"):
        run(fake, tmp_path / "j.json", plan, phase_check=check)
    assert len(fake.branches) == 0 and len(fake.providers) == len(plan.providers)


# ------------------------------------------------------------------ errors, auth, secrets
def test_unexpected_response_is_recorded_and_resumed(plan, tmp_path):
    fake = FakeInstitute()
    fake.malformed["enrollment"] = 3                          # commits, then answers a body the loader cannot read
    first, _, _ = run(fake, tmp_path / "j.json", plan)
    assert "students" in first["failed"]
    result, _, _ = run(fake, tmp_path / "j.json", plan)
    assert result["failed"] == {}
    assert_state_matches_plan(fake, plan)


def test_failure_rate_above_threshold_stops_the_run(plan, tmp_path):
    fake = FakeInstitute()
    fake.fail["provider"] = (422, "INVALID")
    plan.providers.update({f"X{i}": P.Provider(f"X{i}", f"LT1 Provider X{i}", f"lt1.x{i}@loadtest.example", "NEET-I",
                                               P.PROFILES["NEET-I"]) for i in range(60)})
    with pytest.raises(AbortRun, match="failure rate"):
        run(fake, tmp_path / "j.json", plan)


def test_missing_subject_aborts_before_any_write(plan, tmp_path):
    fake = FakeInstitute(missing_subjects=("SUB008",))
    with pytest.raises(AbortRun, match="SUB008"):
        run(fake, tmp_path / "j.json", plan)
    assert sum(fake.attempts.values()) == 0 and sum(fake.creates.values()) == 0


def test_concurrent_session_expiry_triggers_exactly_one_reauthentication(plan, tmp_path):
    fake = FakeInstitute()
    fake.expire_session_after = 30
    api = Api("http://127.0.0.1:18010", transport=fake.transport(), sleep=lambda s: None)
    api.login("STF000001", lambda: PASSWORD, lambda: OTP)
    logins = []

    def relogin():
        logins.append(1)
        api.login("STF000001", lambda: PASSWORD, lambda: OTP)
    api.reauth = relogin
    journal = Journal(tmp_path / "j.json", ident(plan))
    try:
        Loader(api, plan, journal, workers=4, report=lambda s: None).run()
    finally:
        journal.close()
    assert len(logins) == 1
    assert_state_matches_plan(fake, plan)


def test_without_reauth_an_expired_session_is_reported():
    fake = FakeInstitute()
    api = Api("http://127.0.0.1:18010", transport=fake.transport(), sleep=lambda s: None)
    with pytest.raises(SessionExpired):
        api.get("/subjects")


def test_wrong_otp_or_password_never_authenticates():
    fake = FakeInstitute()
    api = Api("http://127.0.0.1:18010", transport=fake.transport(), sleep=lambda s: None)
    with pytest.raises(ApiError, match="INVALID_OTP"):
        api.login("STF000001", lambda: PASSWORD, lambda: "000000")
    with pytest.raises(ApiError, match="INVALID_CREDENTIALS"):
        api.login("STF000001", lambda: "wrong", lambda: OTP)


def test_secrets_never_reach_logs_journal_or_reports(plan, tmp_path, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    fake = FakeInstitute()
    fake.fail["enrollment"] = (500, f"BOOM password={PASSWORD} otp={OTP}")    # an error message that echoes secrets
    result, _, out = run(fake, tmp_path / "j.json", plan)
    csrf = next(iter(fake.sessions.values()))
    texts = [caplog.text, (tmp_path / "j.json").read_text(), "\n".join(out), capsys.readouterr().out, json.dumps(result)]
    for secret in (PASSWORD, OTP, csrf, *fake.sessions):
        assert all(secret not in t for t in texts)
    assert sum(PASSWORD.encode() in b for b in fake.seen_bodies) == 1
    assert sum(OTP.encode() in b for b in fake.seen_bodies) == 1
