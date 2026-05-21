"""Generate clip candidates from a transcript via an LLM."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from pydantic import ValidationError

from .config import Config
from .models import Candidate, CandidateList, Word

CANDIDATES_FILENAME = "candidates.json"


class CandidatesError(RuntimeError):
    """Raised when the LLM step fails or returns unusable output."""


# --------------------------------------------------------------------- prompts

SYSTEM_ES = """\
Seleccionas momentos cortos y autoconclusivos de una transcripción que \
funcionarían bien como clips para redes sociales. Cada clip debe sostenerse \
por sí solo sin contexto previo, abrir con un gancho fuerte en la primera \
frase y cerrar en una idea completa. Prioriza ideas reveladoras, afirmaciones \
sorprendentes, historias vívidas o frases contundentes. Evita rellenos, cortes \
a mitad de pensamiento y preámbulos largos.

Un buen gancho cumple una función: es una afirmación que despierta curiosidad, \
una pregunta que el espectador quiere resolver, o una imagen vívida que lo \
ancla a la escena. No copies fórmulas; busca el efecto.

Devuelve ÚNICAMENTE JSON válido, sin prosa y sin bloques de código."""

SYSTEM_EN = """\
You select short, self-contained moments from a transcript that would work \
well as social-media clips. Each clip must stand on its own without prior \
context, open with a strong hook in its first sentence, and close on a \
complete idea. Prioritize revealing insights, surprising claims, vivid \
stories, or punchy lines. Avoid filler, mid-thought cuts, and long preambles.

A good hook does a job: a statement that sparks curiosity, a question the \
viewer wants resolved, or a vivid image that anchors them in the scene. \
Don't copy formulas; aim for the effect.

Return ONLY valid JSON, no prose and no code fences."""

_SCHEMA = """\
{
  "candidates": [
    {
      "title": "string, 3-8 words, descriptive",
      "hook": "string, the actual first sentence of the clip",
      "category": "insight | story | quote | reaction | explainer",
      "start": 12.34,
      "end": 47.80,
      "reason": "one sentence on why this works"
    }
  ]
}"""

_USER_ES = """\
Duración de cada clip: entre {min_d:.0f} y {max_d:.0f} segundos.
Propón como máximo {n} clips, ordenados por su aparición en el video.

Devuelve EXACTAMENTE este esquema JSON:
{schema}

Reglas:
- "category" debe ser uno de: insight, story, quote, reaction, explainer.
- "start" y "end" en segundos, como números decimales tomados de la transcripción.
- "title": 3 a 8 palabras, descriptivo.
- "hook": la primera frase real del clip.
- "reason": una sola frase sobre por qué funciona.
- Responde SOLO con el JSON, sin texto adicional ni bloques de código.

Transcripción (formato [inicio-fin] texto):
{transcript}"""

_USER_EN = """\
Each clip should last between {min_d:.0f} and {max_d:.0f} seconds.
Propose at most {n} clips, ordered by their appearance in the video.

Return EXACTLY this JSON schema:
{schema}

Rules:
- "category" must be one of: insight, story, quote, reaction, explainer.
- "start" and "end" in seconds, as decimal numbers taken from the transcript.
- "title": 3 to 8 words, descriptive.
- "hook": the actual first sentence of the clip.
- "reason": a single sentence on why it works.
- Reply with ONLY the JSON, no extra text and no code fences.

