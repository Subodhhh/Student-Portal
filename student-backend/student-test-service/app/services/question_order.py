"""Question order of a sectioned test (a unified Global Test): sections in the configured order, and inside each section an order of
its own for every student that never changes for that student (sha256 of student, test and question). One attempt per student and
test, so it is stable across reloads, devices, subject switches and resumes, and two students sharing a paper get different orders.
Tests without sections keep their existing order. The same function is in the attempt and result services."""
import hashlib


def section_order(rows, sections, student_id: str, test_id, subject_of, id_of):
    if not sections:
        return list(rows)
    position = {s["subject"]: s["position"] for s in sections}
    return sorted(rows, key=lambda r: (position.get(subject_of(r), 10 ** 6),
                                       hashlib.sha256(f"{student_id}:{test_id}:{id_of(r)}".encode()).hexdigest()))
