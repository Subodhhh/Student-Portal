"""Preflight checks with local fixture files, a fake `docker inspect` and a fake read-only connection (no Neon, Docker or network)."""
import json
import sys
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

LOAD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOAD))
from generator import preflight  # noqa: E402

URLS = {"student": "postgresql+psycopg://u:pw@ep-load-student-pooler.example/db?sslmode=require&channel_binding=require",
        "institute": "postgresql+psycopg://u:pw@ep-load-inst-pooler.example/db?sslmode=require&channel_binding=require"}


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    (tmp_path / "neon").mkdir()
    for part in ("student", "institute"):
        (tmp_path / f"neon/{part}.env").write_text(f"DATABASE_URL={URLS[part]}\nAPP_ENV=dev\nEMAIL_BACKEND=console\n"
                                                   "SESSION_COOKIE_SECURE=false\nMIGRATION_DATABASE_URL=\n")
    (tmp_path / "compose.env").write_text("INSTITUTE_IDENTITY_KEY=new-key-1\nSTUDENT_AUTH_SERVICE_KEY=new-key-2\n")
    monkeypatch.setenv("LT_CONFIG_DIR", str(tmp_path))
    return tmp_path


def container(name, part, *, project="student-portal-loadtest", running=True, url=None, email="console", cookie="false", sync="15",
              audit="0", expiry="0", evaluation="0"):
    env = [f"DATABASE_URL={url or URLS[part]}", f"EMAIL_BACKEND={email}", f"SESSION_COOKIE_SECURE={cookie}", "PATH=/usr/bin"]
    if part == "institute":
        env += [f"AUDIT_FORWARD_INTERVAL_SECONDS={audit}"] if audit is not None else []
        env += [f"REFERENCE_SYNC_INTERVAL_SECONDS={sync}"] if sync is not None else []
    if name == "lt-student-attempt-service" and expiry is not None:
        env.append(f"ATTEMPT_EXPIRY_SWEEP_SECONDS={expiry}")
    if name == "lt-student-result-service" and evaluation is not None:
        env.append(f"AUTO_EVALUATION_INTERVAL_SECONDS={evaluation}")
    return {"Name": "/" + name, "State": {"Running": running}, "Config": {"Labels": {"com.docker.compose.project": project}, "Env": env}}


def gateway(ports=None, project="student-portal-loadtest", running=True):
    return {"Name": "/" + preflight.GATEWAY, "State": {"Running": running},
            "Config": {"Labels": {"com.docker.compose.project": project}, "Env": []},
            "NetworkSettings": {"Ports": {"80/tcp": ports if ports is not None else [{"HostIp": "127.0.0.1", "HostPort": "18010"}]}}}


def inspect_with(**change):
    gw = change.pop("gw", None)
    worker = change.pop("worker", None)

    def inspect(names):
        out = [container(n, preflight.APP_CONTAINERS[n], **change) for n in names if n in preflight.APP_CONTAINERS]
        return out + [gw if gw is not None else gateway()] + ([worker] if worker else [])
    return inspect


def test_env_files_pass_and_detect_email_and_cookie_problems(cfg):
    assert preflight.check_env_files() == []
    (cfg / "neon/institute.env").write_text(f"DATABASE_URL={URLS['institute']}\nAPP_ENV=dev\nEMAIL_BACKEND=resend\n"
                                            "RESEND_API_KEY=re_x\nSESSION_COOKIE_SECURE=true\n")
    problems = " ".join(preflight.check_env_files())
    for expected in ("EMAIL_BACKEND must be console", "RESEND_API_KEY", "SESSION_COOKIE_SECURE must be false"):
        assert expected in problems


def test_background_job_values_in_env_files_are_irrelevant_to_the_env_check(cfg):
    # the Compose override sets them with precedence over env files; the running containers are what preflight verifies
    (cfg / "neon/institute.env").write_text((cfg / "neon/institute.env").read_text() + "REFERENCE_SYNC_INTERVAL_SECONDS=0\n")
    assert preflight.check_env_files() == []


