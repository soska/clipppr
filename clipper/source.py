"""Resolve a YouTube URL or local path into a usable local video file."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .naming import slugify

CACHE_DIR_NAME = ".clipper-cache"
VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi")

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


class SourceError(RuntimeError):
    """Raised when a source cannot be resolved or downloaded."""


@dataclass(slots=True)
class ResolvedSource:
    """A resolved input: a local video file plus its cache location."""

    video_path: Path
    cache_dir: Path
    video_id: str
    is_youtube: bool
    url: str | None
    title: str


def is_url(source: str) -> bool:
    """Return True if ``source`` looks like an http(s) URL."""
    return bool(_URL_RE.match(source.strip()))


def resolve_source(source: str, console) -> ResolvedSource:
    """Resolve ``source`` (URL or local path) to a :class:`ResolvedSource`."""
    if is_url(source):
        return _resolve_url(source.strip(), console)
    return _resolve_local(source)


# --------------------------------------------------------------------------- local


def _resolve_local(source: str) -> ResolvedSource:
    path = Path(source).expanduser()
    if not path.is_file():
        raise SourceError(f"Local file not found: {path}")
    path = path.resolve()
    video_id = _local_id(path)
    cache_dir = _cache_root() / video_id
    cache_dir.mkdir(parents=True, exist_ok=True)
    return ResolvedSource(
        video_path=path,
        cache_dir=cache_dir,
        video_id=video_id,
        is_youtube=False,
        url=None,
        title=path.stem,
    )


def _local_id(path: Path) -> str:
    """A stable per-file cache id: slugified stem plus a short path hash."""
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:8]
    stem = slugify(path.stem)[:32] or "video"
    return f"{stem}-{digest}"


# ----------------------------------------------------------------------------- url


def _resolve_url(url: str, console) -> ResolvedSource:
    if shutil.which("yt-dlp") is None:
        raise SourceError(
            "yt-dlp not found on PATH. Install it with: pip install yt-dlp"
        )
    video_id, title = _metadata_for_url(url)
    cache_dir = _cache_root() / video_id
    cache_dir.mkdir(parents=True, exist_ok=True)

    cached = _find_video(cache_dir, video_id)
    if cached is not None:
        console.print(f"  [dim]cache hit:[/] {cached.name}")
        return ResolvedSource(cached, cache_dir, video_id, True, url, title)

    console.print("  downloading with yt-dlp...")
    video_path = _download(url, cache_dir, video_id)
    return ResolvedSource(video_path, cache_dir, video_id, True, url, title)


def _metadata_for_url(url: str) -> tuple[str, str]:
    """Return (video_id, title) from yt-dlp metadata (no download)."""
    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--no-playlist",
        "--skip-download",
        "--print",
        "%(id)s",
        "--print",
        "%(title)s",
        url,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise SourceError(
            f"yt-dlp could not read {url}:\n{exc.stderr.strip()}"
        ) from exc
    # The two --print flags emit one line each, in order: id, then title.
    lines = proc.stdout.splitlines()
    video_id = lines[0].strip() if lines else ""
    title = lines[1].strip() if len(lines) > 1 else ""
    if not video_id:
        raise SourceError(f"Could not determine a video id for {url}")
    return video_id, title


def _download(url: str, cache_dir: Path, video_id: str) -> Path:
    """Download the video into ``cache_dir`` as ``<video_id>.<ext>``."""
    out_template = str(cache_dir / f"{video_id}.%(ext)s")
    cmd = [
        "yt-dlp",
        "-f",
        "bv*+ba/b",
        "--merge-output-format",
        "mp4",
        "--no-playlist",
        "-o",
        out_template,
        url,
    ]
    # Stream yt-dlp output so the user sees download progress.
    try:
        proc = subprocess.run(cmd)
    except FileNotFoundError as exc:
        raise SourceError("yt-dlp not found on PATH.") from exc
    if proc.returncode != 0:
        raise SourceError("yt-dlp download failed (see output above).")
    found = _find_video(cache_dir, video_id)
    if found is None:
        raise SourceError(
            "yt-dlp finished but no video file was found in the cache dir."
        )
    return found


# ------------------------------------------------------------------------- shared


def _cache_root() -> Path:
    return Path.cwd() / CACHE_DIR_NAME


def _find_video(cache_dir: Path, video_id: str) -> Path | None:
    for ext in VIDEO_EXTS:
        path = cache_dir / f"{video_id}{ext}"
        if path.is_file():
            return path
    return None
