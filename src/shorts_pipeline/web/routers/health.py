"""Health router helpers."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.jobs.preflight import run_preflight


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/health", tags=["health"])

    @router.get("/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/ready")
    def ready() -> JSONResponse:
        report = run_preflight(settings).model_dump()
        return JSONResponse(report, status_code=200 if report["ok"] else 503)

    return router
