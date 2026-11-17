"""Scientific recognition step of paper import: runs after the all-or-nothing extraction validator and before the paper is stored.

  review_paper()        every question field -> outcome (CONVERTED / PLAIN_TEXT / NEEDS_REVIEW / EXTRACTION_ERROR), proposed text,
                        reason codes and spans: the provider preview.
  apply_resolutions()   the provider's decision per flagged (or any) field: keep the original as plain text, or an edited text that
                        must itself pass recognition with no flag. Unresolved NEEDS_REVIEW / EXTRACTION_ERROR fields block the import.
  record()              what is stored with the upload (paper_uploads.recognition): every field's original, stored text, outcome,
                        reasons, spans, the decision and who made it, and the engine version; the original file is stored too.

The original text is never overwritten in the record; the stored question text is the accepted conversion (LaTeX in \\( \\)).
"""
import hashlib
import json
import re
from dataclasses import replace

from inst_paper.services import extraction
from inst_paper.services.question_images import IMAGE_COLUMNS
from inst_paper.services.math.suggest import MAX_CANDIDATES, candidate_set, readings
from inst_paper.services.math import (CONVERTED, ENGINE_VERSION, EXTRACTION_ERROR, NEEDS_REVIEW, PLAIN_TEXT, markers, recognise)

FIELDS = ("question", "option_a", "option_b", "option_c", "option_d", "explanation")
BLOCKING = (NEEDS_REVIEW, EXTRACTION_ERROR)
MAX_RESOLUTIONS_BYTES = 1_000_000


class ResolutionError(ValueError):
    pass


def _get(q, f: str) -> str | None:
    if f == "question":
        return q.stem
    if f == "explanation":
        return q.explanation
    return q.options[f[-1].upper()]


def _suggestions(r, domain: str | None, option: bool, sets: list[dict] | None = None) -> list[dict]:
    """For each expression the engine would not decide (a unit or a variable; a power, a fraction or a superscript flattened by
    copying), the readings the reviewer can pick: the whole field with
    only that expression replaced. A reading is offered only when the whole field, read that way, passes recognition (a field with
    another doubt left gets none - choosing it would not resolve the field). The import checks the choice again like any correction."""
    out = []

    def passes(text: str) -> str | None:
        c = recognise(text, subject=domain, formula_field=option)
        return c.text if c.outcome not in BLOCKING else None

    for s in r.spans:
        if s.status == "flagged" and s.code != "AMBIGUOUS_UNIT" and s.domain in ("mathematics", "chemistry") and markers.readable(r.original) == r.original:
            # a power, fraction or flattened superscript in doubt: every reading written out explicitly and checked as a correction
            found = candidate_set(r.original, s.start, s.end, s.code, passes)
            for label, text in found.readings:
                out.append({"expression": s.original, "label": label, "text": passes(text)})   # typeset, as it would be stored
            if sets is not None:
                # what the teacher is told about the list: every reading the grammar allows, or how many were not checked
                sets.append({"expression": s.original, "shown": len(found.readings), "combinations": found.combinations,
                             "checked": found.checked, "rejected": found.rejected, "complete": found.complete,
                             "limit": MAX_CANDIDATES})
            continue
        if s.status != "flagged" or s.code != "AMBIGUOUS_UNIT":
            continue
        word = s.domain == "word-equation"
        for label, latex in readings(s.code, s.original, s.latex if word else None, word):
            text = markers.strip_private(markers.readable(r.original[:s.start]) + "\\(" + latex + "\\)" + markers.readable(r.original[s.end:]))
            if recognise(text, subject=domain, formula_field=option).outcome not in BLOCKING:
                out.append({"expression": markers.readable(s.original), "label": label, "text": text})
    return out


def asks_for_unit(stem: str | None) -> bool:
    """The question asks for a unit ("Unit of rate constant ... is:", "SI units of k"): its options are units."""
    return bool(stem and re.search(r"\bunits?\b", stem, re.IGNORECASE))


