"""Command-line entry point for clipppr."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

from rich.console import Console

from . import __version__
from .candidates import CandidatesError, get_candidates
from .config import Config, ConfigError, load_config
from .naming import slugify, unique_slug
from .render import RenderError, plan_clip, probe_duration, render_clip
from .select import format_timestamp, select_candidates, show_candidates
from .source import CACHE_DIR_NAME, ResolvedSource, SourceError, resolve_source
from .transcript import TranscriptError, get_transcript

# litellm provider prefix -> required env var.
_PROVIDER_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "cohere": "COHERE_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "xai": "XAI_API_KEY",
}
# Providers that need no API key.
_LOCAL_PROVIDERS = {"ollama", "ollama_chat"}


def main(argv: list[str] | None = None) -> int:
    """Program entry point. Returns a process exit code."""
    args = _parse_args(argv)
    console = Console()
    try:
        return _run(args, console)
    except (
        ConfigError,
        SourceError,
        TranscriptError,
        CandidatesError,
        RenderError,
    ) as exc:
        console.print(f"[bold red]Error:[/] {exc}")
        return 1
    except KeyboardInterrupt:
        console.print("\n[yellow]Aborted.[/]")
        return 130


def _run(args: argparse.Namespace, console: Console) -> int:
    if args.clear_cache:
        _clear_cache(console)
        if not args.source:
            return 0

    if not args.source:
        console.print(
            "[bold red]Error:[/] a source (YouTube URL or video file path) "
            "is required."
        )
        return 2

    if shutil.which("ffmpeg") is None:
        console.print("[bold red]Error:[/] ffmpeg was not found on your PATH.")
        console.print(
            "Install it with [cyan]brew install ffmpeg[/] (macOS) "
            "or your system package manager."
        )
        return 1

    config = load_config(args.config)
    if args.out:
        config.clips.output_dir = args.out
    _check_api_key(config)

    console.print(f"[bold]Source[/]  {args.source}")
    source = resolve_source(args.source, console)

    console.rule("[bold]Transcript")
    words = get_transcript(source, config, console)
    console.print(f"  {len(words)} words")

    console.rule("[bold]Candidates")
    candidates = get_candidates(words, config, source.cache_dir, console)
    if not candidates:
        console.print("[yellow]The LLM proposed no clip candidates. Nothing to do.[/]")
        return 0
    console.print(f"  {len(candidates)} candidate(s)")

    if args.yes:
        show_candidates(candidates, console)
        selected = candidates
    else:
        selected = select_candidates(candidates, console)
        if selected is None:
            console.print("[yellow]Quit — no clips rendered.[/]")
            return 0
        if not selected:
            console.print("[yellow]Nothing selected.[/]")
            return 0

    console.rule("[bold]Render")
    return _render_all(selected, source, words, config, console, args.dry_run)


def _render_all(
    selected: list,
    source: ResolvedSource,
    words: list,
    config: Config,
    console: Console,
    dry_run: bool,
) -> int:
    # Each video gets its own subfolder inside the output directory, named
    # after the video title (the filename for a local file).
    base_dir = Path(config.clips.output_dir).expanduser()
    folder = slugify(source.title) if source.title.strip() else source.video_id
    out_dir = base_dir / folder
    out_dir.mkdir(parents=True, exist_ok=True)
    console.print(f"  output folder: [cyan]{out_dir}[/]")
    taken = _existing_slugs(out_dir)
    video_duration = probe_duration(source.video_path)

    for candidate in selected:
        slug = unique_slug(slugify(candidate.title), taken)
        mp4_path = out_dir / f"{slug}.mp4"
        srt_path = out_dir / f"{slug}.srt"
        plan = plan_clip(candidate, words, config, video_duration)
        span = (
            f"{format_timestamp(plan.cut_start)}-{format_timestamp(plan.cut_end)} "
            f"({plan.duration:.1f}s)"
        )
        if dry_run:
            console.print(f"  [dim][dry-run][/] {slug}.mp4 + .srt  [dim]{span}[/]")
            continue
        console.print(f"  rendering [cyan]{slug}.mp4[/]  [dim]{span}[/]")
        cues = render_clip(
            source.video_path, plan, words, mp4_path, srt_path, config
        )
        console.print(f"  [green]done[/] {mp4_path.name} + {srt_path.name} "
                      f"[dim]({cues} subtitle cues)[/]")

    if dry_run:
        console.print("[yellow]Dry run — no files were written.[/]")
    else:
        console.print(f"[bold green]Finished.[/] Clips in {out_dir}")
    return 0


def _existing_slugs(out_dir: Path) -> set[str]:
    """Collect basenames already in the output dir so new clips never clobber."""
    slugs: set[str] = set()
    if out_dir.is_dir():
        for path in out_dir.iterdir():
            if path.suffix.lower() in (".mp4", ".srt"):
                slugs.add(path.stem)
    return slugs


def _clear_cache(console: Console) -> None:
    """Delete the ./.clipper-cache directory (downloads, transcripts, candidates)."""
    cache_dir = Path.cwd() / CACHE_DIR_NAME
    if not cache_dir.is_dir():
        console.print(f"[dim]No cache to clear — {cache_dir} does not exist.[/]")
        return
    freed = sum(f.stat().st_size for f in cache_dir.rglob("*") if f.is_file())
    shutil.rmtree(cache_dir)
    console.print(
        f"[green]Cleared cache:[/] {cache_dir} "
        f"[dim]({freed / 1_000_000:.1f} MB freed)[/]"
    )


def _check_api_key(config: Config) -> None:
    """Fail fast with the exact env var name if a needed API key is missing."""
    model = config.llm.model
    provider = model.split("/", 1)[0].lower() if "/" in model else ""

    if provider in _LOCAL_PROVIDERS:
        return

    env_var = _PROVIDER_KEYS.get(provider)
    if env_var is None:
        if "/" in model:
            return  # unknown provider — let litellm decide
        env_var = "OPENAI_API_KEY"  # bare model name defaults to OpenAI

    if not os.environ.get(env_var):
        raise CandidatesError(
            f"Missing API key for model '{model}'. "
            f"Set the {env_var} environment variable."
        )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="clipper",
        description="Extract social-media-worthy video clips proposed by an LLM.",
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="YouTube URL or path to a local video file",
    )
    parser.add_argument(
        "--config", metavar="PATH", help="path to a config.toml file"
    )
    parser.add_argument(
        "--out", metavar="DIR", help="output directory (overrides config)"
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the selection prompt and render all candidates",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="do everything except writing clip files",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="delete the ./.clipper-cache directory; with no source, exit "
        "after clearing, otherwise run fresh",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    return parser.parse_args(argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
