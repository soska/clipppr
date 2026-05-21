# "Clipppr" Video Clip Extractor — Claude Code Brief

Name of app: clipppr

## Goal

Build a Python CLI that takes a video (YouTube URL or local file), uses an LLM to propose social-media-worthy clip candidates from its transcript, lets the user pick which to render in the terminal, and outputs each selected clip as an `.mp4` with a matching `.srt` of just that clip's subtitles.

## Stack

- Python 3.11+ (uses stdlib `tomllib`)
- `yt-dlp` — download + caption extraction
- `faster-whisper` — fallback transcription with word-level timestamps
- `litellm` — provider-agnostic LLM client (Anthropic, OpenAI, Gemini, Ollama, OpenRouter, etc.)
- `ffmpeg` — assumed installed on PATH; call via `subprocess`
- `rich` — pretty CLI tables and prompts
- `pydantic` — typed config + LLM response schema

## Architecture

Single package, one module per concern:

```
clipper/
  __init__.py
  config.py        # load + validate config.toml
  source.py        # resolve YouTube URL or local path → local video file
  transcript.py    # get word-level transcript (yt-dlp subs → faster-whisper fallback)
  candidates.py    # call LLM, parse + validate clip candidates
  select.py        # render candidates table, get user selection
  render.py        # ffmpeg cut + sliced .srt writer
  naming.py        # kebab-case slug from title
  cli.py           # argparse entry point
```

A clean dataclass/pydantic model for `Word { text, start, end }`, `Candidate { title, hook, category, start, end, reason }`, and `Clip` (selected candidate + output paths).

## Flow

1. **Resolve source.** If input is a URL, `yt-dlp` downloads to a working dir (cache by video id). If a local path, use it directly. Working dir defaults to `./.clipper-cache/<video-id>/`.
2. **Get transcript.**
   - First try `yt-dlp --write-auto-subs --write-subs --sub-format vtt --skip-download --sub-langs <lang>,<lang>-*` for YouTube (e.g. `es,es-419,es-ES,es-MX`); parse VTT into word-level `Word` list if possible (auto-subs include word timings; manual subs are line-level — flag this and fall back).
   - Otherwise run `faster-whisper` with `word_timestamps=True` and `language=<config.transcript.language>` on the audio. Model name from config; default `medium`, `compute_type="int8"`. `medium` is sufficient for Spanish — don't default to `large-v3`.
   - Persist the full transcript to `<cache>/transcript.json` so re-runs skip this step.
3. **Generate candidates.** Send the transcript to the configured LLM via `litellm.completion`. Expect strict JSON back, validate with pydantic, reject + retry once on parse failure.
4. **User selection.** Print a `rich` table (index, title, duration, hook, category, time range). Prompt for comma-separated indices, ranges (`1-3`), or `all`. Accept `q` to quit.
5. **Render.** For each selected candidate, run ffmpeg to cut + write a sliced `.srt`. Write to `./clips/` by default (configurable).

## Config (`config.toml`)

```toml
[llm]
# Any litellm model string. Keys read from env automatically:
# ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY, etc.
model = "anthropic/claude-haiku-4-5"
temperature = 0.4
max_candidates = 10

[clips]
min_duration = 15      # seconds
max_duration = 60
lead_padding = 0.2     # seconds added before clip start
trail_padding = 0.3    # seconds added after clip end
output_dir = "./clips"

[transcript]
language = "es"                   # ISO 639-1 code; passed to Whisper and yt-dlp
whisper_model = "medium"          # tiny | base | small | medium | large-v3
whisper_compute = "int8"          # int8 | int8_float16 | float16 | float32
prefer_youtube_captions = true

[ffmpeg]
video_codec = "libx264"
audio_codec = "aac"
crf = 20
preset = "veryfast"
loudnorm = true                   # apply -af loudnorm=I=-16:TP=-1.5:LRA=11
```

Resolution order: `--config <path>` flag → `./config.toml` → `~/.config/clipper/config.toml` → built-in defaults.

## LLM prompt (used inside `candidates.py`)

**Language.** Write the system prompt in the configured language (`config.transcript.language`). For Spanish, the whole system prompt is in Spanish — this makes the LLM follow instructions more reliably and returns `title`, `hook`, and `reason` in Spanish without an extra translation step. Keep one English prompt and one Spanish prompt as constants in the module; pick by config. If the language is something else, fall back to the English prompt with an instruction line saying "respond in <language>".

System prompt (Spanish version) is roughly:

