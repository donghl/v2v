# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# Start server (dev)
uvicorn app.main:app --reload

# Start server (prod)
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# Docker
docker compose up
```

## Architecture

```
User → FastAPI router (app/api/routes.py)
         ├── POST /upload        (file upload → background thread → pipeline)
         ├── POST /download-url  (yt-dlp download → background thread → pipeline)
         ├── GET  /status/{id}   (poll pipeline state)
         ├── GET  /download/{id} (serve output video)
         ├── GET  /              (web UI — app/templates/index.html)
         └── GET  /health
```

**Pipeline** (`app/services/pipeline.py`) runs synchronously in a **background thread**:

```
extract_audio (ffmpeg) → transcribe (faster-whisper) → generate original.srt
→ translate (3 engines available) → generate translated.srt
→ burn_subtitles (ffmpeg subtitles filter) → output.mp4
```

Tasks are stored **in memory** (`_tasks: dict[str, TaskState]`) — restarting the server loses all task state. Replace with Redis/Celery for production.

When `USE_CROSS_VALIDATION=true`, the pipeline calls `cross_validation.py` which runs a second ASR engine (OpenAI Whisper) and a second translation engine, then picks results via consensus scoring.

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

## Configuration

All config via `.env` (loaded by `python-dotenv` in `app/config/settings.py`).

Key settings:

| Variable | Default | Notes |
|----------|---------|-------|
| `TRANSLATION_ENGINE` | `google` | Start here (no model download) |
| `WHISPER_MODEL` | `medium` | `large-v3` for best accuracy on GPU |
| `WHISPER_DEVICE` | `auto` | `cuda` on 4090, `cpu` otherwise |
| `USE_CROSS_VALIDATION` | `false` | `true` doubles processing time |
| `WHISPER_BEAM_SIZE` | `5` | `10` for GPU |

## File Map

```
app/main.py                      FastAPI app, CORS middleware
app/api/routes.py                All endpoints + web UI
app/config/settings.py           Dataclass from env vars
app/services/pipeline.py         Task orchestration, TaskState, _tasks dict
app/services/audio_extractor.py  ffmpeg → mono 16kHz WAV
app/services/speech_to_text.py   faster-whisper with GPU-optimized params
app/services/translator.py       3 engines (google/marian/nllb) + lang maps
app/services/subtitle_generator.py  Segments → SRT formatting
app/services/video_burner.py     ffmpeg subtitles filter
app/services/video_downloader.py yt-dlp wrapper
app/services/cross_validation.py Multi-engine consensus validation
app/templates/index.html         Web UI (vanilla HTML/CSS/JS)
app/utils/logger.py              Structured logging
```
