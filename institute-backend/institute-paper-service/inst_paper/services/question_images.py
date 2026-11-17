"""Images attached to questions and options in an uploaded paper.

Template columns: question_image, option_a_image, option_b_image, option_c_image, option_d_image. An .xlsx carries the pictures
inside the workbook (placed over the cell or in the cell); a CSV is uploaded as a .zip package whose image columns name image files
inside the same package.

Every image is checked here, inside the isolated extraction process (services/isolation.py), before anything is stored:
  * the bytes must really be PNG, JPEG, GIF or WebP: the signature is sniffed, the file name and declared type are not trusted;
  * at most MAX_IMAGE_BYTES and MAX_IMAGE_PIXELS (a decompression bomb is refused from its header, before decoding);
  * the image must decode completely, so a truncated or corrupt file is reported to the teacher instead of reaching students.
The original bytes are kept exactly as uploaded (source evidence). Nothing is re-encoded, resized or stripped here.
"""
import hashlib
import io
from dataclasses import dataclass, field

# template column -> the question field it illustrates ("question" or the option letter)
IMAGE_COLUMNS = {"question_image": "question", "option_a_image": "A", "option_b_image": "B", "option_c_image": "C",
                 "option_d_image": "D"}

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGES_PER_PAPER = 2_500          # 500 questions x (question + 4 options)

# signature -> MIME type; the only formats accepted (no SVG: it can carry script; no TIFF/BMP/FITS/DDS decoders are ever reached)
_SIGNATURES = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF87a", "image/gif"),
               (b"GIF89a", "image/gif"))
_PILLOW_FORMAT = {"image/png": "PNG", "image/jpeg": "JPEG", "image/gif": "GIF", "image/webp": "WEBP"}


class ImageProblem(ValueError):
    """A teacher-readable reason why an image cannot be used."""


@dataclass(frozen=True)
class ExtractedImage:
    """One checked image from an uploaded paper. `name` is the file name inside the upload (for messages only)."""
    name: str
    mime: str
    sha256: str
    width: int
    height: int
    data: bytes = field(repr=False)


def sniff_mime(data: bytes) -> str | None:
    """The image type from its leading bytes, or None when it is not one of the accepted formats."""
    for signature, mime in _SIGNATURES:
        if data.startswith(signature):
            return mime
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def check_image(data: bytes, name: str) -> ExtractedImage:
    """Validate one image completely (type, size, pixel count, full decode). Raises ImageProblem with a teacher-readable reason."""
    from PIL import Image, UnidentifiedImageError

    if not data:
        raise ImageProblem(f"The image {name} is empty.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageProblem(f"The image {name} is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB; use a smaller image.")
    mime = sniff_mime(data)
    if mime is None:
        raise ImageProblem(f"{name} is not a PNG, JPEG, GIF or WebP image.")
    try:
        # formats=[...] restricts Pillow to the one decoder the signature names, so no other decoder ever parses these bytes
        with Image.open(io.BytesIO(data), formats=[_PILLOW_FORMAT[mime]]) as img:
            width, height = img.size
            if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
                raise ImageProblem(f"The image {name} is {width}×{height} pixels, which is too large; use a smaller image.")
            img.load()                                    # full decode: a truncated or corrupt file fails here
    except ImageProblem:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ImageProblem(f"The image {name} is damaged or cannot be read.") from exc
    return ExtractedImage(name=name, mime=mime, sha256=hashlib.sha256(data).hexdigest(), width=width, height=height, data=data)
