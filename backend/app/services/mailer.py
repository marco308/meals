"""Outbound email: a password reset (Q20), an address verification code and
the warning before a never-used household is reaped (Q26), and the two dunning
notices (services/dunning.py). Nothing else sends any.

Deliberately plain SMTP rather than a provider SDK: this project is self-hosted
by design, and every relay speaks SMTP while every SDK needs an account with one
particular company.

Every message goes to an address somebody typed, and until a code has come
back from it that somebody may not be its owner. So no message to an address
that is not verified carries text the caller chose (`greeting`), and any that
does goes through `mail_text` first (#253).
"""

import asyncio
import re
import unicodedata
import uuid
from email.message import EmailMessage

import aiosmtplib

from app.config import get_settings
from app.observability import log_event


class EmailNotConfigured(RuntimeError):
    """Raised when a send is attempted with no SMTP host set."""


class EmailSendFailed(RuntimeError):
    """Raised when the relay refused the message. Its text is the class of what
    went wrong and nothing more, because a relay's reply routinely quotes the
    recipient's address back and this exception is what callers log."""


async def send_email(to: str, subject: str, body: str, *, purpose: str, **ids: uuid.UUID) -> None:
    """Send one message. `purpose` and the ids (`user_id=`, `household_id=`)
    are for the log line a failure leaves behind, which is the only place
    they go."""
    settings = get_settings()
    if not settings.email_configured:
        raise EmailNotConfigured("SMTP is not configured")

    message = EmailMessage()
    message["From"] = settings.email_sender
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        async with asyncio.timeout(settings.smtp_timeout_seconds):
            await aiosmtplib.send(
                message,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_username,
                password=settings.smtp_password,
                start_tls=settings.smtp_start_tls,
            )
    except Exception as exc:  # noqa: BLE001 — the relay's failure modes are many and all equivalent here
        # Logged for the operator as what failed and for whom, by id. Never the
        # address, and never the relay's words, which quote it back ("550
        # <someone@example.com>: no such user"). The caller does not pass the
        # reason on to the user either, since the endpoint must not reveal
        # whether an address exists, let alone what the mail server said. A
        # send cut off by SMTP_TIMEOUT_SECONDS arrives here as TimeoutError.
        error = type(exc).__name__
        log_event("email.failed", outcome=purpose, error=error, **ids)
        raise EmailSendFailed(error) from exc


#: Characters that reorder or hide the text around them. Not control
#: characters to `unicodedata` (they are format, Cf, like the joiners an emoji
#: needs), so they are named here rather than stripped by category.
_BIDI_CONTROLS = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u200e\u200f\u061c")


def mail_text(value: str) -> str:
    """Caller-supplied text (a display name, a household name) made fit to sit
    inside a sentence of a server email: one line, no control characters, no
    bidi overrides, runs of whitespace folded to one space (#253).

    Render side only. The fields themselves keep accepting what they always
    have, because tightening validation on an existing field breaks clients
    that are already out there; what is taken away is only the power to start
    a new paragraph inside somebody else's message."""
    kept = []
    for char in value:
        category = unicodedata.category(char)
        if category in ("Zl", "Zp") or (category == "Cc" and char.isspace()):
            kept.append(" ")
        elif category == "Cc" or char in _BIDI_CONTROLS:
            continue
        else:
            kept.append(char)
    return re.sub(r"\s+", " ", "".join(kept)).strip()


def greeting(name: str | None) -> str:
    """The first line of a message. `None` is what a caller passes for an
    address nobody has shown to be theirs: anyone can register with somebody
    else's address and whatever name they like, so a name there is the
    stranger's words in a genuine email from this server, and the greeting
    stays plain."""
    shown = mail_text(name) if name else ""
    return f"Hello {shown}," if shown else "Hello,"


def password_reset_body(display_name: str | None, code: str, ttl_minutes: int) -> str:
    """`display_name` is None when the account's address is not verified."""
    return (
        f"{greeting(display_name)}\n\n"
        f"Someone asked to reset the password on your Meals account. Enter this "
        f"code in the app:\n\n"
        f"    {code}\n\n"
        f"It works once and expires in {ttl_minutes} minutes.\n\n"
        f"If this wasn't you, you can ignore this email — nothing has changed "
        f"and your current password still works.\n"
    )


def email_verification_body(code: str, ttl_hours: int) -> str:
    """Always to an address that is not verified yet, which is the point of
    it, so it never carries a name the caller chose."""
    return (
        f"{greeting(None)}\n\n"
        f"Welcome to Meals. To confirm this is your address, enter this code in "
        f"Settings:\n\n"
        f"    {code}\n\n"
        f"It works once and expires in {ttl_hours} hours. Until then everything "
        f"works except inviting people and importing recipes from a link.\n\n"
        f"If you didn't sign up, you can ignore this email. Nobody can use the "
        f"account without the password it was made with.\n"
    )
