"""The words and examples of the bulk-import template (Excel and Word). One source for both files, kept beside the parser rules.

ONE template for every question type: each row names its own type in Question Type - MCQ (one correct option), MSQ (one or more
correct options, 2-8 options A-H) or NAT (a typed number answer) - and its key in Correct Answer. Rows of different types can follow
each other in any order. The first template's 16 columns are kept (an MCQ file made before MSQ / NAT still imports); the MSQ and NAT
columns are added after them.
Every column name here must be one the parser accepts (services/bulk_reader.TEMPLATE_COLUMNS) and every example must pass validation
(services/bulk_validation): tests/test_import_templates.py checks both, so the template can never drift from the importer.
Written for teachers: short sentences, no technical words.
"""


def _option(letter: str, required: str, example: str = "") -> list[tuple]:
    return [(f"Option {letter} Text", required, f"The text of option {letter}. Give text or a picture, not both.", example),
            (f"Option {letter} Image", "Optional", f"A picture used as option {letter}. Then leave Option {letter} Text empty.", "(a picture)")]


# (column title in the file, required?, what to enter, example) - in the order the template shows them
COLUMNS = [
    ("Chapter", "Required", "The chapter, exactly as it is named in the Question Bank for this subject.", "Laws of Motion"),
    ("Topic", "Optional", "A topic of that chapter. Leave empty if you do not use topics.", "Force"),
    ("Subtopic", "Optional", "A subtopic of that topic. Needs a Topic.", "Newton's Second Law"),
    ("Difficulty", "Required", "Easy, Medium or Hard. Choose from the list.", "Easy"),
    ("Question Type", "Required", "MCQ (one correct option), MSQ (one or more correct options) or NAT (the answer is a number). "
                                  "Choose from the list. Each row can have its own type.", "MCQ"),
    ("Question Text", "Required, unless the whole question is a picture",
     "The question as text. Leave it empty only when the complete question is in Question Image.", "What is the SI unit of force?"),
    ("Question Image", "Optional", "A picture for the question: a diagram, a graph, or the complete question.", "(a picture)"),
    *_option("A", "MCQ and MSQ: required, unless Option A is a picture", "Newton"),
    *_option("B", "MCQ and MSQ: required, unless Option B is a picture", "Joule"),
    *_option("C", "MCQ: required, unless a picture. MSQ: optional", "Watt"),
    *_option("D", "MCQ: required, unless a picture. MSQ: optional", "Pascal"),
    ("Correct Answer", "Required",
     "MCQ: the letter of the correct option (A, B, C or D). MSQ: every correct letter, separated by commas (A, C). NAT: the correct "
     "number, digits only (no commas, units or exponents; at most 12 digits and 6 decimal places).", "A"),
    *_option("E", "MSQ only, optional"), *_option("F", "MSQ only, optional"),
    *_option("G", "MSQ only, optional"), *_option("H", "MSQ only, optional"),
    ("Tolerance Type", "NAT only, required", "Exact (only the Correct Answer), Plus-minus (the answer give or take the Tolerance) or "
                                             "Range (any number from Minimum to Maximum). Choose from the list.", "Plus-minus"),
    ("Tolerance", "NAT with Plus-minus", "How far a student's answer may be from the Correct Answer and still be right (the limits "
                                         "count as right).", "0.05"),
    ("Minimum", "NAT with Range", "The smallest accepted number (accepted itself).", "14.5"),
    ("Maximum", "NAT with Range", "The largest accepted number (accepted itself).", "15"),
    ("Answer Format", "NAT only, required", "Integer (whole numbers only) or Decimal. Choose from the list.", "Decimal"),
    ("Decimal Places", "NAT only, optional", "For Decimal: the most decimal places the answer may have (0 to 6; empty means 6).", "2"),
    ("Unit", "NAT only, optional", "Shown next to the answer box, for example m/s². Students never type it; it is not checked.", "m/s²"),
]
TITLES = [c[0] for c in COLUMNS]
# Word: the columns that fit a landscape page. Option E-H, Minimum, Maximum and Decimal Places columns may be added to the table when
# a question needs them (any template column is accepted, in any order).
WORD_TITLES = [t for t in TITLES if not (t.startswith("Option ") and t[7] in "EFGH") and t not in ("Minimum", "Maximum", "Decimal Places")]

DIFFICULTY_CHOICES = ("Easy", "Medium", "Hard")
QUESTION_TYPE_CHOICES = ("MCQ", "MSQ", "NAT")
TOLERANCE_TYPE_CHOICES = ("Exact", "Plus-minus", "Range")
ANSWER_FORMAT_CHOICES = ("Integer", "Decimal")

