# clipppr

A Python CLI that turns a video (YouTube URL or local file) into short,
social-media-ready clips. It transcribes the video, asks an LLM to propose
clip candidates, lets you pick which to render in the terminal, and writes each
selected clip as an `.mp4` with a matching `.srt`.

## Requirements

- Python 3.11+
- `ffmpeg` on your `PATH`
- An API key for whichever LLM provider you configure (or a local Ollama)

## Install

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Usage

```bash
clipper <source> [--config PATH] [--out DIR] [--yes] [--dry-run] [--clear-cache]
clipper <source> --adjust <CLIP> [--start SEC] [--end SEC]
```

- `<source>` — a YouTube URL or a path to a local video file
- `--config` — path to a `config.toml` (see resolution order below)
- `--out` — output directory (overrides `clips.output_dir`)
- `--yes` — skip the selection prompt, render every candidate
- `--dry-run` — do everything except writing clip files
- `--clear-cache` — delete the `./.clipper-cache` directory; with no source it
  just clears and exits, with a source it clears then runs fresh
- `--adjust` — correct a clip's edges and re-render it (see below)

Examples:

```bash
clipper https://youtu.be/abc123
clipper ./interview.mp4 --out ./shorts
clipper ./talk.mkv --config ./my-config.toml --yes
clipper --clear-cache                       # wipe all cached work
clipper --clear-cache https://youtu.be/abc  # re-run a video from scratch
```

You can also run it as a module: `python -m clipper <source>`.

## Adjusting clips

If a rendered clip starts or ends a little too tight or too loose, correct it
without re-running the whole pipeline:

```bash
clipper <source> --adjust 3 --start +2        # 2s more before clip 3 starts
clipper <source> --adjust 3 --end -1          # trim 1s off clip 3's end
clipper <source> --adjust 3 --start +2 --end -1
clipper <source> --adjust all --start +1.5    # 1.5s more lead-in on every clip
clipper <source> --adjust 3                   # no change: just list the clips
```

`--start` / `--end` take signed seconds — **`+N` lengthens that edge of the
clip, `-N` shortens it**. The correction is saved into `candidates.json` (so it
survives later runs) and only the affected clip is re-cut, overwriting its
`.mp4` and `.srt`. Adjustments are cumulative, and `--dry-run` previews the
before/after without writing anything.

## How it works

1. **Resolve source.** URLs are downloaded with `yt-dlp`; local files are used
   in place. Working files live in `./.clipper-cache/<video-id>/`.
2. **Get transcript.** YouTube auto-captions (which carry word-level timing)
   are tried first; otherwise `faster-whisper` transcribes the audio. The
   transcript is cached, so re-runs skip this step.
3. **Generate candidates.** The transcript is sent to the configured LLM via
   `litellm`, which returns strict JSON validated with `pydantic`. The result
   is cached, so re-runs skip this step too.
4. **Select.** A `rich` table lists the candidates; pick them with indices
   (`1,3`), ranges (`1-3`), `all`, or `q` to quit.
5. **Render.** Each selected clip is cut with `ffmpeg` (re-encoded for a
   frame-accurate cut) and gets a sibling `.srt`, with cues anchored to the
   rendered file's start so they stay in sync with the audio. Output lands in
   a per-video subfolder: `<output_dir>/<video-title>/`. Files are named
   `NN-clip-title.mp4` — the `NN` prefix matches the clip's number in the
   table and in `--adjust`.

## Caching

Per video, `./.clipper-cache/<video-id>/` holds three reusable artifacts:

| File | Skipped on re-run when... |
|---|---|
| the downloaded video | always (a YouTube URL is never re-downloaded) |
| `transcript.json` | always |
| `candidates.json` | the model, generation settings, and transcript are unchanged |

So a second run on the same source is fast and does **not** call the LLM
again. Changing `llm.model`, `llm.temperature`, the duration bounds, or the
language invalidates `candidates.json` automatically. To force everything from
scratch, use `--clear-cache`.

## Configuration

Resolution order: `--config <path>` → `./config.toml` →
`~/.config/clipper/config.toml` → built-in defaults. See [`config.toml`](config.toml)
for the full set of options.

The LLM model is any [litellm](https://docs.litellm.ai/) model string. Provider
API keys are read from the environment automatically (`ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, ...). Switching
`llm.model` between, say, an Anthropic, OpenAI, or Ollama model needs no code
changes.

## Tests

```bash
pip install -e ".[dev]"
pytest
```
