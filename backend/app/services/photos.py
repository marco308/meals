"""Recipe photos a household uploads (issue #280, decision Q32).

Off unless the operator sets `RECIPE_PHOTO_UPLOADS_ENABLED`. On, a household
may store one JPEG per recipe, in the database (`models.catalog.RecipePhoto`),
served at `/photos/{token}.jpg` and written into the recipe's `image_url`, so
every client that already shows a recipe's picture shows this one unchanged.

Three things decide the shape of it:

- **The server strips the metadata, whatever the client did.** A phone photo
  carries where it was taken, on what, and when, and an app that promises to
  remove that cannot rely on every client remembering to. `sanitise_jpeg`
  walks the JPEG's segments and keeps only what decoding needs: the frame and
  scan data, the tables, and three application segments (JFIF, the ICC colour
  profile and Adobe's colour transform). EXIF goes, and the orientation tag
  goes with it, so clients bake orientation into the pixels before uploading.
  Nothing is re-encoded: the pixels that arrive are the pixels served.
- **The link is the secret.** An image view cannot send an Authorization
  header, so the URL carries 256 random bits instead. "Private to the
  household" therefore means "private unless somebody has the link", which is
  the trade the issue agreed to.
- **One recipe has one picture, and `image_url` says which.** Replacing an
  upload makes a new row under a new token, so the URL changes and no cache
  anywhere has to be told. Setting `image_url` to anything else deletes the
  upload, and a re-parse never replaces one.

Like every service here it flushes and never commits; the router owns that.
"""

import re
import secrets
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import limits
from app.models import Recipe, RecipePhoto

#: The longest side an upload may have. Clients are asked for 1600 px; this is
#: the refusal, a little above it so a client rounding differently still fits.
MAX_DIMENSION = 2048

#: `secrets.token_urlsafe(32)`: 256 bits, 43 characters of [A-Za-z0-9_-].
TOKEN_BYTES = 32
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")

#: What a client should do instead, appended to every refusal of the bytes.
_RESIZE = (
    "Resize it so its longest side is at most 1600 px, draw it into a fresh image so its orientation is in the "
    "pixels, and encode it as JPEG (quality about 0.8). HEIC and PNG are not accepted."
)

# Start-of-frame markers, the ones that carry the image's dimensions. C4 (DHT),
# C8 (reserved for extensions) and CC (DAC) share the range and are not frames.
_SOF_MARKERS = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})
_SOI, _EOI, _SOS, _COM = 0xD8, 0xD9, 0xDA, 0xFE
# Markers that stand alone, with no length after them: the restart markers, and TEM.
_STANDALONE = frozenset({*range(0xD0, 0xD8), 0x01})


class PhotoRefused(Exception):
    """The upload cannot be stored. The message is a sentence for the caller."""


@dataclass(frozen=True)
class CleanJpeg:
    data: bytes
    width: int
    height: int


def _application_segment_kept(marker: int, payload: bytes) -> bool:
    """Whether an APPn segment is needed to show the picture correctly.

    JFIF says how to read the colour channels, the ICC profile what colours
    they are (a Display P3 photo is wrong without it), and Adobe's segment how
    a CMYK or YCCK image was transformed. Everything else an application
    segment can hold is somebody's notes about the photo: EXIF (place, device,
    time, a thumbnail of the uncropped original), XMP, IPTC, MPF's pointers to
    the extra images an iPhone appends, maker notes.
    """
    if marker == 0xE0:
        return payload.startswith(b"JFIF\x00")
    if marker == 0xE2:
        return payload.startswith(b"ICC_PROFILE\x00")
    if marker == 0xEE:
        return payload.startswith(b"Adobe")
    return False


