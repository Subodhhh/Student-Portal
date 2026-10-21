"""Phone-camera-like photos of printed question papers, for the disposable photo-paper journey (tests/e2e/global_schedule_ui_disposable.py).

These are SYNTHETIC stand-ins for real phone photos: a printed A4 page placed on a desk, photographed at an angle (perspective), with
uneven light, lens blur, sensor noise and phone-camera size/JPEG compression (4000x3000, several MB). A page can be photographed sideways
or out of focus. They exercise the same code paths as real photos (in-browser shrinking, orientation, preprocessing, OCR, review) but do
not replace a check with photos from an actual phone.
"""
import io
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"


def _font(size: int):
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default(size=size)


def printed_page(lines: list[str], size: int = 34) -> Image.Image:
    page = Image.new("L", (1240, 1754), 250)
    draw = ImageDraw.Draw(page)
    font, y = _font(size), 110
    for line in lines:
        draw.text((110, y), line, fill=25, font=font)
        y += int(size * 1.7)
    return page


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting (8x8; no numpy needed)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c], strict=True)]
    return [m[i][n] / m[i][i] for i in range(n)]


def _coeffs(src, dst):
    """Perspective coefficients mapping output points (dst) to input points (src), for Image.transform."""
    rows = []
    for (x, y), (u, v) in zip(dst, src, strict=True):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    return _solve(rows, [float(c) for pt in src for c in pt])


def phone_photo(lines: list[str], *, seed: int, sideways: bool = False, blur: float = 1.0, size: int = 34) -> bytes:
    rnd = random.Random(seed)
    page = printed_page(lines, size).convert("RGB")
    W, H = 3000, 4000                                                       # portrait phone photo
    desk = Image.new("RGB", (W, H), (118, 84, 58))
    px = desk.load()
    for _ in range(W * H // 900):                                           # wood grain / desk texture
        x, y = rnd.randrange(W), rnd.randrange(H)
        shade = rnd.randint(-18, 18)
        px[x, y] = (118 + shade, 84 + shade, 58 + shade)
    desk = desk.filter(ImageFilter.GaussianBlur(2))
    # where the page's corners land in the photo: slightly off-square (the phone is held at an angle)
    j = lambda: rnd.randint(-90, 90)
    dst = [(330 + j(), 380 + j()), (2670 + j(), 330 + j()), (2730 + j(), 3650 + j()), (280 + j(), 3700 + j())]
    src = [(0, 0), (page.width, 0), (page.width, page.height), (0, page.height)]
    warped = page.transform((W, H), Image.PERSPECTIVE, _coeffs(src, dst), Image.BICUBIC)
    mask = Image.new("L", page.size, 255).transform((W, H), Image.PERSPECTIVE, _coeffs(src, dst), Image.BICUBIC)
    desk.paste(warped, (0, 0), mask)
    light = Image.linear_gradient("L").resize((W, H)).rotate(rnd.choice((30, 135, 210)), expand=False)  # uneven light / hand shadow
    photo = Image.composite(desk, Image.blend(desk, Image.new("RGB", (W, H), (40, 40, 40)), 0.35), light.point(lambda v: 150 + v * 105 // 255))
    photo = photo.filter(ImageFilter.GaussianBlur(blur))
    noise = Image.effect_noise((W, H), 9).convert("RGB")
    photo = Image.blend(photo, noise, 0.10)
    if sideways:
        photo = photo.rotate(90, expand=True)
    out = io.BytesIO()
    photo.save(out, "JPEG", quality=95)
    return out.getvalue()


def blank_photo(seed: int) -> bytes:
    """A photo of the desk with a blank sheet: nothing to read."""
    return phone_photo([""], seed=seed)
