"""The taxonomy suggestion engine (inst_paper/services/taxonomy_match.py) against the spelling variants in
contracts/taxonomy/match_cases.json: what is pre-selected (decision A1: still confirmed), what is offered, and what is not."""
import json
from pathlib import Path

import pytest

from inst_paper.services.taxonomy_match import match_key, preselect, rank

CASES = json.loads((Path(__file__).resolve().parents[3] / "contracts" / "taxonomy" / "match_cases.json").read_text())
NAMES = [(n, n) for n in CASES["records"]]


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["typed"])
def test_spelling_variants(case):
    ranked = rank(case["typed"], NAMES)
    offered = [item for item, _score, _name in ranked]
    if case.get("none"):
        assert offered == []
    if "preselect" in case:
        assert preselect(ranked) == case["preselect"], ranked
    for wanted in case.get("offers", []):
        assert wanted in offered, ranked
    if "offers" in case:
        assert preselect(ranked) is None or len(case["offers"]) == 1, ranked          # several candidates: the person chooses


def test_match_keys():
    assert match_key("Newton's 1st Law of Motion") == match_key("Newtons First Law of Motion") == "newton first law motion"
    assert match_key("  Work,  Energy & Power ") == "work energy power"
    assert match_key("") == match_key(None) == ""


def test_an_alias_counts_like_the_name_and_each_record_is_offered_once():
    ranked = rank("Newtons1st Law", [("A", "Newton's Laws of Motion"), ("A", "Newton's First Law"), ("B", "Kinematics")])
    assert [r[0] for r in ranked] == ["A"] and ranked[0][2] == "Newton's First Law"
