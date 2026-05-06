# V2V — Automatic Video Subtitle Translation

Automatically transcribe, translate, and burn subtitles into videos.

**Input:** A video file + target language → **Output:** Subtitled video.

---

## How It Works

```
Video → Extract Audio → Whisper ASR → SRT → Translate → Burn Subtitles → Output
```

1. **Extract audio** — ffmpeg extracts mono 16kHz WAV
2. **Transcribe** — faster-whisper detects language + generates timed segments
3. **Translate** — segments translated via MarianMT, NLLB-200, or Google Translate
4. **Burn** — ffmpeg burns translated SRT into the video

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
# Edit .env to change model size, device, etc.
```

Recommended for first run (no model download needed):

```env
TRANSLATION_ENGINE=google
```

### Run

```bash
uvicorn app.main:app --reload
```

Server starts at http://localhost:8000

---

## API

### Upload a video

```bash
curl -X POST http://localhost:8000/upload \
  -F "video=@test.mp4" \
  -F "target_language=zh"
```

Response:

```json
{"task_id": "a1b2c3d4e5f6"}
```

### Download from URL

```bash
curl -X POST http://localhost:8000/download-url \
  -F "url=https://www.youtube.com/watch?v=xxxxx" \
  -F "target_language=zh"
```

Response:

```json
{"task_id": "a1b2c3d4e5f6"}
```

Supported platforms: YouTube, Facebook, TikTok, X (Twitter), Instagram, Vimeo, Bilibili, and more. Then track progress and download results the same way as upload.

### Check status

```bash
curl http://localhost:8000/status/a1b2c3d4e5f6
```

Response:

```json
{"task_id": "a1b2c3d4e5f6", "status": "translating", "progress": 65.0}
```

Status values: `waiting` → `extracting_audio` → `transcribing` → `translating` → `burning_subtitles` → `completed` / `failed`

### Download result

```bash
curl -O http://localhost:8000/download/a1b2c3d4e5f6
```

### Health check

```bash
curl http://localhost:8000/health
```

---

## Docker

```bash
docker compose up
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
| + more | |

---

## Translation Engines

| Engine | Quality | Setup | Offline |
|--------|---------|-------|---------|
| `google` | Good | Zero setup (pip install deep-translator) | No |
| `marian` | Decent | Downloads models automatically (~300MB/pair) | Yes |
| `nllb` | Best | Downloads 2.4GB model | Yes |

Set via `TRANSLATION_ENGINE` in `.env`.

---

## Model Download

Whisper models download automatically to `./models/` on first run.

MarianMT / NLLB models download via HuggingFace transformers cache.

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
│   │   └── video_downloader.py  # yt-dlp URL downloads
│   ├── config/
│   │   └── settings.py      # Configuration
│   └── utils/
│       └── logger.py
├── input/                   # Uploaded videos
├── output/                  # Processed videos
├── temp/                    # Temporary files per task
├── models/                  # Whisper model storage
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

## Next Steps (Production)

- Replace in-memory task store with Redis + Celery
- Add Web UI (React/Vue)
- Add batch processing endpoint
- Add bilingual subtitle support
- Add GPU Docker support (nvidia-docker)
