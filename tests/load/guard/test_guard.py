"""Branch-guard tests. The pure checks run anywhere; the live test runs only against a DISPOSABLE local PostgreSQL given in
LT_GUARD_TEST_PG (e.g. postgresql://u:p@127.0.0.1:25432/lt_student) and never touches Neon."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import guard  # noqa: E402

DENY = {"branch_ids": ["br-morning-paper-b8tphpl3", "br-old-moon-b8rqkho1"],
        "endpoint_ids": ["ep-dark-heart-b8aehym2", "ep-snowy-unit-b875nn0h"]}
NEON = {"kind": "neon", "project_id": "red-cloud-37130434", "branch_id": "br-load-student", "endpoint_id": "ep-load-1",
        "database": "neondb"}
LOCAL = {"kind": "local", "database": "lt_student", "identity": "7300000000000000001:16384"}


def neon_seen(**kw):
    return {"branch_id": "br-load-student", "endpoint_id": "ep-load-1", "project_id": "red-cloud-37130434",
            "database": "neondb", "read_only": True, "identity": "nosysid:16384", **kw}


def local_seen(**kw):
    return {"branch_id": None, "endpoint_id": None, "project_id": None, "database": "lt_student", "read_only": True,
            "identity": "7300000000000000001:16384", **kw}


def test_pinned_neon_branch_passes():
    assert guard.evaluate("neon-student", NEON, neon_seen(), DENY) == []


@pytest.mark.parametrize("branch, endpoint", [("br-morning-paper-b8tphpl3", "ep-dark-heart-b8aehym2"),
                                              ("br-old-moon-b8rqkho1", "ep-snowy-unit-b875nn0h")])
def test_development_branches_are_refused_even_if_pinned_by_mistake(branch, endpoint):
    target = {**NEON, "branch_id": branch, "endpoint_id": endpoint}
    problems = guard.evaluate("neon-student", target, neon_seen(branch_id=branch, endpoint_id=endpoint), DENY)
    assert any("DEVELOPMENT" in p for p in problems)


def test_other_neon_branch_is_refused():
    problems = guard.evaluate("neon-student", NEON, neon_seen(branch_id="br-something-else"), DENY)
    assert any("branch_id is br-something-else" in p for p in problems)


def test_unpinned_neon_target_is_refused():
    problems = guard.evaluate("neon-student", {**NEON, "branch_id": None, "endpoint_id": None}, neon_seen(), DENY)
    assert any("not pinned" in p for p in problems)


def test_wrong_project_and_write_session_are_refused():
    problems = guard.evaluate("neon-student", NEON, neon_seen(project_id="other", read_only=False), DENY)
    assert any("project is other" in p for p in problems) and "session is not read-only" in problems


def test_local_target_refuses_a_neon_server():
    problems = guard.evaluate("local-student", LOCAL, local_seen(branch_id="br-load-student", endpoint_id="ep-load-1"), DENY)
    assert any("answered from Neon" in p for p in problems)


def test_local_target_refuses_recreated_or_other_database():
    assert guard.evaluate("local-student", LOCAL, local_seen(), DENY) == []
    assert any("identity is" in p for p in guard.evaluate("local-student", LOCAL, local_seen(identity="7300000000000000001:99999"), DENY))
    assert any("database is lt_institute" in p for p in guard.evaluate("local-student", LOCAL, local_seen(database="lt_institute"), DENY))


def test_same_database_detection():
    assert guard.same_database(neon_seen(), neon_seen())
    assert not guard.same_database(neon_seen(), neon_seen(branch_id="br-load-institute"))
    assert not guard.same_database(local_seen(), local_seen(identity="7300000000000000001:16385"))


def test_env_parsing_keeps_unquoted_ampersands_and_strips_quotes(tmp_path, monkeypatch):
    env = tmp_path / "x.env"
    env.write_text("# c\nexport DATABASE_URL=postgresql+psycopg://u:p@h/db?sslmode=require&channel_binding=require\n"
                   "MIGRATION_DATABASE_URL='postgresql://u:p@h/db'\nEMPTY=\n", encoding="utf-8")
    parsed = guard.read_env(env)
    assert parsed["DATABASE_URL"].endswith("sslmode=require&channel_binding=require")
    assert parsed["MIGRATION_DATABASE_URL"] == "postgresql://u:p@h/db" and parsed["EMPTY"] == ""


def test_libpq_url_and_host_swap():
    assert guard.libpq_url("postgresql+psycopg://u:p@lt-postgres:5432/lt_student") == "postgresql://u:p@lt-postgres:5432/lt_student"
    assert guard.libpq_url("postgresql+psycopg://u:p@lt-postgres:5432/lt_student", "127.0.0.1:15432") == \
        "postgresql://u:p@127.0.0.1:15432/lt_student"


def test_lan_host_is_only_for_local_targets_and_private_addresses(monkeypatch):
    target = {"kind": "local", "connect_host": "127.0.0.1:15432"}
    assert guard.connect_host(target) == "127.0.0.1:15432"
    monkeypatch.setenv("LT_PG_CONNECT_HOST", "192.168.2.103:15432")
    assert guard.connect_host(target) == "192.168.2.103:15432"
    assert guard.connect_host({"kind": "neon"}) is None
    for bad in ("8.8.8.8:15432", "db.example.com:15432", "192.168.2.103"):
        monkeypatch.setenv("LT_PG_CONNECT_HOST", bad)
        with pytest.raises(guard.GuardError):
            guard.connect_host(target)


def test_redaction_hides_urls_and_passwords():
    fake = "s3" + "cret"                     # built at run time so the repository's secret scan never sees a URL with a password
    url = "postgresql+psycopg://user:" + fake + "@ep-x.neon.tech/db?sslmode=require"
    msg = guard.redact(f'connection to "{url}" failed; password={fake}')
    assert "s3cret" not in msg and "user:" not in msg


def test_missing_env_file_is_a_refusal_not_a_crash(capsys, monkeypatch, tmp_path):
    cfg = {"deny": DENY, "pairs": {}, "targets": {"local-student": {**LOCAL, "env_file": "local/__missing__.env"}}}
    monkeypatch.setattr(guard, "load_config", lambda: cfg)
    assert guard.main(["check", "local-student"]) == 1
    assert "env file missing" in capsys.readouterr().out


def test_guard_reads_env_files_from_the_same_dir_as_compose(monkeypatch, tmp_path):
    (tmp_path / "local").mkdir()
    (tmp_path / "local/student.env").write_text("DATABASE_URL=postgresql+psycopg://u:p@lt-postgres:5432/lt_student\n")
    monkeypatch.setenv("LT_CONFIG_DIR", str(tmp_path))
    assert guard.database_urls({"env_file": "local/student.env"}) == [("DATABASE_URL", "postgresql+psycopg://u:p@lt-postgres:5432/lt_student")]


@pytest.mark.skipif(not os.environ.get("LT_GUARD_TEST_PG"), reason="needs a disposable local PostgreSQL (LT_GUARD_TEST_PG)")
def test_live_local_observation_is_read_only_and_not_neon():
    seen = guard.observe(os.environ["LT_GUARD_TEST_PG"])
    assert seen["read_only"] and seen["branch_id"] is None and seen["endpoint_id"] is None
    assert seen["database"] == "lt_student" and seen["identity"].split(":")[1].isdigit()
    target = {"kind": "local", "database": "lt_student", "identity": seen["identity"]}
    assert guard.evaluate("local-student", target, seen, DENY) == []
    assert any("identity is" in p for p in guard.evaluate("local-student", {**target, "identity": "0:1"}, seen, DENY))
