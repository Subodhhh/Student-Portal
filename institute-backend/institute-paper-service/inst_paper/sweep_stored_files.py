"""Find (and, with --execute, delete) stored files nothing uses any more (services/file_sweeper.py). Run by an operator only.

    python -m inst_paper.sweep_stored_files --provider-id <uuid> [--min-age-days 7]            unreferenced records of ONE provider
    python -m inst_paper.sweep_stored_files --unowned-bytes [--min-age-days 7]                 bytes in the store with no record
    python -m inst_paper.sweep_stored_files --unused-pictures                                   question pictures no question uses (7 days)
    ... add --execute to delete; without it: a dry run that prints what would be deleted and changes nothing.

Uses the database in DATABASE_URL and the store in FILE_STORE_DIR of the environment it runs in."""
import argparse
import json
import sys
from uuid import UUID

from institute_common.context import service_context
from institute_common.database import new_session
from inst_paper.services import file_sweeper, image_assets


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m inst_paper.sweep_stored_files", description=__doc__.split("\n\n")[0])
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--provider-id", type=UUID, help="unreferenced stored_files records of this provider only")
    which.add_argument("--unowned-bytes", action="store_true", help="files in the store directory that no record names")
    which.add_argument("--unused-pictures", action="store_true", help="question pictures stored by previews that no question uses")
    ap.add_argument("--min-age-days", type=int, default=file_sweeper.MIN_AGE_DAYS, help="never anything younger than this")
    ap.add_argument("--execute", action="store_true", help="really delete (without it: a dry run)")
    a = ap.parse_args(argv)
    try:
        with service_context("paper"), new_session() as db:
            if a.unused_pictures:
                report = image_assets.sweep_unreferenced(db, dry_run=not a.execute)
            elif a.unowned_bytes:
                report = file_sweeper.sweep_unowned_bytes(db, min_age_days=a.min_age_days, dry_run=not a.execute)
            else:
                report = file_sweeper.sweep_records(db, provider_id=a.provider_id, min_age_days=a.min_age_days, dry_run=not a.execute)
    except file_sweeper.SweepError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
