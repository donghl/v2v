# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# Lint
pip install ruff && ruff check .

# Server (dev)
uvicorn app.main:app --reload

# Server (prod)
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# Docker
docker compose up
```

**Python**: `3.12.9` (see `.python-version`). **ffmpeg** required (system dependency).

**Additional docs**: `AGENTS.md` (project-level agent instructions), `docs/PIPELINE-LOGIC.md` (Chinese-language pipeline documentation with known issues tracker).

## Architecture

```
User → FastAPI router (app/api/routes.py)
         ├── POST /upload        (file upload → background thread → pipeline)
         ├── POST /process       (process file already in input/ — no copy)
         ├── POST /process-all   (batch process all pending files in input/)
         ├── POST /download-url  (yt-dlp download → background thread → pipeline)
         ├── GET  /status/{id}   (poll pipeline state)
         ├── GET  /download/{id} (serve output video)
         ├── GET  /              (web UI — app/templates/index.html)
         └── GET  /health
```

**`/upload` vs `/process`**: `/upload` receives a file stream, writes it to `input/`, and auto-renames if the filename exists (`video_1.mp4`). `/process` uses a file already in `input/` in-place — no copy. For batch processing, use `/process` or `/process-all` to avoid duplicating files.

**Pipeline** (`app/services/pipeline.py`) runs synchronously in a **background thread**:

```
extract_audio (ffmpeg) → [vocal_separation (Demucs, optional)] → transcribe (faster-whisper)
→ generate original.srt → translate (3 engines available) → generate translated.srt
→ burn_subtitles (ffmpeg subtitles filter) → output.mp4
```

When `USE_VOCAL_SEPARATION=true`, Demucs separates vocals from background music after audio extraction. Demucs outputs 44.1kHz stereo — `_convert_audio_for_whisper` in pipeline.py resamples to 16kHz mono for Whisper.

**Whisper model** (`speech_to_text.py`): global singleton loaded once via `_get_model()` with a thread lock — the model is reused across all tasks. Loading happens lazily on first transcription.

**`Segment` type** (TypedDict with `start: float`, `end: float`, `text: str`): defined in `speech_to_text.py` and imported across 5+ files (`pipeline.py`, `translator.py`, `cross_validation.py`, `subtitle_generator.py`, `video_burner.py`).

**Task state machine**:
```
waiting → extracting_audio → transcribing → translating → burning_subtitles → completed
                                                                             ↘ failed
