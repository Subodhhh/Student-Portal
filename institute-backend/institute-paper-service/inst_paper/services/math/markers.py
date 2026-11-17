"""In-band markers the Word/Excel extractors put into field text for content that is not plain text, so it travels through the
question/option/line parser unchanged and reaches recognition: a converted Word equation, an equation that could not be converted,
an image/embedded object, or a symbol-font character. Private-use characters, which never occur in provider text."""
import re

OPEN, SEP, CLOSE = "", "", ""
EQUATION, EQUATION_FAILED, EQUATION_UNVERIFIED, OBJECT, SYMBOL, SCRIPT_SPLIT = "EQ", "EQFAIL", "EQCHECK", "OBJECT", "SYMBOL", "SCRIPTS"
PATTERN = re.compile(f"{OPEN}([A-Z]+){SEP}([^{CLOSE}]*){CLOSE}")
SPLIT_TEXT = f"{OPEN}SCRIPTS{SEP}{CLOSE}"      # stays inside the text: it only keeps two adjacent scripts apart (see office._join)
SPLITTING = re.compile(f"{OPEN}(?!SCRIPTS{SEP})([A-Z]+){SEP}([^{CLOSE}]*){CLOSE}")
REASONS = {
    EQUATION_FAILED: "This Word equation uses a structure the converter does not support; it is shown in a linear form "
                     "(powers as ^( ), fractions as ( )/( )) - correct it before importing.",
    EQUATION_UNVERIFIED: "This Word equation was converted, but the result did not match the equation's own symbols, so it was "
                         "not used; it is shown in a linear form - check and correct it before importing.",
    OBJECT: "The document has an image or embedded object here; it cannot be imported as text.",
    SYMBOL: "A symbol-font character could not be identified.",
    SCRIPT_SPLIT: "Two separately formatted superscripts (or subscripts) are side by side: it looks like one power (like 5²³) but "
                  "may be a power of a power (5^2 then ^3); check how it should read.",
}


def make(kind: str, payload: str) -> str:
    return f"{OPEN}{kind}{SEP}{payload.replace(OPEN, '').replace(SEP, '').replace(CLOSE, '')}{CLOSE}"


DETAIL = "\ue003"     # separates a marker's displayed text from a detail shown only in the review reason


def readable(text: str) -> str:
    """Markers as text: an equation's LaTeX in \\( \\), an unconverted equation's extracted text, '[image]' for objects."""
    def one(m):
        kind, payload = m.group(1), m.group(2)
        if kind == EQUATION:
            return f"\\({payload}\\)"
        if kind == OBJECT:
            return f"[{payload or 'image'}]"
        if kind == SCRIPT_SPLIT:
            return ""
        return payload.split(DETAIL)[0]
    return PATTERN.sub(one, text or "")


def strip_private(text: str) -> str:
    return (text or "").replace(OPEN, "").replace(SEP, "").replace(CLOSE, "").replace(DETAIL, "")
