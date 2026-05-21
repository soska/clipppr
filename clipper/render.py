"""ffmpeg cut + sliced .srt writer."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .models import Candidate, Word


class RenderError(RuntimeError):
    """Raised when ffmpeg fails to render a clip."""


@dataclass(slots=True)
class ClipPlan:
    """Resolved timing for one clip."""

    cut_start: float  # padded start fed to ffmpeg
    cut_end: float  # padded end fed to ffmpeg
    clip_start: float  # snapped word-boundary start (SRT anchor)
    clip_end: float  # snapped word-boundary end

    @property
    def duration(self) -> float:
        return self.cut_end - self.cut_start


def snap_to_words(
    start: float, end: float, words: list[Word]
) -> tuple[float, float]:
    """Snap approximate ``start``/``end`` to the nearest transcript word boundary."""
    if not words:
        return start, end
    snapped_start = min((w.start for w in words), key=lambda t: abs(t - start))
    snapped_end = min((w.end for w in words), key=lambda t: abs(t - end))
    if snapped_end <= snapped_start:
        snapped_end = max(end, snapped_start + 0.1)
    return snapped_start, snapped_end


def plan_clip(
    candidate: Candidate,
    words: list[Word],
    config: Config,
    video_duration: float | None = None,
) -> ClipPlan:
    """Compute snapped, padded cut points for ``candidate``."""
    clip_start, clip_end = snap_to_words(candidate.start, candidate.end, words)
    cut_start = max(0.0, clip_start - config.clips.lead_padding)
    cut_end = clip_end + config.clips.trail_padding
    if video_duration is not None:
        cut_end = min(cut_end, video_duration)
        cut_start = min(cut_start, max(0.0, cut_end - 0.1))
    return ClipPlan(cut_start, cut_end, clip_start, clip_end)


def render_clip(
    input_path: Path,
    plan: ClipPlan,
    words: list[Word],
    mp4_path: Path,
    srt_path: Path,
    config: Config,
) -> int:
    """Render the clip's .mp4 and write its sibling .srt. Returns the cue count."""
    cue_count = write_srt(srt_path, words, plan.clip_start, plan.clip_end)
    _run_ffmpeg(input_path, plan, mp4_path, config)
    return cue_count


def probe_duration(path: Path) -> float | None:
    """Return the media duration in seconds via ffprobe, or None if unavailable."""
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return float(proc.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        return None


# ----------------------------------------------------------------------- ffmpeg


def _run_ffmpeg(
    input_path: Path, plan: ClipPlan, out_path: Path, config: Config
) -> None:
    fc = config.ffmpeg
    # -ss before -i for a fast seek; the re-encode handles the frame-accurate cut.
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{plan.cut_start:.3f}",
        "-to",
        f"{plan.cut_end:.3f}",
        "-i",
        str(input_path),
        "-c:v",
        fc.video_codec,
        "-preset",
        fc.preset,
        "-crf",
        str(fc.crf),
        "-c:a",
        fc.audio_codec,
        "-b:a",
        "192k",
    ]
    if fc.loudnorm:
        cmd += ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]
    cmd += ["-movflags", "+faststart", str(out_path)]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RenderError(
            f"ffmpeg failed for {out_path.name}:\n{proc.stderr[-1800:]}"
        )


# -------------------------------------------------------------------------- srt


def write_srt(
    path: Path, words: list[Word], clip_start: float, clip_end: float
) -> int:
    """Write a clip-relative .srt for the words in ``[clip_start, clip_end]``.

    Words are grouped into cues of up to ~7 words or ~2.5 s, and all
    timestamps are shifted so the first cue starts at ``00:00:00,000``.
    Returns the number of cues written.
    """
    sliced = [w for w in words if w.end > clip_start and w.start < clip_end]
    cues = _group_cues(sliced)

    blocks: list[str] = []
    for index, cue in enumerate(cues, 1):
        start = max(0.0, cue[0].start - clip_start)
        end = max(start + 0.1, cue[-1].end - clip_start)
        text = " ".join(w.text for w in cue).strip()
        blocks.append(
            f"{index}\n"
            f"{_format_srt_time(start)} --> {_format_srt_time(end)}\n"
            f"{text}\n"
        )
    path.write_text("\n".join(blocks), encoding="utf-8")
    return len(cues)


def _group_cues(
    words: list[Word], max_words: int = 7, max_duration: float = 2.5
) -> list[list[Word]]:
    """Group words into subtitle cues, splitting on word count or duration."""
    cues: list[list[Word]] = []
    current: list[Word] = []
    for word in words:
        if current:
            cue_start = current[0].start
            if len(current) >= max_words or (word.end - cue_start) > max_duration:
                cues.append(current)
                current = []
        current.append(word)
    if current:
        cues.append(current)
    return cues


def _format_srt_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
