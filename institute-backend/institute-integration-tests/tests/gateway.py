"""An in-process stand-in for the nginx gateway: one ASGI app that sends every request to the Institute service that owns the path.
It uses the same routing table (institute_common.gateway) as the generated nginx configuration."""
import json

from institute_common.gateway import ambiguities, resolve, route_table

from inst_academics.main import app as academics
from inst_enrollment.main import app as enrollment
from inst_exam.main import app as exam
from inst_identity.main import app as identity
from inst_operations.main import app as operations
from inst_organization.main import app as organization
from inst_paper.main import app as paper

SERVICES = {"identity": identity, "organization": organization, "academics": academics, "enrollment": enrollment,
            "paper": paper, "exam": exam, "operations": operations}
TABLE = route_table(SERVICES)
assert not ambiguities(TABLE), ambiguities(TABLE)


async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    if scope["type"] != "http":
        return
    path = scope["path"]
    if path in ("/health", "/ready"):          # the gateway's own probes are answered by any one service in the test setup
        service = "identity"
    elif scope["method"] == "OPTIONS":         # CORS preflight goes to whichever service owns the path
        service = next((resolve(TABLE, m, path) for m in ("GET", "POST", "PUT", "PATCH", "DELETE") if resolve(TABLE, m, path)), None)
    else:
        service = resolve(TABLE, scope["method"], path)
    if service is None:
        body = json.dumps({"detail": {"code": "NOT_FOUND", "message": "No such route."}}).encode()
        await send({"type": "http.response.start", "status": 404, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})
        return
    await SERVICES[service](scope, receive, send)
