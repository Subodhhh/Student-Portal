"""Reproducible OCR evaluation dataset: printed MCQ papers and separate answer keys, photographed like a phone would, with ground truth.

Every case is generated from a seed, so the dataset is identical on every run and nothing binary is stored in the repository. Pages
are rendered from bank.py with varied numbering ("1.", "1)", "(1)", "Q1.", "Q.1", "Q1"), option markers ("A)", "(A)", "a)", "(a)",
"A.") and option layouts (one per line, two per line, all on one line); keys use varied styles ("1. A", "1-A", "1) (A)", "Q1: A",
"1 A", several entries per line). Each page is then photographed: placed on a desk at an angle (perspective), with uneven light,
lens blur, sensor noise and JPEG compression, optionally rotated, skewed, dim or out of focus, and finally shrunk to at most 2400 px
and 1.9 MB like the portal does in the browser before upload.

LIMITS (stated in the report): the text is rendered with DejaVu fonts, so this measures PRINTED papers only. Real handwriting cannot
be synthesised credibly without handwriting samples; it is not part of this dataset and its accuracy is not measured here."""
import io
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests" / "e2e"))
from phone_photos import _coeffs  # noqa: E402  (perspective solver shared with the e2e photo simulator)

from ocr_eval.bank import GENERAL, MATH, REGRESSION  # noqa: E402

FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
PAGE_W, PAGE_H, MARGIN = 1240, 1754, 110
NUMBERING = ["{n}.", "{n})", "({n})", "Q{n}.", "Q.{n}", "Q{n}"]
OPTION_MARKS = ["{L})", "({L})", "{l})", "({l})", "{L}."]
KEY_STYLES = ["{n}. {a}", "{n}-{a}", "{n}) ({a})", "Q{n}: {a}", "{n}  {a}", "multi"]


@dataclass
class Case:
    case_id: str
    category: str
    pages: list[bytes]
    keys: list[bytes]
    truth: dict[int, tuple[str, list[str], str]] = field(default_factory=dict)   # number -> (stem, options A-D, answer)
    unreadable: bool = False                                                     # the photos are meant to be refused or flagged
    layout: str = ""


def _font(size: int, face: int):
    return ImageFont.truetype(FONTS[face % len(FONTS)], size)