# What a teacher is told about a flagged expression: no recognition codes, no notation to learn. The technical `reason` stays for
# Diagnostics. Codes not listed fall back to EXPLANATION_DEFAULT.
_CHOOSE = " Choose the version that matches your paper, or build it yourself."
EXPLANATIONS = {
    "OPTION_LETTER_IN_TEXT": "The option begins with an option letter (such as 'A.'). Students already see the letter beside each option, "
                             "so it would appear twice. Remove it if it is only a label; keep it if it is part of the answer.",
    "WEB_COPY_WITHOUT_SOURCE": "This formula was copied from a web page without its LaTeX source, so its characters are in the order the "
                               "page lays them out (a fraction's bottom can come before its top). It cannot be read reliably: type it, "
                               "or copy it again so the source comes with it.",
    "UNSUPPORTED_WEB_FORMULA": "This formula was copied from a web page with its LaTeX source, but the source uses something the portals "
                               "cannot display safely. Type the formula instead.",
    "FLATTENED_POWER": "A small raised number or letter may have been lost when this was copied (e13x could be e¹³x or e¹³ˣ)." + _CHOOSE,
    "FLATTENED_BRACKET_POWER": "A number after a bracket may be a lost power, or it may multiply the bracket." + _CHOOSE,
    "AMBIGUOUS_NUMBER_ADJACENT": "A number is written right after a letter, so it could be a power, a small index below, or a multiplication."
                                 + _CHOOSE,
    "AMBIGUOUS_POWER": "It is not clear how much of this belongs in the raised power." + _CHOOSE,
    "AMBIGUOUS_FRACTION": "It is not clear which parts are on the top and which are on the bottom of the fraction." + _CHOOSE,
    "AMBIGUOUS_DENOMINATOR": "It is not clear how much of this is on the bottom of the fraction." + _CHOOSE,
    "AMBIGUOUS_DERIVATIVE": "It is not clear what is being differentiated." + _CHOOSE,
    "AMBIGUOUS_MATRIX": "A minus sign in the matrix could start a negative entry or subtract two entries." + _CHOOSE,
    "BOUND_TYPED_ON_LINE": "A limit seems to be written on the line instead of above or below the symbol." + _CHOOSE,
    "BOUND_INCOMPLETE": "The integral shows only one of its two limits. Add the missing limit, or remove the one shown.",
    "AMBIGUOUS_UNIT": "This letter could be a unit (like m for metre) or a quantity in the formula." + _CHOOSE,
    "UNIT_DIGIT_POWER": "A number right after a unit could be its power (m²) or a separate number." + _CHOOSE,
    "INLINE_EXPONENT": "A unit's power seems to be written with a hyphen (like ms-1 for m s⁻¹)." + _CHOOSE,
    "DIMENSION_NOTATION": "This looks like a dimensional formula (like MLT-2) written without its raised powers." + _CHOOSE,
    "AMBIGUOUS_CHEMICAL_CHARGE": "In a chemical formula, the numbers before + or − could be a count of atoms or the charge "
                                 "(Fe3+ is usually Fe³⁺)." + _CHOOSE,
    "UNKNOWN_ELEMENT_SYMBOL": "This looks like a chemical formula, but one symbol is not a chemical element. Check the spelling.",
    "AMBIGUOUS_DOMAIN": "This could be a chemical formula or a mathematical expression." + _CHOOSE,
    "AMBIGUOUS_CHARACTER": "A digit sits next to a letter that looks like it (0 and O, 1 and l). Check which one is meant.",
    "JOINED_TO_TEXT": "A formula is joined to a word, so it is not clear where the formula ends. Check the expression.",
    "EXPRESSION_TOO_COMPLEX": "This is too long or too deeply nested to check automatically. Check it and correct it if needed.",
    "UNSUPPORTED_WORD_EQUATION": "Part of this Word equation could not be read. Rebuild the expression.",
    "WORD_EQUATION_NOT_VERIFIED": "This Word equation could not be checked automatically. Compare it with your file.",
    "UNSUPPORTED_OBJECT": "This part of the file is a picture or an object, not text. Rebuild it as text or a formula.",
    "UNSUPPORTED_SYMBOL": "A symbol in the file could not be read. Rebuild the expression.",
    "LATEX_WITHOUT_DELIMITERS": "This contains maths commands that cannot be shown as written. Rebuild the expression.",
    "LATEX_VALIDATION_FAILED": "This maths cannot be shown to students safely as written. Rebuild the expression.",
}
EXPLANATION_DEFAULT = "This could not be read with certainty. Compare it with your paper and correct it if needed."


def explanation(code: str | None) -> str:
    return EXPLANATIONS.get(code or "", EXPLANATION_DEFAULT)


def _span(r, s) -> dict:
    # start/end: the expression's position in the readable original (the text shown to the reviewer), so a correction can replace
    # exactly that part of the field
    start = len(markers.readable(r.original[:s.start]))
    shown = markers.readable(s.original)
    return {"original": shown, "status": s.status, "latex": s.latex, "code": s.code, "reason": s.reason, "domain": s.domain,
            "start": start, "end": start + len(shown),
            "explanation": explanation(s.code) if s.status in ("flagged", "error") else None}


