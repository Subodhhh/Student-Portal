"""Photo papers: how OCR lines become questions (services/photo_drafts.py), without an OCR engine. Covers the reported failure (a
rotated question photo + a separate answer-key photo -> "no numbered questions" and "unreadable answer-key lines"), numbering and
option styles seen on photographed papers, answer keys read from images, and the rule that nothing is ever guessed."""
from inst_paper.services import photo_drafts as pd


def lines(*texts, conf=96.0):
    return [[t, conf, conf] for t in texts]


def build(pages, key=None, min_conf=0.85):
    return pd.build_questions(pages, key, min_conf, 200)


REGRESSION_PAGE = ["1. For a conservative force, the curl of the force is:", "A) Zero", "B) Negative", "C) 1", "D) None"]


def test_reported_example_is_extracted_with_its_answer_from_the_key_photo():
    qs, issues = build([lines(*REGRESSION_PAGE)], lines("Answer Key", "1. A"))
    assert issues == []
    q = qs[0]
    assert (q["number"], q["question"], q["option_a"], q["option_b"], q["option_c"], q["option_d"], q["correct"]) == (
        1, "For a conservative force, the curl of the force is:", "Zero", "Negative", "1", "None", "A")
    assert not q["needs_review"]
    assert q["answer_source"] == "1. A" and [s["text"] for s in q["source"]] == REGRESSION_PAGE      # traceable to what was read


def test_reported_example_variants_as_ocr_reads_them():
    # the OCR glyph "©" for C, options on one line, and the key written "1-A"
    qs, issues = build([lines("1. For a conservative force, the curl of the force is:", "A) Zero  B) Negative  ©) 1  D) None")], lines("1-A"))
    assert issues == [] and [qs[0][f"option_{x}"] for x in "abcd"] == ["Zero", "Negative", "1", "None"] and qs[0]["correct"] == "A"


def test_numbering_styles_of_photographed_papers():
    for first in ("(1) What is the SI unit of force?", "Q1 What is the SI unit of force?", "Q.1 What is the SI unit of force?",
                  "Q1. What is the SI unit of force?", "1] What is the SI unit of force?", "Question 1: What is the SI unit of force?",
                  "1) What is the SI unit of force?", "1. What is the SI unit of force?"):
        qs, issues = build([lines(first, "A) Newton", "B) Joule", "C) Watt", "D) Pascal")], lines("1. A"))
        assert [(q["number"], q["question"]) for q in qs] == [(1, "What is the SI unit of force?")], first
        assert issues == [], first


def test_option_layouts_and_markers():
    for opts in (["(a) Newton", "(b) Joule", "(c) Watt", "(d) Pascal"], ["A. Newton", "B. Joule", "C. Watt", "D. Pascal"],
                 ["A) Newton    B) Joule", "C) Watt    D) Pascal"], ["(A) Newton (B) Joule (C) Watt (D) Pascal"]):
        qs, _ = build([lines("1. What is the SI unit of force?", *opts)], lines("1. A"))
        assert [qs[0][f"option_{x}"] for x in "abcd"] == ["Newton", "Joule", "Watt", "Pascal"], opts


def test_a_letter_inside_text_never_splits_an_option():
    qs, _ = build([lines("1. Pick the true statement", "A) a) is wrong", "B) both a) and b) hold", "C) none", "D) all")], lines("1. B"))
    assert qs[0]["option_b"] == "both a) and b) hold"


def test_option_e_is_reported_not_merged_into_d():
    qs, _ = build([lines("1. Which is a vector?", "A) Mass", "B) Speed", "C) Time", "D) Energy", "E) Velocity")], lines("1. A"))
    q = qs[0]
    assert q["option_d"] == "Energy" and q["needs_review"] and any("option E" in i and "Velocity" in i for i in q["issues"])


def test_a_line_starting_with_e_dot_in_the_stem_is_not_option_e():
    qs, _ = build([lines("1. Which bacterium is common in the gut?", "E. coli is one; name the genus", "A) Escherichia", "B) Bacillus",
                         "C) Vibrio", "D) Listeria")], lines("1. A"))
    assert qs[0]["question"].endswith("E. coli is one; name the genus") and not any("option E" in i for i in qs[0]["issues"])


