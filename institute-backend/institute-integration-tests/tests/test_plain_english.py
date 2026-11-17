"""Plain English questions and options are imported exactly as typed, in every subject: the notation review never blocks or rewrites
them (pictures carry anything that is not plain text since recognition was discontinued on 2026-11-16)."""
import pytest

from inst_paper.services import recognition

PLAIN = ["Which organelle is known as the powerhouse of the cell?", "Mitochondria", "Golgi apparatus",
         "Who wrote the play 'Romeo and Juliet'?", "William Shakespeare", "60 km/h", "What is 25% of 200?", "1947",
         "Read the passage. I am a student, and I live in a small town.", "I", "a", "Vitamin D", "for example",
         "Which statement is true? (i) Water boils at 100 °C at sea level. (ii) Ice is denser than water.", "Only (i)"]


@pytest.mark.parametrize("domain", [None, "MATHEMATICS", "PHYSICS", "CHEMISTRY", "OTHER"])
@pytest.mark.parametrize("field", ["question", "option_a", "explanation"])
def test_plain_english_is_kept_verbatim_and_never_blocks(domain, field):
    for text in PLAIN:
        r = recognition.field_result(text, domain, field)
        assert (r["outcome"], r["text"]) == ("PLAIN_TEXT", text), text
