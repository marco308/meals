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
