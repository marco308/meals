"""What a caller-supplied name can do inside a server email (#253): nothing
but be a name, on one line."""

import pytest

from app.services.mailer import email_verification_body, greeting, mail_text, password_reset_body


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("Marcus", "Marcus"),
        ("  Marcus  Williams ", "Marcus Williams"),
        ("Marcus\n\nClick https://evil.example", "Marcus Click https://evil.example"),
        ("a\r\nb\tc\x0bd\x0ce\x85f", "a b c d e f"),
        ("a\u2028b\u2029c", "a b c"),
        ("bell\x07 and nul\x00", "bell and nul"),
        ("\u202egnp.exe", "gnp.exe"),
        ("Isla \U0001f468\u200d\U0001f469\u200d\U0001f467", "Isla \U0001f468\u200d\U0001f469\u200d\U0001f467"),
        ("Zoë", "Zoë"),
        ("\n\x01\n", ""),
    ],
)
def test_mail_text_keeps_a_name_on_one_line(raw, shown):
    assert mail_text(raw) == shown


def test_no_name_or_an_empty_one_is_a_plain_hello():
    assert greeting(None) == "Hello,"
    assert greeting("") == "Hello,"
    assert greeting("\n\n") == "Hello,"
    assert greeting("Marcus\n\nhi") == "Hello Marcus hi,"


def test_the_verification_email_never_names_anybody():
    body = email_verification_body("ABCD-EFGH-JKMN", 48)
    assert body.startswith("Hello,\n\n")


def test_a_reset_email_names_only_whom_it_is_told_to():
    assert password_reset_body(None, "ABCD-EFGH-JKMN", 30).startswith("Hello,\n\n")
    assert password_reset_body("Marcus\nX", "ABCD-EFGH-JKMN", 30).startswith("Hello Marcus X,\n\n")
