"""What this server allows the calling household — published, not discovered.

planning/08-freemium.md §4: "Better than a good error is not hitting the wall
at all." An assistant that is about to import two hundred recipes can ask first
and act on the answer, instead of finding out on the fifty-first.

Two rules shape the endpoint:

- **It always answers.** A self-hosted server that has configured nothing
  reports every resource as unlimited rather than 404ing, so no client ever has
  to special-case the absence of limits (and no client learns that some *other*
  deployment sells something).
- **It holds no numbers of its own.** Everything here comes from
  `app/limits.py`, which is the module that would refuse the write — a limit
  published from a second source is a limit that will eventually disagree with
  the one being enforced.
"""

from fastapi import APIRouter

from app import limits
from app.deps import CurrentUser, DbSession
from app.schemas.limits import LimitsOut, ResourceAllowanceOut

router = APIRouter(tags=["meta"])


@router.get("/limits", response_model=LimitsOut)
async def get_limits(user: CurrentUser, db: DbSession) -> LimitsOut:
    """Every limit that applies to your household, with how much is used and how
    much is left.

    **Check this before a bulk import.** `remaining` is how many more of a thing
    you can create; a `limit` of `null` means unlimited, and on a server that
    limits nothing every one of them is null and `limited` is `false`.

    `used` is null wherever the count has no household-wide meaning: an
    unlimited allowance (nothing is counted, so nothing is spent) and the
    per-meal and per-plan ones, whose usage depends on which meal or plan you
    mean — for those, `limit` is still the number to keep that one under.

    Growing past a limit is refused with 402 when `upgradable` is true and 403
    when it is false; nothing is ever deleted, everything already here stays
    readable, and the shopping list is exempt from both.
    """
    snapshot = await limits.snapshot(db, user.household)
    return LimitsOut(
        tier=snapshot.tier,
        limited=snapshot.limited,
        resources=[ResourceAllowanceOut(**vars(allowance)) for allowance in snapshot.resources],
    )
