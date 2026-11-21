"""tests/load/lt.sh: target profiles, background-job modes, clean config errors, label-only stop.
Rendering uses the real `docker compose config` on TEMPORARY placeholder configs (copies of the *.env.example files plus a fake
marker secret). Commands that would act on containers (stop, build, up, ps) run against recording stubs of `docker` and of the
guard's Python: no container is started, stopped or built, and no database is contacted."""
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LT = ROOT / "tests/load/lt.sh"
EXAMPLES = ROOT / "tests/load/config"
SECRET = "FAKE-MARKER-SECRET-7b1e"
JOBS = ("REFERENCE_SYNC_INTERVAL_SECONDS", "AUDIT_FORWARD_INTERVAL_SECONDS", "ATTEMPT_EXPIRY_SWEEP_SECONDS", "AUTO_EVALUATION_INTERVAL_SECONDS")
INSTITUTE = ("institute-identity-service", "institute-organization-service", "institute-academics-service", "institute-enrollment-service",
             "institute-paper-service", "institute-exam-service", "institute-operations-service")


def make_config(base: Path, *, local: bool) -> Path:
    d = base / "config"
    names = ["compose.env", "monitoring.env", "neon/student.env", "neon/institute.env"]
    if local:
        names += ["local/postgres.env", "local/student.env", "local/institute.env"]
    for name in names:
        dest = d / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(EXAMPLES / f"{name}.example", dest)
    (d / "compose.env").write_text((d / "compose.env").read_text().replace("INSTITUTE_IDENTITY_KEY=", f"INSTITUTE_IDENTITY_KEY={SECRET}"))
    for part in ("student", "institute"):
        f = d / f"neon/{part}.env"
        f.write_text(f.read_text().replace("USER:PASSWORD", f"user:{SECRET}"))
    return d