def test_answer_key_formats_from_photos():
    page = lines("1. q one", "A) a", "B) b", "C) c", "D) d", "2. q two", "A) a", "B) b", "C) c", "D) d", "3. q three", "A) a", "B) b", "C) c", "D) d")
    for key in (["Answer Key", "Q1: A", "Q2: C", "Q3: B"], ["1-A  2-C  3-B"], ["1) (A)", "2) (C)", "3) (B)"], ["1 A", "2 C", "3 B"],
                ["1.A, 2.C, 3.B"], ["3. B", "1. A", "2. C"]):                        # the last: out of order, matched by number
        qs, issues = build([page], lines(*key))
        assert [q["correct"] for q in qs] == ["A", "C", "B"], key
        assert issues == [], (key, issues)


def test_missing_conflicting_extra_and_unsupported_key_entries_are_flagged_never_guessed():
    page = lines("1. q one", "A) a", "B) b", "C) c", "D) d", "2. q two", "A) a", "B) b", "C) c", "D) d", "3. q three", "A) a", "B) b", "C) c", "D) d")
    qs, issues = build([page], lines("1. A", "1. B", "3. E", "9. C"))
    q = {x["number"]: x for x in qs}
    assert q[1]["correct"] == "" and any("two answers for question 1" in i for i in issues)
    assert q[2]["correct"] == "" and any("no answer" in i for i in q[2]["issues"])
    assert q[3]["correct"] == "" and any("gives E for question 3" in i for i in issues)
    assert any("not found in the paper: 9" in i for i in issues)
    assert all(x["needs_review"] for x in qs)


def test_duplicate_question_numbers_are_flagged():
    page = lines("1. What is the SI unit of force?", "A) Newton", "B) Joule", "C) Watt", "D) Pascal", "2. Which gas do plants absorb?",
                 "A) Oxygen", "B) Carbon dioxide", "C) Nitrogen", "D) Helium")
    qs, _ = build([page, lines("2. Which gas do plants absorb?", "A) Oxygen", "B) Carbon dioxide", "C) Nitrogen", "D) Helium")],
                  lines("1. A", "2. B"))
    assert any("appears again" in i for q in qs for i in q["issues"])


def test_uncertain_ocr_is_flagged_for_review():
    page = [["1. What is the SI unit of force?", 96, 96], ["A) Newton", 41, 55], ["B) Joule", 96, 96], ["C) Watt", 96, 96], ["D) Pascal", 96, 96]]
    qs, _ = build([page], lines("1. A"))
    assert qs[0]["needs_review"] and qs[0]["confidence"] < 0.85
    qs, issues = build([lines(*REGRESSION_PAGE)], [["1. A", 60, 70]])                 # a key line read with low confidence
    assert qs[0]["correct"] == "A" and qs[0]["needs_review"] and any("unclear answer-key line" in i for i in qs[0]["issues"])


def test_unreadable_key_text_gives_no_answer_and_says_so():
    qs, issues = build([lines(*REGRESSION_PAGE)], lines("Answer Key", "i ae"))
    assert qs[0]["correct"] == "" and qs[0]["needs_review"] and any("no answer" in i for i in qs[0]["issues"])


def test_garbled_page_gives_no_invented_questions():
    qs, issues = build([lines("ee ~ |", "@ yr", "ah")], lines("1. A"))
    assert qs == [] and any("No numbered questions" in i for i in issues)


# ---- questions the first rebuild lost (90-case evaluation): the OCR text exactly as read, from clean-08 and clean-01
def _q(n, stem, mark="{n}.", opts=("Dioptre", "Lux", "Candela", "Metre"), om="{L})"):
    return [mark.format(n=n) + " " + stem] + [om.format(L=L) + " " + o for L, o in zip("ABCD", opts, strict=True)]


def test_question_numbers_with_a_misread_dot_are_kept():          # clean-08: "4," and "5" without its dot
    page = _q(1, "Sound cannot travel through:") + _q(2, "Momentum is the product of mass and:") + _q(3, "Which planet is red?") + \
        ["4, The power of a lens is measured in:", "A) Dioptre", "B) Lux", "C) Candela", "D) Metre",
         "5 Which organelle is called the powerhouse of the cell?", "A) Nucleus", "B) Ribosome", "C) Mitochondrion", "D) Golgi body"]
    qs, issues = build([lines(*page)], lines("1. A", "2. B", "3. C", "4. A", "5. C"))
    assert [q["number"] for q in qs] == [1, 2, 3, 4, 5], issues
    assert qs[3]["question"] == "The power of a lens is measured in:" and qs[4]["option_c"] == "Mitochondrion"