def sanitise_jpeg(data: bytes) -> CleanJpeg:
    """The same JPEG with every metadata segment removed, and its dimensions.

    Raises `PhotoRefused` for anything that is not a whole baseline,
    progressive or lossless JPEG. Linear in the size of the upload: the scan
    data is crossed with `bytes.find`, never byte by byte in Python, and
    nothing after the end-of-image marker is copied, which is where an iPhone
    appends its depth map and HDR gain map.
    """
    if not data.startswith(b"\xff\xd8"):
        raise PhotoRefused(f"this is not a JPEG. {_RESIZE}")
    truncated = PhotoRefused(f"this JPEG is cut short or damaged, so it cannot be stored. {_RESIZE}")
    out = bytearray(b"\xff\xd8")
    size = len(data)
    pos = 2
    width = height = None
    scanned = False

    while True:
        if pos >= size or data[pos] != 0xFF:
            raise truncated
        while pos < size and data[pos] == 0xFF:  # fill bytes before a marker
            pos += 1
        if pos >= size:
            raise truncated
        marker = data[pos]
        pos += 1

        if marker == _EOI:
            out += b"\xff\xd9"
            break
        if marker in _STANDALONE:
            out += bytes((0xFF, marker))
            continue
        if marker in (0x00, _SOI):
            raise truncated

        if pos + 2 > size:
            raise truncated
        length = int.from_bytes(data[pos : pos + 2], "big")
        end = pos + length
        if length < 2 or end > size:
            raise truncated
        payload = data[pos + 2 : end]

        keep = True
        if 0xE0 <= marker <= 0xEF:
            keep = _application_segment_kept(marker, payload)
        elif marker == _COM:
            keep = False
        elif marker in _SOF_MARKERS and width is None:
            if len(payload) < 5:
                raise truncated
            height = int.from_bytes(payload[1:3], "big")
            width = int.from_bytes(payload[3:5], "big")
        if keep:
            out += bytes((0xFF, marker))
            out += data[pos:end]
        pos = end

        if marker == _SOS:
            if width is None:
                raise truncated
            scanned = True
            # Entropy-coded data runs until the next marker that is not a
            # stuffed 0xFF00 or a restart marker.
            start = pos
            while True:
                ff = data.find(b"\xff", pos)
                if ff < 0 or ff + 1 >= size:
                    raise truncated
                following = data[ff + 1]
                if following == 0x00 or 0xD0 <= following <= 0xD7:
                    pos = ff + 2
                elif following == 0xFF:
                    pos = ff + 1  # a fill byte: the marker is further on
                else:
                    out += data[start:ff]
                    pos = ff
                    break

    if not scanned or width is None or height is None:
        raise truncated
    if width == 0 or height == 0:
        # A height of zero defers it to a DNL segment, which nothing we accept
        # writes and nothing here could check against MAX_DIMENSION.
        raise PhotoRefused(f"this JPEG does not say how big it is, so it cannot be stored. {_RESIZE}")
    if max(width, height) > MAX_DIMENSION:
        raise PhotoRefused(
            f"this photo is {width}×{height} px and this server takes at most {MAX_DIMENSION} px on the longest "
            f"side. {_RESIZE}"
        )
    return CleanJpeg(data=bytes(out), width=width, height=height)


def photo_url(origin: str, token: str) -> str:
    return f"{origin}/photos/{token}.jpg"


async def has_photo(db: AsyncSession, recipe_id: uuid.UUID) -> bool:
    result = await db.execute(select(RecipePhoto.id).where(RecipePhoto.recipe_id == recipe_id))
    return result.scalar_one_or_none() is not None


async def store(db: AsyncSession, recipe: Recipe, photo: CleanJpeg, origin: str) -> bool:
    """Make `photo` the recipe's picture. Returns True if it replaced an upload.

    Replacing never counts against the allowance: the household ends with as
    many photos as it started with. The old row is deleted by statement rather
    than through the session, because the unit of work inserts before it
    deletes and the two rows would meet on the unique `recipe_id`.
    """
    replacing = await has_photo(db, recipe.id)
    if replacing:
        await db.execute(delete(RecipePhoto).where(RecipePhoto.recipe_id == recipe.id))
    else:
        await limits.enforce(db, recipe.household_id, "recipe_photos")
    token = secrets.token_urlsafe(TOKEN_BYTES)
    db.add(
        RecipePhoto(
            household_id=recipe.household_id,
            recipe_id=recipe.id,
            token=token,
            data=photo.data,
            size_bytes=len(photo.data),
            width=photo.width,
            height=photo.height,
        )
    )
    recipe.image_url = photo_url(origin, token)
    await db.flush()
    return replacing


async def remove(db: AsyncSession, recipe: Recipe) -> bool:
    """Delete the recipe's uploaded photo, if it has one, and clear `image_url`
    with it. Returns whether there was one. A picture linked from elsewhere is
    not an upload and is left alone: changing that is `PATCH /recipes/{id}`."""
    result = await db.execute(delete(RecipePhoto).where(RecipePhoto.recipe_id == recipe.id))
    if not result.rowcount:
        return False
    recipe.image_url = None
    await db.flush()
    return True


async def forget_upload(db: AsyncSession, recipe_id: uuid.UUID) -> None:
    """Delete an upload whose recipe is being given another picture, or none.
    The caller is setting `image_url` itself."""
    await db.execute(delete(RecipePhoto).where(RecipePhoto.recipe_id == recipe_id))


async def read(db: AsyncSession, token: str) -> bytes | None:
    """The JPEG served under `token`, or None. Only ever a lookup by the whole
    token, which is the only thing that makes the unauthenticated route safe."""
    if not TOKEN_RE.match(token):
        return None
    result = await db.execute(select(RecipePhoto.data).where(RecipePhoto.token == token))
    return result.scalar_one_or_none()