GENERAL_RULES = [
    "Choose the Subject on the upload page. Do not add a Subject column: every question in one file belongs to that subject.",
    "Write one question per row, of any type: an MCQ, then an MSQ, then a NAT question, in any order. Keep the header row exactly as it "
    "is; do not rename or merge columns.",
    "Fill only the columns of the row's type and leave the others empty: a NAT question has no options; an MCQ has no tolerance and "
    "no options E to H.",
    "MSQ: give 2 to 8 options in order (A, B, C ...), and every correct letter in Correct Answer. A student scores only when they "
    "choose exactly those options; a wrong or incomplete choice scores 0, never negative.",
    "Marks are not entered here. They are set when a test is created.",
    "Type plain text and numbers only: no formulas, no merged cells, no hidden rows.",
    "Pictures: PNG, JPG, GIF or WebP, at most 5 MB each. Put each picture in its own image cell, one picture per cell.",
    "If the options are printed inside the question picture, type A, B, C and D in the Option Text columns.",
    "An Excel file may hold up to 100,000 questions. A Word file may hold up to 5,000 questions; use Excel for more.",
    "Chapter, Topic and Subtopic must already exist for the subject. You will be shown any name that does not match, with suggestions.",
    "Nothing is added to the Question Bank until you check the preview and click Upload.",
]

EXCEL_PICTURE_STEPS = [
    "Click the cell in the right image column (for example Question Image) on the question's row.",
    "Excel 365: Insert > Pictures > Place in Cell, and choose the picture. It now sits inside that cell.",
    "Other Excel versions: Insert > Pictures, then drag the picture so its top-left corner is inside that cell.",
    "Use one picture per cell. Make the row taller if you want to see the picture; its size does not matter.",
    "Leave the matching text cell empty when an option is a picture.",
]

WORD_PICTURE_STEPS = [
    "Click inside the right table cell (for example Option B Image) on the question's row.",
    "Insert > Pictures > This Device, and choose the picture. It is placed inside that cell.",
    "Keep only the picture in that cell: no text next to it, and one picture per cell.",
    "Type your questions in the FIRST table (Your questions). The examples table below it is not imported.",
]

EXCEL_NOTE = "Put your questions on the Questions sheet. The Examples and Instructions sheets are never imported."

# Picture examples are drawn by build.py; here each one is named by what it shows. One list, every type, in one sheet.
EXAMPLES = [
    {   # text only
        "kind": "MCQ, text only",
        "Chapter": "Laws of Motion", "Topic": "Force", "Subtopic": "", "Difficulty": "Easy", "Question Type": "MCQ",
        "Question Text": "What is the SI unit of force?",
        "Option A Text": "Newton", "Option B Text": "Joule", "Option C Text": "Watt", "Option D Text": "Pascal", "Correct Answer": "A",
    },
    {   # text with a supporting picture
        "kind": "MCQ, text with a picture",
        "Chapter": "Laws of Motion", "Topic": "Force", "Subtopic": "", "Difficulty": "Medium", "Question Type": "MCQ",
        "Question Text": "The block rests on the inclined plane shown. Which force acts at right angles to the surface?",
        "Question Image": "incline",
        "Option A Text": "Normal force", "Option B Text": "Friction", "Option C Text": "Weight", "Option D Text": "Tension",
        "Correct Answer": "A",
    },
    {   # the complete question (with its options) is one picture
        "kind": "MCQ, picture only",
        "Chapter": "Laws of Motion", "Topic": "", "Subtopic": "", "Difficulty": "Hard", "Question Type": "MCQ",
        "Question Text": "", "Question Image": "whole_question",
        "Option A Text": "A", "Option B Text": "B", "Option C Text": "C", "Option D Text": "D", "Correct Answer": "C",
    },
    {   # text question whose options are pictures
        "kind": "MCQ, options as pictures",
        "Chapter": "Laws of Motion", "Topic": "Force", "Subtopic": "", "Difficulty": "Medium", "Question Type": "MCQ",
        "Question Text": "Which velocity-time graph shows a body moving with constant velocity?",
        "Option A Image": "graph_flat", "Option B Image": "graph_rising", "Option C Image": "graph_falling", "Option D Image": "graph_curve",
        "Correct Answer": "A",
    },
    {
        "kind": "MSQ, two correct options",
        "Chapter": "Laws of Motion", "Topic": "Force", "Difficulty": "Medium", "Question Type": "MSQ",
        "Question Text": "Which of these are vector quantities?", "Option A Text": "Force", "Option B Text": "Mass",
        "Option C Text": "Velocity", "Option D Text": "Time", "Correct Answer": "A, C",
    },
    {
        "kind": "MSQ, three options",
        "Chapter": "Laws of Motion", "Topic": "", "Difficulty": "Hard", "Question Type": "MSQ",
        "Question Text": "Which of these are SI base units?", "Option A Text": "metre", "Option B Text": "newton",
        "Option C Text": "kilogram", "Correct Answer": "A, C",
    },
    {
        "kind": "NAT, exact whole number",
        "Chapter": "Laws of Motion", "Topic": "Force", "Difficulty": "Easy", "Question Type": "NAT",
        "Question Text": "A 2 kg body is pushed with a net force of 10 N. What is its acceleration in m/s²?", "Correct Answer": "5",
        "Tolerance Type": "Exact", "Answer Format": "Integer", "Unit": "m/s²",
    },
    {
        "kind": "NAT, plus-minus",
        "Chapter": "Laws of Motion", "Topic": "", "Difficulty": "Medium", "Question Type": "NAT",
        "Question Text": "What is the acceleration due to gravity at the Earth's surface, in m/s²?", "Correct Answer": "9.8",
        "Tolerance Type": "Plus-minus", "Tolerance": "0.05", "Answer Format": "Decimal", "Unit": "m/s²",
    },
]
