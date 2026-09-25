import asyncio
import hashlib
import secrets
from functools import cache

import bcrypt

TOKEN_PREFIX = "meals_"

#: bcrypt reads at most this many bytes of a password, and bcrypt 5 raises
#: rather than quietly ignoring the rest. Bytes, not characters: an accented
#: letter is two bytes in UTF-8 and an emoji is four, so a password of them
#: reaches the limit in far fewer characters. `schemas/auth.py` refuses a longer
#: one before it gets here.
PASSWORD_MAX_BYTES = 72


# bcrypt costs about a quarter of a second of CPU a check, which is the point
# of it. Run on the event loop, that quarter-second is taken from every other
# request the process is serving, `/healthz` included. bcrypt releases the GIL
# while it hashes, so a worker thread is all it takes for the loop to carry on.
# These two are the only way into it, and both have to be awaited: an
# un-awaited `verify_password` is a coroutine, which is truthy, and
# `tests/unit/test_security.py` checks every call for exactly that.


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_hash, password)


async def verify_password(password: str, password_hash: str | None) -> bool:
    """Whether `password` matches `password_hash`, checked off the event loop.

    Pass `None` when there is no account to check against: the answer is False,
    but only after the same bcrypt work a real account costs, so how long a
    failed login takes says nothing about whether the address has an account.
    """
    if password_hash is None:
        await asyncio.to_thread(_check, password, _stand_in_hash())
        return False
    return await asyncio.to_thread(_check, password, password_hash)


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _check(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:  # a malformed stored hash, or a password over PASSWORD_MAX_BYTES
        return False


async def warm_up() -> None:
    """Make the stand-in hash before any login needs it; called at startup.
    Left to the first failed login for an unknown address, making it would
    double that one answer, and the slowest answer after a restart would say
    which address has no account."""
    await asyncio.to_thread(_stand_in_hash)


@cache
def _stand_in_hash() -> str:
    """A hash of nothing anybody knows, made by the same `gensalt()` as a real
    one so that checking against it costs exactly what a real check does. Made
    on first use (`warm_up`, for a server) rather than at import, so a CLI that
    never answers a login doesn't pay for it."""
    return _hash(secrets.token_urlsafe(32))


def generate_token() -> tuple[str, str]:
    """Return (plaintext, sha256-hash). Only the hash is stored."""
    plain = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return plain, hash_token(plain)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# Crockford-style base32: no I, L, O or U, so nothing looks like anything
# else when it's read off a phone screen, or out of an email, and typed in.
SHORT_CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
SHORT_CODE_LENGTH = 12
# What a human might type instead, given the character isn't in the alphabet.
SHORT_CODE_LOOKALIKES = str.maketrans({"I": "1", "L": "1", "O": "0", "U": "V"})


def generate_short_code() -> tuple[str, str]:
    """Return (display code, sha256-hash of the normalised code).

    For the codes a person types rather than a program pastes: household
    invites (Q19) and password resets (Q20). 12 base32 characters — 60 bits, so
    unguessable, and both uses are single-use behind a rate limit — shown as
    XXXX-XXXX-XXXX rather than as a 43-char urlsafe token. Only the hash is
    stored, exactly as with auth tokens.
    """
    raw = "".join(secrets.choice(SHORT_CODE_ALPHABET) for _ in range(SHORT_CODE_LENGTH))
    display = "-".join(raw[i : i + 4] for i in range(0, SHORT_CODE_LENGTH, 4))
    return display, hash_short_code(raw)


def normalise_short_code(code: str) -> str:
    """Fold a typed code back to its canonical form: upper-cased, separators
    dropped, and look-alike characters mapped to the ones in the alphabet. So
    `abcd efgh jkmn`, `ABCD-EFGH-JKMN` and `abcd-efgh-jkmn` are one code."""
    stripped = "".join(ch for ch in code.upper() if ch.isalnum())
    return stripped.translate(SHORT_CODE_LOOKALIKES)


def hash_short_code(code: str) -> str:
    return hashlib.sha256(normalise_short_code(code).encode("utf-8")).hexdigest()
