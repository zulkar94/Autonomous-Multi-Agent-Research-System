"""Application factory: middleware stack, routers, lifespan, error handling."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .api import routes_auth, routes_runs, routes_system
from .config import get_settings
from .db import engine, init_db
from .logging_setup import configure_logging, get_logger
from .security.middleware import (
    BodySizeLimitMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from .services.runs import manager, recover_orphaned_runs

logger = get_logger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(json_output=settings.is_production)
    logger.info("startup env=%s version=%s", settings.app_env, __version__)
    await init_db()
    await recover_orphaned_runs()
    try:
        yield
    finally:
        logger.info("shutdown draining_runs=%d", manager.active_count)
        await manager.shutdown()
        await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="Autonomous multi-agent research: search, verify, debate, cite.",
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    # Middleware runs bottom-up: context wraps everything, CORS sits outermost.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,  # bearer tokens only; no cookie surface
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        max_age=600,
    )

    app.include_router(routes_auth.router, prefix=settings.api_prefix)
    app.include_router(routes_runs.router, prefix=settings.api_prefix)
    app.include_router(routes_system.router)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "detail": [
                    {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
                    for e in exc.errors()
                ][:10],
                "request_id": getattr(request.state, "request_id", None),
            },
        )

    @app.exception_handler(HTTPException)
    async def http_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": exc.detail if isinstance(exc.detail, str) else "request_failed",
                "request_id": getattr(request.state, "request_id", None),
            },
            headers=exc.headers,
        )

    if FRONTEND_DIST.is_dir():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        async def spa_index() -> FileResponse:
            return FileResponse(FRONTEND_DIST / "index.html")

    else:

        @app.get("/", include_in_schema=False)
        async def console() -> FileResponse:
            """Zero-build fallback client, served when no SPA bundle is present."""
            return FileResponse(STATIC_DIR / "console.html")

    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=int(os.getenv("PORT", settings.port)),
        reload=not settings.is_production,
    )