def _wrap(draw, text: str, font, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    return lines + ([cur] if cur else [])


def question_lines(draw, font, n: int, stem: str, options: list[str], numbering: str, marks: str, layout: str) -> list[str]:
    """The printed lines of one question in the chosen style (falls back to one option per line when a combined line would wrap)."""
    width = PAGE_W - 2 * MARGIN
    out = _wrap(draw, f"{numbering.format(n=n)} {stem}", font, width)
    opts = [f"{marks.format(L=L, l=L.lower())} {text}" for L, text in zip("ABCD", options, strict=True)]
    if layout == "one_line" and draw.textlength("    ".join(opts), font=font) <= width:
        return out + ["    ".join(opts)]
    if layout == "two_per_line" and all(draw.textlength("    ".join(opts[i:i + 2]), font=font) <= width for i in (0, 2)):
        return out + ["    ".join(opts[0:2]), "    ".join(opts[2:4])]
    return out + opts


def render(lines: list[str], size: int, face: int) -> Image.Image:
    page = Image.new("L", (PAGE_W, PAGE_H), 250)
    draw, font, y = ImageDraw.Draw(page), _font(size, face), 110
    for line in lines:
        draw.text((MARGIN, y), line, fill=25, font=font)
        y += int(size * 1.7)
    return page


def photograph(page: Image.Image, rnd: random.Random, *, rotate: int = 0, blur: float = 1.0, dim: float = 0.35, jitter: int = 90,
               tilt: float = 0.0) -> bytes:
    """A phone photo of the page (see module doc), shrunk like the portal's upload (<= 2400 px, JPEG)."""
    page = page.convert("RGB")
    if tilt:
        page = page.rotate(tilt, expand=True, fillcolor=(250, 250, 250), resample=Image.BICUBIC)
    W, H = 3000, 4000
    desk = Image.new("RGB", (W, H), (118, 84, 58))
    px = desk.load()
    for _ in range(W * H // 900):
        x, y = rnd.randrange(W), rnd.randrange(H)
        s = rnd.randint(-18, 18)
        px[x, y] = (118 + s, 84 + s, 58 + s)
    desk = desk.filter(ImageFilter.GaussianBlur(2))
    j = lambda: rnd.randint(-jitter, jitter)  # noqa: E731
    dst = [(330 + j(), 380 + j()), (2670 + j(), 330 + j()), (2730 + j(), 3650 + j()), (280 + j(), 3700 + j())]
    src = [(0, 0), (page.width, 0), (page.width, page.height), (0, page.height)]
    warped = page.transform((W, H), Image.PERSPECTIVE, _coeffs(src, dst), Image.BICUBIC)
    mask = Image.new("L", page.size, 255).transform((W, H), Image.PERSPECTIVE, _coeffs(src, dst), Image.BICUBIC)
    desk.paste(warped, (0, 0), mask)
    light = Image.linear_gradient("L").resize((W, H)).rotate(rnd.choice((30, 135, 210)), expand=False)
    photo = Image.composite(desk, Image.blend(desk, Image.new("RGB", (W, H), (40, 40, 40)), dim), light.point(lambda v: 150 + v * 105 // 255))
    photo = photo.filter(ImageFilter.GaussianBlur(blur))
    # sensor noise from the case's own random generator (Image.effect_noise is unseeded, which made runs differ)
    noise = Image.frombytes("L", (W // 2, H // 2), rnd.randbytes(W // 2 * H // 2)).point(lambda v: 128 + (v - 128) // 7)
    photo = Image.blend(photo, noise.resize((W, H), Image.NEAREST).convert("RGB"), 0.10)
    if rotate:
        photo = photo.rotate(rotate, expand=True)
    photo.thumbnail((2400, 2400), Image.LANCZOS)                  # the portal's shrinkPhoto: <= 2400 px long side, JPEG
    for quality in (88, 80, 70):
        out = io.BytesIO()
        photo.save(out, "JPEG", quality=quality)
        if out.tell() <= 1_900_000:
            break
    return out.getvalue()


def key_lines(answers: dict[int, str], style: str) -> list[str]:
    head = ["Answer Key"]
    if style == "multi":
        items = [f"{n}-{a}" for n, a in sorted(answers.items())]
        return head + ["    ".join(items[i:i + 5]) for i in range(0, len(items), 5)]
    return head + [style.format(n=n, a=a) for n, a in sorted(answers.items())]


def _paper(case_id: str, category: str, rnd: random.Random, bank, *, count: int, per_page: int, numbering: str, marks: str,
           layout: str, key_style: str, size: int, face: int, photo: dict, key_photo: dict, first: tuple | None = None,
           split_last: bool = False) -> Case:
    items = rnd.sample(bank[1:] if first else bank, count - (1 if first else 0))
    if first:
        items = [first] + items
    draw, font = ImageDraw.Draw(Image.new("L", (10, 10))), _font(size, face)
    truth, blocks = {}, []
    for n, (stem, options) in enumerate(items, start=1):
        answer = rnd.choice("ABCD")
        truth[n] = (stem, options, answer)
        blocks.append(question_lines(draw, font, n, stem, options, numbering, marks, layout))
    pages, cur = [], []
    for i, block in enumerate(blocks):
        if split_last and len(pages) == 0 and i == per_page - 1:     # a question that continues on the next page
            cur += block[:1]
            pages.append(cur)
            cur = block[1:]
            continue
        cur += block
        if (i + 1) % per_page == 0 and i + 1 < len(blocks):
            pages.append(cur)
            cur = []
    pages.append(cur)
    page_imgs = [photograph(render(p, size, face), rnd, **photo) for p in pages]
    key_img = photograph(render(key_lines({n: t[2] for n, t in truth.items()}, key_style), size, face), rnd, **key_photo)
    return Case(case_id, category, page_imgs, [key_img], truth, layout=f"{numbering}|{marks}|{layout}|key:{key_style}|font{face}")


def cases(scale: int = 1) -> list[tuple[str, dict]]:
    """(case_id, spec) for every case; build(spec) generates one. Specs are cheap, so workers can generate cases in parallel."""
    specs: list[tuple[str, dict]] = []

    def add(category: str, n: int, **kw):
        for i in range(n * scale):
            specs.append((f"{category}-{i:02d}", {"category": category, "i": i, **kw}))

    add("regression", 4)
    add("clean", 12)
    add("rotated", 12)
    add("skewed", 10)
    add("low_light", 10)
    add("blurred", 8)
    add("layouts", 12)
    add("multi_page", 8)
    add("math", 8)
    add("unreadable", 6)
    return specs


def build(case_id: str, spec: dict) -> Case:
    cat, i = spec["category"], spec["i"]
    rnd = random.Random(f"{case_id}")
    common = dict(numbering=NUMBERING[i % 2], marks=OPTION_MARKS[i % 2], layout="lines", key_style=KEY_STYLES[i % len(KEY_STYLES)],
                  size=rnd.choice((30, 32, 34)), face=i % 2, photo={}, key_photo={}, count=5, per_page=5)
    if cat == "regression":                  # the reported example: question 1 with options A-D, separate key "1 -> A", rotations
        variants = [({}, {}), ({"rotate": 90}, {}), ({}, {"rotate": 270}), ({"rotate": 270}, {"rotate": 90})]
        photo, key_photo = variants[i % 4]
        stem, options = REGRESSION
        draw, font = ImageDraw.Draw(Image.new("L", (10, 10))), _font(34, 0)
        lines = question_lines(draw, font, 1, stem, options, "{n}.", "{L})", "lines")
        page = photograph(render(lines, 34, 0), rnd, **photo)
        key = photograph(render(["Answer Key", "1. A"], 34, 0), rnd, **key_photo)
        return Case(case_id, cat, [page], [key], {1: (stem, options, "A")}, layout="regression")
    if cat == "unreadable":
        kind = ["blank", "heavy_blur", "dark", "blank_key", "tiny", "heavy_blur_key"][i % 6]
        good = _paper(case_id, cat, rnd, GENERAL, **common)
        page_lines = question_lines(ImageDraw.Draw(Image.new("L", (10, 10))), _font(32, 0), 1, *GENERAL[1], "{n}.", "{L})", "lines")
        if kind == "blank":
            good.pages = [photograph(render([], 32, 0), rnd)]
        elif kind == "heavy_blur":
            good.pages = [photograph(render(page_lines, 32, 0), rnd, blur=9)]
        elif kind == "dark":
            good.pages = [photograph(render(page_lines, 32, 0), rnd, dim=0.97)]
        elif kind == "tiny":
            good.pages = [photograph(render(page_lines, 8, 0), rnd, blur=2.5)]
        elif kind == "blank_key":
            good.keys = [photograph(render([], 32, 0), rnd)]
        else:
            good.keys = [photograph(render(["Answer Key", "1. A", "2. B"], 32, 0), rnd, blur=9)]
        if kind in ("heavy_blur", "dark", "tiny"):          # the page now shows only question 1 = GENERAL[1]; the key is the paper's
            good.truth = {1: (GENERAL[1][0], GENERAL[1][1], good.truth[1][2])}
        good.unreadable, good.layout = True, kind
        return good
    if cat == "rotated":
        r = (90, 270, 180)[i % 3]
        common.update(photo={"rotate": r}, key_photo={"rotate": (0, r)[i % 2]})
    elif cat == "skewed":
        common.update(photo={"jitter": 220, "tilt": rnd.choice((-4, -3, 3, 4))}, key_photo={"jitter": 220, "tilt": rnd.choice((-3, 3))})
    elif cat == "low_light":
        common.update(photo={"dim": 0.62}, key_photo={"dim": 0.62})
    elif cat == "blurred":
        common.update(photo={"blur": 1.9}, key_photo={"blur": 1.9})
    elif cat == "layouts":
        common.update(numbering=NUMBERING[i % len(NUMBERING)], marks=OPTION_MARKS[i % len(OPTION_MARKS)],
                      layout=("one_line", "two_per_line", "lines")[i % 3])
    elif cat == "multi_page":
        common.update(count=8, per_page=4, split_last=i % 2 == 0)
    if cat == "math":
        return _paper(case_id, cat, rnd, MATH, **common)
    return _paper(case_id, cat, rnd, GENERAL, **common)
