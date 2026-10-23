"""Printed-text OCR for scanned question papers (Tesseract), registered only when OCR_ENGINE=tesseract.

The page must follow the numbered-block layout accepted for Word/PDF papers (see services/documents.py). A question's confidence (0..1)
is the lower of: the weakest word on the lines that decide scoring (its Answer / Marks / Negative lines and the paper defaults), and
the mean confidence of all its words. extract_image then rejects the whole paper if any question is below OCR_MIN_CONFIDENCE. Handwriting and poor scans therefore fail instead of importing
guesses, and an accepted OCR paper is still only a VALIDATED draft that a person must preview and approve.

Photo papers use read_lines: a phone photo is cleaned up (preprocess: page detection, light flattening, orientation, text-size
normalisation, binarisation) and returned as text lines with Tesseract's word confidences; services/photo_drafts.py turns them into
questions. Tesseract 5 (LSTM, English) reads printed text well; it is NOT a handwriting recogniser, so handwritten papers come back
with low confidence and are flagged or refused, never trusted. Measured accuracy and latency: ocr_eval/ (scripts/run-ocr-eval.sh)."""
import io

from inst_paper.services.documents import _DEFAULT, _FIELD, _Q, parse_question_blocks
from inst_paper.services.extraction import ExtractionResult, OcrQuestion, OcrRejected, OcrUnreadable

TIMEOUT_SECONDS = 60
MIN_WIDTH = 1600            # read_questions: small scans are upscaled so 10-12 pt text is legible to Tesseract
WORK_LONG_SIDE = 2400       # phone photos (12+ MP) are reduced to this first: enough for printed text, ~4x less work for Tesseract
TARGET_LINE_PX = 48         # text lines are scaled to about this height (ascender to descender), where Tesseract reads best
OSD_MIN_CONFIDENCE = 3.0    # below this, Tesseract's orientation guess is not trusted and the four orientations are compared instead
INK = 140                   # grey level below which a pixel of the flattened page counts as ink


def _otsu(img) -> int:
    """Otsu's threshold of a greyscale image (from its histogram)."""
    hist = img.histogram()
    total, weighted = sum(hist), sum(i * v for i, v in enumerate(hist))
    best, threshold, wb, sb = -1.0, 128, 0, 0
    for t, count in enumerate(hist):
        wb += count
        if not wb or wb == total:
            continue
        sb += t * count
        between = wb * (total - wb) * (sb / wb - (weighted - sb) / (total - wb)) ** 2
        if between > best:
            best, threshold = between, t
    return threshold


def _profile(img, axis: str) -> list[float]:
    """Mean grey level of every column ("x") or row ("y"), computed by a box resize (fast, no numpy)."""
    from PIL import Image
    size = (img.width, 1) if axis == "x" else (1, img.height)
    return list(img.resize(size, Image.BOX).getdata())


def _page_mask(gray):
    """Where the sheet of paper is, at reduced size: pixels brighter than the photo's Otsu threshold (a desk or background is darker),
    with the holes left by the writing closed and the outline pulled in slightly, so the sheet's edge and its shadow are excluded.
    Returns (mask, factor from mask to `gray` coordinates)."""
    from PIL import ImageFilter
    small = gray.reduce(max(1, max(gray.size) // 400))
    t = _otsu(small)
    mask = small.point(lambda v: 255 if v > t else 0)
    mask = mask.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.MinFilter(9))       # close the writing's holes
    mask = mask.filter(ImageFilter.MinFilter(5))                                         # step inside the sheet's edge
    return mask, gray.width / small.width


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting (the 8x8 perspective system; no numpy in the image)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        if abs(m[c][c]) < 1e-9:
            raise ValueError("degenerate quadrilateral")
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c], strict=True)]
    return [m[i][n] / m[i][i] for i in range(n)]


