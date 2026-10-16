from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routes.profile import router as profile_router

app = FastAPI(
    title="Student Profile Service",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(profile_router)

@app.get("/health")
def health_check():
    from fastapi.responses import JSONResponse
    from sqlalchemy import text

    from app.database import engine

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"status": "degraded", "service": "student-profile-service", "database": "unavailable"})
    return {"status": "ok", "service": "student-profile-service", "database": "ok"}
