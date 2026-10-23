# V2V — Automatic Video Subtitle Translation

**One video → one subtitled video.**

Transcribe, translate, and burn subtitles into videos. No coding required.

---

## What It Does

```
Video → Subtitled Video (in any language)
```

1. **Detects** the original language
2. **Transcribes** speech to text
3. **Translates** text to your target language
4. **Burns** subtitles directly into the video

---

## Quick Start

### 1. Install

```bash
cd v2v
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure (optional)

```bash
cp .env.example .env
```

**For first-time use, just use the defaults** — `TRANSLATION_ENGINE=google` (free, no download).

### 3. Translate — choose your method

#### Option A: CLI (recommended, fastest)

```bash
# Local video → Chinese subtitles
python -m v2v video.mp4 -t zh

# Video from URL (YouTube, Bilibili, etc.) → English subtitles
python -m v2v https://www.youtube.com/watch?v=xxxxx -t en

# Force reprocessing (ignore cache)
python -m v2v video.mp4 -t zh --force
```

#### Option B: Web API (for integration)

Start the server:

```bash
uvicorn app.main:app --reload
```

Then use any HTTP client or browser to call:

| Action | Endpoint |
|---|---|
| Upload video | `POST /upload` |
| Process from URL | `POST /download-url` |
| Check progress | `GET /status/{task_id}` |
| Download result | `GET /download/{task_id}` |
| Health check | `GET /health` |

#### Option C: Subscribe to a channel

```bash
curl -X POST http://localhost:8000/subscriptions \
  -H "Content-Type: application/json" \
  -d '{"channel": "https://www.youtube.com/c/ChannelName", "target_language": "zh"}'
```

---

## Supported Languages

| Code | Language | Code | Language |
|------|----------|------|----------|
| zh | Chinese | fr | French |
| en | English | de | German |
| ja | Japanese | ru | Russian |
| ko | Korean | pt | Portuguese |
| es | Spanish | ar | Arabic |

> + more via engine-specific language maps

---

## Translation Engines

| Engine | Quality | Offline? | Note |
|---|---|---|---|
| `google` | Good | No | Zero setup, recommended for first run |
| `marian` | Decent | Yes | Auto-downloads (~300MB per language pair) |
| `nllb` | Best | Yes | 1.3GB model, best quality |

Set via `TRANSLATION_ENGINE` in `.env`.

---

## Settings

| Setting | Default | Description |
|---|---|---|
| `WHISPER_MODEL` | `medium` | ASR model: `tiny`, `base`, `small`, `medium`, `large-v3` |
| `WHISPER_DEVICE` | `auto` | `cpu` or `cuda` |
| `TRANSLATION_ENGINE` | `google` | `google`, `marian`, or `nllb` |
| `USE_VOCAL_SEPARATION` | `false` | Remove background music (Demucs) |
| `USE_CROSS_VALIDATION` | `false` | Dual-engine validation (slower, more accurate) |
| `DUAL_SUBTITLES` | `false` | Bilingual subtitles (original + translation) |
| `SUBTITLE_FONT_SIZE` | `24` | Font size |

---

## Docker

```bash
# With GPU (recommended)
docker compose up -d

# Without GPU
docker compose --config docker-compose.yml --config docker-compose.cpu.yml up -d
```

Serves on port **8010** (not 8000).

---

## Files

| Folder | Purpose |
|---|---|
| `input/` | Uploaded / downloaded videos |
| `output/` | Subtitled videos (`<name>_subtitled_<lang>.mp4`) |
| `temp/` | Working files (auto-cleanup) |
| `models/` | Whisper models (auto-download) |

---

## Notes

- **Supported video formats:** mp4, mov, mkv, avi, webm, flv, wmv
- **Supported platforms:** YouTube, Bilibili, Facebook, TikTok, X, Instagram, Vimeo
- First run downloads models to `models/` (~200MB for `medium` Whisper)
- Results are cached — rerun to reprocess, use `--force` to skip cache
