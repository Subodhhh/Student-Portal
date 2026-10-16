from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.database import engine
from app.routes.attempt import router as attempt_router
from app.routes.internal import router as internal_router
from app.services.expiry import start_background


@asynccontextmanager
async def lifespan(app):
    start_background()
    yield


app = FastAPI(
    title="Student Attempt Service",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in settings.cors_origins.split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"status": "degraded", "service": "student-attempt-service", "database": "unavailable"})
    return {
        "status": "ok",
        "service": "student-attempt-service",
        "database": "ok",
    }


app.include_router(attempt_router)
app.include_router(internal_router)