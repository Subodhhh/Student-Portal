"""Pure checks of the generator, plus a resume test on the disposable fixture (python -m bulk.schema <dir> first; skipped when it is down)."""
import hashlib
import json
import socket
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bulk import __main__ as cli, load as L, tables as T
from bulk.world import STAGE2, World, scaled

ANCHOR = datetime(2026, 11, 4, tzinfo=timezone.utc)
FRACTION = 0.005


def world():
    return L.build_world(FRACTION, ANCHOR)


def digest(rows) -> str:
    h = hashlib.sha256()
    for r in rows:
        h.update(repr(r).encode())
    return h.hexdigest()


def test_scaling_keeps_the_shape_and_full_size_matches_the_targets():
    c = STAGE2
    assert c["providers"] == 10000 and sum(n * k for n, k in c["branches_per_provider"].items()) == 10000 and c["students"] == 50000
    assert c["questions_per_subject"] * 8 == 400000 and sum(n * k for n, k in c["memberships"].items()) == 100000
    p = scaled(c, 0.01)
    assert p["providers"] == 100 and p["students"] == 500 and p["questions_per_subject"] == 500
    assert sum(p["branches_per_provider"].values()) == 100


def test_the_same_input_gives_the_same_rows_and_another_seed_does_not():
    a, b = world(), world()
    for _db, name, _c, make in T.PLAN:
        if name in ("attempt_answers", "questions"):
            continue
        assert digest(make(a)) == digest(make(b)), name
    other = World({**scaled(STAGE2, FRACTION), "seed": 1}, ANCHOR)
    assert digest(T.providers(other)) != digest(T.providers(a))


def test_every_row_matches_its_column_list():
    w = world()
    for _db, name, columns, make in T.PLAN:
        first = next(iter(make(w)), None)
        assert first is not None, name
        assert len(first) == len(columns), name
        assert all(v is None or isinstance(v, str) for v in first), name


def test_large_tables_are_lazy():
    w = world()
    assert iter(T.answers(w)) is not None and hasattr(T.answers(w), "__next__")


def test_results_use_the_applications_scoring():
    assert World.score(4, [(1, "A", "A"), (2, "B", "A"), (3, "A", "A")]) == {
        "total_questions": 4, "attempted": 3, "correct": 2, "incorrect": 1, "unanswered": 1, "total_marks": 16, "obtained": 7, "accuracy": 66.67}


def test_paper_hash_is_the_paper_services_format():
    items = [("stem", "1", "2", "3", "4", "A", "4", "1")]
    assert T.hash_questions(items) == hashlib.sha256("stem\x1f1\x1f2\x1f3\x1f4\x1fA\x1f4\x1f1\x1e".encode()).hexdigest()


def test_each_student_sits_each_exam_once_and_only_past_exams_have_outcomes():
    w = world()
    for e in w.exams():
        students = [s for s, _e, _b in w.audience(e)]
        assert len(students) == len(set(students))
    attempted = {e.key for e, _s, _r in T._attempted(w)}
    assert attempted and all(e.end <= ANCHOR and not e.cancelled for e in w.exams() if e.key in attempted)
    assert any(e.start > ANCHOR for e in w.exams())


def test_the_full_dataset_needs_explicit_approval():
    with pytest.raises(SystemExit, match="approved-full"):
        cli.main(["load", "--fraction", "1"])


def test_generation_is_identical_across_processes_and_detects_drift():
    a, b = L.fingerprint(world()), L.fingerprint(world())
    assert a == b
    assert a != L.fingerprint(World({**scaled(STAGE2, FRACTION), "attendance": 0.5}, ANCHOR))


def test_the_bundle_holds_no_plain_secret_and_can_run_alone():
    from bulk import remote
    assert not any("env" in x and not x.endswith(".py") for x in remote.CODE)
    for rel in remote.CODE:
        assert (Path(__file__).resolve().parents[1] / rel).is_file(), rel


def test_pilot_databases_cannot_be_prepared_or_dropped():
    from bulk import remote
    with pytest.raises(SystemExit, match="never prepared"):
        remote.prepare("local")
    with pytest.raises(SystemExit, match="only the disposable"):
        remote.drop("local")
    with pytest.raises(SystemExit, match="only the disposable"):
        remote.drop("stage2")


