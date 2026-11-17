"""Isolated processing of uploaded papers (security workstream S).

Reading an uploaded CSV/XLSX/DOCX/PDF and recognising its notation runs in a separate, short-lived process, so that a crafted file
cannot exhaust the API worker. The child process gets:
  * a wall-clock timeout (the parent kills it),
  * a CPU-time limit (RLIMIT_CPU, enforced by the kernel),
  * an address-space limit (RLIMIT_AS): the memory it already has plus a fixed budget.
Processes come from a forkserver (started once, single-threaded, with the import modules preloaded), so each upload costs a fork,
not a new interpreter, and the server's threads are never forked. Images (OCR) keep running in-process: the OCR backend is
registered at start-up in the server process and is outside this workstream.

The child only receives bytes and returns plain data (no database, no network, no files). Any failure becomes an ordinary,
explicit extraction error; a partial result is never returned.
"""
import multiprocessing as mp
import os
import resource
import threading
from dataclasses import dataclass

_lock = threading.Lock()
_ctx = None


class IsolationError(Exception):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}")
        self.kind, self.detail = kind, detail


@dataclass
class Limits:
    timeout_seconds: float
    memory_mb: int


def _context():
    global _ctx
    with _lock:
        if _ctx is None:
            ctx = mp.get_context("forkserver")
            ctx.set_forkserver_preload(["inst_paper.services.isolation", "inst_paper.services.recognition",
                                        "inst_paper.services.documents"])
            _ctx = ctx
        return _ctx


def _address_space_bytes() -> int:
    """Current virtual memory size of this process (Linux /proc), 0 if unknown."""
    try:
        with open("/proc/self/statm") as fh:
            return int(fh.read().split()[0]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return 0


def _child(conn, fn, args, memory_mb: int, cpu_seconds: int) -> None:
    try:
        budget = _address_space_bytes() + memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (budget, budget))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        conn.send(("ok", fn(*args)))
    except MemoryError:
        conn.send(("memory", ""))
    except RecursionError:
        conn.send(("complexity", ""))
    except BaseException as exc:  # noqa: BLE001 - reported to the parent as an explicit failure, never a partial result
        conn.send(("error", type(exc).__name__))
    finally:
        conn.close()


def run_isolated(fn, *args, limits: Limits):
    """fn(*args) in a separate process within `limits`. Raises IsolationError(kind) with kind timeout | memory | complexity |
    crashed | error. `fn` must be a module-level function and its arguments and result picklable."""
    ctx = _context()
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child, args=(child, fn, args, limits.memory_mb, max(1, int(limits.timeout_seconds) + 1)), daemon=True)
    proc.start()
    child.close()
    try:
        if not parent.poll(limits.timeout_seconds):
            raise IsolationError("timeout", f"{limits.timeout_seconds:g} s")
        try:
            kind, value = parent.recv()
        except (EOFError, OSError):
            raise IsolationError("crashed", f"exit code {proc.exitcode}")
    finally:
        parent.close()
        if proc.is_alive():
            proc.kill()
        proc.join(5)
    if kind != "ok":
        raise IsolationError(kind, value)
    return value


# ---------------------------------------------------------------------------------------------------------------- the work itself

def extract_upload(data: bytes, ext: str, stored_type: str, mapping, key_errors: list[dict], has_key: bool, max_questions: int):
    """Read one uploaded paper (exactly what the upload route did in-process). Returns an ExtractionResult."""
    from inst_paper.services import documents, extraction
    with extraction.answer_key(mapping if has_key and not key_errors else None):
        if key_errors:
            return extraction.ExtractionResult(errors=key_errors)     # an unreadable key rejects the paper before extraction
        if ext == ".csv":
            return extraction.extract_csv(data, max_questions)
        if ext == ".xlsx":
            return extraction.extract_xlsx(data, max_questions)
        if ext == ".zip":
            return extraction.extract_zip_package(data, max_questions)
        if ext == ".docx":
            return documents.extract_docx(data, max_questions)
        if ext == ".pdf":
            return documents.extract_pdf(data, max_questions)
        raise ValueError(f"unsupported paper type {ext!r}")          # the upload route accepts only the types above


def extract_and_review(data: bytes, ext: str, stored_type: str, mapping, key_errors: list[dict], has_key: bool, max_questions: int,
                       domain: str | None):
    """Extraction plus the notation review of every text field, in the isolated process: -> (ExtractionResult, review or None).
    Pictures are only checked and carried through as uploaded; their contents are never read."""
    from inst_paper.services import recognition
    result = extract_upload(data, ext, stored_type, mapping, key_errors, has_key, max_questions)
    review = recognition.review_paper(result.questions, domain) if result.ok else None
    return result, review


FAILURE_MESSAGES = {
    "timeout": "The file took too long to process (the limit is {detail}); it was not imported. Upload a smaller or simpler file.",
    "memory": "The file needs more memory to process than is allowed; it was not imported. Upload a smaller or simpler file.",
    "complexity": "The file's structure is too complex to process safely; it was not imported.",
    "crashed": "The file could not be processed safely; it was not imported.",
    "error": "The file could not be processed safely; it was not imported.",
}
