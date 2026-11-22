"""Validation of bulk-import rows: one function per concern, used by the full validation pass and by single-row corrections alike.

Rules (MCQ, MSQ and NAT - docs/institute-portal/QUESTION_TYPES_ARCHITECTURE.md; every other type is refused with a clear message).
One template holds every type: each row names its type in Question Type (MCQ, MSQ or NAT) and its key in Correct Answer (the
column the first template called Correct Option). An MCQ row (exactly as before):
  * required: Chapter, Difficulty, Question Type, Correct Option, the question (text, picture or both) and every option (text or
    picture, never both - the same rule as every other way a question is written);
  * Difficulty is EASY, MEDIUM or HARD; Question Type is MCQ; Correct Option is A, B, C or D;
  * Chapter, Topic and Subtopic must name canonical taxonomy records (by name or approved alias, or a mapping confirmed for this
    import) in the hierarchy Chapter > Topic > Subtopic. Unmatched values are errors with suggestions - never auto-mapped;
  * texts are at most 5,000 characters, contain no control characters, and two options may not be identical.
MSQ rows (2-8 options A-H, Correct Answer such as "A, C") and NAT rows (Correct Answer a number; Tolerance Type Exact / Plus-minus /
Range, Answer Format Integer / Decimal, optional unit) share the classification rules above, and their content and key are checked by
the shared rulebook (institute_common.core.question_types). Columns that do not belong to a row's type (an MCQ's tolerance, a NAT's
options, option E-H of an MCQ) must be empty, so nothing typed is silently ignored.

A row's content hash (question + options + pictures, normalised like the paper importer's duplicate check) lets the job find
duplicates inside the file and against the provider's existing questions of the subject.
"""
import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from institute_common.core import question_types as qtypes
from institute_common.core.taxonomy import DIFFICULTIES, LEVEL_MAX, clean_label, label_key
from institute_common.models.bulk_import import BulkImportRow
from institute_common.models.papers import Question, QuestionImageAsset, QuestionTaxonomyNode, QuestionVersion

from inst_paper.services import taxonomy
from inst_paper.services.extraction import same_text_key

MAX_TEXT = 5000
OPTION_LETTERS = ("a", "b", "c", "d")
SUPPORTED_TYPES = {"MCQ": "MCQ_SINGLE", "MCQ_SINGLE": "MCQ_SINGLE", "SINGLE_CORRECT": "MCQ_SINGLE"}
TYPED = {"MSQ": "MCQ_MULTI", "MCQ_MULTIPLE": "MCQ_MULTI", "MCQ_MULTI": "MCQ_MULTI", "MULTIPLE_CORRECT": "MCQ_MULTI",
         "NAT": "NUMERICAL", "NUMERICAL": "NUMERICAL", "NUMERIC": "NUMERICAL", "INTEGER": "NUMERICAL"}
NAT_COLUMNS = ("tolerance_type", "tolerance", "minimum", "maximum", "answer_format", "decimal_places", "unit")
TOLERANCE_KINDS = {"EXACT": "EXACT", "PLUS_MINUS": "ABS", "PLUSMINUS": "ABS", "±": "ABS", "ABS": "ABS", "ABSOLUTE": "ABS",
                   "RANGE": "RANGE"}
_CORRECT = re.compile(r"^\(?\s*(?:option\s*)?([a-d])\s*\)?$", re.IGNORECASE)
_ALLOWED_CONTROLS = {"\n", "\t"}


def error(field_name: str | None, message: str, **extra) -> dict:
    return {"field": field_name, "message": message, **extra}


# ------------------------------------------------------------------ content hash (duplicates)

def content_hash(stem: str, stem_image: str | None, options: list[tuple[str, str | None]]) -> str:
    """Identity of a question's content: normalised text (same_text_key) and picture checksums of the question and its options.
    Marks, explanation and classification are not part of it - the same question classified differently is still a duplicate."""
    parts = [same_text_key(stem or ""), stem_image or ""]
    for text, image in options:
        parts += [same_text_key(text or ""), image or ""]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def typed_hash(question_type: str, stem: str, stem_image: str | None, content: dict | None, picture=lambda ref: ref or "") -> str:
    """Identity of an MSQ / NAT question's content (decision D11): its type, normalised stem, stem picture and the type's own fields
    (MSQ options with their pictures; NAT format and unit) - never its key. An MCQ keeps `content_hash` exactly as before, so the same
    text as another type is never "the same question"."""
    parts = [same_text_key(stem or ""), stem_image or "", *qtypes.content_identity(question_type, content, same_text_key, picture)]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def version_hash(v: QuestionVersion, checksum_of) -> str:
    """The content identity of a stored version; `checksum_of(asset_id)` turns picture asset ids into checksums."""
    if (v.question_type or qtypes.MCQ) == qtypes.MCQ:
        return content_hash(v.stem, checksum_of(v.stem_image_id),
                            [(getattr(v, f"option_{x}"), checksum_of(getattr(v, f"option_{x}_image_id"))) for x in OPTION_LETTERS])
    return typed_hash(v.question_type, v.stem, checksum_of(v.stem_image_id), v.content,
                      lambda ref: checksum_of(UUID(str(ref))) if ref else "")


