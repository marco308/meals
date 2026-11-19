"""`services/photos.sanitise_jpeg` (Q32): what an uploaded photo keeps.

The server promises to remove location, device and time from every upload
whatever the client did, so these build JPEGs carrying each kind of metadata a
phone writes and check that none of it survives, while everything a decoder
needs does, byte for byte.
"""

from pathlib import Path

import pytest

from app.services.photos import MAX_DIMENSION, PhotoRefused, sanitise_jpeg

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "photo.jpg"

SOI, EOI = b"\xff\xd8", b"\xff\xd9"
JFIF = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
# Entropy-coded data with a stuffed 0xFF00 and a restart marker in it, both of
# which are scan data rather than the end of the scan.
SCAN = b"\x12\x34\xff\x00\x56\xff\xd0\x78\x9a"


def segment(marker: int, payload: bytes) -> bytes:
    return bytes((0xFF, marker)) + (len(payload) + 2).to_bytes(2, "big") + payload


def frame(width: int, height: int, marker: int = 0xC0) -> bytes:
    components = bytes((3, 1, 0x22, 0, 2, 0x11, 1, 3, 0x11, 1))
    return segment(marker, bytes((8,)) + height.to_bytes(2, "big") + width.to_bytes(2, "big") + components)


SOS = segment(0xDA, bytes((3, 1, 0x00, 2, 0x11, 3, 0x11, 0, 63, 0)))
TABLES = segment(0xDB, bytes(65)) + segment(0xC4, bytes(20))

EXIF = segment(0xE1, b"Exif\x00\x00MM\x00\x2a GPSLatitude 51.5074 N GPSLongitude 0.1278 W iPhone 17 Pro")
XMP = segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta>photoshop:DateCreated 2026-11-18</x:xmpmeta>")
IPTC = segment(0xED, b"Photoshop 3.0\x008BIM City: London")
MPF = segment(0xE2, b"MPF\x00 pointers to the gain map")
COMMENT = segment(0xFE, b"taken at 51 Acacia Avenue")
ICC = segment(0xE2, b"ICC_PROFILE\x00\x01\x01 Display P3")
ADOBE = segment(0xEE, b"Adobe\x00\x64\x00\x00\x00\x00\x01")
# An iPhone appends its HDR gain map as a second JPEG after the first one ends.
APPENDED = SOI + segment(0xE1, b"Exif\x00\x00 gain map with GPSLatitude") + EOI


def jpeg(width: int = 1600, height: int = 1200, *, before: bytes = b"", after_eoi: bytes = b"") -> bytes:
    return SOI + segment(0xE0, JFIF) + before + TABLES + frame(width, height) + SOS + SCAN + EOI + after_eoi


class TestWhatGoes:
    def test_location_device_and_time_are_removed(self):
        clean = sanitise_jpeg(jpeg(before=EXIF + XMP + IPTC + MPF + COMMENT, after_eoi=APPENDED)).data
        for leak in (b"GPS", b"iPhone", b"Exif", b"xmpmeta", b"London", b"MPF", b"Acacia", b"gain map"):
            assert leak not in clean, leak

    def test_nothing_after_the_end_of_the_image_is_kept(self):
        clean = sanitise_jpeg(jpeg(after_eoi=APPENDED + b"trailing")).data
        assert clean.endswith(EOI)
        assert clean.count(SOI) == 1

    def test_metadata_after_the_first_scan_is_removed_too(self):
        """A progressive JPEG has several scans with tables between them, and
        nothing stops a writer putting a segment there as well."""
        progressive = (
            SOI + segment(0xE0, JFIF) + TABLES + frame(64, 48, 0xC2) + SOS + SCAN
            + segment(0xC4, bytes(20)) + EXIF + SOS + SCAN + EOI
        )  # fmt: skip
        clean = sanitise_jpeg(progressive).data
        assert b"GPS" not in clean
        assert clean.count(SOS) == 2


class TestWhatStays:
    def test_what_a_decoder_needs_is_kept_byte_for_byte(self):
        bare = jpeg()
        assert sanitise_jpeg(jpeg(before=EXIF + COMMENT)).data == bare

    def test_the_colour_profile_and_adobe_transform_are_kept(self):
        clean = sanitise_jpeg(jpeg(before=ICC + ADOBE + EXIF)).data
        assert ICC in clean and ADOBE in clean

    def test_scan_data_with_stuffed_bytes_and_restarts_is_copied_whole(self):
        assert SCAN in sanitise_jpeg(jpeg()).data

    def test_it_reads_the_dimensions(self):
        clean = sanitise_jpeg(jpeg(1600, 1200))
        assert (clean.width, clean.height) == (1600, 1200)

    def test_a_real_jpeg_comes_through_smaller_and_whole(self):
        """Made by macOS `sips`, which writes an EXIF block of its own. Decoded
        by hand once when this was written: same pixels before and after."""
        original = FIXTURE.read_bytes()
        assert b"Exif" in original
        clean = sanitise_jpeg(original)
        assert b"Exif" not in clean.data
        assert (clean.width, clean.height) == (48, 32)
        assert len(clean.data) < len(original)
        assert sanitise_jpeg(clean.data).data == clean.data  # stripping twice changes nothing


class TestWhatIsRefused:
    @pytest.mark.parametrize(
        "body",
        [
            b"",
            b"\x89PNG\r\n\x1a\n" + bytes(100),
            b"\x00\x00\x00\x18ftypheic" + bytes(100),
            b"GIF89a",
        ],
        ids=["empty", "png", "heic", "gif"],
    )
    def test_anything_that_is_not_a_jpeg(self, body):
        with pytest.raises(PhotoRefused, match="not a JPEG"):
            sanitise_jpeg(body)

    @pytest.mark.parametrize(
        "body",
        [
            SOI,
            jpeg()[:-2],  # no end of image
            jpeg()[:40],  # cut off inside a segment
            SOI + segment(0xE0, JFIF) + TABLES + SOS + SCAN + EOI,  # a scan with no frame before it
            SOI + segment(0xE0, JFIF) + EOI,  # no picture at all
            SOI + b"\xff\xe1\xff\xff" + bytes(10),  # a length running past the end
        ],
        ids=["soi-only", "no-eoi", "cut-short", "no-frame", "no-scan", "overlong"],
    )
    def test_anything_cut_short_or_damaged(self, body):
        with pytest.raises(PhotoRefused, match="cut short or damaged"):
            sanitise_jpeg(body)

    def test_larger_than_the_server_takes(self):
        with pytest.raises(PhotoRefused, match=r"2049×1000 px .* at most 2048"):
            sanitise_jpeg(jpeg(MAX_DIMENSION + 1, 1000))
        assert sanitise_jpeg(jpeg(MAX_DIMENSION, MAX_DIMENSION)).width == MAX_DIMENSION

    def test_a_height_left_for_later(self):
        with pytest.raises(PhotoRefused, match="does not say how big"):
            sanitise_jpeg(jpeg(1600, 0))

    def test_every_refusal_says_how_to_send_one_that_works(self):
        with pytest.raises(PhotoRefused, match="longest side is at most 1600 px"):
            sanitise_jpeg(b"nope")


def test_it_is_linear_in_the_size_of_the_upload():
    """A megabyte of scan data that is all stuffed bytes is the worst case for
    the scan loop; it has to stay a C-speed search, never a Python byte loop."""
    import time

    scan = b"\xff\x00" * 500_000
    body = SOI + segment(0xE0, JFIF) + TABLES + frame(100, 100) + SOS + scan + EOI
    start = time.perf_counter()
    sanitise_jpeg(body)
    assert time.perf_counter() - start < 2
