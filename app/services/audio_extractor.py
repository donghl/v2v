"""Extract audio from video files using ffmpeg."""

import subprocess
from pathlib import Path

from app.config.settings import settings
from app.utils.logger import logger


class AudioExtractionError(Exception):
    """Raised when audio extraction fails."""


def extract_audio(video_path: Path, output_dir: Path) -> Path:
    """
    Extract mono 16kHz WAV audio from a video file.

    Args:
        video_path: Path to the input video.
        output_dir: Directory to write the extracted audio.

    Returns:
        Path to the extracted WAV file.

    Raises:
        AudioExtractionError: If ffmpeg fails or audio stream is missing.
    """
    if not video_path.exists():
        raise AudioExtractionError(f"Video file not found: {video_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    audio_path = output_dir / f"{video_path.stem}_audio.wav"

    logger.info(f"Extracting audio from {video_path.name} ...")

    cmd = [
        settings.FFMPEG_PATH,
        "-i", str(video_path),
        "-vn",                          # no video
        "-acodec", "pcm_s16le",         # 16-bit PCM
        "-ar", str(settings.AUDIO_SAMPLE_RATE),  # 16kHz
        "-ac", "1",                     # mono
        "-y",                           # overwrite
        str(audio_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 min timeout for large files
        )
        if result.returncode != 0:
            stderr = result.stderr
            logger.error(f"ffmpeg stderr: {stderr}")
            raise AudioExtractionError(f"ffmpeg exited with code {result.returncode}: {stderr[-500:]}")

    except subprocess.TimeoutExpired:
        raise AudioExtractionError("Audio extraction timed out (10 min)")
    except FileNotFoundError:
        raise AudioExtractionError(
            f"ffmpeg not found at '{settings.FFMPEG_PATH}'. Install with: brew install ffmpeg"
        )

    if not audio_path.exists() or audio_path.stat().st_size == 0:
        raise AudioExtractionError("Audio extraction produced an empty file")

    logger.info(f"Audio extracted: {audio_path} ({audio_path.stat().st_size / 1024:.0f} KB)")
    return audio_path
