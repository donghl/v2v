"""Speech-to-text using faster-whisper with GPU-optimized decoding."""

import json
from pathlib import Path

from app.config.settings import settings
from app.utils.logger import logger


class TranscriptionError(Exception):
    """Raised when transcription fails."""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

from typing import TypedDict


class Segment(TypedDict):
    start: float
    end: float
    text: str


def transcribe(audio_path: Path, temp_dir: Path) -> tuple[list[Segment], str]:
    """
    Run faster-whisper on an audio file with GPU-optimized parameters.

    On a 4090 with large-v3 + float16, expect ~20x realtime and significant
    accuracy improvement over medium/int8 on CPU.

    Returns:
        (segments, detected_language).
    """
    if not audio_path.exists():
        raise TranscriptionError(f"Audio file not found: {audio_path}")

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise TranscriptionError("faster-whisper not installed. Run: pip install faster-whisper")

    device = _resolve_device()
    compute_type = _resolve_compute_type(device)

    logger.info(
        f"Loading Whisper model: {settings.WHISPER_MODEL} "
        f"(device={device}, compute={compute_type})"
    )

    try:
        model = WhisperModel(
            settings.WHISPER_MODEL,
            device=device,
            compute_type=compute_type,
            download_root=str(settings.MODELS_DIR),
            cpu_threads=4,  # limit CPU threads when GPU is primary
            num_workers=1,
        )
    except Exception as e:
        raise TranscriptionError(f"Failed to load Whisper model '{settings.WHISPER_MODEL}': {e}")

    # Parse temperature fallback schedule
    temperatures = _parse_temperatures(settings.WHISPER_TEMPERATURE)

    transcribe_kwargs = {
        "beam_size": settings.WHISPER_BEAM_SIZE,
        "best_of": settings.WHISPER_BEST_OF,
        "temperature": temperatures,
        "repetition_penalty": settings.WHISPER_REPETITION_PENALTY,
        "no_speech_threshold": settings.WHISPER_NO_SPEECH_THRESHOLD,
        "word_timestamps": settings.WHISPER_WORD_TIMESTAMPS,
        "vad_filter": True,
        "vad_parameters": dict(
            min_silence_duration_ms=settings.WHISPER_VAD_MIN_SILENCE_MS,
        ),
    }

    logger.info(
        f"Transcribing (beam={settings.WHISPER_BEAM_SIZE}, "
        f"best_of={settings.WHISPER_BEST_OF}, "
        f"temps={temperatures}, "
        f"rep_penalty={settings.WHISPER_REPETITION_PENALTY}) ..."
    )

    try:
        segments_raw, info = model.transcribe(str(audio_path), **transcribe_kwargs)
    except Exception as e:
        raise TranscriptionError(f"Transcription failed: {e}")

    detected_language = info.language
    logger.info(
        f"Detected language: {detected_language} "
        f"(probability={info.language_probability:.2f})"
    )

    segments: list[Segment] = []
    for seg in segments_raw:
        segments.append(Segment(
            start=round(seg.start, 3),
            end=round(seg.end, 3),
            text=seg.text.strip(),
        ))

    # Persist for caching / debugging
    json_path = temp_dir / f"{audio_path.stem}_segments.json"
    json_path.write_text(
        json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(f"Transcription complete: {len(segments)} segments → {json_path}")

    return segments, detected_language


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_device() -> str:
    """Auto-detect best available device or honour WHISPER_DEVICE setting."""
    if settings.WHISPER_DEVICE != "auto":
        return settings.WHISPER_DEVICE

    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except ImportError:
        pass
    return "cpu"


def _resolve_compute_type(device: str) -> str:
    """Pick a sensible compute type for CTranslate2."""
    if settings.WHISPER_COMPUTE_TYPE != "auto":
        return settings.WHISPER_COMPUTE_TYPE
    if device == "cuda":
        return "float16"
    return "int8"


def _parse_temperatures(temp_str: str) -> list[float] | float:
    """
    Parse temperature setting.  Returns a list for fallback decoding
    (tries each temperature in order) or a single float.

    Examples:
        "0.0,0.2,0.4,0.6,0.8,1.0" → [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
        "0.0" → 0.0
    """
    parts = [p.strip() for p in temp_str.split(",")]
    temps = [float(p) for p in parts if p]
    if len(temps) == 1:
        return temps[0]
    return temps