> Seleccionas momentos cortos y autoconclusivos de una transcripción que funcionarían bien como clips para redes sociales. Cada clip debe sostenerse por sí solo sin contexto previo, abrir con un gancho fuerte en la primera frase y cerrar en una idea completa. Prioriza ideas reveladoras, afirmaciones sorprendentes, historias vívidas o frases contundentes. Evita rellenos, cortes a mitad de pensamiento y preámbulos largos.

Describe the *function* of a good hook (una afirmación, una pregunta, una imagen vívida) rather than giving English-flavored examples — what makes a clip pop in Spanish doesn't map 1:1 from English.

User message includes:

- min/max duration from config
- max candidates from config
- the transcript formatted as `[start-end] text` per sentence or per ~10-word chunk
- a strict JSON schema to follow:

```json
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
}
```

Instruct: return ONLY JSON, no prose, no code fences. Validate with pydantic; on failure, send one retry with the parse error appended.

Snap `start`/`end` to the nearest word boundary from the actual transcript before rendering (LLM times will be approximate).

## FFmpeg specifics

- **Re-encode, don't stream-copy** — exact cuts matter more than speed.
- Apply lead/trail padding from config, clamped to `[0, duration]`.
- Command shape:

  ```
  ffmpeg -ss <start> -to <end> -i <input> \
    -c:v libx264 -preset veryfast -crf 20 \
    -c:a aac -b:a 192k \
    [-af loudnorm=I=-16:TP=-1.5:LRA=11] \
    -movflags +faststart \
    <output>.mp4
  ```

- Put `-ss` **before** `-i` for fast seek, then a precise re-encode handles the frame-accurate cut.

## SRT per clip

For each rendered clip, write a sibling `.srt` with the same basename. Slice the full word-level transcript to `[start, end]`, group words into subtitle cues of ~5-8 words or ~2-3 seconds (whichever comes first), and rewrite all timestamps relative to the clip start (so the first cue begins at `00:00:00,000`). Standard SRT format.

## Naming

`naming.slugify(title)` → kebab-case, max ~60 chars, collisions resolved with `-2`, `-3`, etc.

For non-ASCII input (Spanish accents, ñ, etc.), normalize with `unicodedata.normalize("NFKD", ...)` and strip combining marks so `"año"` → `ano`, `"qué"` → `que`. Don't drop the word.

Examples:

- "Por qué la mayoría de startups muere en el año dos" → `por-que-la-mayoria-de-startups-muere-en-el-ano-dos.mp4` + `.srt`
- "El error que mató a mi primera empresa" → `el-error-que-mato-a-mi-primera-empresa.mp4`

## CLI

```
clipper <source> [--config PATH] [--out DIR] [--yes] [--dry-run]
```

- `<source>`: YouTube URL or path to local video
- `--yes`: skip selection, render all candidates
- `--dry-run`: do everything except the final ffmpeg render

Example runs:

```
clipper https://youtu.be/abc123
clipper ./interview.mp4 --out ./shorts
clipper ./talk.mkv --config ./my-config.toml --yes
```

## Error handling

- Missing ffmpeg on PATH → fail fast with a clear install hint.
- Missing API key for the configured provider → name the exact env var.
- yt-dlp / network failures → surface the error, don't retry silently.
- LLM returns malformed JSON twice → exit with the raw output for debugging.
- Empty candidate list → tell the user, exit 0.

## Out of scope (do not build)

- Aspect ratio conversion / vertical cropping
- Burned-in subtitles
- Web UI or TUI beyond the rich table + prompt
- Speaker diarization
- Thumbnail generation
- Anything async / parallel — sequential is fine

## Acceptance criteria

1. `clipper <youtube-url>` on a video with auto-captions produces candidates without invoking Whisper.
2. `clipper ./local.mp4` produces candidates by running faster-whisper.
3. Selected clips render as playable `.mp4` files with frame-accurate cuts and matching, correctly-timed `.srt` files.
4. Switching `llm.model` in `config.toml` between an Anthropic, OpenAI, and Ollama model works with no code changes.
5. Re-running on the same source skips download and transcription via the cache.
6. Filenames are kebab-case and collision-safe, with accented characters normalized.
7. With `transcript.language = "es"`, candidate titles, hooks, and reasons come back in Spanish; SRT cues are Spanish text.

## Suggested build order

1. `config.py` + `naming.py` (pure, easy to test)
2. `source.py` + `transcript.py` with the yt-dlp path only
3. faster-whisper fallback
4. `candidates.py` against one provider, then verify provider-swap works
5. `select.py`
6. `render.py` (ffmpeg + srt slicing)
7. `cli.py` wiring everything together
