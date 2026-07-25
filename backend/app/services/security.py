import hashlib
import secrets

import bcrypt

TOKEN_PREFIX = "meals_"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def generate_token() -> tuple[str, str]:
    """Return (plaintext, sha256-hash). Only the hash is stored."""
    plain = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return plain, hash_token(plain)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# Crockford-style base32: no I, L, O or U, so nothing looks like anything else
# when it's read off a phone screen and typed into another one.
INVITE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
INVITE_LENGTH = 12
# What a human might type instead, given the character isn't in the alphabet.
INVITE_LOOKALIKES = str.maketrans({"I": "1", "L": "1", "O": "0", "U": "V"})


def generate_invite_code() -> tuple[str, str]:
    """Return (display code, sha256-hash of the normalised code).

    Household invites are typed by a person, not pasted by a program, so this
    is 12 base32 characters (60 bits — unguessable, and single-use behind the
    /auth/register rate limit) shown as XXXX-XXXX-XXXX rather than a 43-char
    urlsafe token. Only the hash is stored, exactly as with auth tokens.
    """
    raw = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(INVITE_LENGTH))
    display = "-".join(raw[i : i + 4] for i in range(0, INVITE_LENGTH, 4))
    return display, hash_invite_code(raw)


def normalise_invite_code(code: str) -> str:
    """Fold a typed code back to its canonical form: upper-cased, separators
    dropped, and look-alike characters mapped to the ones in the alphabet. So
    `abcd efgh jkmn`, `ABCD-EFGH-JKMN` and `abcd-efgh-jkmn` are one code."""
    stripped = "".join(ch for ch in code.upper() if ch.isalnum())
    return stripped.translate(INVITE_LOOKALIKES)


def hash_invite_code(code: str) -> str:
    return hashlib.sha256(normalise_invite_code(code).encode("utf-8")).hexdigest()
