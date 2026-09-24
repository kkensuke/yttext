from __future__ import annotations

import argparse
import ipaddress
import logging
import os
import threading
import uuid
import webbrowser
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import __version__
from .errors import AppError
from .gemini import DEFAULT_GEMINI_MODEL
from .gemini import list_gemini_models as fetch_gemini_models
from .service import (
    MAX_SUMMARY_LENGTH,
    ExtractionOptions,
    extract_transcript_only,
    mark_summary_pending,
    summarize_transcript,
)
from .summary_languages import normalize_summary_language, summary_language_options
from .web_state import (
    PendingSummaryStore,
    SummaryJobBusy,
    SummaryJobExpired,
    SummaryJobNotFound,
    SummaryStoreFull,
)

LOGGER = logging.getLogger(__name__)
UI_ROOT = Path(__file__).with_name("ui")
API_KEY_HEADER = "X-Gemini-Api-Key"
UI_ASSETS = frozenset(
    {
        "Temml-Local.css",
        "Temml.woff2",
        "app.js",
        "enhancements.css",
        "enhancements.js",
        "mdit-plugin-tex.min.js",
        "markdown-it.min.js",
        "markdown-renderer.js",
        "styles.css",
        "temml.min.js",
        "theme-control.css",
        "theme-control.js",
    }
)


class WebApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.hint = hint


class RequestTooLarge(Exception):
    pass


class RequestBodyLimitMiddleware:
    """Reject oversized request bodies even when Content-Length is absent or incorrect."""

    def __init__(self, app: ASGIApp, max_body_size: int) -> None:
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                if int(content_length) > self.max_body_size:
                    await self._send_rejection(scope, receive, send)
                    return
            except ValueError:
                await self._send_rejection(scope, receive, send)
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_size:
                    raise RequestTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestTooLarge:
            await self._send_rejection(scope, receive, send)

    @staticmethod
    async def _send_rejection(scope: Scope, receive: Receive, send: Send) -> None:
        response = _error_response(
            413,
            "request_too_large",
            "The request body is too large.",
            "Reduce the request size and try again.",
        )
        await response(scope, receive, send)


class ExtractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    url: str = Field(min_length=1, max_length=2048)
    transcript_format: Literal["md", "txt", "json", "srt", "vtt"] = "md"
    prepare_summary: bool = True
    cookie_browser: Literal["chrome", "chromium", "edge", "firefox", "safari", "brave"] | None = (
        None
    )


class SummarizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    job_id: str = Field(min_length=20, max_length=128)
    mode: Literal["truncate", "full", "skip"]
    summary_language: str = Field(default="auto", min_length=2, max_length=35)
    gemini_model: str = Field(default=DEFAULT_GEMINI_MODEL, min_length=1, max_length=100)


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    job_id: str = Field(min_length=20, max_length=128)


