"""Warn, then delete, households that were made and never used (issue #122).

The policy is in services/reaping.py and planning/04-open-questions.md Q25. Like
dunning, this is a command for cron rather than a scheduler in the app:

    docker exec -i <api-container> .venv/bin/python -m app.reaping
    ... -m app.reaping --dry-run     # what would happen, and to whom

Off unless REAP_ABANDONED_AFTER_DAYS is set, in which case it says so and does
nothing. Safe to run twice: a warning is marked once sent, a relay failure marks
nothing, and nobody is deleted who was not warned first.
"""

import argparse
import asyncio

from app.database import SessionLocal
from app.services import reaping


async def _run(dry_run: bool) -> str:
    if not reaping.enabled():
        return "Reaping is off on this server (REAP_ABANDONED_AFTER_DAYS is not set); nothing done."
    async with SessionLocal() as db:
        notices = await reaping.run(db, dry_run=dry_run)
    if not notices:
        return "Nothing due."
    verb = "would" if dry_run else "did"
    lines = [f"{verb}:"]
    lines += [
        f"  {notice.kind:<7}  {notice.household_name}  ->  {notice.to}  (reaps on {notice.reap_on:%Y-%m-%d})"
        for notice in notices
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="list what is due without sending or deleting")
    args = parser.parse_args()
    print(asyncio.run(_run(args.dry_run)))


if __name__ == "__main__":
    main()
