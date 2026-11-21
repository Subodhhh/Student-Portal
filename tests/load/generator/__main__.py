"""Stage 1 command line (run from tests/load, or through `tests/load/lt.sh stage1 ...`):

  plan                         deterministic plan summary (no network)
  preflight [--base-url]       branch guard, env files, keys, stack binding, gateway (read-only)
  run [--execute] --staff-code STF... [--workers 3] [--base-url]
                               without --execute: preflight + what would be done, nothing is sent
                               with --execute: preflight must pass, then the interactive Super Admin login (password and one-time
                               code are typed, never stored) and the resumable load
  status                       journal summary (no network)
  validate [--wait-sync-minutes N]   read-only validation report (branch guard first)
Reports and the journal are written under tests/load/results/stage1/ (git-ignored; IDs only, no secrets)."""
import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

LOAD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOAD))
os.environ.setdefault("LT_CONFIG_DIR", str(LOAD / "config"))

from generator import api as api_mod  # noqa: E402
from generator import plan as plan_mod  # noqa: E402
from generator import preflight  # noqa: E402
from generator.journal import Journal  # noqa: E402
from generator.loader import AbortRun, Loader  # noqa: E402

RESULTS = LOAD / "results" / "stage1"
DEFAULT_BASE = "http://127.0.0.1:18010"


class Redacting(logging.Filter):
    def filter(self, record):
        record.msg = api_mod.redact(record.getMessage())
        record.args = ()
        return True


def setup_logging() -> None:
    from generator.journal import _private_dir
    _private_dir(RESULTS)                                    # 0700, like the journal's directory
    log_path = RESULTS / "loader.log"
    os.close(os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600))
    os.chmod(log_path, 0o600)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.addFilter(Redacting())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    for noisy in ("httpx", "httpcore"):                       # they would log every URL; never bodies, but keep the log focused
        logging.getLogger(noisy).setLevel(logging.WARNING)


def save(name: str, data: dict) -> Path:
    from generator.journal import _private_dir, _write_private
    _private_dir(RESULTS)
    path = RESULTS / f"{name}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    _write_private(path, json.dumps(data, indent=2, default=str))
    return path