def _sheet_corners(mask) -> list[tuple[int, int]] | None:
    """The sheet's four corners (top-left, top-right, bottom-right, bottom-left) in mask coordinates, when the sheet is a clear
    quadrilateral that does not fill the whole photo (otherwise None: nothing to straighten, or no sheet edge visible)."""
    w, h = mask.size
    data = mask.getdata()
    tl = tr = br = bl = None
    count = 0
    for idx, v in enumerate(data):
        if not v:
            continue
        count += 1
        x, y = idx % w, idx // w
        if tl is None or x + y < tl[0] + tl[1]:
            tl = (x, y)
        if br is None or x + y > br[0] + br[1]:
            br = (x, y)
        if tr is None or x - y > tr[0] - tr[1]:
            tr = (x, y)
        if bl is None or x - y < bl[0] - bl[1]:
            bl = (x, y)
    if not count or not 0.15 * w * h <= count <= 0.97 * w * h:
        return None
    quad = [tl, tr, br, bl]
    area = abs(sum(quad[i][0] * quad[(i + 1) % 4][1] - quad[(i + 1) % 4][0] * quad[i][1] for i in range(4))) / 2
    return quad if 0.9 <= area / count <= 1.1 else None                                  # the bright region really is a sheet


def _straighten(gray):
    """Perspective correction: map the photographed sheet (a quadrilateral) onto an upright rectangle, which also removes skew and
    the desk. Without a visible sheet, the photo is kept and only whitened outside the bright region. Returns (image, how)."""
    from PIL import Image
    mask, f = _page_mask(gray)
    quad = _sheet_corners(mask)
    if quad:
        q = [(x * f, y * f) for x, y in quad]
        dist = lambda a, b: ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5  # noqa: E731
        width = round(max(dist(q[0], q[1]), dist(q[3], q[2])))
        height = round(max(dist(q[0], q[3]), dist(q[1], q[2])))
        dst = [(0, 0), (width, 0), (width, height), (0, height)]
        rows, rhs = [], []
        for (x, y), (u, v) in zip(dst, q, strict=True):                                  # output point -> photo point
            rows += [[x, y, 1, 0, 0, 0, -u * x, -u * y], [0, 0, 0, x, y, 1, -v * x, -v * y]]
            rhs += [u, v]
        try:
            return gray.transform((width, height), Image.PERSPECTIVE, _solve(rows, rhs), Image.BICUBIC, fillcolor=255), "perspective"
        except ValueError:
            pass
    box = mask.getbbox()
    if box and (box[2] - box[0]) * (box[3] - box[1]) < 0.97 * mask.width * mask.height:
        white = Image.new("L", gray.size, 255)
        outside = mask.resize(gray.size, Image.BILINEAR).point(lambda v: 0 if v > 127 else 255)
        gray = Image.composite(white, gray, outside).crop(tuple(round(v * f) for v in box))
        return gray, "masked"
    return gray, "none"


