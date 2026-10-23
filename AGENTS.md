# V2V — Video Subtitle Translation

## Quick Start

```bash
# Install dependencies
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env to set TRANSLATION_ENGINE (google recommended for first run)

# Run dev server
uvicorn app.main:app --reload
```

Server runs on http://localhost:8000 (Docker: port **8010**).

## Architecture

**Pipeline**: Video → Extract Audio → (optional Demucs vocal separation) → Whisper ASR → SRT → Translate → Burn Subtitles → Output

**Entry points**:
- `app/main.py` — FastAPI app, initializes SQLite + subscription scheduler on startup
- `v2v` — standalone CLI (same pipeline, no server needed)

**Key services** (`app/services/`):
- `pipeline.py` — Orchestrates the full workflow (server path)
- `audio_extractor.py` — ffmpeg audio extraction (16kHz mono WAV)
- `vocal_separator.py` — Demucs vocal/instrumental separation (optional)
- `speech_to_text.py` — faster-whisper transcription (global model singleton)
- `translator.py` — MarianMT/NLLB/Google translation (3 engines, English-pivot fallback)
- `subtitle_generator.py` — SRT generation + `split_long_segments` (sentence splitting)
- `video_burner.py` — ffmpeg **overlay** subtitle burning (Pillow PNG rendering, no libass; supports bilingual)
- `video_downloader.py` — yt-dlp **CLI** download (returns `list[(path, title)]` for playlists)
- `subscription_checker.py` — background thread polling channels for new videos
- `cross_validation.py` — Cross-validation for ASR and translation (optional)

**Persistence**: `app/database.py` (SQLite, WAL mode) stores tasks + subscriptions. In-memory `_tasks` dict in `pipeline.py` is the live state used by `/status` and `/download`; the DB is a mirrored record used by `/history`.

**Configuration**: `app/config/settings.py` loads from `.env` and creates required directories.

## Important Directories

- `input/` — Uploaded/downloaded videos
- `output/` — Processed videos (`<stem>_subtitled_<lang>.mp4`)
- `temp/` — Per-task working dirs (cleaned after processing)
- `models/` — Whisper model storage (auto-downloaded on first run)
- `v2v.db` — SQLite database (auto-created; ignored by git)

## Environment Variables

Key settings in `.env`:
- `WHISPER_MODEL` — tiny/base/small/medium/large-v3 (default: medium)
- `WHISPER_DEVICE` — auto/cpu/cuda (default: auto)
- `WHISPER_COMPUTE_TYPE` — auto/float16/int8 (default: auto)
- `WHISPER_BEAM_SIZE` — 5=fast, 10=accurate (default: 5)
- `WHISPER_BEST_OF` — candidates to sample (default: 5)
- `WHISPER_TEMPERATURE` — fallback decoding temps (default: 0.0,0.2,0.4,0.6,0.8,1.0)
- `WHISPER_REPETITION_PENALTY` — reduce hallucinations (default: 1.1)
- `WHISPER_NO_SPEECH_THRESHOLD` — silence detection (default: 0.6)
- `WHISPER_WORD_TIMESTAMPS` — word-level timestamps (default: false)
- `WHISPER_VAD_MIN_SILENCE_MS` — min silence between segments (default: 500)
- `TRANSLATION_ENGINE` — google/marian/nllb (default: marian)
- `TRANSLATION_DEVICE` — auto/cuda/cpu (default: auto)
- `USE_CROSS_VALIDATION` — dual-engine ASR/translation validation (default: false)
- `USE_VOCAL_SEPARATION` — Demucs vocal removal (default: false; `.env.example` sets true)
- `DUAL_SUBTITLES` — bilingual subtitles, original on top / translation below (default: false; **only honored by the `v2v` CLI, not the server pipeline**)
- `SUBTITLE_FONT` — Font file path (.ttf/.ttc/.otf); empty = auto-detect (PingFang → STHeiti → Helvetica)
- `SUBTITLE_FONT_SIZE` — Default: 24
- `SUBTITLE_FONT_COLOR` — Default: white
- `SUBTITLE_OUTLINE_COLOR` — Default: black
- `SUBTITLE_OUTLINE` — Default: 2
- `MAX_SEGMENT_LENGTH` — seconds per chunk for long audio (default: 30)
- `DATABASE_PATH` — SQLite path (default: ./v2v.db)
- `SUBSCRIPTION_CHECK_INTERVAL` — seconds between channel polls (default: 3600)
- `HOST` — Server host (default: 0.0.0.0)
- `PORT` — Server port (default: 8000)