Transcript (format [start-end] text):
{transcript}"""


def _system_prompt(language: str) -> str:
    lang = language.lower()
    if lang.startswith("es"):
        return SYSTEM_ES
    if lang.startswith("en"):
        return SYSTEM_EN
    return f"{SYSTEM_EN}\n\nRespond in the language with ISO code '{language}'."


def _user_template(language: str) -> str:
    return _USER_ES if language.lower().startswith("es") else _USER_EN


# ----------------------------------------------------------------------- public


def generate_candidates(words: list[Word], config: Config) -> list[Candidate]:
    """Call the configured LLM and return validated, ordered clip candidates."""
    if not words:
        raise CandidatesError("Cannot generate candidates from an empty transcript.")

    language = config.transcript.language
    system = _system_prompt(language)
    user = _user_template(language).format(
        min_d=config.clips.min_duration,
        max_d=config.clips.max_duration,
        n=config.llm.max_candidates,
        schema=_SCHEMA,
        transcript=format_transcript(words),
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    raw = _complete(messages, config)
    try:
        candidates = _parse(raw)
    except (ValidationError, json.JSONDecodeError, ValueError) as first_error:
        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "user", "content": _retry_message(first_error)})
        raw = _complete(messages, config)
        try:
            candidates = _parse(raw)
        except (ValidationError, json.JSONDecodeError, ValueError) as second_error:
            raise CandidatesError(
                "The LLM returned malformed output twice.\n"
                f"--- parse error ---\n{second_error}\n"
                f"--- raw output ---\n{raw}"
            ) from second_error

    candidates.sort(key=lambda c: c.start)
    return candidates[: config.llm.max_candidates]


def format_transcript(words: list[Word], chunk_size: int = 10) -> str:
    """Format the transcript as one ``[start-end] text`` line per ~10 words."""
    lines: list[str] = []
    for i in range(0, len(words), chunk_size):
        chunk = words[i : i + chunk_size]
        text = " ".join(w.text for w in chunk)
        lines.append(f"[{chunk[0].start:.2f}-{chunk[-1].end:.2f}] {text}")
    return "\n".join(lines)


# ------------------------------------------------------------------------- cache


def get_candidates(
    words: list[Word], config: Config, cache_dir: Path, console
) -> list[Candidate]:
    """Return clip candidates, reusing a cached LLM response when still valid.

    The cache is invalidated automatically whenever the model, generation
    settings, or transcript change, so a re-run with the same inputs skips
    the LLM call entirely.
    """
    cache_path = cache_dir / CANDIDATES_FILENAME
    key = _cache_key(words, config)

    cached = _load_candidates_cache(cache_path, key)
    if cached is not None:
        console.print(f"  [dim]cache hit:[/] {CANDIDATES_FILENAME}")
        return cached

    console.print(f"  asking {config.llm.model}...")
    candidates = generate_candidates(words, config)
    _save_candidates_cache(cache_path, key, candidates)
    return candidates


def _cache_key(words: list[Word], config: Config) -> str:
    """A digest of every input that affects the LLM's candidate output."""
    parts = [
        config.llm.model,
        repr(config.llm.temperature),
        repr(config.llm.max_candidates),
        repr(config.clips.min_duration),
        repr(config.clips.max_duration),
        config.transcript.language,
        format_transcript(words),
    ]
    return hashlib.sha1("\x00".join(parts).encode("utf-8")).hexdigest()


def _load_candidates_cache(path: Path, key: str) -> list[Candidate] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("key") != key:
            return None  # stale — inputs changed since this was written
        return [Candidate.model_validate(c) for c in data["candidates"]]
    except (json.JSONDecodeError, KeyError, TypeError, ValidationError, OSError):
        return None


def _save_candidates_cache(
    path: Path, key: str, candidates: list[Candidate]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "key": key,
        "candidates": [c.model_dump(mode="json") for c in candidates],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )


# ---------------------------------------------------------------------- internal


def _complete(messages: list[dict], config: Config) -> str:
    """Run one LLM completion and return its text content."""
    import logging
    import os

    # Quiet litellm's noisy import-time and per-call logging.
    os.environ.setdefault("LITELLM_LOG", "ERROR")
    try:
        import litellm
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise CandidatesError(
            "litellm is not installed. Install it with: pip install litellm"
        ) from exc

    litellm.suppress_debug_info = True
    logging.getLogger("LiteLLM").setLevel(logging.ERROR)
    try:
        response = litellm.completion(
            model=config.llm.model,
            messages=messages,
            temperature=config.llm.temperature,
            max_tokens=4096,
        )
    except Exception as exc:  # litellm raises a wide range of provider errors
        raise CandidatesError(f"LLM request failed: {exc}") from exc

    content = response.choices[0].message.content
    if not content or not content.strip():
        raise CandidatesError("LLM returned an empty response.")
    return content


def _parse(raw: str) -> list[Candidate]:
    data = _extract_json(raw)
    if isinstance(data, list):
        data = {"candidates": data}
    return CandidateList.model_validate(data).candidates


def _extract_json(raw: str) -> object:
    """Parse JSON from a model response, tolerating fences and stray prose."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    end = max(text.rfind("}"), text.rfind("]"))
    if not starts or end <= min(starts):
        raise json.JSONDecodeError("no JSON value found in response", text, 0)
    return json.loads(text[min(starts) : end + 1])


def _retry_message(error: Exception) -> str:
    return (
        f"Your previous response could not be parsed: {error}\n"
        "Return ONLY the valid JSON described above — no prose, no code fences."
    )
