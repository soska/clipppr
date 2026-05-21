"""Command-line entry point for clipppr."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

from rich.console import Console
from rich.table import Table

from . import __version__
from .candidates import (
    CandidatesError,
    get_candidates,
    load_candidates_file,
    save_candidates_file,
)
from .config import Config, ConfigError, load_config
from .models import Candidate, Word
from .naming import slugify, unique_slug
from .render import RenderError, ClipPlan, plan_clip, probe_duration, render_clip
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

    if (args.start is not None or args.end is not None) and args.adjust is None:
        console.print(
            "[bold red]Error:[/] --start/--end only apply together with --adjust."
        )
        return 2

    if shutil.which("ffmpeg") is None:
        console.print("[bold red]Error:[/] ffmpeg was not found on your PATH.")
        console.print(
            "Install it with [cyan]brew install ffmpeg[/] (macOS) "
            "or your system package manager."
        )
        return 1

    if args.adjust is not None:
        return _adjust(args, console)

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
        selected = list(range(len(candidates)))
    else:
        selected = select_candidates(candidates, console)
        if selected is None:
            console.print("[yellow]Quit — no clips rendered.[/]")
            return 0
        if not selected:
            console.print("[yellow]Nothing selected.[/]")
            return 0

    console.rule("[bold]Render")
    return _render_all(candidates, selected, source, words, config, console, args.dry_run)


# ------------------------------------------------------------------------- render


def _render_all(
    candidates: list[Candidate],
    selected: list[int],
    source: ResolvedSource,
    words: list[Word],
    config: Config,
    console: Console,
    dry_run: bool,
) -> int:
    out_dir = _video_out_dir(source, config)
    out_dir.mkdir(parents=True, exist_ok=True)
    console.print(f"  output folder: [cyan]{out_dir}[/]")
    paths = _clip_paths(candidates, out_dir)
    video_duration = probe_duration(source.video_path)

    for i in selected:
        mp4_path, srt_path = paths[i]
        plan = plan_clip(candidates[i], words, config, video_duration)
        span = _span(plan)
        if dry_run:
            console.print(f"  [dim][dry-run][/] {mp4_path.name} + .srt  [dim]{span}[/]")
            continue
        console.print(f"  rendering [cyan]{mp4_path.name}[/]  [dim]{span}[/]")
        cues = render_clip(
            source.video_path, plan, words, mp4_path, srt_path, config
        )
        console.print(
            f"  [green]done[/] {mp4_path.name} + {srt_path.name} "
            f"[dim]({cues} subtitle cues)[/]"
        )

    if dry_run:
        console.print("[yellow]Dry run — no files were written.[/]")
    else:
        console.print(f"[bold green]Finished.[/] Clips in {out_dir}")
    return 0


# ------------------------------------------------------------------------- adjust


def _adjust(args: argparse.Namespace, console: Console) -> int:
    """Correct a clip's edges, persist it to candidates.json, and re-render."""
    config = load_config(args.config)
    if args.out:
        config.clips.output_dir = args.out

    console.print(f"[bold]Source[/]  {args.source}")
    source = resolve_source(args.source, console)

    loaded = load_candidates_file(source.cache_dir)
    if loaded is None:
        raise CandidatesError(
            f"No saved clips for this video — run 'clipper {args.source}' first."
        )
    key, candidates = loaded

    targets = _adjust_targets(args.adjust, len(candidates))
    lead_delta = args.start or 0.0
    trail_delta = args.end or 0.0

    words = get_transcript(source, config, console)
    video_duration = probe_duration(source.video_path)
    out_dir = _video_out_dir(source, config)
    paths = _clip_paths(candidates, out_dir)

    console.rule("[bold]Adjust")
    if lead_delta == 0.0 and trail_delta == 0.0:
        _show_clip_table(candidates, words, config, video_duration, console)
        console.print(
            "[yellow]No change requested.[/] Pass [cyan]--start[/] and/or "
            "[cyan]--end[/], e.g. [cyan]--start +2 --end -1[/]."
        )
        return 0

    # Apply the corrections, showing each targeted clip before -> after.
    for i in targets:
        candidate = candidates[i]
        before = plan_clip(candidate, words, config, video_duration)
        candidate.lead_adjust += lead_delta
        candidate.trail_adjust += trail_delta
        after = plan_clip(candidate, words, config, video_duration)
        console.print(
            f"  clip {i + 1}  [cyan]{paths[i][0].name}[/]\n"
            f"    {_span(before)}  ->  {_span(after)}"
        )

    if args.dry_run:
        console.print(
            "[yellow]Dry run — candidates.json unchanged, nothing re-rendered.[/]"
        )
        return 0

    save_candidates_file(source.cache_dir, key, candidates)
    out_dir.mkdir(parents=True, exist_ok=True)

    console.rule("[bold]Re-render")
    for i in targets:
        mp4_path, srt_path = paths[i]
        plan = plan_clip(candidates[i], words, config, video_duration)
        console.print(f"  rendering [cyan]{mp4_path.name}[/]")
        cues = render_clip(
            source.video_path, plan, words, mp4_path, srt_path, config
        )
        console.print(
            f"  [green]done[/] {mp4_path.name} + {srt_path.name} "
            f"[dim]({cues} subtitle cues)[/]"
        )
    console.print(
        f"[bold green]Finished.[/] Updated {len(targets)} clip(s) in {out_dir}"
    )
    return 0