def create_app(
    *,
    mode: Literal["local", "hosted"] | None = None,
    store: PendingSummaryStore | None = None,
    allowed_hosts: list[str] | None = None,
    allowed_origins: set[str] | None = None,
) -> FastAPI:
    web_mode = mode or _configured_mode()
    local_api_key, local_gemini_model = _local_gemini_configuration(web_mode)
    default_summary_language = _configured_summary_language()
    hosts = allowed_hosts or _configured_hosts(web_mode)
    origins = (
        allowed_origins if allowed_origins is not None else _configured_origins(web_mode, hosts)
    )
    summary_store = (
        store
        if store is not None
        else PendingSummaryStore(
            ttl_seconds=_positive_int_env("YTTEXT_JOB_TTL", 600),
            max_jobs=_positive_int_env("YTTEXT_MAX_JOBS", 64),
            max_characters=_positive_int_env(
                "YTTEXT_MAX_PENDING_CHARACTERS",
                5_000_000,
            ),
        )
    )
    work_slots = threading.BoundedSemaphore(_positive_int_env("YTTEXT_MAX_WORKERS", 4))

    application = FastAPI(
        title="yttext",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.state.web_mode = web_mode
    application.state.summary_store = summary_store
    application.add_middleware(RequestBodyLimitMiddleware, max_body_size=64 * 1024)
    application.add_middleware(GZipMiddleware, minimum_size=1024)
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts, www_redirect=False)

    @application.middleware("http")
    async def protect_web_requests(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH"}:
            if request.headers.get("content-type", "").split(";", 1)[0].lower() != (
                "application/json"
            ):
                return _error_response(
                    415,
                    "unsupported_media_type",
                    "API requests must use application/json.",
                )
            origin = request.headers.get("origin")
            if origin and origin not in origins:
                return _error_response(
                    403,
                    "origin_not_allowed",
                    "The request origin is not allowed.",
                )

        response = await call_next(request)
        return _secure_response(
            response,
            no_store=request.url.path.startswith("/api/"),
        )

    @application.exception_handler(WebApiError)
    async def handle_web_error(_request: Request, error: WebApiError) -> JSONResponse:
        return _error_response(error.status_code, error.code, error.message, error.hint)

    @application.exception_handler(AppError)
    async def handle_app_error(_request: Request, error: AppError) -> JSONResponse:
        return _error_response(400, error.code, error.message, error.hint)

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        fields = [
            {
                "location": [str(value) for value in item.get("loc", ())],
                "message": str(item.get("msg") or "Invalid value"),
                "type": str(item.get("type") or "validation_error"),
            }
            for item in error.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                **_error_content(
                    "invalid_request",
                    "The request contains invalid values.",
                    "Review the marked settings and try again.",
                ),
                "fields": fields,
            },
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_error(_request: Request, error: Exception) -> JSONResponse:
        request_id = uuid.uuid4().hex
        LOGGER.error(
            "Unhandled web request %s",
            request_id,
            exc_info=(type(error), error, error.__traceback__),
        )
        return JSONResponse(
            status_code=500,
            content={
                **_error_content(
                    "unexpected_error",
                    "An unexpected server error occurred.",
                    "Try again. If the problem continues, report the request ID.",
                ),
                "request_id": request_id,
            },
        )

    @application.get("/")
    def index() -> FileResponse:
        return FileResponse(UI_ROOT / "index.html", media_type="text/html")

    @application.get("/static/{asset_name}")
    def static_asset(asset_name: str) -> FileResponse:
        if asset_name not in UI_ASSETS:
            raise WebApiError(404, "asset_not_found", "The requested asset was not found.")
        return FileResponse(UI_ROOT / asset_name)

    @application.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/info")
    def app_info(request: Request) -> dict[str, object]:
        local_configuration = _may_use_local_gemini_configuration(request, web_mode)
        return {
            "version": __version__,
            "gemini_model": local_gemini_model if local_configuration else DEFAULT_GEMINI_MODEL,
            "summary_languages": summary_language_options(),
            "summary_language": default_summary_language,
            "summary_limit_characters": MAX_SUMMARY_LENGTH,
            "capabilities": {
                "byok": True,
                "server_api_key": bool(local_api_key) and local_configuration,
                "browser_cookies": web_mode == "local",
            },
        }

    @application.post("/api/extract")
    def extract(payload: ExtractRequest) -> dict[str, object]:
        if web_mode == "hosted" and payload.cookie_browser:
            raise WebApiError(
                400,
                "browser_cookies_unavailable",
                "Browser cookies are unavailable on the hosted service.",
                "Run the app locally to use cookies from a browser on your computer.",
            )
        options = ExtractionOptions(
            url=payload.url,
            transcript_format=payload.transcript_format,
            generate_summary=False,
            cookie_browser=payload.cookie_browser,
        )
        result = _with_work_slot(work_slots, lambda: extract_transcript_only(options))
        job_payload = None
        response_result = result
        if payload.prepare_summary:
            try:
                job_id, expires_in = summary_store.create(result)
            except SummaryStoreFull as error:
                raise WebApiError(
                    503,
                    "summary_store_full",
                    str(error),
                    "Try again later.",
                ) from error
            long_summary = result.character_count > MAX_SUMMARY_LENGTH
            if long_summary:
                response_result = mark_summary_pending(result)
            job_payload = {
                "id": job_id,
                "expires_in_seconds": expires_in,
                "requires_long_summary_choice": long_summary,
            }
        return {"ok": True, "result": response_result.to_dict(), "summary_job": job_payload}

    @application.post("/api/summarize")
    def summarize(
        request: Request,
        payload: SummarizeRequest,
        api_key: Annotated[
            SecretStr | None,
            Header(alias=API_KEY_HEADER, max_length=512),
        ] = None,
    ) -> dict[str, object]:
        result = _begin_summary(summary_store, payload.job_id)
        try:
            key = ""
            if payload.mode != "skip":
                key = api_key.get_secret_value().strip() if api_key else ""
                if (
                    not key
                    and local_api_key
                    and _may_use_local_gemini_configuration(request, web_mode)
                ):
                    key = local_api_key.get_secret_value()
                if not key:
                    raise WebApiError(
                        400,
                        "missing_api_key",
                        "Enter a Gemini API key to create the summary.",
                    )
            summarized = _with_work_slot(
                work_slots,
                lambda: summarize_transcript(
                    result,
                    mode=payload.mode,
                    api_key=key,
                    language=payload.summary_language,
                    model=payload.gemini_model,
                ),
            )
        except Exception:
            summary_store.finish(payload.job_id, result=result)
            raise

        completed = payload.mode == "skip" or summarized.summary is not None
        summary_store.finish(payload.job_id, result=summarized, delete=completed)
        return {
            "ok": True,
            "result": summarized.to_dict(),
            "summary_job": None
            if completed
            else {
                "id": payload.job_id,
                "expires_in_seconds": summary_store.ttl_seconds,
                "requires_long_summary_choice": summarized.character_count > MAX_SUMMARY_LENGTH,
            },
        }

    @application.post("/api/gemini/models")
    def gemini_models(
        request: Request,
        api_key: Annotated[
            SecretStr | None,
            Header(alias=API_KEY_HEADER, max_length=512),
        ] = None,
    ) -> dict[str, object]:
        key = api_key.get_secret_value().strip() if api_key else ""
        if not key and local_api_key and _may_use_local_gemini_configuration(request, web_mode):
            key = local_api_key.get_secret_value()
        if not key:
            raise WebApiError(
                400,
                "missing_api_key",
                "Enter a Gemini API key to load available models.",
            )
        models = _with_work_slot(work_slots, lambda: fetch_gemini_models(key))
        return {"ok": True, "models": models}

    @application.post("/api/summary/discard")
    def discard_summary(payload: JobRequest) -> dict[str, bool]:
        try:
            summary_store.discard(payload.job_id)
        except SummaryJobBusy as error:
            raise WebApiError(409, "summary_job_busy", str(error)) from error
        return {"ok": True}

    return application


def _begin_summary(store: PendingSummaryStore, job_id: str):
    try:
        return store.begin(job_id)
    except SummaryJobExpired as error:
        raise WebApiError(
            410,
            "summary_job_expired",
            str(error),
            "Extract the transcript again to create a new summary job.",
        ) from error
    except SummaryJobNotFound as error:
        raise WebApiError(404, "summary_job_not_found", str(error)) from error
    except SummaryJobBusy as error:
        raise WebApiError(409, "summary_job_busy", str(error)) from error


def _with_work_slot(semaphore: threading.BoundedSemaphore, operation):
    if not semaphore.acquire(blocking=False):
        raise WebApiError(
            429,
            "server_busy",
            "The server is processing too many requests.",
            "Wait a moment and try again.",
        )
    try:
        return operation()
    finally:
        semaphore.release()


def _error_response(
    status_code: int,
    code: str,
    message: str,
    hint: str = "",
) -> JSONResponse:
    response = JSONResponse(status_code=status_code, content=_error_content(code, message, hint))
    return _secure_response(response, no_store=True)


def _secure_response(response, *, no_store: bool = False):
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
        "script-src 'self'; style-src 'self' 'unsafe-inline'; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if no_store:
        response.headers["Cache-Control"] = "no-store"
    return response


def _error_content(code: str, message: str, hint: str = "") -> dict[str, object]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "hint": hint,
        },
    }


