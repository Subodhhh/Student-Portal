"""Proves the load-test stack cannot collide with the dev stack, using only the rendered Compose models (nothing is started).
The load-test model is rendered with PLACEHOLDER copies of the *.env.example files (no credentials), the dev model with the
real dev configuration; only names, ports, networks, volumes and file paths are compared, never values.
Run: tests/load/.venv/bin/python -m pytest tests/load/test_compose_isolation.py"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "tests/load/config"


def render(args: list[str], env: dict | None = None) -> dict:
    out = subprocess.run(["docker", "compose", *args, "config", "--format", "json"], cwd=ROOT, env={**os.environ, **(env or {})},
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def placeholder_config(tmp_path_factory):
    d = tmp_path_factory.mktemp("lt-config")
    for example in CONFIG.rglob("*.env.example"):
        dest = d / example.relative_to(CONFIG).with_suffix("")      # x.env.example -> x.env
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(example, dest)
    return d


@pytest.fixture(scope="module", params=["neon", "local"])
def lt(request, placeholder_config):
    args = ["--project-directory", str(ROOT), "-p", "student-portal-loadtest", "--env-file", str(placeholder_config / "compose.env"),
            "-f", "docker-compose.yml", "-f", "tests/load/compose.loadtest.yml", "--profile", "*"]
    return request.param, placeholder_config, render(args, {"LT_TARGET": request.param, "LT_CONFIG_DIR": str(placeholder_config),
                                                              "LT_TAG": "test"})


@pytest.fixture(scope="module")
def dev():
    return render([])     # the untouched dev model exactly as `docker compose up` runs it (its legacy profile has no .env here)


def host_ports(model):
    return {(p.get("host_ip", ""), p["published"]) for s in model["services"].values() for p in s.get("ports", []) if p.get("published")}


def test_project_and_container_names_are_disjoint(lt, dev):
    _, _, m = lt
    assert m["name"] == "student-portal-loadtest" and dev["name"] == "student-portal"
    lt_names = {s["container_name"] for s in m["services"].values()}
    dev_names = {s.get("container_name") for s in dev["services"].values()}
    assert all(n.startswith("lt-") for n in lt_names)
    assert not lt_names & dev_names


def test_ports_are_loopback_only_and_never_dev_ports(lt, dev):
    _, _, m = lt
    ports = host_ports(m)
    assert ports and all(ip == "127.0.0.1" for ip, _ in ports)
    assert not {p for _, p in ports} & {p for _, p in host_ports(dev)}


def test_edge_network_is_detached_and_networks_are_project_scoped(lt):
    _, _, m = lt
    assert "student-portal-edge" not in m.get("networks", {})
    for name, s in m["services"].items():
        assert "student-portal-edge" not in (s.get("networks") or {}), name
    assert all(n["name"].startswith("student-portal-loadtest") for n in m["networks"].values())


def test_volumes_are_project_scoped(lt, dev):
    _, _, m = lt
    lt_vols = {v["name"] for v in m["volumes"].values()}
    assert all(v.startswith("student-portal-loadtest_") for v in lt_vols)
    assert not lt_vols & {v["name"] for v in dev.get("volumes", {}).values()}


def test_env_files_come_only_from_the_load_test_config(lt):
    target, cfg, m = lt
    for name, s in m["services"].items():
        for f in s.get("env_file") or []:
            path = f["path"] if isinstance(f, dict) else f
            assert path.startswith(str(cfg)), (name, path)
            assert "/deploy/" not in path and not path.endswith("/.env"), (name, path)
    stu = m["services"]["student-authentication-service"]["environment"]
    assert "CHANGE_ME" in stu["DATABASE_URL"] or "USER:PASSWORD" in stu["DATABASE_URL"]       # the placeholder, not a dev URL
    assert ("lt-postgres:5432/lt_student" in stu["DATABASE_URL"]) == (target == "local")


def test_service_keys_do_not_come_from_the_root_env(lt):
    _, _, m = lt
    # compose.env.example leaves every key empty: an interpolated dev key would show up as a non-empty value here
    assert m["services"]["student-enrollment-service"]["environment"]["INSTITUTE_SERVICE_API_KEYS"] == ""
    assert m["services"]["institute-enrollment-service"]["environment"]["STUDENT_SERVICE_API_KEY"] == ""


def test_images_never_reuse_dev_tags(lt, dev):
    _, _, m = lt
    dev_built = {s["image"] for s in dev["services"].values() if s.get("build")}
    for name, s in m["services"].items():
        if s.get("build"):
            assert s["image"].startswith("loadtest/") and s["image"].endswith(":test"), name
            assert s["image"] not in dev_built


def test_every_service_is_opt_in_limited_and_never_auto_restarts(lt):
    _, _, m = lt
    for name, s in m["services"].items():
        assert s.get("profiles"), f"{name} would start without a mode"
        if "never" not in s["profiles"] and "legacy" not in s["profiles"]:
            assert s.get("mem_limit"), f"{name} has no memory limit"
            assert s.get("restart", "no") == "no", name


def test_monitoring_profile_uses_the_sampler_not_cadvisor(lt):
    _, _, m = lt
    s = m["services"]
    assert s["cadvisor"]["profiles"] == ["never"], "cAdvisor gives no per-container data on Docker Desktop"
    assert s["grafana"]["mem_limit"] in (268435456, "268435456", "256m")
    assert any(h.replace("=", ":") == "host.docker.internal:host-gateway" for h in s["prometheus"].get("extra_hosts", []))
    monitoring = {n for n, x in s.items() if "monitoring" in x.get("profiles", [])}
    assert monitoring == {"prometheus", "grafana", "redis-exporter"}
