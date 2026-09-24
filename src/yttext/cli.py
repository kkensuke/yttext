from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .errors import AppError
from .gemini import DEFAULT_GEMINI_MODEL, list_gemini_models
from .service import ExtractionOptions, extract_transcript
from .summary_languages import COMMON_SUMMARY_LANGUAGES, normalize_summary_language

WEB_COMMAND = "web"
WEB_DEPENDENCIES = frozenset({"fastapi", "pydantic", "starlette", "uvicorn"})


def _summary_language_argument(value: str) -> str:
    try:
        return normalize_summary_language(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    default_gemini_model = os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL
    default_summary_language = os.getenv("YTTEXT_SUMMARY_LANG", "").strip() or "auto"
    default_transcript_format = os.getenv("YTTEXT_TRANSCRIPT_FORMAT", "").strip().lower() or "md"
    if default_transcript_format not in {"md", "txt", "json", "srt", "vtt"}:
        raise RuntimeError("YTTEXT_TRANSCRIPT_FORMAT must be one of: md, txt, json, srt, vtt.")
    parser = argparse.ArgumentParser(
        prog="yttext",
        description="Extract original-language YouTube captions in multiple formats.",
        epilog="Run 'yttext web' to start the local browser app.",
        allow_abbrev=False,
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "url",
        nargs="?",
        help="YouTube URL or 11-character video ID; use 'web' to start the browser app",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        help="Directory for transcript and summary files",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=("md", "txt", "json", "srt", "vtt"),
        default=default_transcript_format,
        help="Transcript output format (default: YTTEXT_TRANSCRIPT_FORMAT or md)",
    )
    parser.add_argument(
        "-n",
        "--no-summary",
        action="store_true",
        help="Skip Gemini summarization",
    )
    parser.add_argument(
        "-l",
        "--summary-lang",
        type=_summary_language_argument,
        default=default_summary_language,
        metavar="LANGUAGE",
        help=(
            "Summary language: auto uses the transcript's primary language; "
            f"common tags are {', '.join(item.code for item in COMMON_SUMMARY_LANGUAGES)}; "
            "other valid BCP 47 tags are accepted (default: YTTEXT_SUMMARY_LANG or auto)"
        ),
    )
    parser.add_argument(
        "-L",
        "--long-summary",
        choices=("skip", "truncate", "full"),
        default="skip",
        help=(
            "For captions over 50,000 characters: skip, summarize a prefix, "
            "or send the full transcript (default: skip)"
        ),
    )
    parser.add_argument(
        "-c",
        "--cookies-from-browser",
        choices=("chrome", "chromium", "edge", "firefox", "safari", "brave"),
        help="Browser cookies for unlisted or age-restricted videos",
    )
    parser.add_argument(
        "-m",
        "--gemini-model",
        default=default_gemini_model,
        help=f"Gemini model ID (default: {default_gemini_model})",
    )
    parser.add_argument(
        "-M",
        "--list-gemini-models",
        action="store_true",
        help="List Gemini models that support generateContent, then exit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == WEB_COMMAND:
        return _run_web(arguments[1:])

    parser = build_parser()
    args = parser.parse_args(arguments)

    if args.list_gemini_models:
        try:
            models = list_gemini_models(os.getenv("GEMINI_API_KEY", "").strip())
        except AppError as exc:
            _print_app_error(exc)
            return 1
        for model in models:
            print(model)
        return 0

    if not args.url:
        parser.error("url is required unless -M/--list-gemini-models is used; see 'yttext --help'")

    options = ExtractionOptions(
        url=args.url,
        transcript_format=args.format,
        generate_summary=not args.no_summary,
        summary_language=args.summary_lang,
        long_summary_mode=args.long_summary,
        cookie_browser=args.cookies_from_browser,
        api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_model=args.gemini_model,
    )

    try:
        result = extract_transcript(
            options,
            progress=lambda percent, message: print(f"[{percent:3d}%] {message}", flush=True),
        )
    except AppError as exc:
        _print_app_error(exc)
        return 1

    output_dir = args.output_dir or Path.cwd()
    transcript_path = output_dir / result.transcript.filename
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(result.transcript.content, encoding="utf-8")
    print(f"Transcript saved to: {transcript_path}")

    if result.summary:
        summary_path = output_dir / result.summary.filename
        summary_path.write_text(result.summary.content, encoding="utf-8")
        print(f"Summary saved to: {summary_path}")
    if result.warning:
        print(f"Note: {result.warning}", file=sys.stderr)
    return 0


def _run_web(argv: list[str]) -> int:
    try:
        from .web import main as web_main
    except ModuleNotFoundError as exc:
        if exc.name not in WEB_DEPENDENCIES:
            raise
        print("Error: The browser app dependencies are not installed.", file=sys.stderr)
        print("Hint: Install the project with its 'web' extra and try again.", file=sys.stderr)
        return 2
    return web_main(argv)


def _print_app_error(error: AppError) -> None:
    print(f"Error: {error.message}", file=sys.stderr)
    print(f"Hint: {error.hint}", file=sys.stderr)
