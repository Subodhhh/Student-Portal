from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routes.enrollment import router as enrollment_router

app = FastAPI(
    title="Student Enrollment Service",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in settings.cors_origins.split(",")
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(enrollment_router)


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "student-enrollment-service",
    }