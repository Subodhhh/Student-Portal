"""Create the canonical taxonomy from existing question labels and link the questions (services/taxonomy_backfill.py).
Run by an operator only, and only after reviewing the dry run.

    python -m inst_paper.backfill_taxonomy --actor-staff-id <uuid> [--provider-id <uuid>]             dry run: a JSON report, no writes
    python -m inst_paper.backfill_taxonomy --actor-staff-id <uuid> [--provider-id <uuid>] --execute   create records and link questions

--actor-staff-id is the operator's staff id; it is recorded as the creator of every new record and in the audit log.
Uses the database in DATABASE_URL of the environment it runs in."""
import argparse
import json
import sys
from uuid import UUID

from institute_common.context import service_context
from institute_common.core import audit
from institute_common.database import new_session
from institute_common.models.staff import Staff
from inst_paper.services.taxonomy_backfill import backfill


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m inst_paper.backfill_taxonomy", description=__doc__.split("\n\n")[0])
    ap.add_argument("--actor-staff-id", type=UUID, required=True, help="the operator's staff id (recorded as creator and in the audit)")
    ap.add_argument("--provider-id", type=UUID, help="only this provider (default: every provider)")
    ap.add_argument("--execute", action="store_true", help="really create records and link questions (without it: a dry run)")
    a = ap.parse_args(argv)
    with service_context("paper"), new_session() as db:
        if db.get(Staff, a.actor_staff_id) is None:            # never attribute records to an id nobody can trace
            print("Unknown --actor-staff-id.", file=sys.stderr)
            return 2
        report = backfill(db, staff_id=a.actor_staff_id, provider_id=a.provider_id, dry_run=not a.execute)
        if a.execute:
            audit.record(db, "taxonomy.backfill", "SUCCESS", actor=a.actor_staff_id, provider_id=a.provider_id,
                         details={"totals": report["totals"]})
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
