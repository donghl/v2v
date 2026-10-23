# V2V — Automatic Video Subtitle Translation

Automatically transcribe, translate, and burn subtitles into videos.

**Input:** A video file (upload or URL) + target language → **Output:** Subtitled video.

---

## How It Works

```
Video → Extract Audio → (optional: Vocal Separation) → Whisper ASR → SRT → Translate → Burn Subtitles → Output
```

1. **Extract audio** — ffmpeg extracts mono 16kHz WAV
2. **Vocal separation** (optional) — Demucs isolates vocals from background music
3. **Transcribe** — faster-whisper detects language + generates timed SRT segments
4. **Translate** — segments translated via MarianMT, NLLB-200, or Google Translate
5. **Burn** — ffmpeg burns translated SRT into the video

---

## Quick Start

### Prerequisites

- Python 3.11+
- [ffmpeg](https://ffmpeg.org)

```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt install ffmpeg

# Windows
# Download from https://ffmpeg.org/download.html
```

### Install

```bash
cd v2v
python -m venv .venv
source .venv/bin/activate   # Linux/Mac
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
# Edit .env to change model size, device, translation engine, etc.
```

**Recommended for first run (no model download needed):**

```env
TRANSLATION_ENGINE=google
```

### Run

```bash
uvicorn app.main:app --reload
```

Server starts at http://localhost:8000 (Docker: port **8010**).

---

## Usage

### Web API

Upload a video (file or URL), track progress, and download the result:

| Endpoint | Method | Description |
|---|---|---|
| `/upload` | POST | Upload video file + `target_language` |
| `/download-url` | POST | Download from YouTube, Bilibili, etc. |
| `/process` | POST | Process a file already in `input/` |
| `/process-all` | POST | Sequentially process all pending files |
| `/status/{task_id}` | GET | Live task state |
| `/download/{task_id}` | GET | Download result video |
| `/health` | GET | Health check |

#### Examples

```bash
# Upload a video
curl -X POST http://localhost:8000/upload \
  -F "video=@test.mp4" \
  -F "target_language=zh"

# Download from a URL (YouTube, Bilibili, etc.)
curl -X POST http://localhost:8000/download-url \
  -F "url=https://www.youtube.com/watch?v=xxxxx" \
  -F "target_language=zh"

# Check status
curl http://localhost:8000/status/a1b2c3d4e5f6

# Download result
curl -O http://localhost:8000/download/a1b2c3d4e5f6
```

**Status values:** `waiting` → `extracting_audio` → `transcribing` → `translating` → `burning_subtitles` → `completed` / `failed`

### CLI

```bash
# Translate and burn subtitles (CLI only)
python -m v2v video.mp4 -t zh

# Dual-subtitle mode (original + translation, bilingual)
python -m v2v video.mp4 -t zh --dual-subtitles

# Long segments split into smaller chunks
python -m v2v video.mp4 -t zh --split-long-segments
```

---

## Subscribe & Watch

Subscribe to YouTube channels and automatically process new videos:

```bash
# Add a channel subscription
curl -X POST http://localhost:8000/subscriptions \
  -H "Content-Type: application/json" \
  -d '{"channel": "https://www.youtube.com/c/ChannelName", "target_language": "zh"}'

# List subscriptions
curl http://localhost:8000/subscriptions

# Remove a subscription
curl -X DELETE http://localhost:8000/subscriptions/{id}

# Manually trigger a channel check
curl -X POST http://localhost:8000/subscriptions/{id}/check
```

---

## Supported Languages

| Code | Language |
|------|----------|
| zh | Chinese |
| en | English |
| ja | Japanese |
| ko | Korean |
| es | Spanish |
| ar | Arabic |
| fr | French |
| de | German |
| ru | Russian |
| pt | Portuguese |
| + more | + more via engine-specific language maps |

---

## Translation Engines

| Engine | Quality | Setup | Offline | Notes |
|---|---|---|---|---|
| `google` | Good | Zero setup (`pip install deep-translator`) | No | Recommended for first run |
| `marian` | Decent | Downloads models (~300MB/pair) | Yes | Use code `zh` for Chinese |
| `nllb` | Best | Downloads 1.3GB model | Yes | Use code `zho_Hans` for Chinese |

Set via `TRANSLATION_ENGINE` in `.env`.

---

## Configuration

Key settings in `.env`:

| Variable | Default | Description |
|---|---|---|
| `WHISPER_MODEL` | `medium` | `tiny` / `base` / `small` / `medium` / `large-v3` |
| `WHISPER_DEVICE` | `auto` | `cpu` / `cuda` / `auto` |
| `TRANSLATION_ENGINE` | `marian` | `google` / `marian` / `nllb` |
| `USE_CROSS_VALIDATION` | `false` | Dual-engine ASR + translation validation |
| `USE_VOCAL_SEPARATION` | `false` | Enable Demucs vocal/instrumental separation |
| `DUAL_SUBTITLES` | `false` | Bilingual subtitles (CLI only) |
| `SUBTITLE_FONT` | *(auto-detect)* | Font file path (.ttf/.ttc/.otf) |
| `SUBTITLE_FONT_SIZE` | `24` | Font size |
| `DATABASE_PATH` | `./v2v.db` | SQLite path |
| `SUBSCRIPTION_CHECK_INTERVAL` | `3600` | Seconds between channel polls |

---

## Model Download

- **Whisper models** download automatically to `./models/` on first run.
- **MarianMT / NLLB models** download via HuggingFace transformers cache.
- **Demucs** models auto-download when `USE_VOCAL_SEPARATION=true`.

---

## Project Structure

```
v2v/
├── app/
│   ├── main.py              # FastAPI entry point
│   ├── api/
│   │   └── routes.py        # API endpoints
│   ├── services/
│   │   ├── pipeline.py      # Orchestrator
│   │   ├── audio_extractor.py
│   │   ├── speech_to_text.py
│   │   ├── translator.py
│   │   ├── subtitle_generator.py
│   │   ├── video_burner.py
│   │   ├── video_downloader.py  # yt-dlp URL downloads
│   │   ├── vocal_separator.py   # Demucs separation
│   │   ├── subscription_checker.py
│   │   └── cross_validation.py
│   ├── config/
│   │   └── settings.py      # Configuration
│   ├── templates/
│   │   └── index.html       # Web UI
│   └── utils/
│       └── logger.py
├── input/                   # Uploaded/downloaded videos
├── output/                  # Processed videos (<stem>_subtitled_<lang>.mp4)
├── temp/                    # Per-task working dirs (auto-cleaned)
├── models/                  # Whisper model storage
├── docs/                    # Engineering logs
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

## Docker

```bash
# Start with GPU (default)
docker compose up -d

# Start without GPU
docker compose --config docker-compose.yml --config docker-compose.cpu.yml up -d

# Run on port 8010 (default)
```

---

## Next Steps (Production)

- Replace in-memory task store with Redis + Celery
- Add full Web UI (React/Vue)
- Add batch processing endpoint
- Wire `DUAL_SUBTITLES` and `split_long_segments` into the server pipeline
- Add GPU Docker support (`nvidia` driver)
- Add webhook notifications on task completion
