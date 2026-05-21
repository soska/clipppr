"""Word-level transcript: YouTube captions first, faster-whisper fallback."""

from __future__ import annotations

import html
import json
import re
import subprocess
from pathlib import Path

from .config import Config
from .models import Word
from .source import ResolvedSource

TRANSCRIPT_FILENAME = "transcript.json"
AUDIO_FILENAME = "audio.wav"


class TranscriptError(RuntimeError):
    """Raised when no usable transcript can be produced."""


def get_transcript(source: ResolvedSource, config: Config, console) -> list[Word]:
    """Return a word-level transcript, using the cache when available.

    Tries YouTube captions with word-level timing first, then falls back to
    faster-whisper. The result is persisted so re-runs skip this step.
    """
    cache_path = source.cache_dir / TRANSCRIPT_FILENAME
    cached = _load_cache(cache_path)
    if cached:
        console.print(f"  [dim]cache hit:[/] {TRANSCRIPT_FILENAME}")
        return cached

    words: list[Word] | None = None
    if source.is_youtube and config.transcript.prefer_youtube_captions:
        words = _try_youtube_captions(source, config, console)

    if words is None:
        words = _whisper_transcribe(source, config, console)

    _save_cache(cache_path, words)
    return words


# ------------------------------------------------------------------- youtube subs


def _try_youtube_captions(
    source: ResolvedSource, config: Config, console
) -> list[Word] | None:
    """Fetch and parse YouTube captions; return None to signal a fallback."""
    lang = config.transcript.language
    subs_dir = source.cache_dir / "subs"
    subs_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "yt-dlp",
        "--write-auto-subs",
        "--write-subs",
        "--sub-format",
        "vtt",
        "--sub-langs",
        f"{lang},{lang}-*",
        "--skip-download",
        # Without this, a "watch?v=...&list=..." URL pulls captions for the
        # whole playlist instead of just the requested video.
        "--no-playlist",
        "--no-warnings",
        "-o",
        str(subs_dir / "%(id)s.%(ext)s"),
        source.url,
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        console.print(
            f"  [yellow]caption fetch failed:[/] {exc.stderr.strip().splitlines()[-1] if exc.stderr.strip() else exc}"
        )
        return None

    vtt_files = sorted(subs_dir.glob("*.vtt"))
    if not vtt_files:
        console.print("  [dim]no captions available; using Whisper[/]")
        return None

    vtt_path = _pick_vtt(vtt_files, lang, source.video_id)
    words = parse_vtt_words(vtt_path.read_text(encoding="utf-8", errors="replace"))
    if not words:
        # Manual subs are line-level only -> no word timing to snap against.
        console.print(
            "  [yellow]captions have no word-level timing; falling back to Whisper[/]"
        )
        return None
    console.print(f"  using YouTube captions ({vtt_path.name})")
    return words


def _pick_vtt(vtt_files: list[Path], lang: str, video_id: str) -> Path:
    """Choose the best VTT file for the target video.

    Files belonging to ``video_id`` win over any others (a guard against a
    playlist URL pulling in sibling videos' captions), and within those a
    file whose language matches ``lang`` is preferred.
    """
    own = [p for p in vtt_files if p.name.startswith(f"{video_id}.")]
    candidates = own or vtt_files
    for path in candidates:
        # filenames look like "<id>.<lang>.vtt"
        parts = path.name.split(".")
        if len(parts) >= 3 and parts[-2].lower().startswith(lang.lower()):
            return path
    return candidates[0]


# ------------------------------------------------------------------- VTT parsing

_CUE_HEADER_RE = re.compile(
    r"(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})"
)
_INLINE_TS_RE = re.compile(r"<(\d{2}:\d{2}:\d{2}[.,]\d{3})>")
_C_TAG_RE = re.compile(r"</?c[^>]*>", re.IGNORECASE)


def parse_vtt_words(vtt_text: str) -> list[Word]:
    """Parse a WebVTT file into word-level :class:`Word` objects.

    Only cues that carry inline ``<timestamp>`` tags (YouTube auto-captions)
    yield words. A file with no inline timestamps is line-level only and
    returns an empty list, signalling that a Whisper fallback is needed.
    """
    raw: list[tuple[float, str, float]] = []  # (start, text, cue_end)
    for cue_start, cue_end, payload in _iter_cues(vtt_text):
        for line in payload:
            if not _INLINE_TS_RE.search(line):
                # Settled / rolling-duplicate line — skip it.
                continue
            for start, text in _tokens_from_line(line, cue_start):
                raw.append((start, text, cue_end))
    return _finalize_words(raw)


