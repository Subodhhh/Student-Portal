"""Docker stats sampler: parsing, project filtering, rendering and the loopback-only rule (fake `docker`, no daemon needed)."""
import json
import math
import sys
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import docker_stats_exporter as X  # noqa: E402


@pytest.mark.parametrize("text, expected", [("0B", 0), ("512B", 512), ("1.5kB", 1500), ("2MB", 2e6), ("137.3MiB", 137.3 * 1024 ** 2),
                                            ("1GiB", 1024 ** 3), (" 3.4 GB ", 3.4e9)])
def test_sizes(text, expected):
    assert X.size(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["--", "", "abc", "5XB"])
def test_unparseable_sizes_are_nan(text):
    assert math.isnan(X.size(text))


def test_pairs_and_percent():
    assert X.pair("1.2kB / 3.4MB") == pytest.approx((1200, 3.4e6))
    assert X.percent("152.31%") == pytest.approx(152.31) and math.isnan(X.percent("--"))


def fake_docker(stats_rows, ps_rows):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[1] == "ps":
            assert "label=com.docker.compose.project=student-portal-loadtest" in cmd
            return SimpleNamespace(returncode=0, stdout="".join(f"{n}\t{s}\n" for n, s in ps_rows))
        assert cmd[1] == "stats" and "--no-stream" in cmd
        return SimpleNamespace(returncode=0, stdout="".join(json.dumps(r) + "\n" for r in stats_rows))
    return run, calls


STATS = [{"Name": "lt-redis", "CPUPerc": "1.25%", "MemUsage": "4.5MiB / 128MiB", "MemPerc": "3.52%", "NetIO": "1.2kB / 800B",
          "BlockIO": "0B / 4.1kB", "PIDs": "6"},
         {"Name": "lt-grafana", "CPUPerc": "152.31%", "MemUsage": "137.3MiB / 256MiB", "MemPerc": "53.63%", "NetIO": "--",
          "BlockIO": "10MB / 0B", "PIDs": "14"},
         {"Name": "student-auth-redis", "CPUPerc": "9%", "MemUsage": "1MiB / 3.7GiB", "MemPerc": "0%", "NetIO": "0B / 0B",
          "BlockIO": "0B / 0B", "PIDs": "1"}]


def test_only_project_containers_are_sampled_and_labelled_by_service():
    run, calls = fake_docker(STATS, [("lt-redis", "redis"), ("lt-grafana", "grafana")])
    rows = X.sample("student-portal-loadtest", run)
    assert {r["name"] for r in rows} == {"lt-redis", "lt-grafana"}, "dev containers are never reported"
    assert calls[1][-2:] == ["lt-grafana", "lt-redis"]
    g = next(r for r in rows if r["name"] == "lt-grafana")
    assert g["service"] == "grafana" and g["cpu_percent"] == pytest.approx(152.31)
    assert g["memory_usage_bytes"] == pytest.approx(137.3 * 1024 ** 2) and g["memory_limit_bytes"] == 256 * 1024 ** 2
    assert math.isnan(g["network_receive_bytes"])


def test_no_running_project_containers_means_no_stats_call():
    run, calls = fake_docker(STATS, [])
    assert X.sample("student-portal-loadtest", run) == [] and len(calls) == 1


def test_render_is_valid_prometheus_text_and_skips_nan():
    run, _ = fake_docker(STATS, [("lt-redis", "redis"), ("lt-grafana", "grafana")])
    text = X.render(X.sample("student-portal-loadtest", run), "student-portal-loadtest", {"ok": True, "duration": 1.5, "last_success": 1e9})
    assert 'lt_container_cpu_percent{project="student-portal-loadtest",name="lt-redis",service="redis"} 1.25' in text
    assert 'lt_container_memory_limit_bytes{project="student-portal-loadtest",name="lt-grafana",service="grafana"} 268435456' in text
    assert 'lt_container_network_receive_bytes{project="student-portal-loadtest",name="lt-grafana"' not in text
    assert "lt_sampler_up 1" in text and "lt_sampler_containers 2" in text
    assert "nan" not in text.lower()
    for line in text.splitlines():
        assert line.startswith("#") or len(line.rsplit(" ", 1)) == 2


def test_a_failed_sample_keeps_the_last_good_one_and_reports_down():
    run, _ = fake_docker(STATS, [("lt-redis", "redis")])
    s = X.Sampler("student-portal-loadtest", 10, run)
    s.once()
    assert "lt_sampler_up 1" in s.text()

    def broken(cmd, **kw):
        return SimpleNamespace(returncode=1, stdout="")
    s.run = broken
    s.once()
    text = s.text()
    assert "lt_sampler_up 0" in text and 'name="lt-redis"' in text


@pytest.mark.parametrize("host", ["0.0.0.0", "172.17.213.104", "::", "192.168.1.5"])
def test_non_loopback_addresses_are_refused(host):
    with pytest.raises(SystemExit, match="loopback only"):
        X.loopback_only(host)


def test_serves_metrics_on_loopback():
    run, _ = fake_docker(STATS, [("lt-redis", "redis")])
    s = X.Sampler("student-portal-loadtest", 10, run)
    s.once()
    server = X.serve(s, "127.0.0.1", 0)
    import threading
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        body = urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5).read().decode()
        assert 'name="lt-redis"' in body
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5)
    finally:
        server.shutdown()
