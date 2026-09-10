from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from enterprise_insight_backend.agent_worker import AgentWorker
from enterprise_insight_backend.api import create_api_router
from enterprise_insight_backend.config import Settings, get_settings
from enterprise_insight_backend.database import Database
from enterprise_insight_backend.errors import (
    DomainError,
    ErrorBody,
    ErrorResponse,
    domain_error_handler,
)

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    database = Database(resolved)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        database.create_schema()
        worker = AgentWorker(database, resolved)
        if resolved.agent_worker_enabled:
            await worker.start()
        try:
            yield
        finally:
            if resolved.agent_worker_enabled:
                await worker.stop()

    application = FastAPI(
        title=resolved.app_name,
        version=resolved.app_version,
        description=(
            "企业可信语义内核与管理探索后端。直观关系经过强类型校验；"
            "潜在关系只进入探索空间，不会直接成为正式事实。"
        ),
        lifespan=lifespan,
        openapi_url=f"{resolved.api_prefix}/openapi.json",
        docs_url=f"{resolved.api_prefix}/docs",
        redoc_url=f"{resolved.api_prefix}/redoc",
    )
    application.state.database = database
    application.state.settings = resolved
    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origin_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.middleware("http")
    async def trace_requests(
        request: Request, call_next: Callable[[Request], Awaitable[Any]]
    ) -> Any:
        request.state.trace_id = request.headers.get("X-Trace-Id") or str(uuid4())
        response = await call_next(request)
        response.headers["X-Trace-Id"] = request.state.trace_id
        return response

    application.add_exception_handler(DomainError, domain_error_handler)  # type: ignore[arg-type]

    @application.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = []
        for item in exc.errors():
            details.append(
                {
                    "path": ".".join(str(part) for part in item.get("loc", [])),
                    "message": item.get("msg", "输入无效"),
                    "type": item.get("type", "validation_error"),
                }
            )
        payload = ErrorResponse(
            error=ErrorBody(
                code="REQUEST_VALIDATION_FAILED",
                message="请检查输入内容。",
                details=details,
                trace_id=request.state.trace_id,
            )
        )
        return JSONResponse(status_code=422, content=payload.model_dump(mode="json"))

    @application.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        """Keep unexpected failures diagnosable without exposing internals to the UI."""
        trace_id = getattr(request.state, "trace_id", None)
        logger.exception("Unhandled request error trace_id=%s", trace_id, exc_info=exc)
        payload = ErrorResponse(
            error=ErrorBody(
                code="INTERNAL_SERVER_ERROR",
                message="服务端发生未预期错误，请根据 trace id 联系开发者。",
                trace_id=trace_id,
            )
        )
        response = JSONResponse(status_code=500, content=payload.model_dump(mode="json"))
        if trace_id:
            response.headers["X-Trace-Id"] = trace_id
        return response

    application.include_router(create_api_router(resolved), prefix=resolved.api_prefix)

    frontend_dist = resolved.frontend_dist_dir
    if frontend_dist is not None and (frontend_dist / "index.html").is_file():
        frontend_root = frontend_dist.resolve()
        assets_dir = frontend_root / "assets"
        if assets_dir.is_dir():
            application.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

        @application.get("/{full_path:path}", include_in_schema=False, response_model=None)
        def frontend_route(full_path: str) -> FileResponse | JSONResponse:
            if full_path.startswith(resolved.api_prefix.lstrip("/") + "/"):
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            candidate = (frontend_root / full_path).resolve()
            if candidate.is_relative_to(frontend_root) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(frontend_root / "index.html")

    return application