def test_service_keys_must_be_new(cfg, tmp_path):
    root_env = tmp_path / "root.env"
    root_env.write_text("INSTITUTE_IDENTITY_KEY=dev-key\n")
    assert preflight.check_service_keys(root_env) == []
    root_env.write_text("SOMETHING=new-key-2\n")
    assert "reuses dev key values for STUDENT_AUTH_SERVICE_KEY" in " ".join(preflight.check_service_keys(root_env))
    (cfg / "compose.env").write_text("INSTITUTE_IDENTITY_KEY=\n")
    assert "empty" in " ".join(preflight.check_service_keys(root_env))


def test_stack_binding_accepts_only_the_guarded_urls(cfg):
    assert preflight.check_stack_binding(inspect_with()) == []
    problems = preflight.check_stack_binding(inspect_with(url="postgresql+psycopg://u:pw@ep-dev/db"))
    assert sum("DATABASE_URL differs" in p for p in problems) == len(preflight.APP_CONTAINERS)
    assert "pw@" not in " ".join(problems)                     # never prints the URL


@pytest.mark.parametrize("change, expected", [
    ({"project": "student-portal"}, "not part of project"),
    ({"running": False}, "not running"),
    ({"email": "smtp"}, "EMAIL_BACKEND is not console"),
    ({"cookie": "true"}, "SESSION_COOKIE_SECURE is not false"),
    ({"gw": gateway(ports=[{"HostIp": "0.0.0.0", "HostPort": "18010"}])}, "must publish port 80 on 127.0.0.1:18010 only"),
    ({"gw": gateway(ports=[{"HostIp": "127.0.0.1", "HostPort": "8010"}])}, "must publish port 80 on 127.0.0.1:18010 only"),
    ({"gw": gateway(project="student-portal")}, "lt-institute-gateway: not part of project"),
])
def test_stack_binding_refuses_dev_stopped_mailing_secure_cookie_paused_sync_or_foreign_gateway(cfg, change, expected):
    assert any(expected in p for p in preflight.check_stack_binding(inspect_with(**change)))


def test_missing_containers_are_reported(cfg):
    problems = preflight.check_stack_binding(lambda names: [])
    assert sum("not found" in p for p in problems) == len(preflight.APP_CONTAINERS) + 1


@pytest.mark.parametrize("url", ["http://127.0.0.1:8010", "http://localhost:8010", "http://10.0.0.5:18010", "https://127.0.0.1:18010"])
def test_base_url_must_be_the_loopback_load_test_gateway(url):
    assert preflight.check_base_url(url)


def test_base_url_health_is_checked():
    ok = httpx.MockTransport(lambda r: httpx.Response(200, json={"status": "ok"}))
    down = httpx.MockTransport(lambda r: httpx.Response(502))
    assert preflight.check_base_url("http://127.0.0.1:18010", ok) == []
    assert preflight.check_base_url("http://127.0.0.1:18010", down) == ["gateway /health answered 502"]


def fake_connect(count):
    class Cur:
        def fetchone(self):
            return (count,)

    class Conn:
        def execute(self, sql, params):
            assert sql.lstrip().lower().startswith("select") and params == ("LT1 Provider %",)
            return Cur()

    @contextmanager
    def connect(name):
        assert name == "neon-institute"
        yield Conn()
    return connect


@pytest.mark.parametrize("on_target, journal, ok, text", [
    (0, 0, True, ""), (2, 2, True, ""),
    (2, 0, False, "another run's data"), (100, 2, False, "another run's data"), (0, 2, False, "stale journal"),
])
def test_clean_target(tmp_path, on_target, journal, ok, text):
    path = tmp_path / "journal.json"
    if journal:
        path.write_text(json.dumps({"done": {"providers": {f"P{i}": f"id{i}" for i in range(journal)}}}))
    problems = preflight.check_clean_target("LT1", path, connect=fake_connect(on_target))
    assert (problems == []) is ok and text in " ".join(problems)


