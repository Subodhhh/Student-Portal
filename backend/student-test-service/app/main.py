from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routes.internal import router as internal_router
from app.routes.test import router as test_router
from app.routes.test_audience import router as test_audience_router


app = FastAPI(
    title="Student Test Service",
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
    return {
        "status": "ok",
        "service": "student-test-service",
    }


app.include_router(test_router)
app.include_router(test_audience_router)
app.include_router(internal_router)