"""Outbound email. The only thing that sends any is a password reset (Q20).

Deliberately plain SMTP rather than a provider SDK: this project is self-hosted
by design, and every relay speaks SMTP while every SDK needs an account with one
particular company.
"""

import logging
from email.message import EmailMessage

import aiosmtplib

from app.config import get_settings

logger = logging.getLogger(__name__)


class EmailNotConfigured(RuntimeError):
    """Raised when a send is attempted with no SMTP host set."""


class EmailSendFailed(RuntimeError):
    """Raised when the relay refused the message."""


async def send_email(to: str, subject: str, body: str) -> None:
    settings = get_settings()
    if not settings.email_configured:
        raise EmailNotConfigured("SMTP is not configured")

    message = EmailMessage()
    message["From"] = settings.email_sender
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        await aiosmtplib.send(
            message,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            start_tls=settings.smtp_start_tls,
        )
    except Exception as exc:  # noqa: BLE001 — the relay's failure modes are many and all equivalent here
        # Log the reason for the operator; the caller deliberately does not pass
        # it to the user, since the endpoint must not reveal whether an address
        # exists, let alone what the mail server said about it.
        logger.warning("password reset email to %s failed: %s", to, exc)
        raise EmailSendFailed(str(exc)) from exc


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
