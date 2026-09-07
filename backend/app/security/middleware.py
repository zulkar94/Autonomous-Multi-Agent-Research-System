"""Security headers, request IDs, body-size limits and rate limiting middleware."""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..config import get_settings
from ..logging_setup import get_logger, request_id_var
from .ratelimit import limiter

logger = get_logger(__name__)
MAX_BODY_BYTES = 256 * 1024


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a correlation ID and emit one structured access log per request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))[:64]
        token = request_id_var.set(request_id)
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("unhandled_error path=%s", request.url.path)
            response = JSONResponse(
                {"error": "internal_error", "request_id": request_id}, status_code=500
            )
        duration_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request method=%s path=%s status=%s duration_ms=%.1f",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        request_id_var.reset(token)
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=(), payment=()"
        )
        response.headers.setdefault("Content-Security-Policy", settings.csp)
        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload"
            )
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            return JSONResponse({"error": "payload_too_large"}, status_code=413)
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Global per-identity limit. Endpoint-specific quotas live in dependencies."""

    EXEMPT = ("/healthz", "/readyz", "/metrics")

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        if not settings.rate_limit_enabled or request.url.path.startswith(self.EXEMPT):
            return await call_next(request)

        identity = request.headers.get("authorization") or (
            request.client.host if request.client else "anonymous"
        )
        key = f"global:{hash(identity) & 0xFFFFFFFF}"
        decision = await limiter.check(
            key, settings.rate_limit_requests, settings.rate_limit_window_seconds
        )
        if not decision.allowed:
            return JSONResponse(
                {"error": "rate_limited", "detail": "too many requests"},
                status_code=429,
                headers={"Retry-After": str(decision.retry_after)},
            )
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_requests)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        return response
