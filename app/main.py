from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.anomalies import router as anomalies_router
from app.database import create_tables
from app.funnel import router as funnel_router
from app.health import router as health_router
from app.heatmap import router as heatmap_router
from app.ingestion import router as ingestion_router
from app.ingestion import validation_exception_handler
from app.metrics import router as metrics_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create SQLite tables when the API container starts."""

    del app
    create_tables()
    yield


app = FastAPI(
    title="Store Intelligence API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.include_router(ingestion_router)
app.include_router(metrics_router)
app.include_router(funnel_router)
app.include_router(heatmap_router)
app.include_router(anomalies_router)
app.include_router(health_router)
