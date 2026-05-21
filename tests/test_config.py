import pytest

from clipper.config import Config, ConfigError, load_config

SAMPLE = """\
[llm]
model = "openai/gpt-4o-mini"
temperature = 0.7

[clips]
min_duration = 20
output_dir = "./out"

[transcript]
language = "en"
"""


def test_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("clipper.config.USER_CONFIG_PATH", tmp_path / "missing.toml")
    config = load_config()
    assert isinstance(config, Config)
    assert config.llm.model == "anthropic/claude-haiku-4-5"
    assert config.clips.min_duration == 15
    assert config.clips.lead_padding == 2.0
    assert config.clips.trail_padding == 2.0
    assert config.transcript.language == "es"


def test_load_merges_with_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE, encoding="utf-8")
    config = load_config(str(path))
    # Overridden values.
    assert config.llm.model == "openai/gpt-4o-mini"
    assert config.llm.temperature == 0.7
    assert config.clips.min_duration == 20
    assert config.transcript.language == "en"
    # Untouched values keep their defaults.
    assert config.llm.max_candidates == 10
    assert config.clips.max_duration == 60
    assert config.ffmpeg.crf == 20


def test_missing_explicit_config_raises():
    with pytest.raises(ConfigError):
        load_config("/nonexistent/path/config.toml")


def test_invalid_toml_raises(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text("this is = = not toml", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(str(path))