def _field(text: str, domain: str | None, field: str = "", unit_answer: bool = False) -> dict:
    # an option may be exactly one formula; an option to a question asking for a unit may be a lone unit with a power (s⁻¹)
    r = recognise(text, subject=domain, formula_field=field.startswith("option_"), unit_answer=unit_answer and field.startswith("option_"))
    if field.startswith("option_"):
        _flag_option_letter(r)
    sets: list[dict] = []
    return {
        "suggestions": _suggestions(r, domain, field.startswith("option_"), sets),
        "candidate_sets": sets,
        "source": r.original, "original": r.readable_original, "text": r.text, "outcome": r.outcome, "reasons": r.reasons,
        "spans": [_span(r, s) for s in r.spans],
    }


def _flag_option_letter(r) -> None:
    """An option pasted with its own letter ('A. 2x', '(B) 3'): the teacher decides whether the letter is a label (remove it) or
    content; it is never removed here."""
    m = re.match(r"\s*(?:\(([A-Da-d])\)|([A-Da-d])[.)])\s+", r.original)
    if m and not any(s.start < m.end() and s.status in ("flagged", "error") for s in r.spans):
        from inst_paper.services.math import Span
        r.spans.insert(0, Span(0, m.end(), m.group(0), "flagged", reason="The option begins with an option letter.",
                               code="OPTION_LETTER_IN_TEXT", domain="layout"))


def field_result(text: str, domain: str | None, field: str) -> dict:
    """One field recognised exactly as on import (live preview of a correction)."""
    return _field(markers.strip_private(text), domain, field)


def review_paper(questions: list, domain: str | None) -> list[dict]:
    out = []
    for n, q in enumerate(questions, start=1):
        fields = {f: _field(_get(q, f), domain, f, asks_for_unit(q.stem)) for f in FIELDS if _get(q, f)}
        images = {f: {"sha256": img.sha256, "mime": img.mime, "width": img.width, "height": img.height, "name": img.name}
                  for f, img in getattr(q, "images", {}).items()}       # the asset id is added once the picture is stored
        out.append({"number": n, "row": q.row, "correct": q.correct, "marks": str(q.marks), "negative_marks": str(q.negative),
                    "topic": q.topic, "chapter": getattr(q, "chapter", None), "sub_topic": getattr(q, "sub_topic", None),
                    "explanation": q.explanation, "images": images, "fields": fields,
                    "status": "NEEDS_REVIEW" if any(x["outcome"] in BLOCKING for x in fields.values()) else "READY"})
    return out


def summary(review: list[dict]) -> dict:
    counts = {CONVERTED: 0, PLAIN_TEXT: 0, NEEDS_REVIEW: 0, EXTRACTION_ERROR: 0}
    for q in review:
        for x in q["fields"].values():
            counts[x["outcome"]] += 1
    return {"questions": len(review), "questions_needing_review": sum(q["status"] != "READY" for q in review), "fields": counts}


def review_token(data: bytes, key_data: bytes | None, domain: str | None) -> str:
    """Binds a provider's decisions to the exact file, answer key, subject domain and engine version they were made on."""
    h = hashlib.sha256()
    for part in (data, key_data or b"", (domain or "").encode(), ENGINE_VERSION.encode()):
        h.update(hashlib.sha256(part).digest())
    return h.hexdigest()


def parse_resolutions(raw: str | None) -> dict[str, dict]:
    """{"<question number>:<field>": {"action": "keep_original" | "edit", "text": "..."}}"""
    if not raw:
        return {}
    if len(raw.encode()) > MAX_RESOLUTIONS_BYTES:
        raise ResolutionError("The review decisions are too large.")
    try:
        data = json.loads(raw)
    except ValueError:
        raise ResolutionError("The review decisions are not valid JSON.")
    if not isinstance(data, dict):
        raise ResolutionError("The review decisions must be an object.")
    out = {}
    for key, value in data.items():
        number, _, f = str(key).partition(":")
        if not number.isdigit() or f not in FIELDS or not isinstance(value, dict):
            raise ResolutionError(f"Unknown review decision {key!r}.")
        action = value.get("action")
        if action not in ("keep_original", "edit"):
            raise ResolutionError(f"Decision {key}: action must be keep_original or edit.")
        text = value.get("text")
        if action == "edit" and (not isinstance(text, str) or not text.strip() or len(text) > extraction.MAX_CELL):
            raise ResolutionError(f"Decision {key}: the corrected text must be 1-{extraction.MAX_CELL} characters.")
        out[f"{int(number)}:{f}"] = {"action": action, "text": text if action == "edit" else None}
    return out


