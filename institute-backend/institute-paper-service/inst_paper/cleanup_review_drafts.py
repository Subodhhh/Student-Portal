"""Remove abandoned and discarded import review drafts of ONE provider, and the stored files only they used (services/draft_retention.py).

    python -m inst_paper.cleanup_review_drafts --provider-id <uuid> [--branch-id <uuid>] [--open-days 30] [--discarded-days 7]
    python -m inst_paper.cleanup_review_drafts --provider-id <uuid> --execute

A dry run (the default) prints what would be removed and changes nothing. Nothing schedules this: an operator runs it, against the
database in DATABASE_URL, for one provider at a time."""
import argparse
import json
import sys
from uuid import UUID

from institute_common.context import service_context
from institute_common.database import new_session
from inst_paper.services import draft_retention


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m inst_paper.cleanup_review_drafts", description=__doc__.split("\n\n")[0])
    ap.add_argument("--provider-id", type=UUID, required=True, help="the only provider whose drafts are considered")
    ap.add_argument("--branch-id", type=UUID, help="limit to one branch of that provider")
    ap.add_argument("--open-days", type=int, default=draft_retention.OPEN_DAYS, help="an OPEN draft neither changed nor opened for this long")
    ap.add_argument("--discarded-days", type=int, default=draft_retention.DISCARDED_DAYS, help="a DISCARDED draft discarded this long ago")
    ap.add_argument("--execute", action="store_true", help="really remove them (without it: a dry run)")
    a = ap.parse_args(argv)
    try:
        with service_context("paper"), new_session() as db:
            report = draft_retention.cleanup(db, provider_id=a.provider_id, branch_id=a.branch_id, open_days=a.open_days,
                                             discarded_days=a.discarded_days, dry_run=not a.execute)
    except draft_retention.RetentionError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
