"""init_env.py against a temporary copy of the templates (no real files, no network). Fake credentials only."""
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import init_env as I  # noqa: E402

STUDENT = "postgresql://neondb_owner:Fake-Secret-111@ep-cool-load-a1b2c3d4-pooler.c-14.us-east-1.aws.neon.tech/Institute_Student_Platform_dev?sslmode=require&channel_binding=require"
INSTITUTE = "postgresql://neondb_owner:Fake-Secret-222@ep-warm-load-e5f6a7b8-pooler.c-14.us-east-1.aws.neon.tech/Institute_Student_Platform_dev?sslmode=require&channel_binding=require"


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    d = root / "tests" / "load" / "config"
    for example in Path(I.CONFIG).rglob("*.env.example"):
        dest = d / example.relative_to(I.CONFIG)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(example, dest)
    monkeypatch.setattr(I, "CONFIG", d)
    return d


def read(path: Path) -> dict:
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and not line.startswith("#"))


def test_neon_files_are_private_complete_and_converted(cfg, capsys):
    answers = iter([STUDENT, INSTITUTE])
    I.init_neon(False, ask=lambda prompt: next(answers))
    for name in ("compose.env", "monitoring.env", "neon/student.env", "neon/institute.env"):
        p = cfg / name
        assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(cfg / "neon").st_mode) == 0o700
    keys = read(cfg / "compose.env")
    assert len(keys) == 13 and all(len(v) >= 40 for v in keys.values()) and len(set(keys.values())) == 13
    assert len(read(cfg / "monitoring.env")["GF_SECURITY_ADMIN_PASSWORD"]) >= 40
    s = read(cfg / "neon/student.env")
    assert s["DATABASE_URL"].startswith("postgresql+psycopg://") and "ep-cool-load" in s["DATABASE_URL"]
    assert s["EMAIL_BACKEND"] == "console" and s["SESSION_COOKIE_SECURE"] == "false"
    out = capsys.readouterr()
    for secret in ("Fake-Secret-111", "Fake-Secret-222", *keys.values()):
        assert secret not in out.out + out.err


@pytest.mark.parametrize("url, reason", [
    (STUDENT.replace("ep-cool-load-a1b2c3d4", "ep-dark-heart-b8aehym2"), "DEVELOPMENT"),
    (STUDENT.replace("-pooler", ""), "POOLED"),
    (STUDENT.replace("neon.tech", "example.com"), "POOLED"),
    (STUDENT.replace("?sslmode=require&", "?"), "sslmode"),
    ("not a url", "complete"),
])
def test_bad_urls_are_refused_without_echoing_them(url, reason):
    with pytest.raises(I.Refused) as e:
        I.check_neon_url(url)
    assert reason in str(e.value.code) and "Fake-Secret" not in str(e.value.code)


def test_same_endpoint_for_both_branches_is_refused(cfg):
    answers = iter([STUDENT, STUDENT])
    with pytest.raises(I.Refused, match="same endpoint"):
        I.init_neon(False, ask=lambda prompt: next(answers))
    assert not (cfg / "neon/student.env").exists()


def test_existing_files_are_never_overwritten_without_force(cfg):
    (cfg / "compose.env").write_text("KEEP=me\n")
    answers = iter([STUDENT, INSTITUTE])
    I.init_neon(False, ask=lambda prompt: next(answers))
    assert (cfg / "compose.env").read_text() == "KEEP=me\n"
    I.init_neon(False, ask=lambda prompt: pytest.fail("must not ask again for existing files"))


def test_local_passwords_stay_in_step(cfg):
    I.init_local(False)
    pg = read(cfg / "local/postgres.env")
    assert pg["LT_STUDENT_PASSWORD"] in read(cfg / "local/student.env")["DATABASE_URL"]
    assert pg["LT_INSTITUTE_PASSWORD"] in read(cfg / "local/institute.env")["DATABASE_URL"]
    assert pg["POSTGRES_USER"] == "lt_admin" and len(pg["DATA_SOURCE_PASS"]) >= 40