def lt(*args, config: Path, target: str | None = "neon", extra_env: dict | None = None, path_prefix: Path | None = None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("LT_")}
    env.update({"LT_CONFIG_DIR": str(config), "LT_TAG": "test"})
    if target:
        env["LT_TARGET"] = target
    if path_prefix:
        env["PATH"] = f"{path_prefix}:{env['PATH']}"
    env.update(extra_env or {})
    return subprocess.run(["bash", str(LT), *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)


def render(config: Path, *profiles, target="neon", sync=None) -> dict:
    env = {**os.environ, "LT_CONFIG_DIR": str(config), "LT_TARGET": target, "LT_TAG": "test"}
    env.pop("LT_REFERENCE_SYNC_INTERVAL_SECONDS", None)
    if sync is not None:
        env["LT_REFERENCE_SYNC_INTERVAL_SECONDS"] = sync
    args = [a for p in profiles for a in ("--profile", p)]
    out = subprocess.run(["docker", "compose", "--project-directory", str(ROOT), "-p", "student-portal-loadtest", "--env-file",
                          str(config / "compose.env"), "-f", "docker-compose.yml", "-f", "tests/load/compose.loadtest.yml", *args,
                          "config", "--format", "json"], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def jobs_section(stdout: str) -> dict[str, dict[str, str]]:
    lines = stdout.split("background jobs (0 = off):", 1)[1].strip().splitlines()
    return {ln.split()[0]: dict(kv.split("=", 1) for kv in ln.split()[1:]) for ln in lines}


@pytest.fixture()
def neon_cfg(tmp_path):
    return make_config(tmp_path, local=False)


@pytest.fixture()
def local_cfg(tmp_path):
    return make_config(tmp_path, local=True)


@pytest.fixture()
def stubs(tmp_path):
    """Recording stand-ins for `docker` and for the guard's Python."""
    bin_dir, log = tmp_path / "bin", tmp_path / "calls.log"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(f"""#!/usr/bin/env bash
echo "docker $* | sync=${{LT_REFERENCE_SYNC_INTERVAL_SECONDS:-unset}}" >> "{log}"
if [[ "$1" == ps ]]; then printf '%s' "${{FAKE_PS_IDS:-}}"; fi
exit 0
""")
    (bin_dir / "guard-python").write_text(f"""#!/usr/bin/env bash
echo "guard $*" >> "{log}"
exit "${{FAKE_GUARD_EXIT:-0}}"
""")
    for f in bin_dir.iterdir():
        f.chmod(f.stat().st_mode | stat.S_IXUSR)
    return bin_dir, log


def calls(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


# ------------------------------------------------------------------ config: target profiles and clean errors
def test_neon_config_works_without_local_postgres_files(neon_cfg):
    r = lt("config", config=neon_cfg)
    assert r.returncode == 0, r.stderr
    assert "lt-institute-gateway" in r.stdout and "lt-student-authentication-service" in r.stdout
    assert "lt-postgres" not in r.stdout and "postgres-exporter" not in r.stdout
    assert "lt-institute-exam-worker" not in r.stdout


def test_config_never_prints_rendered_secrets(neon_cfg):
    r = lt("config", config=neon_cfg)
    assert r.returncode == 0 and SECRET not in r.stdout + r.stderr


def test_local_config_without_its_env_files_fails_cleanly(neon_cfg):
    r = lt("config", config=neon_cfg, target="local")
    assert r.returncode != 0
    assert "/local/" in r.stderr and "not found" in r.stderr and "Compose could not render" in r.stderr
    assert "Traceback" not in r.stdout + r.stderr and str(ROOT) not in r.stderr and SECRET not in r.stdout + r.stderr


def test_local_config_with_its_env_files_includes_local_postgres(local_cfg):
    r = lt("config", config=local_cfg, target="local")
    assert r.returncode == 0, r.stderr
    assert "lt-postgres" in r.stdout and "lt-postgres-exporter" in r.stdout


def test_config_rejects_an_unknown_mode_and_a_missing_target(neon_cfg):
    assert lt("config", "everything", config=neon_cfg).returncode != 0
    r = lt("config", config=neon_cfg, target=None)
    assert r.returncode != 0 and "LT_TARGET" in r.stderr


# ------------------------------------------------------------------ background jobs
def test_all_background_writers_default_to_off(neon_cfg):
    r = lt("config", config=neon_cfg)
    jobs = jobs_section(r.stdout)
    assert all(jobs[s] == {"REFERENCE_SYNC_INTERVAL_SECONDS": "0", "AUDIT_FORWARD_INTERVAL_SECONDS": "0"} for s in INSTITUTE)
    assert jobs["student-attempt-service"] == jobs["student-result-service"] == \
           {"ATTEMPT_EXPIRY_SWEEP_SECONDS": "0", "AUTO_EVALUATION_INTERVAL_SECONDS": "0"}


def test_stage1_mode_turns_on_only_the_reference_sync(neon_cfg):
    jobs = jobs_section(lt("config", "app-stage1", config=neon_cfg).stdout)
    assert jobs["institute-enrollment-service"]["REFERENCE_SYNC_INTERVAL_SECONDS"] == "15"
    assert all(jobs[s]["AUDIT_FORWARD_INTERVAL_SECONDS"] == "0" for s in INSTITUTE)
    assert jobs["student-attempt-service"]["ATTEMPT_EXPIRY_SWEEP_SECONDS"] == "0"
    assert jobs["student-result-service"]["AUTO_EVALUATION_INTERVAL_SECONDS"] == "0"


def test_no_environment_can_switch_a_writer_on(neon_cfg):
    (neon_cfg / "compose.env").write_text((neon_cfg / "compose.env").read_text() + "LT_REFERENCE_SYNC_INTERVAL_SECONDS=99\n")
    for part, extra in (("institute", "REFERENCE_SYNC_INTERVAL_SECONDS=15\nAUDIT_FORWARD_INTERVAL_SECONDS=5\n"),
                        ("student", "ATTEMPT_EXPIRY_SWEEP_SECONDS=30\nAUTO_EVALUATION_INTERVAL_SECONDS=20\n")):
        f = neon_cfg / f"neon/{part}.env"
        f.write_text(f.read_text() + extra)
    r = lt("config", config=neon_cfg, extra_env={"LT_REFERENCE_SYNC_INTERVAL_SECONDS": "99"})
    jobs = jobs_section(r.stdout)
    assert {v for s in jobs.values() for v in s.values()} == {"0"}, jobs


def test_rendered_app_profile_excludes_the_worker_and_pins_every_interval(local_cfg):
    app = render(local_cfg, "app", target="local")["services"]
    assert "institute-exam-worker" not in app
    for name in INSTITUTE:
        assert app[name]["environment"]["REFERENCE_SYNC_INTERVAL_SECONDS"] == "0"
        assert app[name]["environment"]["AUDIT_FORWARD_INTERVAL_SECONDS"] == "0"
    for name in ("student-attempt-service", "student-result-service"):
        assert app[name]["environment"]["ATTEMPT_EXPIRY_SWEEP_SECONDS"] == "0"
        assert app[name]["environment"]["AUTO_EVALUATION_INTERVAL_SECONDS"] == "0"
    every = render(local_cfg, "*", target="local")["services"]
    assert every["institute-exam-worker"]["profiles"] == ["worker"]
    assert every["institute-exam-worker"]["environment"]["AUDIT_FORWARD_INTERVAL_SECONDS"] == "0"
    stage1 = render(local_cfg, "app", target="local", sync="15")["services"]
    assert stage1["institute-enrollment-service"]["environment"]["REFERENCE_SYNC_INTERVAL_SECONDS"] == "15"


# ------------------------------------------------------------------ up modes (stubs)
@pytest.mark.parametrize("mode, sync", [("app", "0"), ("app-stage1", "15")])
def test_up_modes_force_their_sync_value_and_never_start_the_worker(neon_cfg, stubs, mode, sync):
    bin_dir, log = stubs
    r = lt("up", mode, config=neon_cfg, path_prefix=bin_dir,
           extra_env={"LT_PYTHON": str(bin_dir / "guard-python"), "LT_REFERENCE_SYNC_INTERVAL_SECONDS": "99"})
    assert r.returncode == 0, r.stderr
    c = calls(log)
    assert c[0].startswith("guard ") and "check neon" in c[0]
    up = [x for x in c if x.startswith("docker compose")]
    assert len(up) == 1 and "--profile app up -d" in up[0] and up[0].endswith(f"sync={sync}")
    assert "worker" not in up[0] and "--profile db" not in up[0]


def test_up_student_is_stage2_only_guarded_and_never_touches_postgres_or_institute(neon_cfg, stubs):
    bin_dir, log = stubs
    py = {"LT_PYTHON": str(bin_dir / "guard-python")}
    assert lt("up", "student", config=neon_cfg, path_prefix=bin_dir, extra_env=py).returncode != 0 and calls(log) == []
    assert lt("up", "student", config=neon_cfg, target="stage2", path_prefix=bin_dir, extra_env={**py, "FAKE_GUARD_EXIT": "1"}).returncode != 0
    assert not any(x.startswith("docker") for x in calls(log))
    r = lt("up", "student", config=neon_cfg, target="stage2", path_prefix=bin_dir, extra_env=py)
    assert r.returncode == 0, r.stderr
    up = [x for x in calls(log) if x.startswith("docker compose")]
    assert len(up) == 1 and "student-attempt-service" in up[0] and " redis " in up[0]
    assert "lt-postgres" not in up[0] and "institute-" not in up[0].split(" up -d ")[1]


def test_up_app_starts_nothing_when_the_guard_refuses(neon_cfg, stubs):
    bin_dir, log = stubs
    r = lt("up", "app", config=neon_cfg, path_prefix=bin_dir,
           extra_env={"LT_PYTHON": str(bin_dir / "guard-python"), "FAKE_GUARD_EXIT": "1"})
    assert r.returncode != 0 and "nothing started" in r.stderr
    assert not any(x.startswith("docker") for x in calls(log))


def test_unknown_up_mode_is_rejected(neon_cfg, stubs):
    bin_dir, log = stubs
    r = lt("up", "worker", config=neon_cfg, path_prefix=bin_dir, extra_env={"LT_PYTHON": str(bin_dir / "guard-python")})
    assert r.returncode != 0 and calls(log) == []


def test_stage2_refuses_neon_and_never_uses_docker(neon_cfg, stubs):
    bin_dir, log = stubs
    r = lt("stage2", "load", config=neon_cfg, path_prefix=bin_dir, extra_env={"LT_PYTHON": str(bin_dir / "guard-python")})
    assert r.returncode != 0 and "local PostgreSQL only" in r.stderr and calls(log) == []


# ------------------------------------------------------------------ stop, build, ps (stubs)
def test_stop_needs_no_env_files_and_targets_only_the_project_label(tmp_path, stubs):
    bin_dir, log = stubs
    empty = tmp_path / "no-config"
    empty.mkdir()
    r = lt("stop", config=empty, target=None, path_prefix=bin_dir, extra_env={"FAKE_PS_IDS": "aaa111\nbbb222\n"})
    assert r.returncode == 0, r.stderr
    c = calls(log)
    assert c[0].startswith("docker ps -q --filter label=com.docker.compose.project=student-portal-loadtest |")
    assert c[1].startswith("docker stop aaa111 bbb222 |")
    assert len(c) == 2 and not any(x.startswith("docker compose") or x.startswith(("docker rm", "docker down")) for x in c)


def test_stop_with_nothing_running_stops_nothing(tmp_path, stubs):
    bin_dir, log = stubs
    r = lt("stop", config=tmp_path, target="neon", path_prefix=bin_dir)
    assert r.returncode == 0 and "no running" in r.stdout
    assert [x.split(" |")[0] for x in calls(log)] == ["docker ps -q --filter label=com.docker.compose.project=student-portal-loadtest"]


def test_build_uses_app_and_optional_profiles_only(neon_cfg, stubs):
    bin_dir, log = stubs
    r = lt("build", "student-profile-service", config=neon_cfg, path_prefix=bin_dir)
    assert r.returncode == 0, r.stderr
    (call,) = calls(log)
    assert "--profile app --profile optional build student-profile-service" in call and "--profile db" not in call


def test_build_profiles_resolve_the_optional_service_without_local_files(neon_cfg):
    model = render(neon_cfg, "app", "optional")
    assert "student-profile-service" in model["services"] and "lt-postgres" not in model["services"]


@pytest.mark.parametrize("target, has_db", [("neon", False), ("local", True)])
def test_ps_uses_the_target_profiles(neon_cfg, stubs, target, has_db):
    bin_dir, log = stubs
    r = lt("ps", config=neon_cfg, target=target, path_prefix=bin_dir)
    assert r.returncode == 0, r.stderr
    (call,) = calls(log)
    assert ("--profile db" in call) is has_db and "--profile app --profile optional --profile monitoring --profile k6" in call