def _clear_border_ink(gray):
    """Paint white the LARGE dark regions that touch the image border - a notebook's edge, the facing page's shadow, a desk or carpet
    left inside the crop - which Tesseract would read as text and which make every row look inked (breaking the line-height
    measurement). A region counts as large when it spans more than 30% of the image's height or width, so writing that merely
    reaches the edge is kept."""
    from PIL import Image, ImageChops, ImageDraw
    f = max(1, max(gray.size) // 600)
    ink = gray.reduce(f).point(lambda v: 255 if v < INK else 0)
    w, h = ink.size
    work, drop = ink.copy(), Image.new("L", ink.size, 0)
    border = [(x, 0) for x in range(w)] + [(x, h - 1) for x in range(w)] + [(0, y) for y in range(h)] + [(w - 1, y) for y in range(h)]
    for xy in border:
        if work.getpixel(xy) != 255:
            continue
        ImageDraw.floodfill(work, xy, 128)
        region = work.point(lambda v: 255 if v == 128 else 0)
        x0, y0, x1, y1 = region.getbbox()
        if y1 - y0 > 0.3 * h or x1 - x0 > 0.3 * w:
            drop = ImageChops.lighter(drop, region)
        work = ImageChops.subtract(work, region.point(lambda v: 128 if v else 0))      # this region is done (0 now)
    if not drop.getbbox():
        return gray, False
    drop = drop.resize(gray.size, Image.NEAREST)
    return Image.composite(Image.new("L", gray.size, 255), gray, drop), True


def _deskew(gray) -> tuple[object, float]:
    """Small remaining tilt (-8..8 degrees, 1-degree steps): the angle whose ink rows (or columns, for a sideways page) are most
    sharply separated, measured on a reduced copy."""
    from PIL import Image
    s = min(1.0, 500 / max(gray.size))
    small = gray.resize((max(1, round(gray.width * s)), max(1, round(gray.height * s))), Image.BILINEAR).point(lambda v: 255 if v < INK else 0)

    def sharpness(angle: float) -> float:
        r = small.rotate(angle, expand=True, fillcolor=0, resample=Image.BILINEAR)
        best = 0.0
        for axis in ("x", "y"):
            prof = _profile(r, axis)
            mean = sum(prof) / len(prof)
            best = max(best, sum((v - mean) ** 2 for v in prof) / len(prof))
        return best

    scores = {a: sharpness(a) for a in range(-8, 9)}
    angle = max(scores, key=lambda a: (scores[a], -abs(a)))
    if angle and scores[angle] > 1.05 * scores[0]:
        return gray.rotate(angle, expand=True, fillcolor=255, resample=Image.BICUBIC), float(angle)
    return gray, 0.0


def _flatten(gray):
    """Divide by an estimate of the paper's local brightness (the page with the strokes removed): shadows, a hand's shade and uneven
    light disappear, faint pencil or ink strokes keep their contrast."""
    from PIL import Image, ImageFilter, ImageMath
    background = gray.reduce(4).filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(8)).resize(gray.size, Image.BILINEAR)
    return ImageMath.lambda_eval(lambda a: a["g"] * 255 / (a["b"] + 1), g=gray, b=background).convert("L")


def _ink_box(gray, margin: int = 40):
    """Bounding box of the ink on a flattened page, with a margin; None for a blank page."""
    ink = gray.point(lambda v: 255 if v < INK else 0)
    xs = [i for i, v in enumerate(_profile(ink, "x")) if v > 1]
    ys = [i for i, v in enumerate(_profile(ink, "y")) if v > 1]
    if not xs or not ys:
        return None
    return max(0, xs[0] - margin), max(0, ys[0] - margin), min(gray.width, xs[-1] + margin), min(gray.height, ys[-1] + margin)


def _line_height(gray) -> int | None:
    """Median height of the text lines (runs of rows containing ink) of an upright page."""
    rows = [v > 2 for v in _profile(gray.point(lambda v: 255 if v < INK else 0), "y")]
    runs, n = [], 0
    for has_ink in rows + [False]:
        if has_ink:
            n += 1
        elif n:
            runs.append(n)
            n = 0
    runs = sorted(r for r in runs if r >= 4)
    return runs[len(runs) // 2] if runs else None


def _binary(gray):
    t = _otsu(gray)
    return gray.point(lambda v: 255 if v > t else 0)


def _readable_score(img) -> float:
    """How much confidently read text an orientation yields (sum of confidences of real words): used to compare orientations."""
    import pytesseract
    d = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT, config="--psm 6", timeout=TIMEOUT_SECONDS)
    return sum(float(c) for w, c in zip(d["text"], d["conf"], strict=True)
               if float(c) > 50 and len(w.strip()) >= 2 and any(ch.isalnum() for ch in w))


