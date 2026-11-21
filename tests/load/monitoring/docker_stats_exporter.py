#!/usr/bin/env python3
"""Per-container CPU / memory / network / block I/O of the load-test project, for Prometheus.

Why: cAdvisor cannot identify containers on Docker Desktop ("failed to identify the read-write layer"), so container metrics come
from the Docker CLI instead. This runs on the WSL host (never in a container, no Docker socket mount), samples
`docker stats --no-stream` every --interval seconds for the containers labelled com.docker.compose.project=<project>, and serves
the latest sample in the Prometheus text format on 127.0.0.1 only (loopback is enforced). Prometheus (in Docker Desktop) scrapes
it as host.docker.internal:19101, which Docker Desktop forwards to this loopback listener; the LAN cannot reach it.
Standard library only. Container names and services are the only labels; no environment, command line or secret is read.

    python3 tests/load/monitoring/docker_stats_exporter.py [--project student-portal-loadtest] [--port 19101] [--interval 10]
    (normally through `tests/load/lt.sh sampler start|stop|status`)"""
import argparse
import ipaddress
import json
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UNITS = {"b": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9, "tb": 1e12, "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3, "tib": 1024 ** 4}
_SIZE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([A-Za-z]*)\s*$")


def size(text: str) -> float:
    """'137.3MiB' -> bytes; '0B' -> 0; '--' -> nan."""
    m = _SIZE.match(text or "")
    if not m:
        return float("nan")
    unit = m.group(2).lower() or "b"
    if unit not in UNITS:
        return float("nan")
    return float(m.group(1)) * UNITS[unit]


def pair(text: str) -> tuple[float, float]:
    """'1.2kB / 3.4MB' -> (1200.0, 3400000.0)."""
    left, _, right = (text or "").partition("/")
    return size(left), size(right)


def percent(text: str) -> float:
    try:
        return float((text or "").strip().rstrip("%"))
    except ValueError:
        return float("nan")


def project_containers(project: str, run=subprocess.run) -> dict[str, str]:
    """Running containers of the project: name -> compose service."""
    out = run(["docker", "ps", "--filter", f"label=com.docker.compose.project={project}",
               "--format", '{{.Names}}\t{{.Label "com.docker.compose.service"}}'], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise RuntimeError(f"docker ps failed ({out.returncode})")
    rows = (line.split("\t", 1) for line in out.stdout.splitlines() if line.strip())
    return {name: (svc if svc else name) for name, svc in ((r[0], r[1] if len(r) > 1 else "") for r in rows)}


def sample(project: str, run=subprocess.run) -> list[dict]:
    names = project_containers(project, run)
    if not names:
        return []
    out = run(["docker", "stats", "--no-stream", "--format", "{{json .}}", *sorted(names)], capture_output=True, text=True, timeout=60)
    if out.returncode != 0 and not out.stdout:
        raise RuntimeError(f"docker stats failed ({out.returncode})")
    rows = []
    for line in out.stdout.splitlines():
        if not line.strip():
            continue
        s = json.loads(line)
        name = s.get("Name", "")
        if name not in names:
            continue
        mem_used, mem_limit = pair(s.get("MemUsage", ""))
        rx, tx = pair(s.get("NetIO", ""))
        rd, wr = pair(s.get("BlockIO", ""))
        try:
            pids = float(s.get("PIDs", "nan"))
        except ValueError:
            pids = float("nan")
        rows.append({"name": name, "service": names[name], "cpu_percent": percent(s.get("CPUPerc")),
                     "memory_usage_bytes": mem_used, "memory_limit_bytes": mem_limit, "memory_percent": percent(s.get("MemPerc")),
                     "network_receive_bytes": rx, "network_transmit_bytes": tx, "block_read_bytes": rd, "block_write_bytes": wr,
                     "pids": pids})
    return rows


HELP = {
    "cpu_percent": ("gauge", "CPU use in percent of ONE core (docker stats CPU %; may exceed 100)"),
    "memory_usage_bytes": ("gauge", "Memory in use"),
    "memory_limit_bytes": ("gauge", "Memory limit of the container"),
    "memory_percent": ("gauge", "Memory use in percent of the limit"),
    "network_receive_bytes": ("counter", "Bytes received since the container started"),
    "network_transmit_bytes": ("counter", "Bytes sent since the container started"),
    "block_read_bytes": ("counter", "Block I/O read since the container started"),
    "block_write_bytes": ("counter", "Block I/O written since the container started"),
    "pids": ("gauge", "Processes / threads in the container"),
}


def _num(v: float) -> str:
    """Full precision ('{:g}' would round 268435456 to 2.68435e+08)."""
    return str(int(v)) if float(v).is_integer() and abs(v) < 1e15 else repr(float(v))


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def render(rows: list[dict], project: str, meta: dict) -> str:
    out = []
    for key, (kind, text) in HELP.items():
        metric = f"lt_container_{key}"
        out += [f"# HELP {metric} {text}", f"# TYPE {metric} {kind}"]
        for r in rows:
            v = r[key]
            if v == v:                                     # skip NaN (unparseable value)
                out.append(f'{metric}{{project="{_label(project)}",name="{_label(r["name"])}",service="{_label(r["service"])}"}} {_num(v)}')
    out += ["# HELP lt_sampler_up 1 if the last docker stats sample succeeded", "# TYPE lt_sampler_up gauge",
            f"lt_sampler_up {1 if meta.get('ok') else 0}",
            "# HELP lt_sampler_containers Containers in the last successful sample", "# TYPE lt_sampler_containers gauge",
            f"lt_sampler_containers {len(rows)}",
            "# HELP lt_sampler_duration_seconds Duration of the last sample", "# TYPE lt_sampler_duration_seconds gauge",
            f"lt_sampler_duration_seconds {_num(meta.get('duration', 0))}",
            "# HELP lt_sampler_last_success_timestamp_seconds Unix time of the last successful sample",
            "# TYPE lt_sampler_last_success_timestamp_seconds gauge",
            f"lt_sampler_last_success_timestamp_seconds {_num(meta.get('last_success', 0))}"]
    return "\n".join(out) + "\n"


class Sampler:
    def __init__(self, project: str, interval: float, run=subprocess.run):
        self.project, self.interval, self.run = project, interval, run
        self.lock = threading.Lock()
        self.rows: list[dict] = []
        self.meta = {"ok": False, "duration": 0.0, "last_success": 0.0}

    def once(self) -> None:
        t0 = time.monotonic()
        try:
            rows = sample(self.project, self.run)
            with self.lock:
                self.rows, self.meta = rows, {"ok": True, "duration": time.monotonic() - t0, "last_success": time.time()}
        except Exception as e:  # noqa: BLE001 - keep serving the last good sample, mark the sampler down
            with self.lock:
                self.meta = {**self.meta, "ok": False, "duration": time.monotonic() - t0}
            print(f"sample failed: {type(e).__name__}", file=sys.stderr, flush=True)

    def loop(self) -> None:
        while True:
            self.once()
            time.sleep(self.interval)

    def text(self) -> str:
        with self.lock:
            return render(list(self.rows), self.project, dict(self.meta))


def loopback_only(host: str) -> str:
    if host != "localhost" and not ipaddress.ip_address(host).is_loopback:
        raise SystemExit(f"refusing to listen on {host}: metrics are served on loopback only")
    return host


def serve(sampler: Sampler, host: str, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?")[0] != "/metrics":
                self.send_response(404)
                self.end_headers()
                return
            body = sampler.text().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass
    return ThreadingHTTPServer((loopback_only(host), port), Handler)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", default="student-portal-loadtest")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=19101)
    ap.add_argument("--interval", type=float, default=10.0)
    args = ap.parse_args(argv)
    sampler = Sampler(args.project, args.interval)
    server = serve(sampler, args.host, args.port)
    threading.Thread(target=sampler.loop, name="docker-stats", daemon=True).start()
    print(f"serving {args.project} container metrics on http://{args.host}:{args.port}/metrics every {args.interval:g} s", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
