"""Writes k6/data/users.json: the same 1,000 students every run, all with at least one attempt whose answers were fully loaded. Pure function of the
Stage 2 definition and the 36,550,000 loaded answer rows: no database, no secrets. Run from tests/load."""
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

LOAD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOAD))
from bulk.tables import _attempted  # noqa: E402
from bulk.world import STAGE2, World, scaled  # noqa: E402

LOADED, COUNT = 36_550_000, 1000
world = World(scaled(STAGE2, 1.0), datetime(2026, 11, 4, tzinfo=timezone.utc))
complete, answers = {}, 0
for _exam, student, outcome in _attempted(world):
    answers += len(outcome[5])
    if answers > LOADED:
        break
    complete[student] = complete.get(student, 0) + 1
assert len(complete) >= COUNT
picked = random.Random(STAGE2["seed"]).sample(sorted(complete), COUNT)
out = [{"id": world.student_id(s), "complete_attempts": complete[s]} for s in picked]
(LOAD / "k6/data").mkdir(exist_ok=True)
(LOAD / "k6/data/users.json").write_text(json.dumps(out, indent=0) + "\n")
print(f"{COUNT} of {len(complete)} students with complete attempts")
