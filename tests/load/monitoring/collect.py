#!/usr/bin/env python3
"""Computer 1 side of one Stage 2 run: samples containers and PostgreSQL every 5 s until Ctrl+C, then saves pg_stat_statements and the service logs.
Connects only to the `postgres` maintenance database as the monitoring role, read-only; pg_stat_* views there show the Stage 2 database, which is never
connected to. Nothing is reset: statements_before.csv and statements_after.csv are compared instead. Starts no container and creates no volume.

    tests/load/.venv/bin/python tests/load/monitoring/collect.py <run name>     # results/stage2-perf/<run name>/, refuses an existing run"""
import csv
import json
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg

LOAD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOAD / "guard"))
import guard  # noqa: E402

DB, PERIOD = "lt_stage2_student", 5
ACTIVITY = """select coalesce(host(client_addr), 'local'), coalesce(state, ''), coalesce(wait_event_type, ''), coalesce(wait_event, ''), count(*),
                     round(coalesce(max(extract(epoch from now() - query_start)) filter (where state = 'active'), 0)::numeric, 2)
              from pg_stat_activity where datname = %s group by 1, 2, 3, 4"""
LOCKS = "select l.mode, l.granted, count(*) from pg_locks l join pg_database d on d.oid = l.database where d.datname = %s group by 1, 2"
COUNTERS = ("numbackends", "xact_commit", "xact_rollback", "blks_read", "blks_hit", "tup_returned", "tup_fetched", "tup_inserted", "tup_updated", "tup_deleted",
            "temp_files", "temp_bytes", "deadlocks")
STATEMENTS = """select s.queryid, s.calls, s.total_exec_time, s.rows, s.shared_blks_hit, s.shared_blks_read, s.temp_blks_written, s.wal_bytes, s.query
                from pg_stat_statements s join pg_database d on d.oid = s.dbid where d.datname = %s"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Csv:
    def __init__(self, path, header):
        self.file = open(path, "w", newline="")
        self.out = csv.writer(self.file)
        self.out.writerow(header)

    def rows(self, stamp, rows):
        self.out.writerows([stamp, *r] for r in rows)
        self.file.flush()       # a crash keeps what was sampled


def docker(*args):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=30).stdout


def containers():
    return [n for n in docker("ps", "--format", "{{.Names}}").split() if n.startswith("lt-")]


def snapshot(conn, path):
    cur = conn.execute(STATEMENTS, (DB,))
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([c.name for c in cur.description])
        w.writerows(cur.fetchall())


def meminfo():
    seen = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    return [int(seen[k].split()[0]) for k in ("MemTotal", "MemAvailable", "SwapFree")] + [Path("/proc/loadavg").read_text().split()[0]]


def stat(conn):
    return dict(zip(COUNTERS, conn.execute(f"select {', '.join(COUNTERS)} from pg_stat_database where datname = %s", (DB,)).fetchone()))


def main(run):
    out = LOAD / "results/stage2-perf" / run
    out.mkdir(parents=True)        # refuses a run that exists
    (out / "logs").mkdir()
    pw = guard.read_env(guard.config_dir() / "local/postgres.env")
    conn = psycopg.connect(host="127.0.0.1", port=15432, dbname="postgres", user=pw["DATA_SOURCE_USER"], password=pw["DATA_SOURCE_PASS"], autocommit=True,
                           options="-c default_transaction_read_only=on", application_name="lt-collect")
    names = containers()
    info = json.loads(docker("inspect", *names))
    (out / "meta.json").write_text(json.dumps({"run": run, "started_utc": now(), "containers": {
        c["Name"].lstrip("/"): {"image": c["Config"]["Image"], "image_id": c["Image"], "mem_limit": c["HostConfig"]["Memory"], "nano_cpus": c["HostConfig"]["NanoCpus"],
                                "ips": [n["IPAddress"] for n in c["NetworkSettings"]["Networks"].values()]} for c in info}}, indent=1))
    snapshot(conn, out / "statements_before.csv")
    first = stat(conn)
    started = now()
    docker_csv = Csv(out / "containers.csv", ["ts", "name", "cpu_pct", "mem_usage", "mem_pct", "net_io", "block_io", "pids"])
    activity = Csv(out / "pg_activity.csv", ["ts", "client", "state", "wait_type", "wait_event", "count", "oldest_active_s"])
    locks = Csv(out / "pg_locks.csv", ["ts", "mode", "granted", "count"])
    database = Csv(out / "pg_database.csv", ["ts", *COUNTERS])
    host = Csv(out / "host.csv", ["ts", "mem_total_kb", "mem_available_kb", "swap_free_kb", "load1"])
    stop = []
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, lambda *_: stop.append(1))
    print(f"collecting into {out}; press Ctrl+C after the k6 run ends", flush=True)
    while not stop:
        t = time.monotonic()
        stamp = now()
        try:
            stats = [json.loads(line) for line in docker("stats", "--no-stream", "--format", "{{json .}}", *names).splitlines()]
            docker_csv.rows(stamp, [[x["Name"], x["CPUPerc"], x["MemUsage"], x["MemPerc"], x["NetIO"], x["BlockIO"], x["PIDs"]] for x in stats])
            activity.rows(stamp, conn.execute(ACTIVITY, (DB,)).fetchall())
            locks.rows(stamp, conn.execute(LOCKS, (DB,)).fetchall())
            database.rows(stamp, [list(stat(conn).values())])
            host.rows(stamp, [meminfo()])
        except (subprocess.SubprocessError, psycopg.Error, ValueError) as e:     # one lost sample must not end the run's monitoring
            print(f"{stamp} sample lost: {guard.redact(e)}", flush=True)
        time.sleep(max(0, PERIOD - (time.monotonic() - t)))
    time.sleep(1)                  # backends flush their counters shortly after a run
    snapshot(conn, out / "statements_after.csv")
    last = stat(conn)
    for name in names:
        r = subprocess.run(["docker", "logs", "--since", started, name], capture_output=True, text=True)
        (out / "logs" / f"{name}.log").write_text(r.stdout + r.stderr)
    changed = {k: last[k] - first[k] for k in ("tup_inserted", "tup_updated", "tup_deleted", "deadlocks", "temp_files")}
    (out / "summary.json").write_text(json.dumps({"finished_utc": now(), "changes": changed}, indent=1))
    print(f"rows changed in {DB} during the run: {changed}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].replace("-", "").isalnum():
        raise SystemExit(__doc__)
    main(sys.argv[1])