def test_a_question_number_read_as_a_letter_is_recovered_but_flagged():   # clean-01: "4)" read as "A)"
    page = _q(1, "Which acid is in the stomach?", "{n})", om="({L})") + _q(2, "Which metal is liquid?", "{n})", om="({L})") + \
        _q(3, "What is the SI unit of force?", "{n})", om="({L})") + \
        ["A) Which vitamin is made in skin exposed to sunlight?", "(A) Vitamin A", "(B) Vitamin B12", "(C) Vitamin C", "(D) Vitamin D"] + \
        _q(5, "Which of these is a noble gas?", "{n})", om="({L})")
    qs, issues = build([lines(*page)], lines("1. A", "2. B", "3. A", "4. D", "5. B"))
    q = {x["number"]: x for x in qs}
    assert sorted(q) == [1, 2, 3, 4, 5], issues
    assert q[3]["option_d"] == "Metre" and q[4]["question"] == "Which vitamin is made in skin exposed to sunlight?"
    assert q[4]["needs_review"] and any("read as" in i for i in q[4]["issues"]) and not q[5]["needs_review"]


def test_a_number_inside_a_sentence_or_an_unexpected_number_never_starts_a_question():
    page = ["1. How many legs does an insect have?", "6 legs are typical of insects, said the text", "A) 4", "B) 6", "C) 8", "D) 10",
            "2. Pick the prime", "A) 4", "B) 6", "C) 7", "D) 9"]
    qs, _ = build([lines(*page)], lines("1. B", "2. C"))
    assert [q["number"] for q in qs] == [1, 2] and qs[0]["question"].endswith("said the text")
    page = _q(1, "First question") + ["A) Not a question, only a long option-looking line", "B) x", "C) y", "D) z"]
    qs, _ = build([lines(*page)], lines("1. A"))
    assert [q["number"] for q in qs] == [1]                             # no option A follows: it is not taken for question 2


# ---- mathematical notation: never accepted on OCR confidence alone, never rewritten
MATH_MISREADS = [            # what Tesseract produced, with high confidence, for printed maths in the 90-case evaluation
    ("The derivative of x° with respect to x is:", ["x?", "3x?", "3x", "x3/3"]),
    ("If sin 9 = 1/2 and @ is acute, then 0 =", ["30°", "45°", "60°", "90°"]),
    ("If x2 - 5x + 6 = 0, the roots are:", ["2 and 3", "1 and 6", "-2 and -3", "5 and 6"]),
    ("The value of √144 is:", ["10", "11", "12", "14"]),
    ("The formula of sulphuric acid is:", ["H₂SO₄", "HCl", "HNO₃", "H₂CO₃"]),
]


def test_questions_with_mathematical_notation_always_go_to_review_unchanged():
    for stem, options in MATH_MISREADS:
        qs, _ = build([lines(f"1. {stem}", *[f"{L}) {o}" for L, o in zip("ABCD", options, strict=True)])], lines("1. A"))
        q = qs[0]
        assert q["needs_review"] and any("mathematical notation" in i for i in q["issues"]), stem
        assert q["question"] == stem and [q[f"option_{x}"] for x in "abcd"] == options, stem          # nothing normalised


def test_a_super_or_subscript_seen_in_the_image_flags_the_question_even_when_the_text_looks_ordinary():
    page = [["1. 25 equals:", 95, 96, ["script"]], ["A) 10", 96, 96, []], ["B) 16", 96, 96, []], ["C) 25", 96, 96, []], ["D) 32", 96, 96, []]]
    qs, _ = build([page], lines("1. D"))
    assert qs[0]["needs_review"] and any("mathematical notation" in i for i in qs[0]["issues"]) and qs[0]["question"] == "25 equals:"


