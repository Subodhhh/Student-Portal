"""Checks that must ALL pass before the Stage 1 loader sends a single write. Nothing here writes anywhere; every database session is
read-only (branch guard) and no secret is printed (values are compared in memory only).

  1. branch guard: both Neon load-test branches pinned and verified, never a development branch;
  2. env files: APP_ENV=dev, EMAIL_BACKEND=console, no Resend key (no real e-mail can be sent), insecure cookie, no migration URL;
  3. service keys: compose.env complete and sharing no value with the dev root .env;
  4. stack binding: the RUNNING lt-* containers belong to project student-portal-loadtest and were started with exactly the
     guarded DATABASE_URLs (so the API the loader calls can only write to the two load-test branches), and with the background
     jobs of the requested mode: `preflight` = every writer off, `stage1` = only the reference sync on (15 s); in both the audit
     forwarders, attempt expiry and auto evaluation are 0 and the exam worker is not running;
  5. the API base URL is the load-test gateway on loopback (127.0.0.1:18010), never the dev gateway (8010), and answers /health."""
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

LOAD = Path(__file__).resolve().parents[1]
ROOT = LOAD.parents[1]
sys.path.insert(0, str(LOAD / "guard"))
import guard  # noqa: E402

PROJECT = "student-portal-loadtest"
APP_CONTAINERS = {   # container -> which env file it must have been started with
    "lt-institute-identity-service": "institute", "lt-institute-organization-service": "institute",
    "lt-institute-academics-service": "institute", "lt-institute-enrollment-service": "institute",
    "lt-institute-paper-service": "institute", "lt-institute-exam-service": "institute",
    "lt-institute-operations-service": "institute", "lt-student-authentication-service": "student",
    "lt-student-enrollment-service": "student", "lt-student-test-service": "student", "lt-student-attempt-service": "student",
    "lt-student-result-service": "student", "lt-student-notification-service": "student",
}
GATEWAY = "lt-institute-gateway"
WORKER = "lt-institute-exam-worker"                 # must NOT run: it acts on inherited plans, outboxes and attempts
STAGE1_SYNC = "15"
MODES = {"preflight": {"0"}, "stage1": {STAGE1_SYNC}, None: {"0", STAGE1_SYNC}}   # accepted reference-sync values per mode
OFF_EVERYWHERE = {"institute": ("AUDIT_FORWARD_INTERVAL_SECONDS",), "lt-student-attempt-service": ("ATTEMPT_EXPIRY_SWEEP_SECONDS",),
                  "lt-student-result-service": ("AUTO_EVALUATION_INTERVAL_SECONDS",)}
ALLOWED_BASE = {("127.0.0.1", 18010), ("localhost", 18010)}
FALSY = {"false", "0", "no", "off"}


def _env(name: str) -> dict:
    return guard.read_env(guard.config_dir() / name)


def check_guard() -> list[str]:
    cfg = guard.load_config()
    return [] if guard.cmd_check(cfg, ["neon"]) == 0 else ["branch guard refused the Neon load-test targets"]


def pinned_branch_ids() -> dict:
    """The pinned Neon branch IDs (only meaningful after check_guard passed): recorded in, and required to match, the journal."""
    targets = guard.load_config()["targets"]
    return {name: targets[name].get("branch_id") for name in ("neon-student", "neon-institute")}


def check_env_files() -> list[str]:
    problems = []
    for part in ("student", "institute"):
        env = _env(f"neon/{part}.env")
        if env.get("APP_ENV") != "dev":
            problems.append(f"neon/{part}.env: APP_ENV must be dev")
        if env.get("EMAIL_BACKEND") != "console":
            problems.append(f"neon/{part}.env: EMAIL_BACKEND must be console (no real e-mail)")
        if env.get("RESEND_API_KEY"):
            problems.append(f"neon/{part}.env: RESEND_API_KEY must not be set")
        if (env.get("SESSION_COOKIE_SECURE") or "").lower() not in FALSY:
            problems.append(f"neon/{part}.env: SESSION_COOKIE_SECURE must be false (a Secure __Host- cookie is never sent over http)")
        if env.get("MIGRATION_DATABASE_URL"):
            problems.append(f"neon/{part}.env: MIGRATION_DATABASE_URL must stay empty (no migrations)")
    return problems


