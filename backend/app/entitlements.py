"""Comp, extend, revoke, and see who is paid.

The ops surface #99 asks for, in the spirit of `app/provision.py`: a command on
the box, not an endpoint. You will comp people in week one — early supporters,
PikaPods, whoever files the good bug — and a spreadsheet is not a source of
truth.

    docker exec -it <api-container> .venv/bin/python -m app.entitlements list
    ... -m app.entitlements comp --household 'The Smiths' --days 365 --note 'early supporter'
    ... -m app.entitlements comp --household 'The Smiths' --forever --note 'PikaPods'
    ... -m app.entitlements extend --household 'The Smiths' --days 365
    ... -m app.entitlements revoke --household 'The Smiths' --note 'asked to stop'

`--household` takes a name or an id; a name that matches more than one
household is refused with the ids, rather than guessed at.

Nothing here is destructive. Revoking puts a household on the free tier and
takes nothing away: everything already there stays readable, and §5 is the whole
argument for why. The one thing the module refuses outright is quietly
repricing somebody, because "founding price for life" is a promise stored on
their row.
"""

import argparse
import asyncio
import sys
import uuid
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import limits
from app.database import SessionLocal
from app.models import Household, User
from app.services import entitlements
from app.services.entitlements import Entitlement, EntitlementError


async def _find(db: AsyncSession, wanted: str) -> Household:
    try:
        found = await db.get(Household, uuid.UUID(wanted))
    except ValueError:
        found = None
    else:
        if found is None:
            raise EntitlementError(f"no household with id {wanted}")
        return found

    matches = list(
        (await db.execute(select(Household).where(func.lower(Household.name) == wanted.strip().lower()))).scalars()
    )
    if not matches:
        raise EntitlementError(f"no household called {wanted!r}; 'list --all' shows every one on this server")
    if len(matches) > 1:
        ids = "\n  ".join(f"{household.id}  {household.name}" for household in matches)
        raise EntitlementError(f"{len(matches)} households are called {wanted!r}; use an id:\n  {ids}")
    return matches[0]


def _date(value: str) -> datetime:
    """A plain YYYY-MM-DD, read as the end of that day in UTC so that
    `--until 2027-08-22` means the whole of the 22nd rather than its first
    instant."""
    try:
        day = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not a date; use YYYY-MM-DD") from exc
    return datetime.combine(day, time.max, tzinfo=UTC)


def _render(rows: list[Entitlement]) -> str:
    """One line per household, plus the note underneath it.

    Columns are sized to what is actually in them rather than to a guess, since
    the common case is two or three households and a fixed width would be mostly
    spaces.
    """
    if not rows:
        return "No household on this server has an entitlement. That is the self-hosted default."

    def tier_of(row: Entitlement) -> str:
        # The one thing worth shouting about: what is stored is not what applies.
        if row.effective_tier != row.stored_tier:
            return f"{row.stored_tier} (reads as {row.effective_tier})"
        return row.stored_tier

    def until_of(row: Entitlement) -> str:
        return row.paid_until.strftime("%Y-%m-%d") if row.paid_until else "no expiry"

    def price_of(row: Entitlement) -> str:
        return f"{row.price_pence / 100:.2f} {row.price_currency}" if row.price_pence is not None else "-"

    cells = [
        [row.household_name, row.state, tier_of(row), until_of(row), row.source or "-", price_of(row)] for row in rows
    ]
    widths = [max(len(cell[column]) for cell in cells) for column in range(len(cells[0]))]
    lines = []
    for row, cell in zip(rows, cells, strict=True):
        padded = "  ".join(value.ljust(width) for value, width in zip(cell, widths, strict=True))
        lines.append(f"{padded}  {row.lead_email or '-'}")
        if row.note:
            lines.append(f"{'':<{widths[0]}}  {row.note}")
    return "\n".join(lines)


async def _run(args: argparse.Namespace, sessions: async_sessionmaker[AsyncSession] | None = None) -> str:
    async with (sessions or SessionLocal)() as db:
        if args.command == "list":
            return _render(await entitlements.listing(db, everyone=args.all))

        household = await _find(db, args.household)
        # Looked up before the change so the one line printed back carries the
        # same columns `list` does, rather than a dash where the lead should be.
        lead = await db.get(User, household.lead_user_id) if household.lead_user_id else None
        if args.command == "comp":
            granted = await entitlements.grant(
                db,
                household,
                tier=args.tier,
                until=None if args.forever else _until(args),
                source=entitlements.COMP,
                note=args.note,
            )
        elif args.command == "extend":
            granted = await entitlements.extend(db, household, days=args.days, until=args.until)
        else:
            granted = await entitlements.revoke(db, household, note=args.note)
        return _render([replace(granted, lead_email=lead.email if lead else None)])


def _until(args: argparse.Namespace) -> datetime:
    if args.until is not None:
        return args.until
    if args.days is None:
        raise EntitlementError("give --days, --until or --forever")
    return datetime.now(UTC) + timedelta(days=args.days)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("list", help="who is on what, most urgent first")
    listing.add_argument("--all", action="store_true", help="include households with no entitlement")

    comp = commands.add_parser("comp", help="put a household on a tier, free")
    comp.add_argument("--household", required=True, help="name or id")
    comp.add_argument("--tier", default=limits.PAID, choices=limits.TIERS, help="default: %(default)s")
    when = comp.add_mutually_exclusive_group(required=True)
    when.add_argument("--days", type=int, help="from today")
    when.add_argument("--until", type=_date, help="YYYY-MM-DD")
    when.add_argument("--forever", action="store_true", help="no expiry, so it never lapses")
    comp.add_argument("--note", help="why, in one line, for whoever reads this in a year")

    extend = commands.add_parser("extend", help="push the expiry out, from wherever it is")
    extend.add_argument("--household", required=True, help="name or id")
    group = extend.add_mutually_exclusive_group(required=True)
    group.add_argument("--days", type=int)
    group.add_argument("--until", type=_date, help="YYYY-MM-DD")

    revoke = commands.add_parser("revoke", help="back to the free tier; deletes nothing")
    revoke.add_argument("--household", required=True, help="name or id")
    revoke.add_argument("--note", help="why")

    args = parser.parse_args()
    for name in ("all", "tier", "forever", "days", "until", "note", "household"):
        args.__dict__.setdefault(name, None)
    try:
        print(asyncio.run(_run(args)))
    except EntitlementError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
