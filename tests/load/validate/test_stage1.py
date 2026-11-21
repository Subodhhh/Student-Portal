"""Pure validation logic of Stage 1 with fixtures (no database)."""
import sys
from pathlib import Path

LOAD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOAD))
from generator import plan as P  # noqa: E402
from validate import stage1 as V  # noqa: E402

MINI = {**P.STAGE1, "seed": 7, "providers": 10, "branches_per_provider": {1: 7, 2: 2, 0: 1}, "students": 40,
        "memberships": {1: 14, 2: 16, 3: 6, 4: 4}, "questions_per_subject": 20, "questions_per_paper": 10}


def test_projection_diff_counts_missing_extra_and_changed_rows():
    inst = {"a": {"name": "x", "status": "ACTIVE"}, "b": {"name": "y", "status": "ACTIVE"}, "c": {"name": "z", "status": "ACTIVE"}}
    stu = {"a": {"name": "x", "status": "ACTIVE"}, "b": {"name": "y", "status": "INACTIVE"}, "d": {"name": "w", "status": "ACTIVE"}}
    d = V.diff_projection(inst, stu)
    assert (d["only_institute"], d["only_student"], d["field_differences"]) == (1, 1, 1)
    assert V.diff_projection(inst, dict(inst))["field_differences"] == 0


def test_plan_compared_with_itself_is_clean_and_drift_is_reported():
    plan = P.build(MINI)
    expected = V.expected_from_plan(plan)
    clean = V.compare_sets(expected, {k: set(v) for k, v in expected.items()})
    assert all(v["missing"] == 0 and v["unexpected"] == 0 for v in clean.values())
    actual = {k: set(v) for k, v in expected.items()}
    actual["providers"].discard(next(iter(actual["providers"])))
    actual["branches"].add(("LT1 Provider 001", "LT1 duplicate"))
    drift = V.compare_sets(expected, actual)
    assert drift["providers"]["missing"] == 1 and drift["branches"]["unexpected"] == 1


def test_expected_sets_have_plan_sizes():
    plan = P.build(MINI)
    e = V.expected_from_plan(plan)
    s = plan.summary()
    assert (len(e["providers"]), len(e["branches"]), len(e["batches"]), len(e["students"]), len(e["enrollments"]), len(e["papers"])) == \
           (s["providers"], s["branches"], s["batches"], s["students"], s["enrollments"], s["papers"])
    assert len(e["roles"]) == s["role_assignments"] and len(e["teaching"]) == s["teaching_assignments"]


def test_membership_rules():
    ok = [{"student": f"S{i}", "provider": p, "branch": p + "b", "status": "ACTIVE"} for i in range(4) for p in ("p1", "p2")]
    assert V.membership_rules(ok) == []
    bad = ok + [{"student": "S0", "provider": "p1", "branch": "other", "status": "ACTIVE"},
                {"student": "S9", "provider": "p1", "branch": "x", "status": "ACTIVE"}] + \
          [{"student": "S8", "provider": f"p{i}", "branch": f"b{i}", "status": "ACTIVE"} for i in range(5)]
    problems = " ".join(V.membership_rules(bad))
    assert "same provider" in problems and "5 memberships" in problems and "average" in problems


def test_question_rules():
    good = [{"subject": c, "stem": f"{c} q{i}", "options": ["1", "2", "3", "4"], "correct": "A", "marks": 4, "negative": 1}
            for c in P.SUBJECT_CODES for i in range(3)]
    expected = {c: 3 for c in P.SUBJECT_CODES}
    assert V.question_rules(good, expected) == []
    bad = good + [{"subject": "SUB001", "stem": "SUB001 q0", "options": ["1", "1", "3", "4"], "correct": "AB", "marks": 2, "negative": 0}]
    problems = " ".join(V.question_rules(bad, expected))
    assert "SUB001: 4 approved questions" in problems and "duplicated" in problems and "break" in problems


def test_canary_validation_uses_only_the_canary_subset():
    full = P.build(MINI)
    canary = P.canary(full)
    e = V.expected_from_plan(canary)
    s = canary.summary()
    assert (len(e["providers"]), len(e["branches"]), len(e["students"]), len(e["enrollments"]), len(e["papers"])) == \
           (s["providers"], s["branches"], s["students"], s["enrollments"], s["papers"])
    assert len(e["roles"]) == s["role_assignments"], "roles at providers outside the canary are not expected yet"
    assert all(r[2] in {p.name for p in canary.providers.values()} for r in e["roles"])
    rows = [{"subject": p.subject, "stem": f"{p.key} q{i}", "options": ["1", "2", "3", "4"], "correct": "A", "marks": 4, "negative": 1}
            for p in canary.papers.values() for i in range(canary.config["questions_per_paper"])]
    assert V.question_rules(rows, s["questions_per_subject"]) == []
    assert V.question_rules(rows, full.summary()["questions_per_subject"]), "the full plan's counts would not match"


def test_membership_average_is_only_enforced_for_the_full_plan():
    rows = [{"student": "S1", "provider": "p1", "branch": "b1", "status": "ACTIVE"}]
    assert V.membership_rules(rows, None) == []
    assert "average" in " ".join(V.membership_rules(rows, 2.0))


# ------------------------------------------------------------------ pre-load Student-only baseline
import copy  # noqa: E402
import json as _json  # noqa: E402

import pytest  # noqa: E402

BASE_FILE = V.BASELINE
PINNED = _json.loads((LOAD / "config/targets.json").read_text())["targets"]
PINNED_IDS = {n: PINNED[n]["branch_id"] for n in ("neon-student", "neon-institute")}


@pytest.fixture()
def baseline():
    return V.load_baseline(BASE_FILE, PINNED_IDS)


