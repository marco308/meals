"""Send the two entitlement emails that are due, and nothing else.

There is no scheduler in this app, on purpose: one process that serves requests
is easier to reason about than one that also wakes up. So this is a command for
cron, run daily, at whatever hour suits.

    docker exec -i <api-container> .venv/bin/python -m app.dunning
    ... -m app.dunning --dry-run     # what would go out, and to whom

Safe to run anywhere, including a self-hosted instance with no mail and no
entitlements, where it does nothing and says so. Safe to run twice: each notice
is marked once sent, and a relay failure marks nothing so the next run retries.
"""

import argparse
import asyncio

from app.database import SessionLocal
from app.services import dunning


async def _run(dry_run: bool) -> str:
    async with SessionLocal() as db:
        notices = await dunning.run(db, dry_run=dry_run)
        if not notices:
            pending = await dunning.due(db) if not dry_run else []
            if pending:
                return f"{len(pending)} notice(s) due but no SMTP configured on this server; nothing sent."
            return "Nothing due."
        verb = "would send" if dry_run else "sent"
        lines = [f"{verb} {len(notices)}:"]
        lines += [
            f"  {notice.kind:<7}  {notice.household_name}  ->  {notice.to}  (expires {notice.paid_until:%Y-%m-%d})"
            for notice in notices
        ]
        return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="list what is due without sending anything")
    args = parser.parse_args()
    print(asyncio.run(_run(args.dry_run)))


if __name__ == "__main__":
    main()