def _configured_summary_language() -> str:
    value = os.getenv("YTTEXT_SUMMARY_LANG", "").strip() or "auto"
    try:
        return normalize_summary_language(value)
    except ValueError as exc:
        raise RuntimeError(f"Invalid YTTEXT_SUMMARY_LANG: {exc}") from exc


def _configured_mode() -> Literal["local", "hosted"]:
    value = os.getenv("YTTEXT_MODE", "local").strip().lower()
    if value not in {"local", "hosted"}:
        raise RuntimeError("YTTEXT_MODE must be either local or hosted.")
    return value  # type: ignore[return-value]


def _local_gemini_configuration(
    mode: Literal["local", "hosted"],
) -> tuple[SecretStr | None, str]:
    if mode != "local":
        return None, DEFAULT_GEMINI_MODEL

    key = os.getenv("GEMINI_API_KEY", "").strip()
    model = os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL
    if len(key) > 512:
        raise RuntimeError("GEMINI_API_KEY must not exceed 512 characters.")
    if len(model) > 100:
        raise RuntimeError("GEMINI_MODEL must not exceed 100 characters.")
    return (SecretStr(key) if key else None), model


def _may_use_local_gemini_configuration(
    request: Request,
    mode: Literal["local", "hosted"],
) -> bool:
    if mode != "local" or request.client is None:
        return False
    try:
        address = ipaddress.ip_address(request.client.host)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped_address = getattr(address, "ipv4_mapped", None)
    return bool(mapped_address and mapped_address.is_loopback)


