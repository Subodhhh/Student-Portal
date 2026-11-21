"""Resume journal: the server ID of every natural key the loader has completed. It holds IDs only, never secrets.

Protection:
  * an exclusive lock (journal.json.lock, flock): a second loader process on the same journal is refused;
  * permissions 0600 (journal, lock, backups) in a 0700 directory; atomic writes (temp file + rename);
  * a timestamped backup of the previous journal every time it is opened;
  * an identity (stage, seed, full-plan fingerprint, BOTH pinned Neon branch IDs) that must match exactly, so a journal is never
    applied to another plan, paper size or branch.
The journal is an accelerator, not the source of truth: the loader verifies its IDs against the server at start-up (a reset branch
is detected and the journal refused) and checks the server before every create whose duplicates the API cannot reject."""
import fcntl
import json
import os
import shutil
import threading
import time
from pathlib import Path


class JournalError(SystemExit):
    pass


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, 0o600)


class Journal:
    def __init__(self, path: Path, identity: dict, *, backup: bool = True):
        """identity: {"stage", "seed", "plan", "branch_ids": {"neon-student": ..., "neon-institute": ...}}"""
        self.path, self.lock = Path(path), threading.Lock()
        self.identity = identity
        _private_dir(self.path.parent)
        lock_path = self.path.with_name(self.path.name + ".lock")
        self._lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self._lock_fd)
            raise JournalError(f"journal {self.path} is in use by another loader process: refusing to run twice") from None
        self.data = {"identity": identity, "done": {}, "failed": {}, "uncertain": {}, "phases": {}, "canary_validated": None, "runs": []}
        if self.path.exists():
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if loaded.get("identity") != identity:
                self.close()
                raise JournalError(f"journal {self.path} belongs to {loaded.get('identity')}, not {identity}: refusing to mix runs "
                                   "(another plan, paper size or branch); move it aside to start a new journal")
            self.data = {**self.data, **loaded}
            if backup:
                stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
                dest = self.path.with_name(f"{self.path.name}.bak-{stamp}")
                shutil.copyfile(self.path, dest)
                os.chmod(dest, 0o600)
        self._dirty, self._last = 0, time.monotonic()
        self._flush()

    # ---- items
    def get(self, kind: str, key: str):
        with self.lock:
            return self.data["done"].get(kind, {}).get(key)

    def put(self, kind: str, key: str, value) -> None:
        with self.lock:
            self.data["done"].setdefault(kind, {})[key] = value
            self.data["failed"].get(kind, {}).pop(key, None)
            self.data["uncertain"].get(kind, {}).pop(key, None)
            self._dirty += 1
            if self._dirty >= 25 or time.monotonic() - self._last > 5:
                self._flush()

    def fail(self, kind: str, key: str, error: str, *, uncertain: bool = False) -> None:
        """uncertain = the server may have applied the request; the next run looks on the server before creating again."""
        with self.lock:
            self.data["failed"].setdefault(kind, {})[key] = error
            if uncertain:
                self.data["uncertain"].setdefault(kind, {})[key] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._flush()

    def mark_uncertain(self, kind: str, key: str) -> None:
        """The create of (kind, key) may have been applied: the next attempt must look on the server first."""
        with self.lock:
            self.data["uncertain"].setdefault(kind, {})[key] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._flush()

    def is_uncertain(self, kind: str, key: str) -> bool:
        with self.lock:
            return key in self.data["uncertain"].get(kind, {})

    def ids(self, kind: str) -> dict:
        with self.lock:
            return dict(self.data["done"].get(kind, {}))

    def count(self, kind: str) -> int:
        with self.lock:
            return len(self.data["done"].get(kind, {}))

    def failures(self) -> dict:
        with self.lock:
            return {k: dict(v) for k, v in self.data["failed"].items() if v}

    # ---- phases and canary gate
    def complete_phase(self, scope: str, phase: str, items: int) -> None:
        with self.lock:
            self.data["phases"].setdefault(scope, {})[phase] = {"items": items, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            self._flush()

    def phase_complete(self, scope: str, phase: str) -> bool:
        with self.lock:
            return phase in self.data["phases"].get(scope, {})

    def record_canary_validation(self, result: dict) -> bool:
        """Only a fully passing validation opens the gate for the full run."""
        with self.lock:
            ok = bool(result.get("ok"))
            self.data["canary_validated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) if ok else None
            self._flush()
            return ok

    def canary_validated(self) -> bool:
        with self.lock:
            return bool(self.data.get("canary_validated"))

    def note_run(self, info: dict) -> None:
        with self.lock:
            self.data["runs"].append(info)
            self._flush()

    # ---- persistence
    def flush(self) -> None:
        with self.lock:
            self._flush()

    def _flush(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        _write_private(tmp, json.dumps(self.data, indent=1, sort_keys=True))
        os.replace(tmp, self.path)
        self._dirty, self._last = 0, time.monotonic()

    def close(self) -> None:
        if getattr(self, "_lock_fd", None) is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(self._lock_fd)
                self._lock_fd = None