def version_key(v: QuestionVersion) -> str:
    """The answer key of a stored version as comparable text (D11: same question, different key = flagged for review)."""
    qtype = v.question_type or qtypes.MCQ
    return qtypes.key_identity(qtype, v.correct_option if qtype == qtypes.MCQ else v.answer_key)


def _type_word(data: dict) -> str:
    return re.sub(r"[\s\-]+", "_", (data.get("question_type") or "").strip().upper())


def row_type(data: dict) -> str:
    """The type a row names in Question Type: MSQ or NAT (with their usual spellings); every other value is read as an MCQ row, whose
    validation then accepts only MCQ (an unknown word is an error there)."""
    return TYPED.get(_type_word(data), qtypes.MCQ)


def typed_row(data: dict, images: dict) -> tuple[dict, dict]:
    """(content, key) of an MSQ / NAT row in canonical form, pictures as checksums; QuestionTypeError naming the column otherwise."""
    qtype = row_type(data)
    if qtype == qtypes.MSQ:
        letters = [x for x in "abcdefgh" if data.get(f"option_{x}_text") or images.get(f"option_{x}_image")]
        last = "abcdefgh".index(letters[-1]) + 1 if letters else 0
        for x in "abcdefgh"[:last]:
            if x not in letters:
                raise qtypes.QuestionTypeError(f"option_{x}_text", f"Option {x.upper()} is empty; fill the options in order (A, B, C ...).")
            if data.get(f"option_{x}_text") and images.get(f"option_{x}_image"):
                raise qtypes.QuestionTypeError(f"option_{x}_text", f"Option {x.upper()} has both text and a picture; keep one.")
        options = [{"id": x.upper(), "text": data.get(f"option_{x}_text", ""), "image": images.get(f"option_{x}_image")}
                   for x in "abcdefgh"[:last]]
        content, key = qtypes.validate_msq({"options": options}, {"correct": data.get("correct_option", "")})
        seen = [("image", o["image"]) if o["image"] else ("text", same_text_key(o["text"])) for o in content["options"]]
        if len(set(seen)) < len(seen):
            raise qtypes.QuestionTypeError("options", "Two options are identical.")
        return content, key
    kind = TOLERANCE_KINDS.get(re.sub(r"[\s\-]+", "_", data.get("tolerance_type", "").strip().upper()))
    if kind is None:
        raise qtypes.QuestionTypeError("tolerance_type", "Tolerance Type must be Exact, Plus-minus or Range.")
    used = {"ABS": ("tolerance",), "RANGE": ("minimum", "maximum"), "EXACT": ()}[kind]
    for column in ("tolerance", "minimum", "maximum"):
        if data.get(column, "").strip() and column not in used:
            raise qtypes.QuestionTypeError(column, f"Leave {column.capitalize()} empty for this tolerance type (or change the type).")
        if column in used and not data.get(column, "").strip():
            raise qtypes.QuestionTypeError(column, f"{column.capitalize()} is required for this tolerance type.")
    tolerance = {"kind": kind, "amount": data.get("tolerance", "").strip(), "min": data.get("minimum", "").strip(),
                 "max": data.get("maximum", "").strip()}
    fmt = data.get("answer_format", "").strip().upper()
    if fmt not in ("INTEGER", "DECIMAL"):
        raise qtypes.QuestionTypeError("answer_format", "Answer Format must be Integer or Decimal.")
    return qtypes.validate_nat({"format": fmt, "decimals": data.get("decimal_places", "").strip() or None, "unit": data.get("unit", "")},
                               {"value": data.get("correct_option", "").strip(), "tolerance": tolerance})


def row_label(row_no: int, data: dict) -> str:
    """How people find a row in their file: its row number (one sheet / one table for every type)."""
    return str(row_no)


def row_key(data: dict) -> str:
    """The answer-key identity of a validated row (D11)."""
    qtype = row_type(data)
    if qtype == qtypes.MCQ:
        return qtypes.key_identity(qtype, normalised(data)["correct_option"])
    try:
        return qtypes.key_identity(qtype, typed_row(data, {})[1])
    except qtypes.QuestionTypeError:
        return ""