def fixture_up() -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", 55439)) == 0


@pytest.mark.skipif(not fixture_up(), reason="disposable PostgreSQL fixture is not running")
def test_an_interrupted_load_resumes_to_exactly_the_clean_result(tmp_path, monkeypatch):
    import psycopg

    admin = psycopg.connect("host=127.0.0.1 port=55439 user=lt dbname=postgres", autocommit=True)
    for run in ("a", "b"):
        for db in ("student", "institute"):
            admin.execute(f"drop database if exists run{run}_{db}")
            admin.execute(f"create database run{run}_{db} template tpl_{db}")

    def conns(run):
        return {db: psycopg.connect(f"host=127.0.0.1 port=55439 user=lt dbname=run{run}_{db}") for db in ("student", "institute")}

    def table_digests(c):
        out = {}
        for db, table, columns, _m in T.PLAN:
            cols = ", ".join(f'"{x}"' for x in columns)
            out[f"{db}.{table}"] = c[db].execute(f'select count(*), md5(string_agg(x::text, \'|\' order by x::text collate "C")) from (select {cols} from "{table}") x').fetchone()
        return out

    clean = conns("a")
    L.load(clean, world(), tmp_path / "a.json", chunk=500, report=lambda _: None)

    broken = conns("b")
    original = T.answers
    rows_seen = {"n": 0}

    def crash_midway(w):
        for row in original(w):
            rows_seen["n"] += 1
            if rows_seen["n"] == 3210:
                raise RuntimeError("simulated crash")
            yield row

    plan = [(d, t, c, crash_midway if t == "attempt_answers" else m) for d, t, c, m in T.PLAN]
    monkeypatch.setattr(L, "PLAN", plan)
    with pytest.raises(RuntimeError):
        L.load(broken, world(), tmp_path / "b.json", chunk=500, report=lambda _: None)
    broken["student"].rollback()
    committed = broken["student"].execute("select count(*) from attempt_answers").fetchone()[0]
    assert committed == 3000                       # only whole chunks survive a crash
    broken["student"].rollback()
    monkeypatch.setattr(L, "PLAN", T.PLAN)
    state = L.load(broken, world(), tmp_path / "b.json", chunk=500, report=lambda _: None)
    assert state["finished_at"]
    assert json.loads((tmp_path / "b.json").read_text())["tables"]["student.attempt_answers"]["done"] is True
    assert table_digests(clean) == table_digests(broken)

    with pytest.raises(SystemExit, match="another dataset"):
        L.load(broken, L.build_world(FRACTION, datetime(2026, 12, 1, tzinfo=timezone.utc)), tmp_path / "b.json", report=lambda _: None)
    for c in (clean, broken):
        for conn in c.values():
            conn.close()
    for run in ("a", "b"):
        for db in ("student", "institute"):
            admin.execute(f"drop database run{run}_{db}")


@pytest.mark.skipif(not fixture_up(), reason="disposable PostgreSQL fixture is not running")
def test_a_target_with_foreign_rows_is_refused(tmp_path):
    import psycopg

    admin = psycopg.connect("host=127.0.0.1 port=55439 user=lt dbname=postgres", autocommit=True)
    for db in ("student", "institute"):
        admin.execute(f"drop database if exists busy_{db}")
        admin.execute(f"create database busy_{db} template tpl_{db}")
    c = {db: psycopg.connect(f"host=127.0.0.1 port=55439 user=lt dbname=busy_{db}") for db in ("student", "institute")}
    c["student"].execute("insert into providers (provider_id, name, email, status, created_at, updated_at) values (gen_random_uuid(), 'x', 'x@x', 'ACTIVE', now(), now())")
    c["student"].commit()
    with pytest.raises(SystemExit, match="already hold rows"):
        L.load(c, world(), Path(tmp_path) / "s.json")
    for conn in c.values():
        conn.close()
    for db in ("student", "institute"):
        admin.execute(f"drop database busy_{db}")
