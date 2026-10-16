"""Private file storage. Local-disk implementation behind a tiny interface so an
object-storage provider (private bucket + short-lived signed URLs) can replace it
once selected. Keys are server-generated; user-supplied names are never used as paths."""
import hashlib
import secrets
from pathlib import Path

from institute_common.config import settings


class LocalFileStore:
    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or settings.file_store_dir).resolve()

    def save(self, data: bytes) -> tuple[str, str]:
        key = secrets.token_hex(16)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.root / key
        path.write_bytes(data)
        path.chmod(0o600)
        return key, hashlib.sha256(data).hexdigest()

    def read(self, key: str) -> bytes:
        path = (self.root / key).resolve()
        if path.parent != self.root:  # defence in depth against traversal
            raise FileNotFoundError(key)
        return path.read_bytes()


_store: LocalFileStore | None = None


def get_store() -> LocalFileStore:
    global _store
    if _store is None:
        _store = LocalFileStore()
    return _store


def set_store(store: LocalFileStore | None) -> None:
    global _store
    _store = store