def row_hash(data: dict, images: dict) -> str:
    qtype = row_type(data)
    if qtype != qtypes.MCQ:
        try:
            content = typed_row(data, images)[0]
        except qtypes.QuestionTypeError:
            content = None                     # an invalid row: its hash only has to be stable
        return typed_hash(qtype, data.get("question_text", ""), images.get("question_image"), content)
    return content_hash(data.get("question_text", ""), images.get("question_image"),
                        [(data.get(f"option_{x}_text", ""), images.get(f"option_{x}_image")) for x in OPTION_LETTERS])


def bank_hashes(db: Session, provider_id: UUID | None, subject_id: UUID) -> dict[str, dict[str, UUID]]:
    """{content identity: {answer-key identity: question id}} of the latest version of every ACTIVE question of one bank and subject
    (streamed, never all rows at once): a provider's bank (all its branches and provider-wide; never the platform's or another
    provider's questions - decision D4), or the platform bank when `provider_id` is None. `hash in bank_hashes(...)` still answers
    "is this content already in the bank"; the inner map tells a duplicate (same key) from a key conflict (D11)."""
    owner_q = Question.provider_id == provider_id if provider_id else Question.scope == "GLOBAL"
    # pictures the bank's questions may use: its own and the platform's; platform questions may also use provider pictures (a provider
    # paper copied into the platform keeps them)
    assets = select(QuestionImageAsset.asset_id, QuestionImageAsset.checksum)
    if provider_id:
        assets = assets.where(or_(QuestionImageAsset.provider_id == provider_id, QuestionImageAsset.provider_id.is_(None)))
    checksums = dict(db.execute(assets).all())
    latest = (select(QuestionVersion.question_id, func.max(QuestionVersion.version_no).label("v"))
              .join(Question, Question.question_id == QuestionVersion.question_id)
              .where(owner_q, Question.subject_id == subject_id, Question.status == "ACTIVE")
              .group_by(QuestionVersion.question_id).subquery())
    rows = db.execute(select(QuestionVersion).join(latest, (latest.c.question_id == QuestionVersion.question_id)
                                                   & (latest.c.v == QuestionVersion.version_no)).execution_options(yield_per=2000))
    out: dict[str, dict[str, UUID]] = {}
    for (v,) in rows:
        out.setdefault(version_hash(v, checksums.get), {}).setdefault(version_key(v), v.question_id)
    return out


# ------------------------------------------------------------------ taxonomy

@dataclass
class TaxonomyContext:
    """Taxonomy lookups for one job: the job's confirmed mappings first, then names and approved aliases. Results are cached
    for the pass (a file usually repeats a few chapter names thousands of times)."""
    provider_id: UUID | None                  # None: the platform list (a GLOBAL import)
    subject_id: UUID
    mappings: dict
    strict: bool = True                       # False: an unknown name is an error only where records exist (GLOBAL imports)
    cache: dict = field(default_factory=dict)

    @staticmethod
    def mapping_key(level: str, parent_id, value: str) -> str:
        return f"{level}|{parent_id or ''}|{label_key(value)}"

    def resolve(self, db: Session, level: str, parent_id, value: str):
        """(node or None, problem) for one value at one place in the hierarchy. problem: None when matched or acceptable as plain
        text (a non-strict list without records here), else {suggestions, preselect, elsewhere} for TAXONOMY_UNMATCHED."""
        key = self.mapping_key(level, parent_id, value)
        if key not in self.cache:
            node = None
            mapped = self.mappings.get(key)
            if mapped:
                candidate = db.get(QuestionTaxonomyNode, UUID(mapped))
                if candidate is not None and candidate.status == "ACTIVE":
                    node = candidate
            if node is None:
                node, _how = taxonomy.find(db, self.provider_id, self.subject_id, level, parent_id, value)
            problem = None
            if node is None and (self.strict or taxonomy.in_use(db, self.provider_id, self.subject_id, level, parent_id)):
                problem = taxonomy.suggestion_payload(taxonomy.suggest(db, self.provider_id, self.subject_id, level, parent_id, value))
            self.cache[key] = (node, problem)
        return self.cache[key]