def test_run_all_fails_closed_and_skips_the_database_check_when_earlier_checks_fail(cfg, monkeypatch, tmp_path):
    lines = []
    monkeypatch.setattr(preflight, "check_stack_binding", lambda **k: [])
    monkeypatch.setattr(preflight, "check_base_url", lambda url: [])
    monkeypatch.setattr(preflight.guard, "cmd_check", lambda cfg, names: 1)
    assert preflight.run_all("http://127.0.0.1:18010", report=lines.append, journal_path=tmp_path / "j.json") is False
    assert "FAIL branch guard" in lines and any(line.startswith("SKIP clean target") for line in lines)


def test_run_all_reports_an_unreachable_database_without_secrets(cfg, monkeypatch, tmp_path):
    lines = []
    monkeypatch.setattr(preflight, "check_guard", lambda: [])
    monkeypatch.setattr(preflight, "check_service_keys", lambda: [])
    monkeypatch.setattr(preflight, "check_stack_binding", lambda **k: [])
    monkeypatch.setattr(preflight, "check_base_url", lambda url: [])

    def boom(marker, path):
        raise RuntimeError(f"cannot reach {URLS['institute']}")
    monkeypatch.setattr(preflight, "check_clean_target", boom)
    assert preflight.run_all("http://127.0.0.1:18010", report=lines.append, journal_path=tmp_path / "j.json") is False
    text = "\n".join(lines)
    assert "FAIL clean target" in text and "pw@" not in text


# ------------------------------------------------------------------ background jobs per mode
@pytest.mark.parametrize("mode, sync, ok", [
    ("preflight", "0", True), ("preflight", "15", False),
    ("stage1", "15", True), ("stage1", "0", False),
    (None, "0", True), (None, "15", True), (None, "7", False), (None, None, False),
])
def test_reference_sync_must_match_the_mode(cfg, mode, sync, ok):
    problems = preflight.check_stack_binding(inspect_with(sync=sync), mode=mode)
    assert (problems == []) is ok, problems


@pytest.mark.parametrize("mode", ["preflight", "stage1", None])
@pytest.mark.parametrize("change, key", [
    ({"audit": "5"}, "AUDIT_FORWARD_INTERVAL_SECONDS"), ({"audit": None}, "AUDIT_FORWARD_INTERVAL_SECONDS"),
    ({"expiry": "30"}, "ATTEMPT_EXPIRY_SWEEP_SECONDS"), ({"expiry": None}, "ATTEMPT_EXPIRY_SWEEP_SECONDS"),
    ({"evaluation": "20"}, "AUTO_EVALUATION_INTERVAL_SECONDS"), ({"evaluation": None}, "AUTO_EVALUATION_INTERVAL_SECONDS"),
])
def test_unrelated_writers_must_be_off_in_every_mode(cfg, mode, change, key):
    sync = "0" if mode == "preflight" else "15"
    problems = preflight.check_stack_binding(inspect_with(sync=sync, **change), mode=mode)
    assert any(key in p and "must be 0" in p for p in problems), problems


def test_audit_forwarding_is_checked_on_every_institute_container(cfg):
    problems = preflight.check_stack_binding(inspect_with(audit="5"), mode="stage1")
    institute = [n for n, part in preflight.APP_CONTAINERS.items() if part == "institute"]
    assert sum("AUDIT_FORWARD_INTERVAL_SECONDS" in p for p in problems) == len(institute)


def test_a_running_exam_worker_is_refused_a_stopped_one_is_fine(cfg):
    running = container(preflight.WORKER, "institute")
    stopped = container(preflight.WORKER, "institute", running=False)
    assert any("must not run" in p for p in preflight.check_stack_binding(inspect_with(worker=running), mode="stage1"))
    assert preflight.check_stack_binding(inspect_with(worker=stopped), mode="stage1") == []
    assert preflight.WORKER not in preflight.APP_CONTAINERS


def test_unknown_mode_is_rejected(cfg):
    with pytest.raises(ValueError):
        preflight.check_stack_binding(inspect_with(), mode="everything")
