"""Builds a hardened FastAPI app for one Institute service: service context, CORS, security headers, safe error handler,
/health, /ready and (for non-identity services) the audit forwarder."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from institute_common.config import settings
from institute_common.context import service_context
from institute_common.database import engine

log = logging.getLogger("institute")


class ServiceContextMiddleware:
    """Pure ASGI middleware: everything executed for a request (routes, dependencies, the DB session guard, audit) knows which service it is."""

    def __init__(self, app, service: str):
        self.app, self.service = app, service

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        with service_context(self.service):
            await self.app(scope, receive, send)


def create_app(service: str, title: str, routers, *, internal_routers=(), ready_redis: bool = False, on_startup=()) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        if service != "identity":
            from institute_common.audit_forward import start_forwarder
            start_forwarder(service)
        for hook in on_startup:
            hook()
        yield

    app = FastAPI(title=title, version="1.0.0", docs_url=f"/api/{service}/docs", openapi_url=f"/api/{service}/openapi.json", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware, allow_origins=settings.origins, allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Content-Type", "X-CSRF-Token", "X-Idempotency-Key", "Idempotency-Key"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        if settings.session_cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)  # no body/headers logged
        return JSONResponse(status_code=500, content={"detail": {"code": "INTERNAL_ERROR", "message": "Something went wrong."}})

    for r in routers:
        app.include_router(r)
    for r in internal_routers:
        app.include_router(r)

    @app.get("/health", include_in_schema=False)
    def health():
        return {"status": "ok", "service": f"institute-{service}"}

    @app.get("/ready", include_in_schema=False)
    def ready():
        checks = {}
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception:
            checks["database"] = "unavailable"
        if ready_redis:
            try:
                from institute_common.core import redis_store
                redis_store.client().ping()
                checks["redis"] = "ok"
            except Exception:
                checks["redis"] = "unavailable"
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse(status_code=200 if ok else 503, content={"status": "ready" if ok else "degraded", **checks})

    # Outermost: set the service context before anything else runs.
    app.add_middleware(ServiceContextMiddleware, service=service)
    return app