def test_ordinary_text_is_not_mistaken_for_mathematics():
    for stem, options in (("What is the SI unit of force?", ["Newton", "Joule", "Watt", "Pascal"]),
                          ("Ohm's law relates voltage, current and:", ["Power", "Resistance", "Charge", "Energy"]),
                          ("Which vitamin is made in skin exposed to sunlight?", ["Vitamin A", "Vitamin B12", "Vitamin C", "Vitamin D"]),
                          ("The speed of light in vacuum is closest to:", ["3 x 10^8 m/s", "3 x 10^6 m/s", "3 x 10^5 km/s", "300 m/s"]),
                          ("The pH of pure water at 25 C is:", ["5", "6", "7", "8"]), ("Which is a noble gas?", ["Argon", "Oxygen", "Neon B", "Iron"])):
        qs, _ = build([lines(f"1. {stem}", *[f"{L}) {o}" for L, o in zip("ABCD", options, strict=True)])], lines("1. A"))
        assert not qs[0]["needs_review"], (stem, qs[0]["issues"])


def test_hocr_character_boxes_reveal_superscripts_and_subscripts():
    from inst_paper.services.ocr_tesseract import _hocr_lines

    def hocr(chars_by_word):
        words = "".join(
            "<span class='ocrx_word' title='bbox 0 0 1 1; x_wconf 95'>" +
            "".join(f"<span class='ocrx_cinfo' title='x_bboxes {x0} {y0} {x1} {y1}; x_conf 99'>{c}</span>" for c, (x0, y0, x1, y1) in w) + "</span>"
            for w in chars_by_word)
        return (f"<html xmlns='http://www.w3.org/1999/xhtml'><body><span class='ocr_line' title='bbox 0 0 9 9'>{words}</span>"
                "</body></html>").encode()

    plain = [[("3", (0, 38, 19, 68)), (".", (20, 63, 25, 68))], [("W", (40, 38, 70, 68)), ("h", (71, 38, 90, 68)), ("y", (91, 46, 110, 76)), ("?", (111, 38, 125, 68))]]
    power = [[("2", (74, 38, 93, 68)), ("5", (97, 39, 111, 55))], [("e", (127, 46, 147, 68)), ("q", (151, 46, 173, 76)), ("l", (225, 38, 236, 68))]]
    sub = [[("H", (370, 39, 401, 68)), ("2", (404, 51, 416, 68)), ("O", (421, 38, 450, 68))], [("1", (460, 38, 475, 68))]]
    apostrophe = [[("O", (0, 38, 30, 68)), ("h", (31, 38, 50, 68)), ("m", (51, 46, 80, 68)), ("'", (81, 38, 85, 48)), ("s", (86, 46, 100, 68))], [("1", (110, 38, 125, 68))]]
    assert _hocr_lines(hocr(plain)) == [["3. Why?", 95.0, 95.0, [], [0.0, 0.0, 9.0, 9.0]]]     # text, weakest, mean, flags, line box
    assert _hocr_lines(hocr(power))[0][3] == ["script"] and _hocr_lines(hocr(sub))[0][3] == ["script"]
    assert _hocr_lines(hocr(apostrophe))[0][3] == []


def test_a_misread_question_number_is_repaired_only_with_evidence_and_flagged():
    body = lambda n, s: [f"{n}. {s}", "A) a", "B) b", "C) c", "D) d"]  # noqa: E731
    # "9" between 4 and 6 (clean-08 read "5." as "9."): without the repair, 6 and 7 would be swallowed into "question 9"
    page = body(1, "one") + body(2, "two") + body(3, "three") + body(4, "four") + body(9, "five") + body(6, "six") + body(7, "seven")
    qs, _ = build([lines(*page)], lines(*[f"{n}. A" for n in range(1, 8)]))
    q = {x["number"]: x for x in qs}
    assert sorted(q) == [1, 2, 3, 4, 5, 6, 7] and q[5]["question"] == "five" and q[6]["question"] == "six"
    assert q[5]["needs_review"] and any("read as 9" in i for i in q[5]["issues"]) and not q[6]["needs_review"]
    # the last question: repaired only because the key has 5 and no 9
    qs, _ = build([lines(*body(1, "a1") + body(2, "a2") + body(3, "a3") + body(4, "a4") + body(9, "a5"))], lines(*[f"{n}. B" for n in range(1, 6)]))
    assert [x["number"] for x in qs] == [1, 2, 3, 4, 5] and qs[4]["needs_review"] and qs[4]["correct"] == "B"
    # a genuine gap (question 5 really missing) is reported, not papered over
    qs, issues = build([lines(*body(1, "a1") + body(2, "a2") + body(3, "a3") + body(4, "a4") + body(6, "a6") + body(7, "a7"))],
                       lines(*[f"{n}. B" for n in (1, 2, 3, 4, 6, 7)]))
    assert [x["number"] for x in qs] == [1, 2, 3, 4, 6, 7] and any("jumps from 4 to 6" in i for x in qs for i in x["issues"] + issues)