def _configured_hosts(mode: Literal["local", "hosted"]) -> list[str]:
    configured = [
        value.strip() for value in os.getenv("YTTEXT_ALLOWED_HOSTS", "").split(",") if value.strip()
    ]
    if configured:
        return configured
    if mode == "hosted":
        raise RuntimeError("YTTEXT_ALLOWED_HOSTS is required in hosted mode.")
    return ["127.0.0.1", "localhost", "testserver"]


def _configured_origins(
    mode: Literal["local", "hosted"],
    hosts: list[str],
) -> set[str]:
    configured = {
        value.strip().rstrip("/")
        for value in os.getenv("YTTEXT_ALLOWED_ORIGINS", "").split(",")
        if value.strip()
    }
    if configured:
        return configured
    if mode == "hosted":
        return {f"https://{host}" for host in hosts if host != "*"}
    port = _positive_int_env("PORT", 8000)
    return {
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
        "http://testserver",
    }


def _positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError as error:
        raise RuntimeError(f"{name} must be a positive integer.") from error
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer.")
    return value


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="yttext web",
        description="Start the local yttext browser app.",
    )


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    build_parser().parse_args(argv)
    mode = _configured_mode()
    port = _positive_int_env("PORT", 8000)
    host = os.getenv("YTTEXT_HOST", "").strip() or ("127.0.0.1" if mode == "local" else "0.0.0.0")
    browser_url = f"http://127.0.0.1:{port}"
    application = create_app(mode=mode)
    should_open = mode == "local" and os.getenv("YTTEXT_OPEN_BROWSER", "1") != "0"
    if should_open:
        timer = threading.Timer(0.8, lambda: webbrowser.open(browser_url))
        timer.daemon = True
        timer.start()

    print(f"yttext is running at:\n{browser_url}\n\nPress Ctrl+C to stop.", flush=True)
    uvicorn.run(application, host=host, port=port, workers=1)
    return 0


app = create_app()


if __name__ == "__main__":
    main()
