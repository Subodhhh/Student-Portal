"""COPY loader. One table at a time, committed every `chunk` rows. A restart continues from each table's own committed row count: the
rows are deterministic, so the generator skips what is already there and memory stays flat. An identity file ties the target to one
dataset, so a resume can never mix two."""
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path

from .tables import PLAN
from .world import SUBJECT_CODES, SUBJECT_NAMES, World, scaled, STAGE2

CHUNK = 50_000


def build_world(fraction: float, anchor: datetime) -> World:
    return World(scaled(STAGE2, fraction), anchor)


def identity(world: World) -> dict:
    c = world.config
    return {"stage": c["stage"], "seed": c["seed"], "providers": c["providers"], "students": c["students"], "anchor": world.anchor.isoformat()}


def fingerprint(world: World, exams: int = 200) -> str:
    """Digest of what the generator produces here. Equal on two machines means equal rows, so a load can run on either."""
    h = hashlib.sha256()
    for row in world.providers + world.branches + world.memberships + world.papers:
        h.update(repr(row).encode())
    for e in islice(world.exams(), exams):
        h.update(repr(e).encode())
        for student, _enrollment, _batch in world.audience(e):
            h.update(repr(world.outcome(e, student)).encode())
    return h.hexdigest()[:16]


def prepare_subjects(conn, world: World) -> None:
    """The 8 subjects: the target's own rows when present, created when the schema is empty."""
    found = dict(conn.execute("select code, subject_id::text from subjects where code = any(%s)", (list(SUBJECT_CODES),)).fetchall())
    for code in SUBJECT_CODES:
        if code not in found:
            conn.execute("insert into subjects (subject_id, code, name, status) values (%s, %s, %s, 'ACTIVE')",
                         (world.subject_ids[code], code, SUBJECT_NAMES[code]))
            found[code] = world.subject_ids[code]
    conn.commit()
    world.subject_ids = found


def check_identity(state_file: Path, world: World, conns: dict) -> dict:
    wanted, printed = identity(world), fingerprint(world)
    if state_file.exists():
        state = json.loads(state_file.read_text())
        if state["identity"] != wanted:
            raise SystemExit(f"{state_file} belongs to another dataset: {state['identity']}")
        if state.get("fingerprint", printed) != printed:
            raise SystemExit("this machine generates different rows than the ones already loaded: nothing was written")
        return state
    busy = [f"{db}.{t}" for db, t, _c, _g in PLAN if conns[db].execute(f'select exists (select 1 from "{t}")').fetchone()[0]]
    if busy:
        raise SystemExit("target tables already hold rows and no state file matches them: " + ", ".join(busy[:8]))
    return {"identity": wanted, "fingerprint": printed, "tables": {}}


def save(state_file: Path, state: dict) -> None:
    tmp = state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    os.replace(tmp, state_file)


def load(conns: dict, world: World, state_file: Path, *, chunk: int = CHUNK, report=print, stop_after: str | None = None) -> dict:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state = check_identity(state_file, world, conns)
    prepare_subjects(conns["institute"], world)
    save(state_file, state)
    for db, table, columns, make in PLAN:
        key = f"{db}.{table}"
        conn = conns[db]
        have = conn.execute(f'select count(*) from "{table}"').fetchone()[0]
        conn.commit()
        entry = state["tables"].get(key, {})
        if entry.get("done"):
            continue
        rows, started, loaded = islice(make(world), have, None), time.monotonic(), have
        sql = f'COPY "{table}" ({", ".join(chr(34) + c + chr(34) for c in columns)}) FROM STDIN'
        while True:
            n = 0
            with conn.cursor() as cur, cur.copy(sql) as copy:
                for row in islice(rows, chunk):
                    copy.write_row(row)
                    n += 1
            conn.commit()
            loaded += n
            if n < chunk:
                break
        seconds = time.monotonic() - started + entry.get("seconds", 0)
        state["tables"][key] = {"rows": loaded, "seconds": round(seconds, 2), "done": True}
        save(state_file, state)
        report(f"{key}: {loaded} rows in {seconds:.1f} s")
        if stop_after == key:
            return state
    conn = conns["student"]
    top = world.config["student_base"] + world.student_count
    conn.execute("update student_id_allocator set last_value = greatest(last_value, %s) where name = 'STU'", (top,))   # never reissue an id
    conn.commit()
    state["finished_at"] = datetime.now(timezone.utc).isoformat()
    save(state_file, state)
    return state