def test_a_stray_mark_between_options_on_one_line_is_dropped_but_the_line_is_checked():
    qs, _ = build([lines("1. What is the SI unit of force?", "A) Newton 8B) Joule C) Watt D) Pascal")], lines("1. A"))
    q = qs[0]
    assert [q[f"option_{x}"] for x in "abcd"] == ["Newton", "Joule", "Watt", "Pascal"] and q["needs_review"]


def test_a_caret_read_as_an_asterisk_is_flagged():
    qs, _ = build([lines("1. The value of g on Earth is about:", "A) 9.8 m/s*2", "B) 8.9 m/s*2", "C) 6.7 m/s*2", "D) 1.6 m/s*2")], lines("1. A"))
    assert qs[0]["needs_review"] and qs[0]["option_a"] == "9.8 m/s*2"


def test_a_misread_number_in_the_answer_key_is_placed_only_when_unambiguous_and_flagged():
    body = lambda n: [f"{n}. question {n}", "A) a", "B) b", "C) c", "D) d"]  # noqa: E731
    page = lines(*[x for n in range(1, 6) for x in body(n)])
    qs, issues = build([page], lines("1. A", "2. B", "3. C", "4. D", "9. A"))               # rotated-04 / blurred-06: "5" read as "9"
    q = {x["number"]: x for x in qs}
    assert q[5]["correct"] == "A" and q[5]["needs_review"] and any("read as question 9" in i for i in issues)
    assert not any("not found in the paper" in i for i in issues)
    qs, issues = build([page], lines("1. A", "2. B", "9. C", "4. D", "3. A"))               # out of order: not placed, never guessed
    q = {x["number"]: x for x in qs}
    assert q[3]["correct"] == "A" and q[5]["correct"] == "" and any("not found in the paper: 9" in i for i in issues)
    qs, issues = build([page], lines("1. A", "2. B", "3. C", "8. D", "9. A"))               # two strays: nothing placed
    assert {x["number"]: x["correct"] for x in qs}[4] == "" and {x["number"]: x["correct"] for x in qs}[5] == ""


def test_a_key_number_read_twice_is_placed_only_when_its_position_proves_it_and_flagged():
    body = lambda n: [f"{n}. question {n}", "A) a", "B) b", "C) c", "D) d"]  # noqa: E731
    page = lines(*[x for n in range(1, 6) for x in body(n)])
    qs, issues = build([page], lines("1) (D)", "2) (A)", "3) (B)", "4) (C)", "3) (A)"))        # rotated-08: "5" read as "3"
    q = {x["number"]: x for x in qs}
    assert (q[3]["correct"], q[5]["correct"]) == ("B", "A") and q[3]["needs_review"] and q[5]["needs_review"]
    assert any("question 3 twice" in i for i in issues) and not any("two answers for question 3" in i for i in issues)
    qs, issues = build([page], lines("3) (B)", "1) (D)", "2) (A)", "4) (C)", "3) (A)"))        # order does not prove anything
    assert {x["number"]: x["correct"] for x in qs}[5] == "" and any("two answers for question 3" in i for i in issues)


def test_large_dark_regions_at_the_border_are_cleared_but_writing_is_kept():
    from PIL import Image, ImageDraw
    from inst_paper.services.ocr_tesseract import _clear_border_ink
    img = Image.new("L", (1200, 900), 255)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 40, 899), fill=10)                       # a notebook edge down the whole left side
    d.polygon([(1199, 0), (1050, 0), (1199, 700)], fill=30)     # carpet in a corner
    d.rectangle((300, 400, 700, 430), fill=0)                   # writing in the middle
    d.rectangle((1180, 820, 1199, 840), fill=0)                 # a small mark that reaches the edge
    out, cleared = _clear_border_ink(img)
    assert cleared
    assert out.getpixel((20, 450)) == 255 and out.getpixel((1190, 100)) == 255
    assert out.getpixel((500, 415)) == 0 and out.getpixel((1190, 830)) == 0
    assert _clear_border_ink(Image.new("L", (600, 400), 255)) [1] is False
