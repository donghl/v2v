"""
Orchestrates the full video → subtitled video pipeline.

Steps:
  1. Extract audio
  2. Transcribe (speech-to-text)
  3. Generate original SRT
  4. Translate segments
  5. Generate translated SRT
  6. Burn subtitles into video
"""

import uuid
from enum import Enum
from pathlib import Path

from app.config.settings import settings
from app.utils.logger import logger
from app.services.audio_extractor import extract_audio, AudioExtractionError
from app.services.vocal_separator import separate_vocals, VocalSeparationError
from app.services.speech_to_text import transcribe, TranscriptionError
from app.services.subtitle_generator import segments_to_srt
from app.services.translator import translate_segments, TranslationError
from app.services.video_burner import burn_subtitles, BurnError
from app.services.cross_validation import (
    transcribe_with_validation,
    translate_with_validation,
    TranscriptionValidationError,
    TranslationValidationError,
)


class PipelineStatus(str, Enum):
    WAITING = "waiting"
    EXTRACTING_AUDIO = "extracting_audio"
    TRANSCRIBING = "transcribing"
    TRANSLATING = "translating"
    BURNING_SUBTITLES = "burning_subtitles"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskState:
    """Holds the state for a single pipeline task."""

    def __init__(self, task_id: str, video_path: Path, target_lang: str):
        self.task_id = task_id
        self.video_path = video_path
        self.target_lang = target_lang
        self.status = PipelineStatus.WAITING
        self.error: str | None = None
        self.output_path: Path | None = None
        self.progress: float = 0.0  # 0–100

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "progress": self.progress,
            "error": self.error,
            "output_file": self.output_path.name if self.output_path else None,
        }


# In-memory task store (MVP — replace with Redis/DB for production)
_tasks: dict[str, TaskState] = {}


def submit_task(video_path: Path, target_lang: str) -> TaskState:
    """Create a new task and return it."""
    task_id = uuid.uuid4().hex[:12]
    state = TaskState(task_id, video_path, target_lang)
    _tasks[task_id] = state
    logger.info(f"Task {task_id} created: {video_path.name} → {target_lang}")
    return state


def get_task(task_id: str) -> TaskState | None:
    return _tasks.get(task_id)


def run_pipeline(task_id: str) -> None:
    """
    Execute the full pipeline synchronously (call from a background thread).

    Updates task status at each step.
    """
    state = _tasks.get(task_id)
    if state is None:
        logger.error(f"Task {task_id} not found")
        return

    video_path = state.video_path
    target_lang = state.target_lang
    work_dir = settings.TEMP_DIR / task_id
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        # ── Step 1: Extract audio ──
        state.status = PipelineStatus.EXTRACTING_AUDIO
        state.progress = 5
        logger.info(f"[{task_id}] Extracting audio ...")
        audio_path = extract_audio(video_path, work_dir)
        state.progress = 15

        # ── Step 1.5: Vocal separation (optional) ──
        if getattr(settings, "USE_VOCAL_SEPARATION", False):
            logger.info(f"[{task_id}] Separating vocals from background music ...")
            vocals = separate_vocals(audio_path, work_dir / "demucs")
            # Convert back to 16kHz mono for Whisper
            audio_path = _convert_audio_for_whisper(vocals, work_dir)
            state.progress = 25

        # ── Step 2: Transcribe ──
        state.status = PipelineStatus.TRANSCRIBING
        logger.info(f"[{task_id}] Transcribing ...")

        # Check if cross-validation is enabled
        use_cross_validation = getattr(settings, "USE_CROSS_VALIDATION", False)

        if use_cross_validation:
            logger.info(f"[{task_id}] Using cross-validated transcription")
            validation_result = transcribe_with_validation(
                audio_path, work_dir, use_cross_validation=True
            )
            segments = validation_result["segments"]
            detected_lang = validation_result["detected_language"]
            confidence = validation_result["confidence"]
            logger.info(f"[{task_id}] Transcription confidence: {confidence:.2f}")
        else:
            segments, detected_lang = transcribe(audio_path, work_dir)

        state.progress = 45

        # Save original SRT
        original_srt = work_dir / "original.srt"
        segments_to_srt(segments, original_srt)
        state.progress = 55

        # ── Step 3: Translate ──
        state.status = PipelineStatus.TRANSLATING

        # Skip translation if source and target are the same language
        if detected_lang == target_lang:
            logger.info(f"[{task_id}] Source=target ({detected_lang}), skipping translation")
            translated = segments
        elif use_cross_validation:
            logger.info(f"[{task_id}] Using cross-validated translation")
            translation_result = translate_with_validation(
                segments,
                detected_lang,
                target_lang,
                work_dir,
                use_cross_validation=True,
            )
            translated = translation_result["segments"]
            translation_confidence = translation_result["confidence"]
            logger.info(
                f"[{task_id}] Translation confidence: {translation_confidence:.2f}"
            )
        else:
            translated = translate_segments(
                segments, detected_lang, target_lang, work_dir
            )
        state.progress = 75

        translated_srt = work_dir / "translated.srt"
        segments_to_srt(translated, translated_srt)
        state.progress = 80

        # ── Step 4: Burn subtitles ──
        state.status = PipelineStatus.BURNING_SUBTITLES
        logger.info(f"[{task_id}] Burning subtitles ...")
        output_filename = f"{video_path.stem}_subtitled_{target_lang}.mp4"
        output_path = settings.OUTPUT_DIR / output_filename
        # Avoid overwriting existing output
        counter = 1
        while output_path.exists():
            output_filename = f"{video_path.stem}_subtitled_{target_lang}_{counter}.mp4"
            output_path = settings.OUTPUT_DIR / output_filename
            counter += 1
        burn_subtitles(video_path, translated_srt, output_path)
        state.progress = 98

        # ── Done ──
        state.status = PipelineStatus.COMPLETED
        state.output_path = output_path
        state.progress = 100
        logger.info(f"[{task_id}] Pipeline complete → {output_path}")

        # Clean up temp working directory only (keep source video)
        _cleanup_temp(work_dir)

    except (
        AudioExtractionError,
        VocalSeparationError,
        TranscriptionError,
        TranslationError,
        BurnError,
        TranscriptionValidationError,
        TranslationValidationError,
    ) as e:
        state.status = PipelineStatus.FAILED
        state.error = str(e)
        logger.error(f"[{task_id}] Pipeline failed: {e}")
        _cleanup_temp(work_dir)
    except Exception as e:
        state.status = PipelineStatus.FAILED
        state.error = f"Unexpected error: {e}"
        logger.exception(f"[{task_id}] Unexpected error")
        _cleanup_temp(work_dir)


def _convert_audio_for_whisper(audio_path: Path, work_dir: Path) -> Path:
    """Convert Demucs output (44.1kHz stereo) to Whisper-optimized (16kHz mono)."""
    import subprocess

    output_path = work_dir / f"{audio_path.stem}_16k.wav"
    cmd = [
        settings.FFMPEG_PATH,
        "-i", str(audio_path),
        "-ar", str(settings.AUDIO_SAMPLE_RATE),
        "-ac", "1",
        "-y",
        str(output_path),
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    logger.info(f"Converted to 16kHz mono: {output_path}")
    return output_path


def _cleanup_temp(work_dir: Path) -> None:
    """Remove temporary working directory after successful processing."""
    import shutil

    try:
        if work_dir.exists():
            shutil.rmtree(work_dir)
            logger.info(f"Cleaned up temp: {work_dir}")
    except Exception as e:
        logger.warning(f"Failed to clean temp {work_dir}: {e}")
