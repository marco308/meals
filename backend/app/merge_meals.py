"""Merge identical meals left behind before POST /meals reused them (#193).

An operator command on the box, like app.entitlements, never an endpoint.
What counts as identical, and what moves, is in services/meal_merge.py.

    docker exec -i <api-container> .venv/bin/python -m app.merge_meals            # what would merge
    ... -m app.merge_meals --apply                                                # do it
    ... -m app.merge_meals --household <uuid> [--apply]                           # one household

Without --apply nothing is written. Safe to run twice: a second run finds
nothing to merge.
"""

import argparse
import asyncio
import uuid

from app.database import SessionLocal
from app.observability import log_event
from app.services import meal_merge


async def _run(apply: bool, household_id: uuid.UUID | None) -> str:
    async with SessionLocal() as db:
        groups = await meal_merge.merge(db, household_id)
        if apply:
            await db.commit()
        else:
            await db.rollback()
    if not groups:
        return "No duplicate meals."
    for group in groups if apply else []:
        log_event(
            "meals.merged",
            household_id=group.household_id,
            meal_id=group.kept,
            removed=len(group.removed),
        )
    verb = "merged" if apply else "would merge"
    lines = [f"{verb}:"]
    lines += [
        f"  {group.name}  x{len(group.removed) + 1}  ->  {group.kept}  "
        f"(household {group.household_id}; plan entries moved {group.plan_entries_moved}, "
        f"folded {group.plan_entries_folded}; cooked {group.times_cooked}x)"
        for group in groups
    ]
    if not apply:
        lines.append("Nothing written. Run again with --apply to merge.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the merge (default: report only)")
    parser.add_argument("--household", type=uuid.UUID, help="only this household")
    args = parser.parse_args()
    print(asyncio.run(_run(args.apply, args.household)))


if __name__ == "__main__":
    main()
