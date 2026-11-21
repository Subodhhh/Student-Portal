"""Deterministic question papers in the application's CSV import format (paper service: services/extraction.py):
question, option_a..option_d, correct (A-D), marks, negative_marks, explanation, topic. Every stem is unique within its subject, the
four options are always distinct, marks are whole numbers (4 / 1, valid under both marking modes). The same paper key always yields
byte-identical CSV, so a repeated upload is recognised by the paper service as the identical paper it already has."""
import csv
import io
import random

HEADER = ["question", "option_a", "option_b", "option_c", "option_d", "correct", "marks", "negative_marks", "explanation", "topic"]
TOPICS = {
    "SUB001": ["Mole Concept", "Atomic Structure", "Chemical Bonding", "Thermodynamics", "Equilibrium"],
    "SUB002": ["Cell Biology", "Genetics", "Plant Physiology", "Human Physiology", "Ecology"],
    "SUB003": ["Algebra", "Calculus", "Coordinate Geometry", "Trigonometry", "Probability"],
    "SUB004": ["Kinematics", "Laws of Motion", "Work and Energy", "Gravitation", "Electrostatics"],
    "SUB005": ["Current Electricity", "Magnetism", "Optics", "Modern Physics", "Waves"],
    "SUB006": ["Organic Reactions", "Coordination Compounds", "Electrochemistry", "Solutions", "Kinetics"],
    "SUB007": ["Reproduction", "Biotechnology", "Evolution", "Molecular Biology", "Human Health"],
    "SUB008": ["Matrices", "Vectors", "Differential Equations", "Integrals", "3D Geometry"],
}


def paper_csv(marker: str, subject: str, first_question: int, count: int, seed: int) -> bytes:
    rng = random.Random(f"{seed}:{subject}:{first_question}")
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(HEADER)
    for q in range(first_question, first_question + count):
        a, b = rng.randint(2, 97), rng.randint(2, 97)
        right = a * b
        wrong = sorted({right + d for d in (-2 * a, -b, b, 2 * a, a + b, -(a + b))} - {right})[:3]
        options = [right, *wrong]
        rng.shuffle(options)
        correct = "ABCD"[options.index(right)]
        topic = TOPICS[subject][q % len(TOPICS[subject])]
        stem = f"{marker} {subject} Q{q:06d} ({topic}): what is the product of {a} and {b}?"
        w.writerow([stem, *map(str, options), correct, "4", "1", f"{a} x {b} = {right}", topic])
    return out.getvalue().encode("utf-8")
