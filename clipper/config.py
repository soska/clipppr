"""Load and validate config.toml."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError


class ConfigError(RuntimeError):
    """Raised when a config file is missing or invalid."""


class LLMConfig(BaseModel):
    model: str = "anthropic/claude-haiku-4-5"
    temperature: float = 0.4
    max_candidates: int = Field(default=10, ge=1)


class ClipsConfig(BaseModel):
    min_duration: float = Field(default=15, ge=0)
    max_duration: float = Field(default=60, gt=0)
    lead_padding: float = Field(default=2.0, ge=0)
    trail_padding: float = Field(default=2.0, ge=0)
    output_dir: str = "./clips"


class TranscriptConfig(BaseModel):
    language: str = "es"
    whisper_model: str = "medium"
    whisper_compute: str = "int8"
    prefer_youtube_captions: bool = True


class FfmpegConfig(BaseModel):
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    crf: int = 20
    preset: str = "veryfast"
    loudnorm: bool = True


class Config(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    clips: ClipsConfig = Field(default_factory=ClipsConfig)
    transcript: TranscriptConfig = Field(default_factory=TranscriptConfig)
    ffmpeg: FfmpegConfig = Field(default_factory=FfmpegConfig)


CONFIG_FILENAME = "config.toml"
USER_CONFIG_PATH = Path.home() / ".config" / "clipper" / CONFIG_FILENAME


def resolve_config_path(explicit: str | None) -> Path | None:
    """Return the first config file that applies, or None for built-in defaults.

    Order: ``explicit`` flag -> ``./config.toml`` -> ``~/.config/clipper/config.toml``.
    """
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigError(f"Config file not found: {path}")
        return path
    local = Path.cwd() / CONFIG_FILENAME
    if local.is_file():
        return local
    if USER_CONFIG_PATH.is_file():
        return USER_CONFIG_PATH
    return None


def load_config(explicit: str | None = None) -> Config:
    """Load, merge with defaults, and validate the configuration."""
    path = resolve_config_path(explicit)
    if path is None:
        return Config()
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {path}: {exc}") from exc
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration in {path}:\n{exc}") from exc