def _orientation(gray) -> tuple[int, str]:
    """Clockwise rotation that makes the page upright, and how it was decided. Tesseract's orientation detection is used when it is
    confident; it needs a fair amount of text, so short pages (an answer key, one question) are read in all four orientations at
    reduced size and the most readable one wins."""
    import pytesseract
    from PIL import Image
    bw = _binary(gray)
    try:
        osd = pytesseract.image_to_osd(bw, output_type=pytesseract.Output.DICT, timeout=TIMEOUT_SECONDS)
        if float(osd.get("orientation_conf", 0)) >= OSD_MIN_CONFIDENCE:
            return int(osd.get("rotate", 0)) % 360, "osd"
    except Exception:  # noqa: BLE001 - too little text for orientation detection: compare the orientations instead
        pass
    s = min(1.0, 1200 / max(bw.size))
    small = _binary(gray.resize((max(1, round(gray.width * s)), max(1, round(gray.height * s))), Image.LANCZOS)) if s < 1 else bw
    scores = {r: _readable_score(small.rotate(-r, expand=True, fillcolor=255)) for r in (0, 90, 180, 270)}
    best = max(scores, key=lambda r: (scores[r], r == 0))
    return (best, "vote") if scores[best] > 0 else (0, "none")


def preprocess(image: bytes, rotate: int | None = None, info: dict | None = None):
    """The binarised page ready for OCR (see _prepare), or None for a photo without ink."""
    prepared = _prepare(image, rotate, info)
    return prepared[1] if prepared else None


def _prepare(image: bytes, rotate: int | None = None, info: dict | None = None):
    """Phone photo -> clean black-on-white image of the text, ready for Tesseract:
      1. honour the camera's EXIF orientation, greyscale, reduce very large photos (speed; printed text stays legible);
      2. find the sheet and map it onto an upright rectangle (perspective correction; the desk and the sheet's edge are dropped and
         its corners, top and bottom lines kept), flatten uneven light and shadows, clear large dark regions at the border (a
         notebook edge, the background), straighten a remaining tilt, crop to the ink;
      3. turn the page upright (`rotate` from the uploader, else Tesseract's orientation detection or a four-way comparison);
      4. scale so text lines are ~TARGET_LINE_PX high (tiny answer keys are not blown up, small text is enlarged), and add a white
         border (Tesseract misreads characters that touch the image edge);
      5. binarise with Otsu's threshold (after flattening one threshold fits the whole page; Tesseract's own thresholding was found
         to drop short lines such as "C) 1").
    Returns (greyscale, binarised) images of the same geometry, or None when the photo contains no ink at all. `info` (optional)
    receives what was done, for diagnostics."""
    from PIL import Image, ImageOps
    info = {} if info is None else info
    with Image.open(io.BytesIO(image)) as img:
        img.load()
        img = ImageOps.exif_transpose(img)
        gray = img.convert("L")
    s = WORK_LONG_SIDE / max(gray.size)
    if s < 1:
        gray = gray.resize((round(gray.width * s), round(gray.height * s)), Image.LANCZOS)
    gray, straightened = _straighten(gray)
    gray = _flatten(gray)
    gray, cleared = _clear_border_ink(gray)
    gray, tilt = _deskew(gray)
    box = _ink_box(gray)
    if box is None:
        info["blank"] = True
        return None
    gray = gray.crop(box)
    if rotate is None:
        rotate, how = _orientation(gray)
    else:
        rotate, how = rotate % 360, "uploader"
    if rotate:
        gray = gray.rotate(-rotate, expand=True, fillcolor=255)
    height = _line_height(gray)
    scale = max(0.4, min(4.0, TARGET_LINE_PX / height)) if height else 1.0
    if abs(scale - 1) > 0.15:
        gray = gray.resize((max(1, round(gray.width * scale)), max(1, round(gray.height * scale))), Image.LANCZOS)
    gray = ImageOps.expand(gray, border=TARGET_LINE_PX, fill=255)        # no text touching the edge (Tesseract misreads it)
    info.update(straightened=straightened, border_cleared=cleared, tilt=tilt, rotate=rotate, orientation=how, line_height=height, scale=round(scale, 2), size=gray.size)
    return gray, _binary(gray)