```

Tasks are stored **in memory** (`_tasks: dict[str, TaskState]`) — restarting the server loses all task state. Replace with Redis/Celery for production.

When `USE_CROSS_VALIDATION=true`, the pipeline calls `cross_validation.py` which runs a second ASR engine (OpenAI Whisper) and a second translation engine, then picks results via consensus scoring. Segments with confidence < 0.1 fall back to single-engine results.

## Translation Engines & Language Codes

Three engines, each with its own language code conventions:

| Engine | Code for Chinese | Setup |
|--------|-----------------|-------|
| `google` (deep-translator) | `zh-CN` (not `zh`!) | No model download |
| `marian` (Helsinki-NLP) | `zh` | Auto-downloads ~300MB/pair |
| `nllb` (facebook/nllb-200) | `zho_Hans` | Downloads 2.4GB model |

**Critical:** Each engine has its own mapping dict (`DEEP_TRANSLATOR_MAP`, `MARIAN_LANG_MAP`, `NLLB_LANG_MAP`) in `translator.py`. When adding a language, update all three maps. Passing `zh` to google engine crashes with `LanguageNotSupportedException`.

## Key Gotchas

- **Whisper model size vs hardware**: `medium` runs on CPU, `large-v3` needs GPU (24GB VRAM). On Mac, CTranslate2 doesn't support MPS — device falls back to `cpu`. Set `WHISPER_DEVICE=cuda` on 4090-class machines.
- **Cross-validation scoring** (`cross_validation.py`): `_improve_translation_with_context` uses consensus (similarity between candidate translations in the same target language), NOT context from original-language segments. The old `_score_translation_quality` that checked word overlap between Chinese and Vietnamese was broken — replaced by consensus.
- **`_calculate_transcription_confidence` and `_calculate_translation_confidence`** are distinct functions with different signatures. The former takes `(list[Segment], list[Segment])`, the latter takes `(list[Segment], dict[str, list[Segment]])`. Do not merge them.
- **Background threads die silently** when uvicorn reloads. The `daemon=True` thread for pipeline execution is killed on reload with no cleanup.
- **Google Translate rate limiting**: individual segments may fail with SSL errors or rate limits. The retry logic (3 attempts, exponential backoff) in `_translate_google` handles most cases; failed segments keep the original text.
- **python-multipart**: If you get `RuntimeError: Form data requires "python-multipart" to be installed`, run `pip install python-multipart`. Required for file upload endpoints.
- **uvicorn reload + pyenv**: `--reload` may spawn a subprocess using system Python instead of the venv. If import errors appear only with `--reload`, run without it: `uvicorn app.main:app`.
- **Demucs sample rate**: Demucs outputs 44.1kHz stereo. Whisper needs 16kHz mono. `_convert_audio_for_whisper` in pipeline.py handles this conversion.
- **Marian English pivot fallback** (`translator.py:118`): If no direct src→tgt Marian model exists, it falls back to two-step src→en→tgt. This is transparent to the caller but doubles translation time.
- **GPU memory pressure**: Running `large-v3` (~3GB) + NLLB-200 (~5GB) + Demucs simultaneously can OOM on 8GB GPUs. On 12GB cards, use `WHISPER_COMPUTE_TYPE=int8` to reduce VRAM.
- **No tests**: The project has no test suite. Always manually verify pipeline output when making changes.

## Configuration

All config via `.env` (loaded by `python-dotenv` in `app/config/settings.py`).

Key settings:

| Variable | Default | Notes |
|----------|---------|-------|
| `TRANSLATION_ENGINE` | `marian` | `google`=fast+free but rate-limited, `marian`=auto-downloads ~300MB/pair, `nllb`=downloads 2.4GB model |
| `WHISPER_MODEL` | `medium` | `large-v3` for best accuracy on GPU |
| `WHISPER_DEVICE` | `auto` | `cuda` on GPU, `cpu` otherwise (MPS not supported) |
| `USE_CROSS_VALIDATION` | `false` | `true` doubles processing time |
| `USE_VOCAL_SEPARATION` | `false` | Remove background music with Demucs |
| `DEMUCS_MODEL` | `htdemucs` | `htdemucs_ft` or `htdemucs_6s` |
| `WHISPER_BEAM_SIZE` | `5` | `10` for GPU |
| `SUBTITLE_FONT_SIZE` | `24` | Passed to ffmpeg subtitles filter |
| `SUBTITLE_FONT_COLOR` | `white` | Mapped to AABBGGRR hex in `video_burner.py` |
| `SUBTITLE_OUTLINE` | `2` | Outline width in pixels |

## File Map

```
app/main.py                      FastAPI app, CORS middleware
app/api/routes.py                All endpoints + web UI
app/config/settings.py           Dataclass from env vars
app/services/pipeline.py         Task orchestration, TaskState, _tasks dict
app/services/audio_extractor.py  ffmpeg → mono 16kHz WAV
app/services/vocal_separator.py  Demucs vocal/instrumental separation
app/services/speech_to_text.py   faster-whisper with GPU-optimized params
app/services/translator.py       3 engines (google/marian/nllb) + lang maps
app/services/subtitle_generator.py  Segments → SRT formatting
app/services/video_burner.py     ffmpeg subtitles filter
app/services/video_downloader.py yt-dlp wrapper
app/services/cross_validation.py Multi-engine consensus validation
app/templates/index.html         Web UI (vanilla HTML/CSS/JS)
app/utils/logger.py              Structured logging
```

## Known Limitations

- No concurrency limit — running many tasks simultaneously can OOM with `large-v3`.
- `_tasks` dict has no locking (MVP single-user, not thread-safe).
- Server restart loses all in-flight task state (tasks become orphans with no cleanup).
- Progress is stale during long-running steps (Demucs, transcription, translation) — no incremental updates during these phases.
- `process-all` runs tasks sequentially in a single thread (no parallelism).
- `download_video` calls `extract_info` twice (duplicate network request).
- `task.video_path` is mutated directly in a thread (not thread-safe).
