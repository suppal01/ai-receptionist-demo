"""FastAPI application entry point.

Run locally:  uvicorn app.main:app --reload
Then open:    http://localhost:8000/docs
"""

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import engine, router
from app.dashboard import router as dashboard_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Warm the model connection in the background so the first caller isn't kept waiting
    # and startup (and the Cloud Run health check) isn't blocked by it.
    for part in (engine.model, engine.classifier):
        warm_up = getattr(part, "warm_up", None)
        if warm_up:
            threading.Thread(target=warm_up, daemon=True).start()
    yield


app = FastAPI(
    title="AI Receptionist (capstone demo)",
    description="Text-only AI receptionist for a dental practice. See docs/plan.md.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router)
app.include_router(dashboard_router)
