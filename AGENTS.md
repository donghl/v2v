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

Server runs on http://localhost:8000

## Architecture

**Pipeline**: Video → Extract Audio → Whisper ASR → SRT → Translate → Burn Subtitles → Output

**Entry point**: `app/main.py` (FastAPI app)

**Key services** (`app/services/`):
- `pipeline.py` — Orchestrates the full workflow
- `audio_extractor.py` — ffmpeg audio extraction
- `speech_to_text.py` — faster-whisper transcription
- `translator.py` — MarianMT/NLLB/Google translation
- `subtitle_generator.py` — SRT generation
- `video_burner.py` — ffmpeg subtitle burning
- `video_downloader.py` — yt-dlp video download from URLs
- `cross_validation.py` — Cross-validation for ASR and translation

**Configuration**: `app/config/settings.py` loads from `.env` and creates required directories

## Important Directories

- `input/` — Uploaded videos
- `output/` — Processed videos
- `temp/` — Temporary files per task (cleaned after processing)
- `models/` — Whisper model storage (auto-downloaded on first run)

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
- `USE_CROSS_VALIDATION` — Enable cross-validation for better accuracy (default: false)
- `SUBTITLE_FONT_SIZE` — Default: 24
- `SUBTITLE_FONT_COLOR` — Default: white
- `SUBTITLE_OUTLINE_COLOR` — Default: black
- `SUBTITLE_OUTLINE` — Default: 2
- `MAX_SEGMENT_LENGTH` — seconds per chunk for long audio (default: 30)
- `HOST` — Server host (default: 0.0.0.0)
- `PORT` — Server port (default: 8000)

## Translation Engines

| Engine | Setup | Offline |
|--------|-------|---------|
| `google` | Zero setup | No |
| `marian` | Auto-downloads ~300MB/pair | Yes |
| `nllb` | Downloads 2.4GB model | Yes |

## Docker

```bash
docker compose up
```

## API Endpoints

- `POST /upload` — Upload video file with `target_language` param
- `POST /download-url` — Download video from URL (YouTube, TikTok, etc.) with `target_language` param
- `GET /status/{task_id}` — Check processing status
- `GET /download/{task_id}` — Download result
- `GET /health` — Health check

**Supported video formats**: mp4, mov, mkv, avi, webm, flv, wmv

## Dependencies

- Python 3.11+
- ffmpeg (must be installed separately)
- See `requirements.txt` for Python packages

**Critical**: If you get `RuntimeError: Form data requires "python-multipart" to be installed`, run:
```bash
pip install python-multipart
```
This is required for file upload endpoints but may not be installed even if listed in requirements.txt.

**Note**: If you get this error with `--reload`, run without reload:
```bash
uvicorn app.main:app
```
This is a known issue with uvicorn reload + pyenv where the subprocess uses system Python instead of the venv.

## Notes

- No test suite currently exists
- Task state stored in-memory (not production-ready)
- CORS enabled for all origins (MVP)
- Whisper models auto-download to `models/` on first use
- Uploaded files with duplicate names are automatically renamed with a counter (e.g., `video_1.mp4`)

## Cross-Validation

Enable cross-validation for improved accuracy by setting `USE_CROSS_VALIDATION=true` in `.env`:

**Transcription cross-validation:**
- Uses both faster-whisper and OpenAI Whisper
- Compares results and selects best transcription
- Provides confidence scores

**Translation cross-validation:**
- Uses primary translation engine plus one alternative (google > marian > nllb priority)
- Applies consensus-based scoring to select best translation
- Improves translation consistency

**Note:** Cross-validation significantly increases processing time but improves accuracy.
