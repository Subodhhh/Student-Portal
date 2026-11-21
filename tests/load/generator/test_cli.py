"""Command-line gates (preflight mocked; nothing reaches the network, Neon or Docker)."""
import json
import sys
from pathlib import Path

import pytest

LOAD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOAD))
import generator.__main__ as cli  # noqa: E402
from generator.journal import Journal  # noqa: E402

PINNED = {"neon-student": "br-load-student", "neon-institute": "br-load-institute"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(cli.preflight, "pinned_branch_ids", lambda: dict(PINNED))
    monkeypatch.setattr(cli.preflight, "run_all", lambda *a, **k: True)
    monkeypatch.setattr(cli, "setup_logging", lambda: None)

    def no_prompt(*a, **k):
        raise AssertionError("must not ask for credentials")
    monkeypatch.setattr(cli.api_mod, "prompt_credentials", no_prompt)
    return tmp_path


def test_dry_run_sends_nothing_and_never_opens_the_journal(env, capsys):
    assert cli.main(["run", "--staff-code", "STF000001"]) == 0
    assert "dry run" in capsys.readouterr().out
    assert not (env / "results" / "journal.json").exists()


def test_full_run_is_refused_until_the_canary_validated(env, capsys):
    assert cli.main(["run", "--staff-code", "STF000001", "--execute"]) == 1
    assert "run and validate the canary first" in capsys.readouterr().out


def test_failed_preflight_sends_nothing(env, monkeypatch, capsys):
    monkeypatch.setattr(cli.preflight, "run_all", lambda *a, **k: False)
    assert cli.main(["run", "--canary", "--staff-code", "STF000001", "--execute"]) == 1
    assert "nothing was sent" in capsys.readouterr().out


def test_a_validated_canary_opens_the_gate_for_the_same_plan_only(env, capsys):
    full = cli.plan_mod.build(cli.plan_mod.STAGE1)
    j = Journal(env / "results" / "journal.json", cli.identity(full))
    j.record_canary_validation({"ok": True})
    j.close()
    with pytest.raises(AssertionError, match="credentials"):      # gate passed: it would now ask for the password
        cli.main(["run", "--staff-code", "STF000001", "--execute"])
    with pytest.raises(SystemExit, match="refusing to mix"):     # another paper size = another plan = another journal
        cli.main(["--questions-per-paper", "25", "run", "--staff-code", "STF000001", "--execute"])


def test_status_reads_without_locking(env, capsys):
    full = cli.plan_mod.build(cli.plan_mod.STAGE1)
    j = Journal(env / "results" / "journal.json", cli.identity(full))      # held by a "running" loader
    try:
        assert cli.main(["status"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["identity"]["branch_ids"] == PINNED and out["canary_validated"] is None
    finally:
        j.close()


def test_plan_command_prints_full_and_canary(env, capsys):
    assert cli.main(["plan"]) == 0
    full = json.loads(capsys.readouterr().out)
    assert (full["providers"], full["branches"], full["students"], full["enrollments"], full["questions"], full["papers"]) == \
           (100, 100, 1000, 2000, 8000, 400)
    assert cli.main(["plan", "--canary"]) == 0
    canary = json.loads(capsys.readouterr().out)
    assert canary["providers"] == 2 and canary["students"] <= 5


def test_results_directory_and_log_are_private(tmp_path, monkeypatch):
    import os
    import stat
    monkeypatch.setattr(cli, "RESULTS", tmp_path / "results")
    cli.setup_logging()
    assert stat.S_IMODE(os.stat(tmp_path / "results").st_mode) == 0o700
    assert stat.S_IMODE(os.stat(tmp_path / "results" / "loader.log").st_mode) == 0o600
