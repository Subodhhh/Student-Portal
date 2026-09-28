from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import engine
from app.routes.auth import router as auth_router

app = FastAPI(title="Student Authentication Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)


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