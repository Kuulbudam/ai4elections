"""Photo intake for result sheets: decode safely, re-encode as JPEG without metadata.

Re-encoding drops EXIF (including GPS location of the person who took the photo) and anything hidden after
the image data. Only JPEG, PNG and WebP are accepted, decided from the file's bytes, never its name.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_INPUT_BYTES = 4_500_000
MAX_PIXELS = 40_000_000  # refuse "decompression bombs" before decoding
MAX_SIDE = 2000  # stored and sent to the model at most this wide/tall
MIN_SIDE = 200
ALLOWED = {"JPEG", "PNG", "WEBP"}


class ImageRejected(ValueError):
    """The upload is not a usable photo; the message is safe to show."""


@dataclass(frozen=True)
class CleanImage:
    jpeg: bytes
    sha256: str  # of the stored JPEG
    original_sha256: str  # of the bytes as received
    width: int
    height: int


def decode_base64(data: str) -> bytes:
    if "," in data[:100] and data.lstrip().startswith("data:"):
        data = data.split(",", 1)[1]  # tolerate a data: URL prefix
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ImageRejected("The photo could not be read (it is not valid base64).") from e
    if len(raw) > MAX_INPUT_BYTES:
        raise ImageRejected("The photo is too large. Use a photo under 4.5 MB.")
    return raw


def clean(raw: bytes) -> CleanImage:
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            fmt = probe.format
            w, h = probe.size
        if fmt not in ALLOWED:
            raise ImageRejected("Upload a JPEG, PNG or WebP photo of the result sheet.")
        if w * h > MAX_PIXELS:
            raise ImageRejected("The photo has too many pixels.")
        with Image.open(io.BytesIO(raw)) as im:
            im.load()
            im = ImageOps.exif_transpose(im)  # respect the camera's rotation before the metadata is dropped
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            if min(im.size) < MIN_SIDE:
                raise ImageRejected("The photo is too small to read. Take it closer, with the whole sheet in view.")
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            out = io.BytesIO()
            im.save(out, format="JPEG", quality=85, optimize=True)  # no exif= argument: metadata is not written
            size = im.size
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError) as e:
        raise ImageRejected("The file is not a photo the platform can open.") from e
    data = out.getvalue()
    return CleanImage(jpeg=data, sha256=hashlib.sha256(data).hexdigest(),
                      original_sha256=hashlib.sha256(raw).hexdigest(), width=size[0], height=size[1])  # fmt: skip
