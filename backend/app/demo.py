"""Empty a demo server of households past their time (services/demo.py).

A command for cron, like `app.reaping`:

    docker exec -i <api-container> .venv/bin/python -m app.demo
    ... -m app.demo --dry-run     # how many would go

Refuses unless DEMO_MODE is on, because on any other server every household it
would delete is somebody's real one.
"""

import argparse
import asyncio

from app.config import get_settings
from app.database import SessionLocal
from app.services import demo


async def _run(dry_run: bool) -> str:
    settings = get_settings()
    if not settings.demo_mode:
        return "This is not a demo server (DEMO_MODE is off); nothing done."
    async with SessionLocal() as db:
        expired = await demo.reap(db, dry_run=dry_run)
    verb = "would delete" if dry_run else "deleted"
    return f"{verb} {len(expired)} household(s) older than {settings.demo_household_ttl_hours} h"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="count what is due without deleting")
    args = parser.parse_args()
    print(asyncio.run(_run(args.dry_run)))


if __name__ == "__main__":
    main()
