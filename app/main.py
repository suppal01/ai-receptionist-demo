"""FastAPI application entry point.

Run locally:  uvicorn app.main:app --reload
Then open:    http://localhost:8000/docs
"""

from fastapi import FastAPI

from app.api.routes import router

app = FastAPI(
    title="AI Receptionist (capstone demo)",
    description="Text-only AI receptionist for a dental practice. See docs/plan.md.",
    version="0.1.0",
)

app.include_router(router)