def enrollment_rows(n: int, prefix: str = "inst") -> dict:
    return {f"{prefix}-{i:03d}": {"provider_id": "p1", "branch_id": "b1", "student_id": f"STU{i:06d}", "status": "ACTIVE"} for i in range(n)}


def test_the_recorded_baseline_is_exactly_the_23_pre_load_enrollments(baseline):
    raw = _json.loads(BASE_FILE.read_text())
    assert raw["branch_ids"] == PINNED_IDS, "recorded on the pinned load-test branches"
    rows = raw["student_only"]["enrollments"]
    assert list(raw["student_only"]) == ["enrollments"] and len(rows) == 23 == raw["evidence"]["student_only"]
    assert len({r["student_id"] for r in rows.values()}) == 23
    assert all(r["created_at"] < "2026-11-03 14:31" for r in rows.values()), "all older than the first loader run"
    assert set(raw["evidence"]["canary_validation_examples_match"]) <= set(rows)
    assert set(baseline) == {"enrollments"} and len(baseline["enrollments"]) == 23
    assert all(set(r) == {"provider_id", "branch_id", "student_id", "status"} for r in baseline["enrollments"].values())


def test_the_23_baseline_rows_are_accepted_and_nothing_else_is_excused(baseline):
    institute = enrollment_rows(74)
    student = {**copy.deepcopy(institute), **copy.deepcopy(baseline["enrollments"])}
    d = V.diff_projection(institute, student, baseline["enrollments"])
    assert (d["only_institute"], d["only_student"], d["baseline_only_student"], d["field_differences"], d["baseline_changed"]) == (0, 0, 23, 0, 0)
    assert V.projection_ok(d)
    strict = V.diff_projection(institute, student)                         # without the baseline: the 23 are discrepancies
    assert strict["only_student"] == 23 and not V.projection_ok(strict)


def test_a_missing_institute_projection_fails(baseline):
    institute = enrollment_rows(5)
    student = {**{k: v for k, v in institute.items() if k != "inst-002"}, **baseline["enrollments"]}
    d = V.diff_projection(institute, student, baseline["enrollments"])
    assert d["only_institute"] == 1 and d["examples"]["only_institute"] == ["inst-002"] and not V.projection_ok(d)


def test_a_field_mismatch_fails(baseline):
    institute = enrollment_rows(5)
    student = {**copy.deepcopy(institute), **baseline["enrollments"]}
    student["inst-001"]["status"] = "INACTIVE"
    d = V.diff_projection(institute, student, baseline["enrollments"])
    assert d["field_differences"] == 1 and d["examples"]["field_differences"] == ["inst-001"] and not V.projection_ok(d)


def test_an_unexpected_new_student_only_record_fails(baseline):
    institute = enrollment_rows(5)
    student = {**copy.deepcopy(institute), **baseline["enrollments"],
               "new-stray-enrollment": {"provider_id": "p1", "branch_id": "b1", "student_id": "STU000999", "status": "ACTIVE"}}
    d = V.diff_projection(institute, student, baseline["enrollments"])
    assert (d["only_student"], d["baseline_only_student"]) == (1, 23) and d["examples"]["only_student"] == ["new-stray-enrollment"]
    assert not V.projection_ok(d)


def test_a_changed_baseline_row_fails(baseline):
    institute = enrollment_rows(3)
    student = {**institute, **copy.deepcopy(baseline["enrollments"])}
    changed = sorted(baseline["enrollments"])[0]
    student[changed]["status"] = "INACTIVE"
    d = V.diff_projection(institute, student, baseline["enrollments"])
    assert d["baseline_changed"] == 1 and d["baseline_only_student"] == 22 and d["examples"]["baseline_changed"] == [changed]
    assert not V.projection_ok(d)


def test_a_baseline_row_that_disappears_or_reaches_institute_is_reported_not_failed(baseline):
    first, second = sorted(baseline["enrollments"])[:2]
    institute = {**enrollment_rows(3), second: baseline["enrollments"][second]}          # now also in Institute, identical
    student = {**institute, **{k: v for k, v in baseline["enrollments"].items() if k != first}}
    d = V.diff_projection(institute, student, baseline["enrollments"])
    assert d["baseline_absent"] == 2 and d["baseline_only_student"] == 21 and V.projection_ok(d)


def test_the_enrollment_baseline_never_excuses_other_tables(baseline):
    providers = {"p1": {"name": "A", "email": "a@x.example", "status": "ACTIVE"}}
    stray = {**providers, "p-stray": {"name": "B", "email": "b@x.example", "status": "ACTIVE"}}
    d = V.diff_projection(providers, stray, baseline.get("providers"))
    assert d["only_student"] == 1 and not V.projection_ok(d)


def test_baseline_is_refused_on_other_branches_and_when_malformed(tmp_path):
    with pytest.raises(V.BaselineError, match="refusing"):
        V.load_baseline(BASE_FILE, {**PINNED_IDS, "neon-student": "br-some-other-branch"})
    raw = _json.loads(BASE_FILE.read_text())
    bad_table = tmp_path / "t.json"
    bad_table.write_text(_json.dumps({**raw, "student_only": {"students": {}}}))
    with pytest.raises(V.BaselineError, match="unknown table"):
        V.load_baseline(bad_table, PINNED_IDS)
    rows = copy.deepcopy(raw["student_only"]["enrollments"])
    first = sorted(rows)[0]
    del rows[first]["status"]
    bad_fields = tmp_path / "f.json"
    bad_fields.write_text(_json.dumps({**raw, "student_only": {"enrollments": rows}}))
    with pytest.raises(V.BaselineError, match="lacks synced fields"):
        V.load_baseline(bad_fields, PINNED_IDS)
    assert V.load_baseline(tmp_path / "missing.json", PINNED_IDS) == {}, "no file: strict comparison, nothing excused"
