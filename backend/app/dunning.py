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


async def _run(dry_run: bool, verbose: bool) -> str:
    async with SessionLocal() as db:
        notices = await dunning.run(db, dry_run=dry_run)
        if not notices:
            pending = await dunning.due(db) if not dry_run else []
            if pending:
                return f"{len(pending)} notice(s) due but no SMTP configured on this server; nothing sent."
            return "Nothing due."
        verb = "would send" if dry_run else "sent"
        lines = [f"{verb} {len(notices)}:"]
        lines += [_describe(notice, verbose) for notice in notices]
        return "\n".join(lines)


def _describe(notice: dunning.Notice, verbose: bool) -> str:
    # Ids by default: a cron's output is usually collected somewhere, and an
    # address there is an address in the logs, which /privacy promises against
    # (#261). `--verbose` is for a human at the terminal who needs the name.
    who = f"{notice.household_name}  ->  {notice.to}" if verbose else f"household {notice.household_id}"
    return f"  {notice.kind:<7}  {who}  (expires {notice.paid_until:%Y-%m-%d})"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="list what is due without sending anything")
    parser.add_argument(
        "--verbose", action="store_true", help="name the household and the address, rather than the household id"
    )
    args = parser.parse_args()
    print(asyncio.run(_run(args.dry_run, args.verbose)))


if __name__ == "__main__":
    main()
