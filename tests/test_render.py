from pathlib import Path

from clipper.config import Config
from clipper.models import Candidate, Word
from clipper.render import (
    _format_srt_time,
    _group_cues,
    plan_clip,
    snap_to_words,
    write_srt,
)


def _words():
    # 12 words, one per second.
    return [Word(text=f"w{i}", start=float(i), end=float(i) + 0.9) for i in range(12)]


def test_snap_to_words_picks_nearest_boundary():
    words = _words()
    start, end = snap_to_words(2.4, 7.7, words)
    assert start == 2.0  # nearest word start
    assert end == 7.9  # nearest word end (w7 ends at 7.9)


def test_plan_clip_applies_padding_and_clamps():
    config = Config()
    config.clips.lead_padding = 1.0
    config.clips.trail_padding = 1.5
    cand = Candidate(
        title="t", hook="h", category="quote", start=3.0, end=8.0
    )
    plan = plan_clip(cand, _words(), config, video_duration=100.0)
    # start 3.0 snaps to w3's start (3.0); end 8.0 snaps to w7's end (7.9).
    assert plan.cut_start == 3.0 - 1.0
    assert round(plan.cut_end, 3) == round(7.9 + 1.5, 3)


def test_plan_clip_clamps_to_video_duration():
    config = Config()
    cand = Candidate(
        title="t", hook="h", category="quote", start=10.0, end=11.5
    )
    plan = plan_clip(cand, _words(), config, video_duration=11.0)
    assert plan.cut_end <= 11.0


def test_format_srt_time():
    assert _format_srt_time(0) == "00:00:00,000"
    assert _format_srt_time(1.5) == "00:00:01,500"
    assert _format_srt_time(3661.25) == "01:01:01,250"


def test_group_cues_splits_on_word_count():
    words = [Word(text="x", start=float(i) * 0.1, end=float(i) * 0.1 + 0.05)
             for i in range(20)]
    cues = _group_cues(words, max_words=7, max_duration=99)
    assert all(len(c) <= 7 for c in cues)
    assert sum(len(c) for c in cues) == 20


def test_write_srt_is_clip_relative(tmp_path: Path):
    words = _words()
    srt = tmp_path / "clip.srt"
    count = write_srt(srt, words, clip_start=3.0, clip_end=8.0)
    content = srt.read_text(encoding="utf-8")
    assert count >= 1
    # First cue starts at zero (timestamps shifted by clip_start).
    assert "00:00:00,000 -->" in content
    # Words outside the slice are excluded.
    assert "w0" not in content
    assert "w11" not in content
    assert "w4" in content
