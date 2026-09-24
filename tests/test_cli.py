from pathlib import Path

import pytest

from yttext import __version__, cli, web
from yttext.cli import build_parser


def test_cli_accepts_gemini_model_override() -> None:
    args = build_parser().parse_args(["dQw4w9WgXcQ", "--gemini-model", "gemini-custom-model"])

    assert args.gemini_model == "gemini-custom-model"


def test_cli_reads_its_default_model_from_the_launch_environment(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_MODEL", "gemini-environment-model")

    args = build_parser().parse_args(["dQw4w9WgXcQ"])

    assert args.gemini_model == "gemini-environment-model"


def test_cli_reads_default_transcript_format_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("YTTEXT_TRANSCRIPT_FORMAT", "VTT")

    args = build_parser().parse_args(["dQw4w9WgXcQ"])

    assert args.format == "vtt"


def test_cli_format_option_overrides_environment(monkeypatch) -> None:
    monkeypatch.setenv("YTTEXT_TRANSCRIPT_FORMAT", "txt")

    args = build_parser().parse_args(["dQw4w9WgXcQ", "--format", "json"])

    assert args.format == "json"


def test_cli_rejects_invalid_transcript_format_environment(monkeypatch) -> None:
    monkeypatch.setenv("YTTEXT_TRANSCRIPT_FORMAT", "invalid")

    with pytest.raises(RuntimeError, match="YTTEXT_TRANSCRIPT_FORMAT"):
        build_parser()


def test_cli_reads_default_summary_language_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("YTTEXT_SUMMARY_LANG", "PT-br")

    args = build_parser().parse_args(["dQw4w9WgXcQ"])

    assert args.summary_lang == "pt-BR"


def test_cli_summary_language_option_overrides_environment(monkeypatch) -> None:
    monkeypatch.setenv("YTTEXT_SUMMARY_LANG", "ja")

    args = build_parser().parse_args(["dQw4w9WgXcQ", "--summary-lang", "en"])

    assert args.summary_lang == "en"


def test_cli_rejects_invalid_summary_language_environment(monkeypatch) -> None:
    monkeypatch.setenv("YTTEXT_SUMMARY_LANG", "custom")

    with pytest.raises(SystemExit):
        build_parser().parse_args(["dQw4w9WgXcQ"])


def test_cli_supports_output_directory() -> None:
    args = build_parser().parse_args(["dQw4w9WgXcQ", "--output-dir", "output"])

    assert args.output_dir == Path("output")


def test_cli_supports_transcript_formats_and_long_summary_modes() -> None:
    args = build_parser().parse_args(["dQw4w9WgXcQ", "--format", "vtt", "--long-summary", "full"])

    assert args.format == "vtt"
    assert args.long_summary == "full"


def test_cli_supports_short_options() -> None:
    args = build_parser().parse_args(
        [
            "dQw4w9WgXcQ",
            "-o",
            "output",
            "-f",
            "vtt",
            "-n",
            "-l",
            "ja",
            "-L",
            "full",
            "-c",
            "chrome",
            "-m",
            "gemini-custom-model",
        ]
    )

    assert args.output_dir == Path("output")
    assert args.format == "vtt"
    assert args.no_summary is True
    assert args.summary_lang == "ja"
    assert args.long_summary == "full"
    assert args.cookies_from_browser == "chrome"
    assert args.gemini_model == "gemini-custom-model"


def test_cli_rejects_abbreviated_long_options() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["dQw4w9WgXcQ", "--out", "output"])


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("auto", "auto"),
        ("zh-hans", "zh-Hans"),
        ("PT-br", "pt-BR"),
        ("it", "it"),
    ],
)
def test_cli_accepts_common_and_custom_summary_languages(value: str, expected: str) -> None:
    args = build_parser().parse_args(["dQw4w9WgXcQ", "--summary-lang", value])

    assert args.summary_lang == expected


def test_cli_rejects_invalid_summary_language() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["dQw4w9WgXcQ", "--summary-lang", "custom"])


@pytest.mark.parametrize("option", ["--list-gemini-models", "-M"])
def test_cli_lists_generate_content_models_without_a_video_url(option, monkeypatch, capsys) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "environment-secret")
    observed = []
    monkeypatch.setattr(
        cli,
        "list_gemini_models",
        lambda key: observed.append(key) or ["gemini-flash-latest", "gemini-pro-latest"],
    )

    assert cli.main([option]) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines() == ["gemini-flash-latest", "gemini-pro-latest"]
    assert captured.err == ""
    assert observed == ["environment-secret"]


def test_cli_model_listing_reports_a_missing_api_key(monkeypatch, capsys) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    assert cli.main(["--list-gemini-models"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "No Gemini API key is configured" in captured.err


def test_cli_dispatches_the_web_subcommand_without_parsing_it_as_a_url(monkeypatch) -> None:
    observed = []
    monkeypatch.setattr(web, "main", lambda argv: observed.append(argv) or 0)

    assert cli.main(["web"]) == 0
    assert observed == [[]]


@pytest.mark.parametrize("option", ["--version", "-V"])
def test_cli_reports_the_package_version(option, capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main([option])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"yttext {__version__}"


def test_cli_help_lists_all_extended_options() -> None:
    help_text = build_parser().format_help()
    expected_options = (
        "-V",
        "--version",
        "-o OUTPUT_DIR",
        "--output-dir OUTPUT_DIR",
        "-f {md,txt,json,srt,vtt}",
        "--format {md,txt,json,srt,vtt}",
        "-n",
        "--no-summary",
        "-l LANGUAGE",
        "--summary-lang LANGUAGE",
        "-L {skip,truncate,full}",
        "--long-summary {skip,truncate,full}",
        "-c {chrome,chromium,edge,firefox,safari,brave}",
        "--cookies-from-browser {chrome,chromium,edge,firefox,safari,brave}",
        "-m GEMINI_MODEL",
        "--gemini-model GEMINI_MODEL",
        "-M",
        "--list-gemini-models",
    )

    for option in expected_options:
        assert option in help_text
    assert "Run 'yttext web' to start the local browser app." in help_text
    assert "--caption-lang" not in help_text
    assert "--no-timestamps" not in help_text