def _adjust_targets(spec: str, count: int) -> list[int]:
    """Resolve the --adjust value (a clip number or 'all') to 0-based indices."""
    if spec.strip().lower() == "all":
        return list(range(count))
    try:
        number = int(spec)
    except ValueError:
        raise CandidatesError(
            f"--adjust expects a clip number or 'all', got '{spec}'."
        ) from None
    if not 1 <= number <= count:
        raise CandidatesError(f"clip {number} is out of range (1-{count}).")
    return [number - 1]


def _show_clip_table(
    candidates: list[Candidate],
    words: list[Word],
    config: Config,
    video_duration: float | None,
    console: Console,
) -> None:
    """Print every clip with its current cut range and applied corrections."""
    slugs = _clip_slugs(candidates)
    table = Table(title="Clips")
    table.add_column("#", justify="right", style="bold cyan", no_wrap=True)
    table.add_column("Clip")
    table.add_column("Range", justify="right", style="dim", no_wrap=True)
    table.add_column("Dur", justify="right", no_wrap=True)
    table.add_column("Correction", no_wrap=True)
    for i, candidate in enumerate(candidates):
        plan = plan_clip(candidate, words, config, video_duration)
        corrections = []
        if candidate.lead_adjust:
            corrections.append(f"start {candidate.lead_adjust:+g}s")
        if candidate.trail_adjust:
            corrections.append(f"end {candidate.trail_adjust:+g}s")
        table.add_row(
            str(i + 1),
            slugs[i],
            f"{format_timestamp(plan.cut_start)}-{format_timestamp(plan.cut_end)}",
            f"{plan.duration:.1f}s",
            ", ".join(corrections) or "[dim]—[/]",
        )
    console.print(table)


# ------------------------------------------------------------------------ shared


def _video_out_dir(source: ResolvedSource, config: Config) -> Path:
    """The per-video output subfolder: ``<output_dir>/<video-title>/``."""
    base = Path(config.clips.output_dir).expanduser()
    folder = slugify(source.title) if source.title.strip() else source.video_id
    return base / folder


def _clip_slugs(candidates: list[Candidate]) -> list[str]:
    """A deterministic kebab-case basename per candidate (stable across runs)."""
    taken: set[str] = set()
    return [unique_slug(slugify(c.title), taken) for c in candidates]


def _clip_paths(
    candidates: list[Candidate], out_dir: Path
) -> list[tuple[Path, Path]]:
    """The (mp4, srt) path for each candidate. Stable, so --adjust hits the
    same file the original render produced."""
    return [
        (out_dir / f"{slug}.mp4", out_dir / f"{slug}.srt")
        for slug in _clip_slugs(candidates)
    ]


def _span(plan: ClipPlan) -> str:
    return (
        f"{format_timestamp(plan.cut_start)}-{format_timestamp(plan.cut_end)} "
        f"({plan.duration:.1f}s)"
    )


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
        "--adjust",
        metavar="CLIP",
        help="correct a clip's edges and re-render it: a clip number (from "
        "the table) or 'all'. Use with --start / --end.",
    )
    parser.add_argument(
        "--start",
        type=float,
        metavar="SEC",
        help="with --adjust: seconds to add (+) or trim (-) at the clip start",
    )
    parser.add_argument(
        "--end",
        type=float,
        metavar="SEC",
        help="with --adjust: seconds to add (+) or trim (-) at the clip end",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    return parser.parse_args(argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