def _check_taxonomy(db: Session, row: BulkImportRow, ctx: TaxonomyContext | None, errors: list[dict]) -> None:
    """Resolve Chapter > Topic > Subtopic; unmatched values become errors carrying suggestions and what to resolve. Without a
    context the labels are only checked as labels."""
    row.chapter_id = row.topic_id = row.subtopic_id = None
    if ctx is None:
        _check_plain_labels(row, errors)
        return
    parent = None
    for level, column, link in (("CHAPTER", "chapter", "chapter_id"), ("TOPIC", "topic", "topic_id"), ("SUBTOPIC", "subtopic", "subtopic_id")):
        value = row.data.get(column, "")
        if not value:
            if level == "CHAPTER":
                errors.append(error("chapter", "Chapter is required."))
            if level == "TOPIC" and row.data.get("subtopic"):
                errors.append(error("subtopic", "A subtopic needs its topic."))
            return
        if level != "CHAPTER" and parent is None:
            return                                   # the parent is unmatched: that error already explains the row
        try:
            label_key(value)
        except ValueError as exc:
            errors.append(error(column, f"{column.capitalize()} {exc}."))
            return
        node, problem = ctx.resolve(db, level, parent, value)
        if node is None and problem is None:         # plain text is fine here (no platform records at this place yet)
            _check_plain_labels(row, errors)
            return
        if node is None:
            errors.append(error(column, f"“{value}” is not a {level.lower()} of this subject yet.", code="TAXONOMY_UNMATCHED",
                                level=level, value=value, parent_id=str(parent) if parent else None, **problem))
            return
        setattr(row, link, node.node_id)
        parent = node.node_id


# ------------------------------------------------------------------ fields

def _clean_text(value: str) -> str | None:
    """None when the text contains control characters (other than line breaks and tabs)."""
    if any(unicodedata.category(ch) == "Cc" and ch not in _ALLOWED_CONTROLS for ch in value):
        return None
    return value


def _check_difficulty(data: dict, errors: list[dict]) -> None:
    difficulty = data.get("difficulty", "").strip().upper()
    if not difficulty:
        errors.append(error("difficulty", "Difficulty is required (Easy, Medium or Hard)."))
    elif difficulty not in DIFFICULTIES:
        errors.append(error("difficulty", f"Difficulty must be Easy, Medium or Hard, not “{data['difficulty']}”."))


def _check_stem(data: dict, images: dict, errors: list[dict]) -> None:
    stem = data.get("question_text", "")
    if len(stem) > MAX_TEXT:
        errors.append(error("question_text", f"At most {MAX_TEXT:,} characters."))
    elif _clean_text(stem) is None:
        errors.append(error("question_text", "Contains invisible control characters; retype or paste as plain text."))
    if not stem and "question_image" not in images:
        errors.append(error("question_text", "The question needs its text or a picture in Question Image."))
    elif stem and len(stem) < 3 and "question_image" not in images:
        errors.append(error("question_text", "The question text is too short."))


def _check_typed_fields(row: BulkImportRow, errors: list[dict]) -> None:
    """An MSQ / NAT row: classification, the question, then its content and key through the shared rulebook."""
    data, images = row.data, row.images or {}
    _check_difficulty(data, errors)
    _check_stem(data, images, errors)
    for x in "abcdefgh":
        value = data.get(f"option_{x}_text", "")
        if _clean_text(value) is None:
            errors.append(error(f"option_{x}_text", "Contains invisible control characters; retype or paste as plain text."))
    try:
        typed_row(data, images)
    except qtypes.QuestionTypeError as exc:     # the rulebook's names for the key are the template's Correct Answer column
        errors.append(error({"correct_options": "correct_option", "answer": "correct_option"}.get(exc.field, exc.field), exc.message))


LABEL = {qtypes.MCQ: "an MCQ", qtypes.MSQ: "an MSQ", qtypes.NAT: "a NAT question"}


def _check_foreign(row: BulkImportRow, qtype: str, errors: list[dict]) -> None:
    """Columns that do not belong to the row's type must be empty: nothing typed in the file is silently dropped."""
    data, images = row.data, row.images or {}
    letters = "" if qtype == qtypes.NAT else "abcd" if qtype == qtypes.MCQ else "abcdefgh"
    foreign = [f"option_{x}_text" for x in "abcdefgh" if x not in letters] + [f"option_{x}_image" for x in "abcdefgh" if x not in letters]
    if qtype != qtypes.NAT:
        foreign += list(NAT_COLUMNS)
    for column in foreign:
        if (data.get(column) or "").strip() or column in images:
            name = column.replace("_", " ").capitalize()
            errors.append(error(column, f"{name} is not used by {LABEL[qtype]}; leave it empty (or change the Question Type)."))


