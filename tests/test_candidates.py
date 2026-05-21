import pytest

from clipper.candidates import (
    _cache_key,
    _extract_json,
    _load_candidates_cache,
    _parse,
    _save_candidates_cache,
    format_transcript,
)
from clipper.config import Config
from clipper.models import Candidate, Word


def _sample_words():
    return [Word(text=f"w{i}", start=float(i), end=float(i) + 0.5) for i in range(12)]


def _sample_candidates():
    return [Candidate(title="T", hook="H", category="quote", start=1.0, end=9.0)]


def test_extract_plain_json():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_strips_code_fences():
    raw = '```json\n{"a": 1}\n```'
    assert _extract_json(raw) == {"a": 1}


def test_extract_json_ignores_surrounding_prose():
    raw = 'Here you go:\n{"a": 1}\nHope that helps!'
    assert _extract_json(raw) == {"a": 1}


def test_extract_json_raises_when_absent():
    with pytest.raises(Exception):
        _extract_json("no json at all")


def test_parse_valid_candidates():
    raw = """{
      "candidates": [
        {"title": "Una idea fuerte", "hook": "Esto te va a sorprender.",
         "category": "insight", "start": 10.0, "end": 35.0,
         "reason": "Abre con curiosidad."}
      ]
    }"""
    candidates = _parse(raw)
    assert len(candidates) == 1
    assert candidates[0].title == "Una idea fuerte"
    assert candidates[0].category.value == "insight"
    assert candidates[0].duration == 25.0


def test_parse_accepts_bare_list():
    raw = '[{"title": "T", "hook": "H", "category": "QUOTE", "start": 1, "end": 9}]'
    candidates = _parse(raw)
    assert candidates[0].category.value == "quote"  # normalized from "QUOTE"


def test_parse_rejects_bad_timing():
    raw = '{"candidates": [{"title": "T", "hook": "H", "category": "story", "start": 9, "end": 9}]}'
    with pytest.raises(Exception):
        _parse(raw)


def test_format_transcript():
    words = [Word(text=f"w{i}", start=float(i), end=float(i) + 0.5) for i in range(25)]
    text = format_transcript(words, chunk_size=10)
    lines = text.splitlines()
    assert len(lines) == 3  # 25 words / 10 per line
    assert lines[0].startswith("[0.00-9.50] ")


def test_candidates_cache_roundtrip(tmp_path):
    key = _cache_key(_sample_words(), Config())
    path = tmp_path / "candidates.json"
    _save_candidates_cache(path, key, _sample_candidates())
    loaded = _load_candidates_cache(path, key)
    assert loaded is not None
    assert loaded[0].title == "T"
    assert loaded[0].category.value == "quote"


def test_candidates_cache_invalidated_by_config_change(tmp_path):
    words = _sample_words()
    key1 = _cache_key(words, Config())
    path = tmp_path / "candidates.json"
    _save_candidates_cache(path, key1, _sample_candidates())

    changed = Config()
    changed.llm.model = "openai/gpt-4o"
    key2 = _cache_key(words, changed)
    assert key1 != key2
    assert _load_candidates_cache(path, key2) is None  # stale -> miss
    assert _load_candidates_cache(path, key1) is not None  # original -> hit


def test_candidates_cache_invalidated_by_transcript_change():
    config = Config()
    key_a = _cache_key(_sample_words(), config)
    longer = _sample_words() + [Word(text="extra", start=99.0, end=99.5)]
    key_b = _cache_key(longer, config)
    assert key_a != key_b


def test_candidates_cache_missing_file_returns_none(tmp_path):
    assert _load_candidates_cache(tmp_path / "absent.json", "anykey") is None
