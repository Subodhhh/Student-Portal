"""Recognition benchmark (tests/recognition_benchmark.json): the engine's measurable promises over Mathematics, Physics, Chemistry and
Biology text. Print the report with:

    cd institute-backend/institute-integration-tests && python -m tests.test_recognition_benchmark

Metrics
  false_acceptance     ambiguous items the engine converted on its own (must be 0: an ambiguous expression is never decided)
  candidate_recall     acceptable readings offered / acceptable readings in the ground truth (must be 1.0)
  complete_sets        ambiguous items whose readings list is complete within the grammar
  exact_conversion     unambiguous items stored exactly as expected
  over_abstention      unambiguous items sent to review although they have one reading (a cost, not an error)
  prose_kept           plain text items left unchanged
"""
import json
import time
from pathlib import Path

from inst_paper.services.recognition import _field

BENCHMARK = Path(__file__).with_name("recognition_benchmark.json")


def _squash(text: str) -> str:
    return text.replace(" ", "")


def run() -> dict:
    items = json.loads(BENCHMARK.read_text())["items"]
    m = {"items": len(items), "ambiguous": 0, "false_acceptance": 0, "acceptable": 0, "offered": 0, "complete_sets": 0,
         "unambiguous": 0, "exact_conversion": 0, "over_abstention": 0, "plain": 0, "prose_kept": 0, "unseen": 0,
         "unseen_recall": [0, 0], "misses": [], "by_subject": {}}
    started = time.perf_counter()
    for item in items:
        text = item.get("prefix", "") + item["text"]
        rec = _field(text, item["subject"])
        subject = item["subject"] or "none"
        m["by_subject"][subject] = m["by_subject"].get(subject, 0) + 1
        if item.get("ambiguous"):
            m["ambiguous"] += 1
            if rec["outcome"] == "CONVERTED":
                m["false_acceptance"] += 1
                m["misses"].append(f"converted on its own: {text}")
            offered = {_squash(s["text"]) for s in rec.get("suggestions", [])}
            for reading in item["acceptable"]:
                m["acceptable"] += 1
                hit = _squash(reading) in offered
                m["offered"] += hit
                if item.get("unseen"):
                    m["unseen_recall"][0] += hit
                    m["unseen_recall"][1] += 1
                if not hit:
                    m["misses"].append(f"reading not offered for {text}: {reading}")
            m["complete_sets"] += all(c["complete"] for c in rec.get("candidate_sets", [])) and bool(rec.get("candidate_sets"))
            m["unseen"] += bool(item.get("unseen"))
        elif item.get("plain"):
            m["plain"] += 1
            kept = rec["outcome"] == "PLAIN_TEXT" and rec["text"] == text
            m["prose_kept"] += kept
            if not kept:
                m["misses"].append(f"prose changed or flagged: {text}")
        else:
            m["unambiguous"] += 1
            if rec["outcome"] == "CONVERTED" and _squash(rec["text"]) == _squash(item["expect"]):
                m["exact_conversion"] += 1
            elif rec["outcome"] == "NEEDS_REVIEW":
                m["over_abstention"] += 1
                m["misses"].append(f"sent to review although it has one reading: {text}")
            else:
                m["misses"].append(f"converted differently: {text} -> {rec['text']}")
    m["seconds"] = round(time.perf_counter() - started, 3)
    m["candidate_recall"] = round(m["offered"] / m["acceptable"], 4) if m["acceptable"] else None
    m["unseen_recall"] = round(m["unseen_recall"][0] / m["unseen_recall"][1], 4) if m["unseen_recall"][1] else None
    return m


def test_benchmark_promises_hold():
    m = run()
    assert m["false_acceptance"] == 0, m["misses"]                 # an ambiguous expression is never decided by the engine
    assert m["candidate_recall"] == 1.0, m["misses"]               # every acceptable reading is offered
    assert m["complete_sets"] == m["ambiguous"]                     # and the list says it is complete
    assert m["prose_kept"] == m["plain"], m["misses"]              # prose (incl. biology terminology) is never touched
    assert m["exact_conversion"] + m["over_abstention"] == m["unambiguous"], m["misses"]   # never a wrong conversion


if __name__ == "__main__":
    print(json.dumps(run(), indent=1, ensure_ascii=False))
