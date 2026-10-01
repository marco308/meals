"""Freezer labels on a label printer, through a label service.

The printer (a SUPVAN E11: 12 mm tape, 40 mm die-cut labels) speaks Bluetooth,
so something in the kitchen has to own it. That is the label service at
LABEL_SERVICE_URL, and this module is a client of its `freezer` template: we
send the dish, the date it went in and, when the batch came from the app, a
link back to it. The service draws the label. All it has to speak:

    POST /api/labels/print
    Authorization: Bearer <token>
    {"template": "freezer", "copies": 1,
     "fields": {"dish": "Chilli", "frozen": "2026-10-01", "qr": "<url>"}}

answering 200 when printed, 401 for a bad token, 429 when over a quota and
503 when the printer is off or out of reach.

Three decisions shape it:

- **The URL is the operator's and the token is the household's.**
  LABEL_SERVICE_URL is configuration, so this server never dials an address a
  user typed in. The token is per household because a server holds many, and
  the printer in one kitchen is not the business of any other. Unset URL or
  unset token, and no client is offered a button.
- **The name is shortened here, not shrunk there.** The service fits whatever
  it is given to the label, so a long title comes out unreadably small. A
  label only has to tell you which tub is which; the app has the full name.
- **The QR code is a short link.** Phones scan a code with fewer, larger
  modules far more reliably, and on a 12 mm label the difference is between
  three dots a module and two. `/L/<code>` is uppercase on purpose: a QR code
  encodes A-Z, 0-9 and a few symbols in its denser alphanumeric mode, which is
  what gets a whole link into the smallest code that fits. Only a batch that
  came from a meal or a recipe gets one; a free-text batch has nowhere to go.
"""

import base64
import uuid
from datetime import date

import httpx

from app.config import get_settings
from app.models import FreezerItem, Household
from app.observability import log_event

#: Characters the dish name may take on a 40 mm label, with and without a QR
#: code beside it. Measured against the service's own previews: past these the
#: name shrinks below what reads at arm's length in a freezer.
MAX_TITLE = 24
MAX_TITLE_BESIDE_QR = 18

#: Labels one press may print. A batch is usually a handful of tubs.
MAX_COPIES = 20

#: Where the part of a name worth keeping ends: "Chilli (batch of 6)", "Thai
#: green curry with jasmine rice", "Lasagne - the good one".
_SEPARATORS = (" with ", " - ", " – ", " — ", ": ", " | ", ", ", " & ")

#: Short-link prefixes: which kind of thing the id after it names.
_KINDS = {"R": "recipes", "M": "meals"}


class LabelError(Exception):
    """Nothing was printed. Carries a sentence for whoever pressed the button."""

    def __init__(self, detail: str, *, status_code: int = 503, retry_after: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


def available() -> bool:
    """Whether this server has a label service at all."""
    return bool(get_settings().label_service_url)


def can_print(household: Household) -> bool:
    return available() and bool(household.label_printer_token)


def _strip_brackets(text: str) -> str:
    out, depth = [], 0
    for char in text:
        if char in "([":
            depth += 1
        elif char in ")]" and depth:
            depth -= 1
        elif not depth:
            out.append(char)
    return " ".join("".join(out).split())


def shorten(name: str, limit: int = MAX_TITLE) -> str:
    """A dish name that fits a label: brackets dropped, then everything after
    the first separator, then whole words off the end behind an ellipsis.
    Never empty, and never cut mid-word unless one word is all there is."""
    name = " ".join(name.split())
    if len(name) <= limit:
        return name
    name = _strip_brackets(name) or name
    for sep in _SEPARATORS:
        head = name.split(sep, 1)[0].strip()
        if head and len(head) < len(name) and len(name) > limit:
            name = head
    if len(name) <= limit:
        return name
    cut = name[: limit - 1]
    if " " in cut and name[limit - 1] != " ":
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-–—&") + "…"


def short_code(item: FreezerItem) -> str | None:
    """`R`/`M` and the id in base32: 27 characters, all in the QR alphanumeric
    set. None for a batch that came from neither (or whose source was deleted)."""
    if item.recipe_id is not None:
        kind, target = "R", item.recipe_id
    elif item.meal_id is not None:
        kind, target = "M", item.meal_id
    else:
        return None
    return kind + base64.b32encode(target.bytes).decode().rstrip("=")


def resolve_code(code: str) -> str | None:
    """The web app's address for a short code, or None if it is not one.
    Case-insensitive, since a code that has been through a camera may not be."""
    code = code.strip().upper()
    kind = _KINDS.get(code[:1])
    if kind is None or len(code) != 27:
        return None
    try:
        target = uuid.UUID(bytes=base64.b32decode(code[1:] + "======"))
    except ValueError:  # binascii.Error is one
        return None
    return f"/app/#/{kind}/{target}"


def label_fields(item: FreezerItem, public_base: str) -> dict:
    """What the service's `freezer` template is sent for one batch."""
    code = short_code(item)
    fields: dict = {
        "dish": shorten(item.label, MAX_TITLE_BESIDE_QR if code else MAX_TITLE),
        "frozen": (item.frozen_on or date.today()).isoformat(),
    }
    if code:
        fields["qr"] = f"{public_base.upper()}/L/{code}"
    return fields


async def print_freezer_label(household: Household, item: FreezerItem, *, copies: int, public_base: str) -> None:
    """Print `copies` labels for one batch, or raise LabelError saying why not.

    The service's own error text is not passed on: it is written for whoever
    runs the service, and the person at the freezer needs to know what to do.
    The outcome goes to the log by name, never the token or the dish."""
    settings = get_settings()
    if not settings.label_service_url or not household.label_printer_token:
        raise LabelError(
            "no label printer is set up for your household; add the label service token in Settings → Label printer",
            status_code=409,
        )
    url = settings.label_service_url.rstrip("/") + "/api/labels/print"
    body = {"template": "freezer", "fields": label_fields(item, public_base), "copies": copies}
    try:
        async with httpx.AsyncClient(timeout=settings.label_service_timeout_seconds) as client:
            response = await client.post(
                url, json=body, headers={"Authorization": f"Bearer {household.label_printer_token}"}
            )
    except httpx.HTTPError as exc:
        log_event("freezer.label", household_id=household.id, outcome="unreachable", error=type(exc).__name__)
        raise LabelError("the label service did not answer, so nothing was printed; try again in a minute") from exc

    if response.status_code == 200:
        log_event("freezer.label", household_id=household.id, outcome="printed", copies=copies)
        return
    if response.status_code in (401, 403):
        log_event("freezer.label", household_id=household.id, outcome="refused")
        raise LabelError(
            "the label service did not accept your household's token; paste a fresh one in Settings → Label printer",
            status_code=409,
        )
    if response.status_code == 429:
        log_event("freezer.label", household_id=household.id, outcome="rate_limited")
        raise LabelError(
            "the label printer has printed a lot just now; wait a few minutes and try again",
            status_code=429,
            retry_after=response.headers.get("retry-after"),
        )
    if response.status_code == 503:
        log_event("freezer.label", household_id=household.id, outcome="printer_off")
        raise LabelError(
            "the label printer could not be reached; it switches itself off after a while, so turn it on and try again"
        )
    log_event("freezer.label", household_id=household.id, outcome="failed", status=response.status_code)
    raise LabelError("the label service could not print that label; nothing was printed", status_code=502)
