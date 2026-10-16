"""Printed-text OCR for scanned question papers (Tesseract), registered only when OCR_ENGINE=tesseract.

The page must follow the numbered-block layout accepted for Word/PDF papers (see services/documents.py). A question's confidence (0..1)
is the lower of: the weakest word on the lines that decide scoring (its Answer / Marks / Negative lines and the paper defaults), and
the mean confidence of all its words. extract_image then rejects the whole paper if any question is below OCR_MIN_CONFIDENCE. Handwriting and poor scans therefore fail instead of importing
guesses, and an accepted OCR paper is still only a VALIDATED draft that a person must preview and approve."""
import io

from inst_paper.services.documents import _DEFAULT, _FIELD, _Q, parse_question_blocks
from inst_paper.services.extraction import ExtractionResult, OcrQuestion, OcrRejected

TIMEOUT_SECONDS = 60


class TesseractBackend:
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
        for (text, c), word_confs in zip(ordered, [[c for _, c in ws] for _, ws in sorted(lines.items())]):
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
