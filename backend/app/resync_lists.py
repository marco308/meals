"""Re-sync live shopping lists after the merge rules change (Q28).

An operator command on the box, like app.merge_meals, never an endpoint. Lines
already on a list keep the units they were built in until their meal is next
edited; this moves every planned meal's shares to the line it would land on
now (salt in ml onto salt in g, "to taste" onto the line with an amount).

    ssh zaphod 'docker exec $(docker ps -q -f name=meals_api | head -1) sh -c \
      "DATABASE_URL=postgresql+asyncpg://meals:\$(cat /run/secrets/postgres_password)@db:5432/meals \
       .venv/bin/python -m app.resync_lists [--apply] [--household <uuid>]"'

`docker exec` doesn't run the service's start-up command, which is what builds
DATABASE_URL from the secret; without it the script opens an empty SQLite
file and fails on "no such table". The API runs on zaphod, not HomeBase.

Without --apply nothing is written. Safe to run twice: a second run changes
nothing.
"""

import argparse
import asyncio
import uuid

from sqlalchemy import select

from app.database import SessionLocal
from app.models import ShoppingList
from app.observability import log_event
from app.services.shopping import resync_active_list


async def _run(apply: bool, household_id: uuid.UUID | None) -> str:
    async with SessionLocal() as db:
        query = select(ShoppingList.household_id).where(ShoppingList.status == "active").distinct()
        if household_id is not None:
            query = query.where(ShoppingList.household_id == household_id)
        households = list((await db.execute(query)).scalars())
        changed = []
        for household in households:
            before, after = await resync_active_list(db, household)
            if before != after:
                changed.append((household, before, after))
        if apply:
            await db.commit()
        else:
            await db.rollback()
    for household, before, after in changed if apply else []:
        log_event("shopping.resynced", household_id=household, lines_before=before, lines_after=after)
    verb = "re-synced" if apply else "would re-sync"
    lines = [f"{verb} {len(households)} live list(s); {len(changed)} changed line count:"]
    lines += [f"  household {household}: {before} -> {after} lines" for household, before, after in changed]
    if not apply:
        lines.append("Nothing written. Run again with --apply to re-sync.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the re-sync (default: report only)")
    parser.add_argument("--household", type=uuid.UUID, help="only this household")
    args = parser.parse_args()
    print(asyncio.run(_run(args.apply, args.household)))


if __name__ == "__main__":
    main()
