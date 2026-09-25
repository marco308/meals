"""Outbound email: a password reset (Q20), and the two dunning notices
(services/dunning.py). Nothing else sends any.

Deliberately plain SMTP rather than a provider SDK: this project is self-hosted
by design, and every relay speaks SMTP while every SDK needs an account with one
particular company.
"""

import asyncio
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


def password_reset_body(display_name: str, code: str, ttl_minutes: int) -> str:
    return (
        f"Hello {display_name},\n\n"
        f"Someone asked to reset the password on your Meals account. Enter this "
        f"code in the app:\n\n"
        f"    {code}\n\n"
        f"It works once and expires in {ttl_minutes} minutes.\n\n"
        f"If this wasn't you, you can ignore this email — nothing has changed "
        f"and your current password still works.\n"
    )