def identity(plan) -> dict:
    """Journal identity: stage, seed, full-plan fingerprint (includes the paper size) and BOTH pinned Neon branch IDs."""
    return {"stage": plan.config["stage"], "seed": plan.config["seed"], "plan": plan.fingerprint(),
            "branch_ids": preflight.pinned_branch_ids()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="stage1", description=__doc__.splitlines()[0])
    ap.add_argument("--questions-per-paper", type=int, default=plan_mod.STAGE1["questions_per_paper"],
                    help="must divide 1000; part of the plan fingerprint (a journal is tied to one paper size)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan").add_argument("--canary", action="store_true")
    sub.add_parser("status")
    pf = sub.add_parser("preflight")
    pf.add_argument("--base-url", default=DEFAULT_BASE)
    run = sub.add_parser("run")
    run.add_argument("--execute", action="store_true", help="actually send requests (requires approval)")
    run.add_argument("--staff-code", required=True, help="Super Admin staff code of the load-test branch (password asked)")
    run.add_argument("--workers", type=int, default=3, choices=range(1, 5))
    run.add_argument("--base-url", default=DEFAULT_BASE)
    run.add_argument("--canary", action="store_true", help="only the deterministic canary subset; stops at its end")
    run.add_argument("--max-failures", type=int, default=None, help="stop after this many failed items (canary default: 1)")
    run.add_argument("--adopt-existing", action="store_true", help="accept marker rows already on the target that the journal does not know")
    val = sub.add_parser("validate")
    val.add_argument("--wait-sync-minutes", type=float, default=0)
    val.add_argument("--canary", action="store_true", help="validate only the canary subset and, if it passes, open the full-run gate")
    args = ap.parse_args(argv)
    setup_logging()
    full = plan_mod.build(plan_mod.with_paper_size(plan_mod.STAGE1, args.questions_per_paper))
    canary = getattr(args, "canary", False)
    plan = plan_mod.canary(full) if canary else full
    journal_path = RESULTS / "journal.json"

    if args.cmd == "plan":
        print(json.dumps(plan.summary(), indent=2))
        return 0
    if args.cmd == "status":       # read-only: never takes the lock or rewrites the journal
        data = json.loads(journal_path.read_text(encoding="utf-8")) if journal_path.is_file() else {}
        print(json.dumps({"identity": data.get("identity"), "done": {k: len(v) for k, v in data.get("done", {}).items()},
                          "failed": data.get("failed", {}), "uncertain": data.get("uncertain", {}), "phases": data.get("phases", {}),
                          "canary_validated": data.get("canary_validated"), "runs": data.get("runs", [])}, indent=2))
        return 0
    if args.cmd == "preflight":         # either app mode is acceptable here; the mode found is reported per check
        return 0 if preflight.run_all(args.base_url, marker=full.config["marker"], journal_path=journal_path) else 1
    if args.cmd == "validate":
        from validate.stage1 import validate
        result = validate(plan, args.wait_sync_minutes)
        print(json.dumps({k: result[k] for k in ("ok", "scope", "rule_violations", "projection_in_sync", "allocator_ok",
                                                 "student_enrollments_without_student", "database_bytes")}, indent=2, default=str))
        print(f"full report: {save('validation-canary' if canary else 'validation', result)}")
        if canary:
            journal = Journal(journal_path, identity(full))
            try:
                opened = journal.record_canary_validation(result)
            finally:
                journal.close()
            print("canary validated: the full run may now be started (after approval)" if opened
                  else "canary validation FAILED: the full run stays blocked")
        return 0 if result["ok"] else 2

    # run
    print(json.dumps({"scope": "canary" if canary else "full", **plan.summary()}, indent=2))
    # real writes need the Stage 1 stack (`lt.sh up app-stage1`: reference sync on, every other writer off); a dry run accepts either
    if not preflight.run_all(args.base_url, marker=full.config["marker"], journal_path=journal_path, adopt_existing=args.adopt_existing,
                             mode="stage1" if args.execute else None):
        print("preflight failed: nothing was sent")
        return 1
    if not args.execute:
        print("dry run: preflight passed; add --execute (after approval) to load the plan above")
        return 0
    journal = Journal(journal_path, identity(full))          # exclusive lock, 0600, backup, identity check
    try:
        if not canary and not journal.canary_validated():
            print("refused: run and validate the canary first (`stage1 run --canary ...`, then `stage1 validate --canary`)")
            return 1
        client = api_mod.Api(args.base_url)
        creds = api_mod.prompt_credentials(args.staff_code)

        def login():
            who = client.login(creds.staff_code, lambda: creds.password, api_mod.prompt_otp)
            print(f"signed in as {who['staff_code']} ({who['primary_role']})")
        try:
            login()
            client.reauth = login
            loader = Loader(client, plan, journal, workers=args.workers,
                            max_failures=args.max_failures if args.max_failures is not None else (1 if canary else None),
                            phase_check=lambda: preflight.check_stack_binding(mode="stage1"))
            result = loader.run(adopt_existing=args.adopt_existing)
        except AbortRun as e:
            print(f"STOPPED: {e}")
            print(f"journal: {journal.path} (re-run the same command to resume)")
            return 3
        except KeyboardInterrupt:
            print(f"interrupted; journal saved: {journal.path} (re-run the same command to resume)")
            return 130
        finally:
            creds.password = ""
            client.close()
        print(json.dumps({k: v for k, v in result.items() if k != "timings"}, indent=2))
        print(f"run report (incl. per-endpoint timings): {save('run-canary' if canary else 'run', result)}")
        if canary:
            print("CANARY COMPLETE: stopped for inspection. Next: `stage1 validate --canary --wait-sync-minutes 10`")
        else:
            print("next: `stage1 validate --wait-sync-minutes 90`")
        return 0 if not result["failed"] else 2
    finally:
        journal.flush()
        journal.close()


if __name__ == "__main__":
    sys.exit(main())
