from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.database import engine
from app.routes.internal import router as internal_router
from app.routes.result import router
from app.services.auto_evaluation import start_background


@asynccontextmanager
async def lifespan(app):
    start_background()
    yield


app = FastAPI(
    title="Student Result Service",
    version="1.0.0",
    lifespan=lifespan,
)

origins = [
    origin.strip()
    for origin in settings.cors_origins.split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(internal_router)


@app.get("/health")
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"status": "degraded", "service": "student-result-service", "database": "unavailable"})
    return {
        "status": "ok",
        "service": "student-result-service",
        "database": "ok",
    }