"""Hardened page fetcher: SSRF-checked, size-capped, redirect-audited."""

from __future__ import annotations

import hashlib

import httpx

from ..config import get_settings
from ..logging_setup import get_logger
from ..security.sanitize import sanitize_content
from ..security.ssrf import SSRFError, validate_url

logger = get_logger(__name__)

ALLOWED_CONTENT_TYPES = ("text/html", "text/plain", "application/xhtml+xml", "application/json")


class FetchResult:
    __slots__ = ("content_hash", "error", "injection_findings", "ok", "text", "url")

    def __init__(
        self,
        url: str,
        text: str = "",
        content_hash: str = "",
        injection_findings: list[str] | None = None,
        ok: bool = True,
        error: str | None = None,
    ) -> None:
        self.url = url
        self.text = text
        self.content_hash = content_hash
        self.injection_findings = injection_findings or []
        self.ok = ok
        self.error = error


async def fetch_page(url: str, client: httpx.AsyncClient | None = None) -> FetchResult:
    """Fetch and sanitize a page. Never raises: failures return `ok=False`."""
    s = get_settings()
    try:
        validate_url(url)
    except SSRFError as exc:
        return FetchResult(url, ok=False, error=f"blocked: {exc}")

    owns_client = client is None
    client = client or httpx.AsyncClient(
        timeout=httpx.Timeout(s.fetch_timeout_seconds),
        follow_redirects=False,  # redirects are re-validated hop by hop
        headers={"User-Agent": "ARS-Research-Agent/1.0 (+https://example.org/bot)"},
    )
    try:
        current = url
        for _ in range(3):
            response = await client.get(current)
            if response.is_redirect:
                location = str(response.next_request.url) if response.next_request else ""
                try:
                    validate_url(location)
                except SSRFError as exc:
                    return FetchResult(url, ok=False, error=f"redirect blocked: {exc}")
                current = location
                continue
            break
        else:
            return FetchResult(url, ok=False, error="too many redirects")

        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";")[0].strip()
        if content_type and content_type not in ALLOWED_CONTENT_TYPES:
            return FetchResult(url, ok=False, error=f"unsupported content-type: {content_type}")

        raw = response.content[: s.fetch_max_bytes]
        text, findings = sanitize_content(raw.decode("utf-8", errors="replace"))
        if findings:
            logger.warning("prompt_injection_detected url=%s patterns=%d", current, len(findings))
        return FetchResult(
            url=current,
            text=text,
            content_hash=hashlib.sha256(raw).hexdigest(),
            injection_findings=findings,
        )
    except httpx.HTTPError as exc:
        return FetchResult(url, ok=False, error=f"fetch failed: {type(exc).__name__}")
    finally:
        if owns_client:
            await client.aclose()
