"""Create an account on a server whose registration is closed.

`REGISTRATION_ENABLED=false` is the right setting for a household server that
doesn't want strangers on it — but it also means there is no way to make the
one account you *do* want, and inviting into your own household is the wrong
answer: an invite shares your recipes and shopping list with whoever redeems it.

This creates a **separate, empty household** with one account in it, the same
shape `POST /auth/register` produces, straight against the database. It bypasses
the endpoint, not the model — which also means `MAX_HOUSEHOLDS` and `MAX_USERS`
do not apply to it. That is deliberate: those refuse *strangers* on the
operator's behalf, and somebody running this on the box has already made the
decision they exist to make.

Written for the Apple Review account (App Review has to be able to sign in and
use the app, and cannot see a real household's data while doing it), but it is
the general answer to "closed server, one more household".

    docker exec -it <api-container> .venv/bin/python -m app.provision \\
        --email apple.review@example.com --password '…' --household 'Apple Review'

Idempotent: run it again with a different password to reset that account's,
which is what you want when a rejected submission has aged out the credentials
in the review notes. It never touches any other household.
"""

import argparse
import asyncio
import secrets
import sys

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import SessionLocal
from app.models import Household, User
from app.services.security import hash_password

# Look-alikes off a screen, dropped in whole pairs so neither half can turn up:
# i/l/1, o/O/0, B/8. Keeping one half of a pair is no better than keeping both,
# because the reader still can't tell which character they are looking at.
AMBIGUOUS_CHARACTERS = "Il1O0oB8"

# Long enough to be safe, typeable enough to go in App Review notes and be
# retyped on a phone by someone who did not choose to be here.
PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyzACDEFGHJKLMNPQRSTUVWXYZ2345679"


def _generate_password() -> str:
    return "-".join("".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(5)) for _ in range(3))


async def provision(
    email: str,
    password: str,
    display_name: str,
    household_name: str,
    sessions: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[str, bool]:
    """Create (or repoint) the account. Returns (password, created).

    `sessions` exists so the tests can point this at their own engine — the
    claim worth testing is that the new household really is isolated, and that
    only holds if it's checked against a database with other households in it.
    """
    email = email.strip().lower()
    async with (sessions or SessionLocal)() as db:
        existing = (await db.execute(select(User).where(func.lower(User.email) == email))).scalar_one_or_none()
        if existing is not None:
            existing.password_hash = hash_password(password)
            # Deliberately no token revocation here: this is a rescue hatch, and
            # signing every device out mid-review would be its own small disaster.
            await db.commit()
            return password, False

        household = Household(name=household_name)
        db.add(household)
        await db.flush()
        user = User(
            household_id=household.id,
            email=email,
            password_hash=hash_password(password),
            display_name=display_name,
        )
        db.add(user)
        await db.flush()
        # The same rule registration follows (Q23): whoever starts a household
        # leads it. Without this the account lands in a household with no lead
        # and cannot invite anybody into it — which for the Apple Review account
        # means a reviewer meeting a 403 on a button the app still offers.
        household.lead_user_id = user.id
        await db.commit()
        return password, True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", help="omit to have one generated and printed")
    parser.add_argument("--name", default="Apple Review", help="display name (default: %(default)s)")
    parser.add_argument("--household", default="Apple Review", help="new household name (default: %(default)s)")
    args = parser.parse_args()

    password, created = asyncio.run(
        provision(args.email, args.password or _generate_password(), args.name, args.household)
    )

    print(f"{'created' if created else 'password reset for'} {args.email}")
    print(f"password: {password}")
    if created:
        print(f"household: {args.household} (new and empty — it shares nothing with any other household)")
    print(
        "\nNext: fill it with the demo data, from anywhere that can reach the server:\n"
        f"  SEED_API_URL=https://your-server SEED_EMAIL={args.email} SEED_PASSWORD='{password}' \\\n"
        "    uv run python -m app.seed",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
