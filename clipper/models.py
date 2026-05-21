"""Typed data models shared across the package."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


@dataclass(slots=True)
class Word:
    """A single transcript word with timing in seconds."""

    text: str
    start: float
    end: float


class Category(str, Enum):
    """Allowed clip categories."""

    insight = "insight"
    story = "story"
    quote = "quote"
    reaction = "reaction"
    explainer = "explainer"


class Candidate(BaseModel):
    """A clip candidate proposed by the LLM (also the LLM response schema)."""

    title: str = Field(min_length=1)
    hook: str = Field(min_length=1)
    category: Category
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    reason: str = ""

    @field_validator("category", mode="before")
    @classmethod
    def _normalize_category(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip().lower()
        return v

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, v: float, info) -> float:
        start = info.data.get("start")
        if start is not None and v <= start:
            raise ValueError("end must be greater than start")
        return v

    @property
    def duration(self) -> float:
        return self.end - self.start


class CandidateList(BaseModel):
    """Top-level envelope the LLM is asked to return."""

    candidates: list[Candidate]


@dataclass(slots=True)
class Clip:
    """A selected candidate plus its resolved output paths."""

    candidate: Candidate
    video_path: Path
    srt_path: Path