SCRIPT_RATIO = 0.72          # a digit/mark under this fraction of the line's digit-and-capital height is a likely super/subscript


def _hocr_lines(hocr: bytes) -> list[list]:
    """Tesseract hOCR (with character boxes) -> [text, weakest word confidence, mean word confidence, flags, line box] per line, in
    reading order. flags contains "script" when a digit or mark is much smaller than the line's digits and capitals: a superscript or
    subscript (x², 2⁵, H₂O) that OCR usually flattens into ordinary text ("x?", "25", "H2O"); its question must be checked."""
    import re
    from xml.etree import ElementTree as ET
    ns = "{http://www.w3.org/1999/xhtml}"
    num = re.compile(r"(-?\d+(?:\.\d+)?)")

    def title(el, key):
        m = re.search(key + r" ([^;]+)", el.get("title", ""))
        return [float(x) for x in num.findall(m.group(1))] if m else []

    out = []
    for line in ET.fromstring(hocr).iter(f"{ns}span"):
        if line.get("class") not in ("ocr_line", "ocr_header", "ocr_caption", "ocr_textfloat"):
            continue
        words, chars = [], []
        for word in line.iter(f"{ns}span"):
            if word.get("class") != "ocrx_word":
                continue
            cinfo = [c for c in word.iter(f"{ns}span") if c.get("class") == "ocrx_cinfo"]
            text = "".join(c.text or "" for c in cinfo) if cinfo else "".join(word.itertext())
            if text.strip():
                words.append((text.strip(), (title(word, "x_wconf") or [0.0])[0]))
                chars += [((c.text or ""), title(c, "x_bboxes")) for c in cinfo]
        if not words:
            continue
        flags = []
        ref = sorted(b[3] - b[1] for ch, b in chars if len(b) == 4 and (ch.isdigit() or ch.isupper() or ch in "bdfhklt"))
        if len(ref) >= 2:
            tall = ref[len(ref) // 2]
            if any(len(b) == 4 and (ch.isdigit() or ch in "?°º") and b[3] - b[1] < SCRIPT_RATIO * tall for ch, b in chars):
                flags.append("script")
        confs = [c for _, c in words]
        out.append([" ".join(w for w, _ in words), min(confs), sum(confs) / len(confs), flags, title(line, "bbox")])
    return out


READABLE_SHARE = 0.5        # at least this share of a photo's text must be on confidently read lines, or the photo is refused
REREAD_BELOW = 80           # question-page lines read below this confidence from the binarised page are read again in greyscale
REREAD_MARGIN = 10          # ... and the greyscale reading replaces it only if its weakest word is at least this much more confident


def _reread(gray, line: list) -> list:
    """Read one doubtful line again from the greyscale page (single-line mode) and keep it only if it is clearly more confident. Binarisation reads most text best, but some glyphs (a serif "5" read as "9" or "3") come out better in greyscale."""
    import pytesseract
    from PIL import ImageOps
    box = line[4]
    if len(box) != 4:
        return line
    x0, y0, x1, y1 = box
    pad = max(8, round((y1 - y0) * 0.4))
    crop = ImageOps.expand(gray.crop((max(0, x0 - pad), max(0, y0 - pad), min(gray.width, x1 + pad), min(gray.height, y1 + pad))),
                           border=pad, fill=255)
    hocr = pytesseract.image_to_pdf_or_hocr(crop, extension="hocr", config="--psm 7 -c hocr_char_boxes=1", timeout=TIMEOUT_SECONDS)
    again = _hocr_lines(hocr)
    if len(again) == 1 and again[0][1] >= line[1] + REREAD_MARGIN:
        return [again[0][0], again[0][1], again[0][2], sorted(set(line[3]) | set(again[0][3])), box]
    return line


class TesseractBackend:
    def read_lines(self, image: bytes, mime: str, purpose: str = "page", rotate: int | None = None, info: dict | None = None) -> list[list]:
        """Text lines of one page in reading order: [text, weakest word confidence, mean word confidence, flags] (confidences
        0..100; flags: see _hocr_lines). Raises OcrUnreadable (with what to retake) for a blank photo or one where nothing could be
        read with confidence."""
        import pytesseract

        prepared = _prepare(image, rotate, info)
        if prepared is None:
            raise OcrUnreadable("No writing was found in this photo. Photograph the whole page, flat and well lit.")
        gray, binary = prepared
        hocr = pytesseract.image_to_pdf_or_hocr(binary, extension="hocr", config="--psm 6 -c hocr_char_boxes=1", timeout=TIMEOUT_SECONDS)
        # Answer keys are not re-read: on their very short lines a higher confidence was often a worse reading ("1D" -> "yD").
        out = [_reread(gray, line) if purpose == "page" and line[1] < REREAD_BELOW else line for line in _hocr_lines(hocr)]
        out = [line[:4] for line in out]
        readable = sum(len(text) for text, _mn, mean, _f in out if mean >= 60)
        if not any(mean >= 60 and len(text.strip()) >= 2 for text, _mn, mean, _f in out) or readable < READABLE_SHARE * sum(len(t[0]) for t in out):
            raise OcrUnreadable("Most of the writing in this photo could not be read. Retake it in focus, flat and well lit, "
                                + ("with the answers clearly written" if purpose == "key" else "with the whole page in the picture")
                                + ". Handwriting is not read reliably yet: a printed or typed copy works best.")
        return out

    def read_questions(self, image: bytes, mime: str) -> list[OcrQuestion]:
        import pytesseract
        from PIL import Image

        with Image.open(io.BytesIO(image)) as img:
            img.load()
            gray = img.convert("L")
        data = pytesseract.image_to_data(gray, output_type=pytesseract.Output.DICT, config="--psm 6", timeout=TIMEOUT_SECONDS)

        # Group words into text lines, keeping each line's weakest word confidence.
        lines: dict[tuple, list[tuple[str, float]]] = {}
        for i, word in enumerate(data["text"]):
            if not (word or "").strip():
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            lines.setdefault(key, []).append((word, float(data["conf"][i])))
        ordered = [(" ".join(w for w, _ in ws), min(c for _, c in ws)) for _, ws in sorted(lines.items())]

        # Attribute every line to its question (same numbering rule as the parser).
        critical: dict[int, float] = {}
        words: dict[int, list[float]] = {}
        default_conf, current, expected = 100.0, None, 1
        for (text, c), word_confs in zip(ordered, [[c for _, c in ws] for _, ws in sorted(lines.items())], strict=False):
            q = _Q.match(text)
            if q and int(q.group(1)) >= expected:
                current, expected = int(q.group(1)), int(q.group(1)) + 1
            if current is None:
                if _DEFAULT.match(text):
                    default_conf = min(default_conf, c)
                continue
            words.setdefault(current, []).extend(word_confs)
            if _FIELD.match(text) and _FIELD.match(text).group(1).lower() not in ("explanation", "topic"):
                critical[current] = min(critical.get(current, 100.0), c)

        def confidence(n: int) -> float:
            if not words.get(n):
                return 0.0
            mean = sum(words[n]) / len(words[n])
            return max(0.0, min(mean, critical.get(n, 0.0), default_conf)) / 100.0   # no Answer line read = no confidence

        res = ExtractionResult()
        rows = parse_question_blocks("\n".join(t for t, _ in ordered), res)
        if res.errors:
            raise OcrRejected(res.errors)
        out = []
        for number, row in rows:
            if row is None:
                continue
            out.append(OcrQuestion(
                stem=row["question"], options={k: row[f"option_{k.lower()}"] for k in "ABCD"}, correct=row["correct"],
                marks=row["marks"], negative=row["negative_marks"],
                confidence=confidence(number)))
        return out