def _check_fields(row: BulkImportRow, errors: list[dict]) -> None:
    _check_foreign(row, row_type(row.data), errors)
    if row_type(row.data) != qtypes.MCQ:
        _check_typed_fields(row, errors)
        return
    data, images = row.data, row.images or {}
    qtype = re.sub(r"[\s\-]+", "_", data.get("question_type", "").strip().upper())
    if not qtype:
        errors.append(error("question_type", "Question Type is required (MCQ)."))
    elif qtype not in SUPPORTED_TYPES:
        errors.append(error("question_type", f"Unknown question type “{data['question_type']}”; use MCQ, MSQ or NAT."))
    _check_difficulty(data, errors)
    correct = data.get("correct_option", "").strip()
    if not correct:
        errors.append(error("correct_option", "Correct Option is required (A, B, C or D)."))
    elif not _CORRECT.match(correct):
        errors.append(error("correct_option", f"Correct Option must be one of A, B, C or D, not “{correct}”."))

    for column in ("question_text",) + tuple(f"option_{x}_text" for x in OPTION_LETTERS):
        value = data.get(column, "")
        if len(value) > MAX_TEXT:
            errors.append(error(column, f"At most {MAX_TEXT:,} characters."))
        elif _clean_text(value) is None:
            errors.append(error(column, "Contains invisible control characters; retype or paste as plain text."))
    stem = data.get("question_text", "")
    if not stem and "question_image" not in images:
        errors.append(error("question_text", "The question needs its text or a picture in Question Image."))
    elif stem and len(stem) < 3 and "question_image" not in images:
        errors.append(error("question_text", "The question text is too short."))
    keys = []
    for x in OPTION_LETTERS:
        text, picture = data.get(f"option_{x}_text", ""), images.get(f"option_{x}_image")
        if text and picture:
            errors.append(error(f"option_{x}_text", f"Option {x.upper()} has both text and a picture; keep one."))
        elif not text and not picture:
            errors.append(error(f"option_{x}_text", f"Option {x.upper()} is empty: give its text or a picture."))
        else:
            keys.append(("image", picture) if picture else ("text", same_text_key(text)))
    if len(set(keys)) < len(keys):
        errors.append(error("options", "Two options are identical."))


def normalised(data: dict) -> dict:
    """The values as they will be stored: question type and difficulty in their canonical spelling, correct option as one letter."""
    out = dict(data)
    if row_type(data) != qtypes.MCQ:
        out["difficulty"] = data.get("difficulty", "").strip().upper()
        return out
    qtype = re.sub(r"[\s\-]+", "_", data.get("question_type", "").strip().upper())
    out["question_type"] = SUPPORTED_TYPES.get(qtype, data.get("question_type", ""))
    out["difficulty"] = data.get("difficulty", "").strip().upper()
    match = _CORRECT.match(data.get("correct_option", "").strip())
    out["correct_option"] = match.group(1).upper() if match else data.get("correct_option", "")
    return out


def content_format(data: dict, images: dict) -> str:
    """How the question is written, derived (never typed): TEXT, IMAGE (the question is a picture) or TEXT_WITH_IMAGE."""
    has_text = bool(data.get("question_text"))
    has_picture = bool(images)
    if not has_text and "question_image" in images:
        return "IMAGE"
    return "TEXT_WITH_IMAGE" if has_text and has_picture else "TEXT"


def _check_plain_labels(row: BulkImportRow, errors: list[dict]) -> None:
    for level, column in (("CHAPTER", "chapter"), ("TOPIC", "topic"), ("SUBTOPIC", "subtopic")):
        value = row.data.get(column, "")
        if not value:
            if level == "CHAPTER":
                errors.append(error("chapter", "Chapter is required."))
            if level == "TOPIC" and row.data.get("subtopic"):
                errors.append(error("subtopic", "A subtopic needs its topic."))
            return
        try:
            clean_label(value, LEVEL_MAX[level])
        except ValueError as exc:
            errors.append(error(column, f"{column.capitalize()} {exc}."))
            return


def plain_labels(data: dict) -> dict:
    """The cleaned chapter/topic/subtopic of a validated GLOBAL row, as stored on the platform question."""
    return {c: clean_label(data.get(c) or None, LEVEL_MAX[level]) for level, c in (("CHAPTER", "chapter"), ("TOPIC", "topic"), ("SUBTOPIC", "subtopic"))}


def validate_row(db: Session, row: BulkImportRow, ctx: TaxonomyContext | None) -> None:
    """Validate one row in place: status VALID or INVALID, errors, taxonomy links and content hash. Duplicates are decided by the
    caller, which sees the whole file (and the bank)."""
    errors: list[dict] = list(row.read_errors or [])
    _check_fields(row, errors)
    _check_taxonomy(db, row, ctx, errors)
    row.errors = errors or None
    row.content_hash = row_hash(row.data, row.images or {})
    row.status = "INVALID" if errors else "VALID"
