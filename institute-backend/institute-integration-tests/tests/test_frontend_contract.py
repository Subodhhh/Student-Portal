"""Frontend <-> gateway <-> service contract: every API call written in the Institute frontend must be routable, with its HTTP method,
by the gateway route table (the same table the nginx configuration is generated from) to a service that implements it.

Regression for the live demo errors "Method Not Allowed" (Question Bank: GET /api/v1/questions reached a service that only had POST)
and "No such route" (Papers: GET /api/v1/papers, Import Processing: GET /api/v1/uploads had no gateway location). Those came from a
frontend deployed against older backend images; this test makes the frontend and backend of one revision provably consistent."""
import re
from pathlib import Path

from institute_common.gateway import resolve

from tests.gateway import TABLE

SRC = Path(__file__).resolve().parents[3] / "institute-frontend" / "src"
LITERAL = re.compile(r"""(["'`])(?:\$\{API_URL\})?(/api/v1/[^"'`]*)\1""")
METHOD = re.compile(r"""method:\s*([^,}]+)""")
VERBS = re.compile(r"""["'](GET|POST|PUT|PATCH|DELETE)["']""")
# Action segments chosen at run time; the values are the union declared next to the call (kept in sync by a test below).
DYNAMIC = {("components/staff.tsx", "confirm.kind"): ["deactivate", "reactivate"],   # "remove" uses its own DELETE call
           ("components/globalTests.tsx", "confirm.kind"): ["publish", "withdraw"],
           ("pages/GlobalTestsPage.tsx", "kind"): ["approve", "reject", "withdraw"],
           ("pages/GlobalTestsPage.tsx", "change"): ["publish", "withdraw"]}


def _interpolations(path: str, src: str = "") -> list[str]:
    """Expand `${cond ? "a" : "b"}` into both literals and DYNAMIC action segments into their values; any other `${expr}` is an id."""
    m = re.search(r"\$\{([^{}]*)\}", path)
    if not m:
        return [path]
    options = DYNAMIC.get((src, m.group(1).strip())) or re.findall(r"""["']([A-Za-z0-9_-]+)["']""", m.group(1)) or ["x0"]
    return [p for opt in options for p in _interpolations(path[:m.start()] + opt + path[m.end():], src)]


def frontend_calls() -> set[tuple[str, str, str]]:
    calls = set()
    for f in SRC.rglob("*.ts*"):
        if ".test." in f.name or f.parts[-2] == "test":
            continue
        text = f.read_text()
        src = str(f.relative_to(SRC))
        for m in LITERAL.finditer(text):
            if text[max(0, m.start() - 12):m.start()].endswith("startsWith("):
                continue                                    # a prefix comparison, not a request
            tail = text[m.end():m.end() + 200]
            # options belong to THIS call only when they follow the path directly: `api(path, { method: ... })`
            method = METHOD.search(tail[:tail.find("})") if "})" in tail else 120]) if tail.lstrip().startswith(",") else None
            verbs = VERBS.findall(method.group(1)) if method else []
            if text[max(0, m.start() - 20):m.start()].endswith("uploadWithProgress("):
                verbs = ["POST"]                                # services/api.ts: a multipart POST that reports progress
            before = text[:m.start()].rstrip()
            if len(verbs) == 2 and before.endswith("?"):            # `cond ? PATH_A : PATH_B, { method: cond ? A : B }`: pair by branch
                verbs = verbs[:1]
            elif len(verbs) == 2 and before.endswith(":") and LITERAL.search(before[-200:]):
                verbs = verbs[1:]
            raw = m.group(2).split("?")[0].rstrip("/")
            for path in _interpolations(raw, src):
                for verb in verbs or ["GET"]:
                    calls.add((verb, path, src))
    return calls


def test_the_frontend_actually_calls_the_api():
    paths = {p for _, p, _ in frontend_calls()}
    assert len(paths) > 80 and "/api/v1/questions" in paths and "/api/v1/papers" in paths and "/api/v1/uploads" in paths


def test_every_frontend_api_call_is_routed_to_a_service_that_implements_it():
    missing = sorted(f"{m} {p}  ({src})" for m, p, src in frontend_calls() if resolve(TABLE, m, p) is None)
    assert not missing, "frontend calls without a route (gateway 'No such route' or service 'Method Not Allowed'):\n" + "\n".join(missing)


def test_the_three_demo_failures_are_routed_by_this_revision():
    for method, path, service in (("GET", "/api/v1/questions", "paper"), ("GET", "/api/v1/papers", "paper"), ("GET", "/api/v1/uploads", "paper")):
        assert resolve(TABLE, method, path) == service, (method, path)


def test_dynamic_action_values_are_still_the_ones_declared_in_the_source():
    for (src, _), values in DYNAMIC.items():
        text = (SRC / src).read_text()
        assert all(f'"{v}"' in text for v in values), src