def check_service_keys(root_env: Path = ROOT / ".env") -> list[str]:
    keys = _env("compose.env")
    empty = sorted(k for k, v in keys.items() if not v)
    problems = [f"compose.env: {', '.join(empty)} empty"] if empty else []
    dev = set(guard.read_env(root_env).values()) if root_env.is_file() else set()
    shared = sorted(k for k, v in keys.items() if v and v in dev)
    if shared:
        problems.append(f"compose.env reuses dev key values for {', '.join(shared)}: generate new ones")
    return problems


def docker_inspect(names: list[str]) -> list[dict]:
    """Container metadata (held in memory only: it contains the env, i.e. secrets)."""
    out = subprocess.run(["docker", "inspect", *names], capture_output=True, text=True)
    if out.returncode != 0:
        return json.loads(out.stdout or "[]")
    return json.loads(out.stdout)


def _belongs(c: dict, name: str) -> list[str]:
    problems = []
    if ((c.get("Config") or {}).get("Labels") or {}).get("com.docker.compose.project") != PROJECT:
        problems.append(f"{name}: not part of project {PROJECT}")
    if not (c.get("State") or {}).get("Running"):
        problems.append(f"{name}: not running")
    return problems


def check_stack_binding(inspect=docker_inspect, mode: str | None = None) -> list[str]:
    """Run before the first write AND before every phase: the containers the gateway forwards to were started with exactly the
    guarded DATABASE_URLs, console e-mail, an insecure-cookie session (http) and the background jobs of `mode` (preflight | stage1;
    None accepts either); the exam worker is not running; the gateway is ours."""
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    expected = {part: _env(f"neon/{part}.env").get("DATABASE_URL") for part in ("student", "institute")}
    found = {c["Name"].lstrip("/"): c for c in inspect([*APP_CONTAINERS, GATEWAY, WORKER])}
    problems = []
    for name, part in APP_CONTAINERS.items():
        c = found.get(name)
        if not c:
            problems.append(f"{name}: not found (start the stack with `LT_TARGET=neon lt.sh up app`)")
            continue
        problems += _belongs(c, name)
        env = dict(e.split("=", 1) for e in (c.get("Config") or {}).get("Env") or [] if "=" in e)
        if not expected[part] or env.get("DATABASE_URL") != expected[part]:
            problems.append(f"{name}: DATABASE_URL differs from the guarded tests/load/config/neon/{part}.env (restart the stack)")
        if env.get("EMAIL_BACKEND") != "console":
            problems.append(f"{name}: EMAIL_BACKEND is not console")
        if part == "institute" and (env.get("SESSION_COOKIE_SECURE") or "").lower() not in FALSY:
            problems.append(f"{name}: SESSION_COOKIE_SECURE is not false")
        for key in OFF_EVERYWHERE.get(part if part == "institute" else name, ()):
            if env.get(key) != "0":
                problems.append(f"{name}: {key} must be 0 (background writer), found {env.get(key)!r}")
        if name == "lt-institute-enrollment-service":
            sync = env.get("REFERENCE_SYNC_INTERVAL_SECONDS")
            if sync not in MODES[mode]:
                wanted = " or ".join(sorted(MODES[mode]))
                problems.append(f"{name}: REFERENCE_SYNC_INTERVAL_SECONDS is {sync!r}, mode {mode or 'any'} expects {wanted} "
                                f"(`lt.sh up {'app-stage1' if mode == 'stage1' else 'app'}`)")
    worker = found.get(WORKER)
    if worker and (worker.get("State") or {}).get("Running"):
        problems.append(f"{WORKER}: running; it must not run during Stage 1 (it acts on inherited plans, outboxes and attempts)")
    gw = found.get(GATEWAY)
    if not gw:
        problems.append(f"{GATEWAY}: not found")
    else:
        problems += _belongs(gw, GATEWAY)
        bindings = ((gw.get("NetworkSettings") or {}).get("Ports") or {}).get("80/tcp") or []
        if {(b.get("HostIp"), str(b.get("HostPort"))) for b in bindings} != {("127.0.0.1", "18010")}:
            problems.append(f"{GATEWAY}: must publish port 80 on 127.0.0.1:18010 only")
    return problems