def _iter_cues(vtt_text: str):
    """Yield (start, end, payload_lines) for each timed cue."""
    text = vtt_text.replace("\r\n", "\n").replace("\r", "\n")
    # Cues are separated by truly empty lines. Split on "\n\n+" only --
    # YouTube auto-captions put a space-only placeholder line inside cues,
    # and a looser "\n\s*\n" would wrongly cut the cue there.
    for block in re.split(r"\n\n+", text):
        header: re.Match | None = None
        payload: list[str] = []
        for line in block.split("\n"):
            match = _CUE_HEADER_RE.search(line)
            if match and header is None:
                header = match
            elif header is not None:
                payload.append(line)
        if header is None:
            continue
        yield _parse_time(header.group(1)), _parse_time(header.group(2)), payload


def _tokens_from_line(line: str, cue_start: float) -> list[tuple[float, str]]:
    """Split a tagged caption line into (start_time, word) pairs."""
    clean = _C_TAG_RE.sub("", line)
    parts = _INLINE_TS_RE.split(clean)
    # parts == [text0, ts1, text1, ts2, text2, ...]
    chunks: list[tuple[float, str]] = [(cue_start, parts[0])]
    for i in range(1, len(parts), 2):
        ts = _parse_time(parts[i])
        chunk = parts[i + 1] if i + 1 < len(parts) else ""
        chunks.append((ts, chunk))

    tokens: list[tuple[float, str]] = []
    for start, chunk in chunks:
        for word in chunk.split():
            cleaned = html.unescape(word).strip()
            if cleaned:
                tokens.append((start, cleaned))
    return tokens


def _finalize_words(raw: list[tuple[float, str, float]]) -> list[Word]:
    """Assign end times and drop adjacent duplicates."""
    words: list[Word] = []
    for idx, (start, text, cue_end) in enumerate(raw):
        if idx + 1 < len(raw):
            nxt = raw[idx + 1][0]
            end = nxt if nxt > start else cue_end
        else:
            end = cue_end
        if end <= start:
            end = start + 0.30
        if end - start > 5.0:  # guard against a runaway gap
            end = start + 0.50
        if (
            words
            and words[-1].text == text
            and abs(words[-1].start - start) < 0.05
        ):
            continue
        words.append(Word(text=text, start=start, end=end))
    return words


def _parse_time(stamp: str) -> float:
    stamp = stamp.replace(",", ".")
    hours, minutes, rest = stamp.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


# ----------------------------------------------------------------------- whisper


def _whisper_transcribe(
    source: ResolvedSource, config: Config, console
) -> list[Word]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise TranscriptError(
            "faster-whisper is not installed. Install it with: pip install faster-whisper"
        ) from exc

    audio_path = _extract_audio(source)
    model_name = config.transcript.whisper_model
    console.print(
        f"  transcribing with faster-whisper ([cyan]{model_name}[/]); "
        "this can take several minutes..."
    )
    model = WhisperModel(
        model_name,
        device="auto",
        compute_type=config.transcript.whisper_compute,
    )
    segments, _info = model.transcribe(
        str(audio_path),
        language=config.transcript.language,
        word_timestamps=True,
    )

    words: list[Word] = []
    for segment in segments:
        for word in segment.words or []:
            text = word.word.strip()
            if text:
                words.append(
                    Word(text=text, start=float(word.start), end=float(word.end))
                )
    if not words:
        raise TranscriptError("Whisper produced an empty transcript.")
    return words


def _extract_audio(source: ResolvedSource) -> Path:
    """Extract 16 kHz mono WAV audio for Whisper, cached in the working dir."""
    audio_path = source.cache_dir / AUDIO_FILENAME
    if audio_path.is_file():
        return audio_path
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source.video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "wav",
        str(audio_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise TranscriptError(
            f"ffmpeg failed to extract audio:\n{proc.stderr[-1500:]}"
        )
    return audio_path


# ------------------------------------------------------------------------- cache


def _save_cache(path: Path, words: list[Word]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [{"text": w.text, "start": w.start, "end": w.end} for w in words]
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def _load_cache(path: Path) -> list[Word]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [
            Word(text=d["text"], start=float(d["start"]), end=float(d["end"]))
            for d in data
        ]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError):
        return []
