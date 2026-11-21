"""The Stage 1 plan and its question papers, at FULL Stage 1 size (no network)."""
import csv
import io
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

LOAD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOAD))
from generator import plan as P  # noqa: E402
from generator.questions import paper_csv  # noqa: E402
from generator.fake_api import extraction  # noqa: E402  (the paper service's own CSV validator)

EMAIL = re.compile(r"^[a-z0-9.]+@loadtest\.example$")


@pytest.fixture(scope="module")
def plan():
    return P.build(P.STAGE1)


def test_targets_are_exact(plan):
    s = plan.summary()
    assert (s["providers"], s["branches"], s["students"], s["enrollments"]) == (100, 100, 1000, 2000)
    assert (s["memberships_min"], s["memberships_max"], s["memberships_avg"]) == (1, 4, 2.0)
    assert s["questions"] == 8000 and s["questions_per_subject"] == {c: 1000 for c in P.SUBJECT_CODES}
    assert (s["papers"], plan.config["questions_per_paper"]) == (400, 20), "20 questions per paper -> 400 papers, still 8,000"
    assert s["providers_without_branch"] == 15


def test_plan_is_deterministic(plan):
    again = P.build(P.STAGE1)
    assert again.summary() == plan.summary()
    assert [m.__dict__ for m in again.memberships.values()] == [m.__dict__ for m in plan.memberships.values()]


def test_natural_keys_are_unique_and_emails_synthetic(plan):
    assert len({p.email for p in plan.providers.values()}) == 100
    assert len({p.name for p in plan.providers.values()}) == 100
    assert len({(b.provider, b.name) for b in plan.branches.values()}) == len(plan.branches)
    assert len({(t.branch, t.name) for t in plan.batches.values()}) == len(plan.batches)
    emails = [s.email for s in plan.staff.values()] + [s.email for s in plan.students.values()] + [p.email for p in plan.providers.values()]
    assert len(emails) == len(set(emails)) and all(EMAIL.match(e) for e in emails)
    assert all(n.name.startswith("LT1 ") for n in [*plan.providers.values(), *plan.branches.values(), *plan.batches.values()])


def test_memberships_follow_the_business_rules(plan):
    for s in plan.students.values():
        ms = [plan.memberships[k] for k in s.memberships]
        provs = [m.provider for m in ms]
        assert 1 <= len(ms) <= 4 and len(set(provs)) == len(provs), "distinct providers per student"
        for m in ms:
            br = plan.branches[m.branch]
            assert br.provider == m.provider
            assert all(plan.batches[t].branch == m.branch for t in m.batches), "batches of the enrollment's own branch"
            assert len({plan.batches[t].class_level for t in m.batches}) == 1 and m.class_level == plan.batches[m.batches[0]].class_level
            for t, subs in m.chosen_subjects.items():
                assert t in m.batches and set(subs) < set(plan.batches[t].subjects) and subs, "a proper, non-empty subset"
    assert Counter(len(s.memberships) for s in plan.students.values()) == Counter(P.STAGE1["memberships"])


def test_academic_structure_and_teaching_are_consistent(plan):
    for t in plan.batches.values():
        assert set(t.subjects) <= set(plan.providers[plan.branches[t.branch].provider].subjects)
        assert t.class_level in {"11TH", "12TH"} and t.start_date <= t.end_date
    for x in plan.teaching.values():
        br = plan.branches[x.branch]
        teacher = plan.staff[x.staff]
        assert teacher.role == "TEACHER" and br.provider in teacher.providers
        assert x.subject in plan.providers[br.provider].subjects
        assert x.batch is None or (plan.batches[x.batch].branch == x.branch and x.subject in plan.batches[x.batch].subjects)
    admins = [s for s in plan.staff.values() if s.role == "PROVIDER_ADMIN"]
    assert len(admins) == 100 and sorted(a.providers[0] for a in admins) == sorted(plan.providers)
    assert any(len(s.providers) > 1 for s in plan.staff.values() if s.role == "TEACHER"), "some teachers work for two providers"
    covered = {c for p in plan.providers.values() if p.branches for c in p.subjects}
    assert covered == set(P.SUBJECT_CODES)


def test_every_paper_passes_the_application_validator_and_is_reproducible(plan):
    cfg = plan.config
    stems: dict[str, set] = {c: set() for c in P.SUBJECT_CODES}
    for paper in plan.papers.values():
        data = paper_csv(cfg["marker"], paper.subject, paper.first_question, cfg["questions_per_paper"], cfg["seed"])
        assert data == paper_csv(cfg["marker"], paper.subject, paper.first_question, cfg["questions_per_paper"], cfg["seed"])
        result = extraction.extract_csv(data, 500)
        assert result.ok, result.errors[:3]
        assert len(result.questions) == cfg["questions_per_paper"] == 20
        assert all(q.marks == 4 and q.negative == 1 and q.correct in "ABCD" for q in result.questions)
        new = {q.stem for q in result.questions}
        assert not new & stems[paper.subject], "stems unique within a subject"
        stems[paper.subject] |= new
        assert any(paper.branch in plan.branches and paper.subject in plan.batches[t].subjects for t in plan.branches[paper.branch].batches)
    assert all(len(v) == 1000 for v in stems.values())
    header = next(csv.reader(io.StringIO(data.decode())))
    assert header == ["question", "option_a", "option_b", "option_c", "option_d", "correct", "marks", "negative_marks", "explanation", "topic"]


@pytest.mark.parametrize("size, papers", [(20, 400), (25, 320), (50, 160), (100, 80)])
def test_configurable_paper_size_keeps_8000_questions_and_the_rest_of_the_plan(plan, size, papers):
    other = P.build(P.with_paper_size(P.STAGE1, size))
    s = other.summary()
    assert (s["papers"], s["questions"]) == (papers, 8000) and set(s["questions_per_subject"].values()) == {1000}
    same = {k: v for k, v in s.items() if k not in ("papers", "questions", "questions_per_subject")}
    assert same == {k: v for k, v in plan.summary().items() if k not in ("papers", "questions", "questions_per_subject")}
    with pytest.raises(ValueError):
        P.with_paper_size(P.STAGE1, 30)


def test_canary_is_a_small_deterministic_consistent_slice(plan):
    c = P.canary(plan)
    assert P.canary(P.build(P.STAGE1)).summary() == c.summary() and c.fingerprint() == plan.fingerprint()
    s = c.summary()
    assert s["providers"] == 2 and s["students"] <= P.CANARY_STUDENTS and s["papers"] == 2
    assert any(len(st.memberships) == 2 for st in c.students.values()), "a student of both canary providers"
    for m in c.memberships.values():
        assert m.provider in c.providers and m.branch in c.branches and all(t in c.batches for t in m.batches)
    for t in c.teaching.values():
        assert t.branch in c.branches and t.staff in c.staff and (t.batch is None or t.batch in c.batches)
    for st in c.staff.values():
        assert c.roles_of(st) and set(c.roles_of(st)) <= set(c.providers)
    assert all(p.branch in c.branches for p in c.papers.values())
    for kind in ("providers", "branches", "batches", "staff", "teaching", "students", "memberships", "papers"):
        sub, full = getattr(c, kind), getattr(plan, kind)
        assert all(full[k] is v for k, v in sub.items()), f"{kind}: the canary uses the full plan's own entities (same keys)"
