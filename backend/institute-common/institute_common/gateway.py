"""Path -> service routing table, derived from the services' own route definitions.

Used by (a) scripts/gen_gateway_conf.py to generate the nginx gateway and (b) the integration test harness, so the two cannot drift."""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RouteEntry:
    service: str
    methods: frozenset[str]
    path: str            # e.g. /api/v1/exams/{exam_id}/publish

    def regex(self) -> re.Pattern:
        return re.compile("^" + re.sub(r"\\\{[^/]+?\\\}", "[^/]+", re.escape(self.path)) + "$")


def route_table(apps: dict) -> list[RouteEntry]:
    """Public (browser-facing) routes only: /api/v1/..., not /internal, /health or the docs. Read from each app's OpenAPI schema
    (routers are included lazily by this FastAPI version, so `app.routes` is not a flat list)."""
    out = []
    for service, app in apps.items():
        for path, ops in app.openapi()["paths"].items():
            if path.startswith("/api/v1/"):
                out.append(RouteEntry(service, frozenset(m.upper() for m in ops if m.lower() in {"get", "post", "put", "patch", "delete"}), path))
    return out


def ambiguities(table: list[RouteEntry]) -> list[str]:
    """Every route must resolve (by specificity) to the service that defines it; otherwise the gateway would misroute it."""
    bad = []
    for e in table:
        probe = re.sub(r"\{[^/]+?\}", "x", e.path)
        for m in e.methods:
            if resolve(table, m, probe) != e.service:
                bad.append(f"{m} {e.path} -> {resolve(table, m, probe)} (defined in {e.service})")
    return bad


def resolve(table: list[RouteEntry], method: str, path: str) -> str | None:
    """Most specific match wins: literal segments beat parameters, so /staff/lookup is not swallowed by /staff/{staff_id}."""
    best, best_score = None, -1
    for e in table:
        if method.upper() in e.methods and e.regex().match(path):
            score = sum(1 for seg in e.path.split("/") if seg and not seg.startswith("{"))
            if score > best_score:
                best, best_score = e.service, score
    return best