def apply_resolutions(questions: list, review: list[dict], decisions: dict[str, dict], domain: str | None):
    """-> (questions with the text to store, per-field record, problems). `problems` lists every blocking field left unresolved and
    every edit that still does not pass recognition; the paper is imported only when it is empty."""
    problems: list[dict] = []
    known = {f"{q['number']}:{f}" for q in review for f in q["fields"]}
    for key in decisions:
        if key not in known:
            problems.append({"row": None, "field": key.split(":")[1], "question": key.split(":")[0],
                             "message": f"Question {key.split(':')[0]} has no {key.split(':')[1]} field to decide on."})
    final = []
    for q, rq in zip(questions, review, strict=False):
        values = {}
        for f, rec in rq["fields"].items():
            key = f"{rq['number']}:{f}"
            d = decisions.get(key)
            rec["decision"] = d["action"] if d else ("accepted" if rec["outcome"] not in BLOCKING else None)
            if d and d["action"] == "keep_original" and rec["outcome"] == EXTRACTION_ERROR:
                # an image or an equation that could not be extracted has no faithful text form: keeping "the original"
                # would store a placeholder or a flattened equation, so the provider must correct it instead
                problems.append({"row": q.row, "field": f, "question": str(rq["number"]), "reasons": rec["reasons"],
                                 "message": "This field contains content that could not be extracted as text "
                                            f"({', '.join(rec['reasons'])}); keeping it as typed is not possible - correct it."})
                rec["stored"] = None
            elif d and d["action"] == "keep_original":
                rec["stored"] = markers.strip_private(rec["original"])     # the field exactly as in the file
            elif d and d["action"] == "edit":
                # a teacher who picked one of the offered readings: record which one (the audit keeps every reading offered)
                picked = next((sg["label"] for sg in rec.get("suggestions", []) if sg["text"] == d["text"]), None)
                if picked:
                    rec["selected_reading"] = picked
                edited = _field(markers.strip_private(d["text"]), domain, f, asks_for_unit(q.stem))
                rec["edited"] = {k: edited[k] for k in ("original", "text", "outcome", "reasons", "spans")}
                if edited["outcome"] in BLOCKING:
                    problems.append({"row": q.row, "field": f, "question": str(rq["number"]),
                                     "message": f"The corrected text still needs review ({', '.join(edited['reasons'])}).",
                                     "reasons": edited["reasons"]})
                rec["stored"] = edited["text"]
            elif rec["outcome"] in BLOCKING:
                problems.append({"row": q.row, "field": f, "question": str(rq["number"]),
                                 "message": f"Needs review: {', '.join(rec['reasons'])}.", "reasons": rec["reasons"]})
                rec["stored"] = None
            else:
                rec["stored"] = rec["text"]
            values[f] = rec["stored"]
        if not problems:
            # a picture-only field has no text to recognise: it stays "" (its picture travels in q.images)
            final.append(replace(q, stem=values.get("question", ""), explanation=values.get("explanation") or q.explanation,
                                 options={k: values.get(f"option_{k.lower()}", "") for k in "ABCD"}))
    if problems:
        return [], review, problems
    # the stored texts go through the same validator as the file (empty options, duplicates ...): one problem rejects the paper
    rows = [(q.row, {"question": q.stem, **{f"option_{k.lower()}": v for k, v in q.options.items()}, "correct": q.correct,
                     "marks": str(q.marks), "negative_marks": str(q.negative), "explanation": q.explanation or "", "topic": q.topic or ""})
            for q in final]
    pictures = {q.row: {column: q.images[f] for column, f in IMAGE_COLUMNS.items() if f in q.images} for q in final}
    checked = extraction.validate_paper(rows, len(rows) + 1, pictures)
    if checked.errors:
        return [], review, checked.errors
    return final, review, []


def record(review: list[dict], domain: str | None, subject_name: str | None, token: str, actor) -> dict:
    return {"engine_version": ENGINE_VERSION, "domain": domain, "subject": subject_name, "review_token": token,
            "decided_by": str(actor), "summary": summary(review),
            "questions": [{"number": q["number"], "row": q["row"], "images": q.get("images", {}),
                           "fields": {f: {k: v for k, v in rec.items() if k != "text"} for f, rec in q["fields"].items()}}
                          for q in review]}
