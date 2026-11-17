"""Runs one disposable E2E driver (tests/e2e/pipeline_disposable.sh <driver>) as the main program WITHOUT making it the importable
__main__ module.

The paper service reads uploads in an isolated process from a multiprocessing forkserver (Workstream S). Every such process first
re-imports the parent's __main__ module from its file; a driver written as a plain script (set-up at module level) would then run its
whole set-up again inside the extraction process (duplicate rows, a crashed extraction). Executing the driver's code in its own
namespace keeps __main__ = this launcher, whose import is harmless (guarded below), so extraction stays isolated with every limit
unchanged and no driver has to be restructured.

    python tests/e2e/run_driver.py tests/e2e/pipeline_disposable.py [args...]
"""
import sys
from pathlib import Path


def main() -> None:
    path = Path(sys.argv[1]).resolve()
    sys.argv = [str(path), *sys.argv[2:]]
    code = compile(path.read_text(), str(path), "exec")
    exec(code, {"__name__": "__main__", "__file__": str(path), "__builtins__": __builtins__})  # noqa: S102 - our own driver file


if __name__ == "__main__":
    main()