def journal_provider_count(journal_path: Path) -> int:
    """Providers recorded in an existing journal (read without taking its lock: informational, for the dry run)."""
    if not journal_path.is_file():
        return 0
    return len(json.loads(journal_path.read_text(encoding="utf-8")).get("done", {}).get("providers", {}))


def check_clean_target(marker: str, journal_path: Path, connect=None) -> list[str]:
    """Read-only, through the branch guard's connection: the Institute branch may hold marker providers only if the journal knows
    exactly as many (the canary's, or an interrupted run's). Anything else means another run's data or a stale journal."""
    if connect is None:
        from validate.stage1 import connect
    with connect("neon-institute") as conn:
        on_target = conn.execute("select count(*) from providers where name like %s", (f"{marker} Provider %",)).fetchone()[0]
    known = journal_provider_count(journal_path)
    if on_target == known:
        return []
    if on_target > known:
        return [f"target holds {on_target} '{marker} Provider' row(s), the journal knows {known}: another run's data "
                "(use its journal, reset the branch, or --adopt-existing)"]
    return [f"journal lists {known} provider(s) but the target holds {on_target}: stale journal (branch reset?); move it aside"]


def check_base_url(base_url: str, transport: httpx.BaseTransport | None = None) -> list[str]:
    u = urlparse(base_url)
    if u.scheme != "http" or (u.hostname, u.port) not in ALLOWED_BASE:
        return [f"API base URL must be the load-test gateway http://127.0.0.1:18010, not {u.scheme}://{u.hostname}:{u.port}"]
    try:
        r = httpx.Client(transport=transport, timeout=10).get(base_url.rstrip("/") + "/health")
    except httpx.HTTPError as e:
        return [f"gateway unreachable: {type(e).__name__}"]
    return [] if r.status_code == 200 else [f"gateway /health answered {r.status_code}"]


def run_all(base_url: str, report=print, *, marker: str = "LT1", journal_path: Path | None = None, adopt_existing: bool = False,
            mode: str | None = None) -> bool:
    """mode: "stage1" before real writes (reference sync must be on), "preflight" for a preflight-only stack, None = either."""
    os.environ.setdefault("LT_CONFIG_DIR", str(LOAD / "config"))
    checks = [("branch guard", check_guard), ("env files", check_env_files), ("service keys", check_service_keys),
              ("stack binding", lambda: check_stack_binding(mode=mode)), ("gateway", lambda: check_base_url(base_url))]
    if journal_path is not None:
        checks.append(("clean target", (lambda: []) if adopt_existing else (lambda: check_clean_target(marker, journal_path))))
    ok = True
    for name, fn in checks:
        if name == "clean target" and not ok:
            report("SKIP clean target (needs the checks above to pass first)")
            continue
        try:
            problems = fn()
        except guard.GuardError as e:
            problems = [str(e)]
        except Exception as e:  # noqa: BLE001 - e.g. the database is unreachable: fail closed, never print a URL
            problems = [f"{type(e).__name__}: {guard.redact(e)}"]
        report(("PASS " if not problems else "FAIL ") + name)
        for p in problems:
            report(f"     - {p}")
        ok &= not problems
    return ok