## Translation Engines

| Engine | Code for Chinese | Setup | Offline |
|--------|------------------|-------|---------|
| `google` | `zh-CN` (not `zh`!) | Zero setup | No |
| `marian` | `zh` | Auto-downloads ~300MB/pair | Yes |
| `nllb` | `zho_Hans` | Downloads 1.3GB model | Yes |

Each engine has its own language map (`MARIAN_LANG_MAP`, `NLLB_LANG_MAP`, `DEEP_TRANSLATOR_MAP`) in `translator.py` — when adding a language, update all three.

## Docker

```bash
docker compose up   # serves on port 8010, GPU (NVIDIA) enabled by default
```

## API Endpoints

- `POST /upload` — Upload video file + `target_language`
- `POST /process` — Process a file already in `input/` (no copy)
- `POST /process-all` — Sequentially process all pending files in `input/`
- `POST /download-url` — Download from URL + `target_language`
- `GET /status/{task_id}` — Live task state (in-memory)
- `GET /download/{task_id}` — Download result video
- `GET /` — Web UI (`app/templates/index.html`)
- `GET /history` — Task list from SQLite (paginated)
- `GET /history/{task_id}` — Single task record from SQLite
- `POST /subscriptions` — Add channel subscription
- `GET /subscriptions` — List subscriptions
- `DELETE /subscriptions/{id}` — Remove subscription
- `POST /subscriptions/{id}/check` — Manually trigger a channel check
- `GET /health` — Health check

**Supported video formats**: mp4, mov, mkv, avi, webm, flv, wmv

## Dependencies

- Python 3.11+
- ffmpeg (must be installed separately)
- `yt-dlp` binary preferred (falls back to `python -m yt_dlp`)
- See `requirements.txt` for Python packages

**Critical**: If you get `RuntimeError: Form data requires "python-multipart" to be installed`, run:
```bash
pip install python-multipart
```
**Note**: If you get this error with `--reload`, run without reload:
```bash
uvicorn app.main:app
```
This is a known issue with uvicorn reload + pyenv where the subprocess uses system Python instead of the venv.

## Notes

- No test suite currently exists — always manually verify pipeline output
- Task state lives in both in-memory `_tasks` and SQLite; `/status` + `/download` use memory (lost on restart), `/history` uses SQLite (persists)
- CORS enabled for all origins (MVP)
- Whisper models auto-download to `models/` on first use
- Uploaded files with duplicate names are automatically renamed with a counter (e.g., `video_1.mp4`)
- Output files with duplicate names get a `_1`, `_2` suffix

## Known Inconsistencies (do not "fix" blindly — flag first)

- `DUAL_SUBTITLES` and `split_long_segments` are wired into the `v2v` CLI but **not** the server pipeline (`run_pipeline` never calls `split_long_segments` nor passes `original_srt_path` to `burn_subtitles`)
- `download_from_url` writes a placeholder path to SQLite and never updates it after the real download
- Subscription checker uses the Python `yt_dlp` library while `video_downloader` deliberately uses the yt-dlp CLI (SSL workaround) — inconsistent, and flat-extracted entries may pass a bare video ID as URL
- `_translate_nllb` skips empty texts and can return fewer results than input segments (misalignment risk)
- DB status updates skip early pipeline stages (`extracting_audio` progress 5–15 never persisted)
- In-memory failure paths in `routes.py` (download/check) set status without persisting to DB

## Cross-Validation

Enable by setting `USE_CROSS_VALIDATION=true` in `.env`:

**Transcription cross-validation:**
- Uses both faster-whisper and OpenAI Whisper, aligns segments by time overlap, picks consensus
- Falls back to primary when confidence < 0.1

**Translation cross-validation:**
- Uses primary engine + one alternative (google > marian > nllb priority)
- Consensus-based scoring (similarity among same-target-language candidates)

**Note:** Cross-validation significantly increases processing time and doubles model memory usage.
