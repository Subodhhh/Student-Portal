"""How alike two chapter / topic / subtopic names are - for SUGGESTIONS only (docs/institute-portal/TAXONOMY_ALIASES_PLAN.md §4.1).

An exact name or approved alias is linked by services/taxonomy.find (normalised with core.taxonomy.label_key); this module only ranks
the records a person may have meant ("Did you mean …?"). It never decides: the caller shows the candidates and a person chooses.

    match_key("Newton's 1st Law of Motion") == "newton first law motion"
    score(match_key(a), match_key(b)) -> 0..1   (1.0: the same words, only spelled / punctuated / ordered differently)
"""
import difflib
import re

from institute_common.core.taxonomy import label_key

STRONG = 0.85            # one candidate at least this close (and clearly ahead of the next) is pre-selected - still confirmed
POSSIBLE = 0.60          # below this a name is not worth suggesting
CLEAR_LEAD = 0.10        # how far the best must be ahead of the second best to be pre-selected
LIMIT = 6

FILLER = frozenset({"of", "the", "a", "an", "and", "in", "on", "for", "to", "with", "its"})
ORDINALS = {"1st": "first", "2nd": "second", "3rd": "third", "4th": "fourth", "5th": "fifth", "6th": "sixth", "7th": "seventh",
            "8th": "eighth", "9th": "ninth", "10th": "tenth", "i": "first", "ii": "second", "iii": "third"}
NUMBERS = {"1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
           "10": "ten"}
_APOSTROPHES = re.compile(r"[’'`´]")
_NOT_WORD = re.compile(r"[^\w\s]")
_LETTER_DIGIT = re.compile(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_]{3,})")     # "newtons1st" -> "newtons 1st" (but "1st" stays)


def _singular(word: str) -> str:
    """Plain English plural folding, enough to make "laws" = "law" and "properties" = "property" (both sides are folded alike)."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def match_key(value: str | None) -> str:
    """The words of a name that matter for "is this the same thing?": case, accents/width (label_key), apostrophes and punctuation,
    "1st"/"first", plural "s" and filler words ("of", "the", ...) are ignored. "" for a blank or invalid name."""
    try:
        key = label_key(value) or ""
    except ValueError:
        return ""
    key = _NOT_WORD.sub(" ", _APOSTROPHES.sub("", key))
    words = []
    for word in _LETTER_DIGIT.sub(" ", key).split():
        word = ORDINALS.get(word) or NUMBERS.get(word) or word
        if word not in FILLER:
            words.append(_singular(word))
    return " ".join(words)


def score(a: str, b: str) -> float:
    """Closeness of two match keys, 0..1: shared words (order ignored; a name contained in the other counts for half its weight)
    blended with letter similarity of the sorted words (typos: "gravitaion" ~ "gravitation")."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    wa, wb = set(a.split()), set(b.split())
    small, large = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    common = sum(_word_match(w, large) for w in small)            # a misspelt word still counts ("gravitaion" ~ "gravitation")
    overlap = 0.5 * (common / len(small) + common / len(large))
    letters = difflib.SequenceMatcher(None, " ".join(sorted(wa)), " ".join(sorted(wb))).ratio()
    blended = 0.55 * overlap + 0.45 * letters
    if common == len(small) and wa != wb:                         # every word of one name is in the other ("Work" in "Work, Energy
        blended = max(blended, POSSIBLE + 0.05)                   # and Power"): always worth offering, never pre-selected on that alone
    return round(blended, 3)


def _word_match(word: str, words: set[str]) -> float:
    """1 for the same word, the letter similarity for a close misspelling (>= 0.8, words of 5+ letters), else 0."""
    if word in words:
        return 1.0
    if len(word) < 5:
        return 0.0
    best = max((difflib.SequenceMatcher(None, word, other).ratio() for other in words if abs(len(other) - len(word)) <= 2), default=0.0)
    return best if best >= 0.8 else 0.0


def rank(value: str, names: list[tuple[object, str]]) -> list[tuple[object, float, str]]:
    """[(item, score, the name or alias that matched)] for every `names` entry (item, name) at least POSSIBLE close, best first,
    one entry per item (its best name), at most LIMIT."""
    key = match_key(value)
    best: dict[int, tuple[object, float, str]] = {}
    for item, name in names:
        s = score(key, match_key(name))
        if s >= POSSIBLE and (id(item) not in best or s > best[id(item)][1]):
            best[id(item)] = (item, s, name)
    return sorted(best.values(), key=lambda x: (-x[1], str(x[2]).lower()))[:LIMIT]


def preselect(ranked: list[tuple[object, float, str]]) -> object | None:
    """The single candidate to pre-select (decision A1: still confirmed by a person), or None when none is clearly the one."""
    if not ranked or ranked[0][1] < STRONG:
        return None
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < CLEAR_LEAD:
        return None
    return ranked[0][0]
