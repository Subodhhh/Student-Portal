from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.auth import redis_client
from app.database import engine
from app.routes.auth import router as auth_router
from app.routes.internal import router as internal_router

app = FastAPI(title="Student Authentication Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(internal_router)


@app.get("/health")
def health_check():
    try:
        with engine.connect():
            database_status = "connected"
    except Exception:
        database_status = "disconnected"

    return {
        "status": "ok",
        "service": "student-authentication-service",
        "database": database_status,
    }


@app.get("/ready", include_in_schema=False)
def ready():
    """Readiness for orchestration: 503 unless both the database and Redis (sessions) answer."""
    checks = {}
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"
    try:
        redis_client.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse(status_code=200 if ok else 503, content={"status": "ready" if ok else "degraded", **checks})
