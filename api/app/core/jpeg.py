"""Strip every metadata segment from a JPEG without re-encoding it.

Shared research photos are already re-encoded on the phone (which drops EXIF), but the server
must not trust that: a modified client could send GPS, device serials, timestamps, maker notes or
an embedded full-size thumbnail. This walks the JPEG marker structure and keeps only what a
decoder needs, so the pixels are byte-identical and no imaging library is required.

Kept: frame/scan/table markers, APP0 (JFIF header) and APP14 (Adobe colour-transform flag, needed
to decode some files correctly). Dropped: APP1-APP13, APP15 (EXIF, XMP, ICC, IPTC, MPF/extra
images, maker notes) and COM. Anything after EOI (e.g. Samsung motion-photo trailers) is dropped.
"""
from __future__ import annotations

_KEEP_APP = {0xE0, 0xEE}
# SOFn markers carrying the frame size. C4 (DHT), C8 (JPG) and CC (DAC) share the range but aren't frames.
_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


class JpegError(ValueError):
    pass


def _is_metadata(marker: int) -> bool:
    return (0xE0 <= marker <= 0xEF and marker not in _KEEP_APP) or marker == 0xFE


def strip_jpeg_metadata(data: bytes) -> tuple[bytes, int, int]:
    """Return (clean_jpeg, width, height). Raises JpegError on anything that isn't a sane JPEG."""
    if not data.startswith(b"\xff\xd8"):
        raise JpegError("not a JPEG")
    out = bytearray(b"\xff\xd8")
    width = height = 0
    i, n = 2, len(data)

    def segment(at: int) -> tuple[int, int]:
        """(marker, end) of the length-prefixed segment whose 0xFF sits at `at`."""
        if at + 4 > n:
            raise JpegError("truncated segment")
        length = int.from_bytes(data[at + 2 : at + 4], "big")
        if length < 2 or at + 2 + length > n:
            raise JpegError("bad segment length")
        return data[at + 1], at + 2 + length

    # Header segments up to the first scan.
    while True:
        if i + 1 >= n or data[i] != 0xFF:
            raise JpegError("expected a marker")
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker == 0xD9:
            raise JpegError("no image data")
        marker, end = segment(i)
        if marker in _SOF:
            if end - i < 9:
                raise JpegError("short frame header")
            height = int.from_bytes(data[i + 5 : i + 7], "big")
            width = int.from_bytes(data[i + 7 : i + 9], "big")
        if not _is_metadata(marker):
            out += data[i:end]
        i = end
        if marker == 0xDA:  # SOS: entropy-coded data follows
            break

    if not width or not height:
        raise JpegError("no frame header")

    # Entropy-coded data, plus any tables/scans between progressive scans, up to EOI.
    start = i
    while i < n - 1:
        if data[i] != 0xFF:
            i += 1
            continue
        nxt = data[i + 1]
        if nxt == 0x00 or 0xD0 <= nxt <= 0xD7 or nxt == 0xFF:  # stuffing, restart, fill
            i += 1 if nxt == 0xFF else 2
            continue
        out += data[start:i]
        if nxt == 0xD9:
            out += b"\xff\xd9"
            return bytes(out), width, height
        marker, end = segment(i)
        if not _is_metadata(marker):
            out += data[i:end]
        i = start = end
    raise JpegError("missing end of image")
