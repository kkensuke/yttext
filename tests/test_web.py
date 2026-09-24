import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from yttext import web
from yttext.models import (
    CaptionTrack,
    OutputArtifact,
    TranscriptDocument,
    TranscriptSegment,
    VideoMetadata,
)
from yttext.service import ExtractionResult
from yttext.web_state import PendingSummaryStore


@pytest.fixture(autouse=True)
def _clear_gemini_environment(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("YTTEXT_SUMMARY_LANG", raising=False)
    monkeypatch.delenv("YTTEXT_TRANSCRIPT_FORMAT", raising=False)


def _result(*, character_count: int = 6) -> ExtractionResult:
    document = TranscriptDocument(
        metadata=VideoMetadata("dQw4w9WgXcQ", "Test video", 10, "", "en"),
        track=CaptionTrack((), "en", "manual"),
        segments=(TranscriptSegment(0.0, 1.0, "Hello."),),
    )
    return ExtractionResult(
        document=document,
        transcript=OutputArtifact(
            "transcript",
            "md",
            "dQw4w9WgXcQ_transcript.md",
            "# Test video\n\nHello.\n",
        ),
        summary=None,
        summary_limit=None,
        warning=None,
        character_count=character_count,
        word_count=1,
    )


def _summary(result: ExtractionResult) -> ExtractionResult:
    return replace(
        result,
        summary=OutputArtifact(
            "summary",
            "md",
            "dQw4w9WgXcQ_summarized.md",
            "# Summary\n\nShort summary.\n",
        ),
        warning=None,
    )


def _extract_job(client: TestClient) -> str:
    response = client.post(
        "/api/extract",
        json={"url": "dQw4w9WgXcQ", "prepare_summary": True},
    )
    assert response.status_code == 200
    return response.json()["summary_job"]["id"]


def test_local_info_reports_environment_configuration_without_exposing_the_key(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "environment-secret")
    monkeypatch.setenv("GEMINI_MODEL", "environment-model")
    monkeypatch.setenv("YTTEXT_SUMMARY_LANG", "PT-br")
    monkeypatch.setenv("YTTEXT_TRANSCRIPT_FORMAT", "VTT")
    client = TestClient(
        web.create_app(mode="local"),
        client=("127.0.0.1", 50_000),
    )

    info = client.get("/api/info")
    index = client.get("/")

    assert info.status_code == 200
    assert info.json()["capabilities"] == {
        "byok": True,
        "server_api_key": True,
        "browser_cookies": True,
    }
    assert info.json()["gemini_model"] == "environment-model"
    assert info.json()["summary_language"] == "pt-BR"
    assert info.json()["transcript_format"] == "vtt"
    assert "environment-secret" not in info.text + index.text


def test_hosted_info_and_model_discovery_ignore_environment_configuration(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "operator-secret")
    monkeypatch.setenv("GEMINI_MODEL", "operator-model")
    monkeypatch.setenv("YTTEXT_SUMMARY_LANG", "ja")
    monkeypatch.setenv("YTTEXT_TRANSCRIPT_FORMAT", "txt")
    client = TestClient(
        web.create_app(
            mode="hosted",
            allowed_hosts=["testserver"],
            allowed_origins={"https://transcript.example"},
        ),
        client=("127.0.0.1", 50_000),
    )

    info = client.get("/api/info")
    models = client.post("/api/gemini/models", json={})

    assert info.json()["capabilities"]["server_api_key"] is False
    assert info.json()["gemini_model"] == web.DEFAULT_GEMINI_MODEL
    assert info.json()["summary_language"] == "ja"
    assert info.json()["transcript_format"] == "txt"
    assert models.status_code == 400
    assert models.json()["error"]["code"] == "missing_api_key"
    assert "operator-secret" not in info.text + models.text
    assert "operator-model" not in info.text + models.text


def test_web_rejects_invalid_transcript_format_environment(monkeypatch) -> None:
    monkeypatch.setenv("YTTEXT_TRANSCRIPT_FORMAT", "invalid")

    with pytest.raises(RuntimeError, match="YTTEXT_TRANSCRIPT_FORMAT"):
        web.create_app(mode="local")


def test_web_rejects_invalid_summary_language_environment(monkeypatch) -> None:
    monkeypatch.setenv("YTTEXT_SUMMARY_LANG", "custom")

    with pytest.raises(RuntimeError, match="Invalid YTTEXT_SUMMARY_LANG"):
        web.create_app(mode="local")


def test_extract_never_accepts_or_forwards_an_api_key(monkeypatch) -> None:
    observed = []
    monkeypatch.setattr(
        web,
        "extract_transcript_only",
        lambda options: observed.append(options) or _result(),
    )
    store = PendingSummaryStore()
    client = TestClient(web.create_app(mode="local", store=store))
    secret = "user-secret-key"

    rejected = client.post(
        "/api/extract",
        json={"url": "dQw4w9WgXcQ", "prepare_summary": True, "api_key": secret},
    )
    accepted = client.post(
        "/api/extract",
        json={"url": "dQw4w9WgXcQ", "prepare_summary": True},
    )

    assert rejected.status_code == 422
    assert secret not in rejected.text
    assert accepted.status_code == 200
    assert len(observed) == 1
    assert observed[0].generate_summary is False
    assert observed[0].api_key == ""
    assert accepted.json()["summary_job"]["id"]
    assert secret not in repr(store._jobs)


def test_local_summary_uses_environment_key_and_prefers_the_user_header(monkeypatch) -> None:
    environment_secret = "local-environment-key"
    monkeypatch.setenv("GEMINI_API_KEY", environment_secret)
    monkeypatch.setattr(web, "extract_transcript_only", lambda _options: _result())
    observed = []

    def summarize(result, **kwargs):
        observed.append(kwargs)
        return _summary(result)

    monkeypatch.setattr(web, "summarize_transcript", summarize)
    store = PendingSummaryStore()
    client = TestClient(
        web.create_app(mode="local", store=store),
        client=("127.0.0.1", 50_000),
    )
    payload = {
        "job_id": _extract_job(client),
        "mode": "full",
        "summary_language": "ja",
        "gemini_model": "gemini-test",
    }

    environment_response = client.post("/api/summarize", json=payload)
    payload["job_id"] = _extract_job(client)
    user_secret = "user-secret-key"
    header_response = client.post(
        "/api/summarize",
        json=payload,
        headers={web.API_KEY_HEADER: user_secret},
    )

    assert environment_response.status_code == 200
    assert header_response.status_code == 200
    assert observed == [
        {
            "mode": "full",
            "api_key": environment_secret,
            "language": "ja",
            "model": "gemini-test",
        },
        {
            "mode": "full",
            "api_key": user_secret,
            "language": "ja",
            "model": "gemini-test",
        },
    ]
    assert environment_secret not in environment_response.text + header_response.text
    assert user_secret not in environment_response.text + header_response.text
    assert len(store) == 0


def test_hosted_summary_requires_the_user_header_and_deletes_completed_job(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "operator-key-must-be-ignored")
    monkeypatch.setattr(web, "extract_transcript_only", lambda _options: _result())
    observed = []

    def summarize(result, **kwargs):
        observed.append(kwargs)
        return _summary(result)

    monkeypatch.setattr(web, "summarize_transcript", summarize)
    store = PendingSummaryStore()
    client = TestClient(
        web.create_app(
            mode="hosted",
            store=store,
            allowed_hosts=["testserver"],
            allowed_origins={"https://transcript.example"},
        )
    )
    job_id = _extract_job(client)
    secret = "user-secret-key"
    payload = {
        "job_id": job_id,
        "mode": "full",
        "summary_language": "ja",
        "gemini_model": "gemini-test",
    }

    missing = client.post("/api/summarize", json=payload)
    completed = client.post(
        "/api/summarize",
        json=payload,
        headers={web.API_KEY_HEADER: secret},
    )

    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "missing_api_key"
    assert "operator-key-must-be-ignored" not in missing.text
    assert completed.status_code == 200
    assert completed.json()["result"]["summary"]["content"].startswith("# Summary")
    assert completed.json()["summary_job"] is None
    assert observed == [
        {
            "mode": "full",
            "api_key": secret,
            "language": "ja",
            "model": "gemini-test",
        }
    ]
    assert secret not in missing.text + completed.text
    assert len(store) == 0


def test_failed_summary_keeps_only_the_job_context_for_retry(monkeypatch) -> None:
    monkeypatch.setattr(web, "extract_transcript_only", lambda _options: _result())
    attempts = []

    def summarize(result, **kwargs):
        attempts.append(kwargs["api_key"])
        if len(attempts) == 1:
            return replace(result, warning="The transcript was created, but summarization failed.")
        return _summary(result)

    monkeypatch.setattr(web, "summarize_transcript", summarize)
    store = PendingSummaryStore()
    client = TestClient(web.create_app(mode="local", store=store))
    job_id = _extract_job(client)
    payload = {
        "job_id": job_id,
        "mode": "full",
        "summary_language": "auto",
        "gemini_model": "gemini-test",
    }

    failed = client.post(
        "/api/summarize",
        json=payload,
        headers={web.API_KEY_HEADER: "first-key"},
    )
    assert failed.status_code == 200
    assert failed.json()["summary_job"]["id"] == job_id
    assert len(store) == 1

    retried = client.post(
        "/api/summarize",
        json=payload,
        headers={web.API_KEY_HEADER: "second-key"},
    )

    assert len(store) == 0
    assert retried.status_code == 200
    assert retried.json()["summary_job"] is None
    assert attempts == ["first-key", "second-key"]
    assert "first-key" not in failed.text
    assert "second-key" not in retried.text


def test_summary_can_be_skipped_without_a_key(monkeypatch) -> None:
    monkeypatch.setattr(web, "extract_transcript_only", lambda _options: _result())
    observed = []

    def skip(result, **kwargs):
        observed.append(kwargs)
        return replace(result, warning="Summary skipped by the user.")

    monkeypatch.setattr(web, "summarize_transcript", skip)
    store = PendingSummaryStore()
    client = TestClient(web.create_app(mode="local", store=store))
    job_id = _extract_job(client)

    response = client.post(
        "/api/summarize",
        json={
            "job_id": job_id,
            "mode": "skip",
            "summary_language": "auto",
            "gemini_model": "gemini-test",
        },
    )

    assert response.status_code == 200
    assert observed[0]["api_key"] == ""
    assert len(store) == 0


def test_local_model_discovery_uses_environment_key_and_prefers_the_header(monkeypatch) -> None:
    environment_secret = "local-model-list-key"
    monkeypatch.setenv("GEMINI_API_KEY", environment_secret)
    observed = []
    monkeypatch.setattr(
        web,
        "fetch_gemini_models",
        lambda key: observed.append(key) or ["gemini-flash-latest"],
    )
    client = TestClient(
        web.create_app(mode="local"),
        client=("127.0.0.1", 50_000),
    )
    secret = "model-list-key"

    environment_response = client.post("/api/gemini/models", json={})
    oversized_key = "s" * 513
    rejected = client.post(
        "/api/gemini/models",
        json={},
        headers={web.API_KEY_HEADER: oversized_key},
    )
    response = client.post(
        "/api/gemini/models",
        json={},
        headers={web.API_KEY_HEADER: secret},
    )

    assert environment_response.json() == {"ok": True, "models": ["gemini-flash-latest"]}
    assert rejected.status_code == 422
    assert oversized_key not in rejected.text
    assert response.json() == {"ok": True, "models": ["gemini-flash-latest"]}
    assert observed == [environment_secret, secret]
    assert environment_secret not in environment_response.text + response.text
    assert secret not in environment_response.text + response.text


def test_local_environment_key_is_unavailable_to_non_loopback_clients(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "local-only-secret")
    monkeypatch.setenv("GEMINI_MODEL", "local-only-model")
    monkeypatch.setattr(web, "extract_transcript_only", lambda _options: _result())
    client = TestClient(
        web.create_app(mode="local"),
        client=("192.0.2.10", 50_000),
    )
    job_id = _extract_job(client)

    info = client.get("/api/info")
    summary = client.post(
        "/api/summarize",
        json={
            "job_id": job_id,
            "mode": "full",
            "summary_language": "auto",
            "gemini_model": "gemini-test",
        },
    )
    models = client.post("/api/gemini/models", json={})

    assert info.json()["capabilities"]["server_api_key"] is False
    assert info.json()["gemini_model"] == web.DEFAULT_GEMINI_MODEL
    assert summary.status_code == 400
    assert summary.json()["error"]["code"] == "missing_api_key"
    assert models.status_code == 400
    assert models.json()["error"]["code"] == "missing_api_key"
    assert "local-only-secret" not in info.text + summary.text + models.text
    assert "local-only-model" not in info.text + summary.text + models.text


def test_hosted_mode_disables_browser_cookie_access(monkeypatch) -> None:
    called = []
    monkeypatch.setattr(
        web,
        "extract_transcript_only",
        lambda _options: called.append(True) or _result(),
    )
    client = TestClient(
        web.create_app(
            mode="hosted",
            allowed_hosts=["testserver"],
            allowed_origins={"https://transcript.example"},
        )
    )

    response = client.post(
        "/api/extract",
        json={"url": "dQw4w9WgXcQ", "cookie_browser": "chrome"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "browser_cookies_unavailable"
    assert client.get("/api/info").json()["capabilities"]["browser_cookies"] is False
    assert called == []


def test_request_guards_and_security_headers() -> None:
    client = TestClient(
        web.create_app(
            mode="hosted",
            allowed_hosts=["testserver"],
            allowed_origins={"https://transcript.example"},
        )
    )
    job_payload = {"job_id": "x" * 24}

    wrong_type = client.post(
        "/api/summary/discard",
        content=json.dumps(job_payload),
        headers={"Content-Type": "text/plain"},
    )
    wrong_origin = client.post(
        "/api/summary/discard",
        json=job_payload,
        headers={"Origin": "https://evil.example"},
    )
    allowed_origin = client.post(
        "/api/summary/discard",
        json=job_payload,
        headers={"Origin": "https://transcript.example"},
    )
    wrong_host = client.get("/", headers={"Host": "evil.example"})
    index = client.get("/")
    script = client.get("/static/app.js")
    markdown_it = client.get("/static/markdown-it.min.js")
    tex_plugin = client.get("/static/mdit-plugin-tex.min.js")
    temml = client.get("/static/temml.min.js")
    temml_css = client.get("/static/Temml-Local.css")
    temml_font = client.get("/static/Temml.woff2")
    markdown_renderer = client.get("/static/markdown-renderer.js")
    hidden_python = client.get("/static/__init__.py")
    info = client.get("/api/info")

    assert wrong_type.status_code == 415
    assert wrong_origin.status_code == 403
    assert allowed_origin.status_code == 200
    assert wrong_host.status_code == 400
    assert script.status_code == 200
    assert markdown_it.status_code == 200
    assert tex_plugin.status_code == 200
    assert temml.status_code == 200
    assert temml_css.status_code == 200
    assert temml_font.status_code == 200
    assert temml_font.headers["content-type"] == "font/woff2"
    assert markdown_renderer.status_code == 200
    assert hidden_python.status_code == 404
    assert "default-src 'self'" in index.headers["content-security-policy"]
    assert index.headers["referrer-policy"] == "no-referrer"
    assert index.headers["x-content-type-options"] == "nosniff"
    assert index.headers["x-frame-options"] == "DENY"
    assert wrong_origin.headers["cache-control"] == "no-store"
    assert info.headers["cache-control"] == "no-store"


def test_oversized_and_unexpected_errors_are_safe(monkeypatch) -> None:
    client = TestClient(web.create_app(mode="local"), raise_server_exceptions=False)
    secret = "body-secret-value"
    oversized = json.dumps({"url": secret * 5_000})

    too_large = client.post(
        "/api/extract",
        content=oversized,
        headers={"Content-Type": "application/json"},
    )
    monkeypatch.setattr(
        web,
        "extract_transcript_only",
        lambda _options: (_ for _ in ()).throw(RuntimeError("private backend detail")),
    )
    unexpected = client.post("/api/extract", json={"url": "dQw4w9WgXcQ"})

    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "request_too_large"
    assert secret not in too_large.text
    assert too_large.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in too_large.headers["content-security-policy"]
    assert unexpected.status_code == 500
    assert unexpected.json()["error"]["code"] == "unexpected_error"
    assert "private backend detail" not in unexpected.text


def test_expired_summary_job_returns_gone(monkeypatch) -> None:
    now = [10.0]
    store = PendingSummaryStore(ttl_seconds=5, clock=lambda: now[0])
    monkeypatch.setattr(web, "extract_transcript_only", lambda _options: _result())
    client = TestClient(web.create_app(mode="local", store=store))
    job_id = _extract_job(client)
    now[0] = 16.0

    response = client.post(
        "/api/summarize",
        json={
            "job_id": job_id,
            "mode": "full",
            "summary_language": "auto",
            "gemini_model": "gemini-test",
        },
        headers={web.API_KEY_HEADER: "ephemeral-key"},
    )

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "summary_job_expired"
    assert "ephemeral-key" not in response.text


@pytest.mark.parametrize("option", ["--no-open", "--port"])
def test_web_parser_rejects_removed_runtime_options(option: str) -> None:
    with pytest.raises(SystemExit):
        web.build_parser().parse_args([option])


def test_web_main_uses_environment_controls(monkeypatch, capsys) -> None:
    import uvicorn

    observed = {}
    application = object()
    monkeypatch.setenv("PORT", "8123")
    monkeypatch.setenv("YTTEXT_OPEN_BROWSER", "0")

    def create_application(**kwargs):
        observed["create_app"] = kwargs
        return application

    monkeypatch.setattr(web, "create_app", create_application)
    monkeypatch.setattr(
        uvicorn,
        "run",
        lambda app, **kwargs: observed.update({"app": app, "uvicorn": kwargs}),
    )
    monkeypatch.setattr(
        web.webbrowser,
        "open",
        lambda _url: (_ for _ in ()).throw(AssertionError("browser should not open")),
    )

    assert web.main([]) == 0
    assert observed == {
        "create_app": {"mode": "local"},
        "app": application,
        "uvicorn": {"host": "127.0.0.1", "port": 8123, "workers": 1},
    }
    assert "http://127.0.0.1:8123" in capsys.readouterr().out


def test_configured_web_port_is_included_in_the_local_origin_allowlist(monkeypatch) -> None:
    monkeypatch.setenv("PORT", "8123")

    assert "http://127.0.0.1:8123" in web._configured_origins("local", ["127.0.0.1"])
