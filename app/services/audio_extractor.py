"""Extract audio from video files using ffmpeg."""

import math
import subprocess
import wave
from pathlib import Path

import numpy as np

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
        "-ac", "2",                     # keep both channels (see _downmix_to_mono)
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

    _downmix_to_mono(audio_path)

    logger.info(f"Audio extracted: {audio_path} ({audio_path.stat().st_size / 1024:.0f} KB)")
    return audio_path


def _downmix_to_mono(audio_path: Path) -> None:
    """
    Convert a stereo PCM WAV to mono in place.

    ffmpeg's default (L+R)/2 downmix cancels anti-phase sources (L ≈ -R),
    which silently produces near-silence and zero transcription segments.
    Detect that case and fall back to the louder channel instead.
    """
    with wave.open(str(audio_path), "rb") as wf:
        n_channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        sample_rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())

    if n_channels <= 1 or sample_width != 2:
        return

    data = np.frombuffer(frames, dtype=np.int16).reshape(-1, n_channels)
    left = data[:, 0].astype(np.float32)
    right = data[:, 1].astype(np.float32)

    def _rms(x: np.ndarray) -> float:
        return float(np.sqrt(np.mean(np.square(x, dtype=np.float64))) + 1e-12)

    summed = (left + right) / 2.0
    rms_sum, rms_l, rms_r = _rms(summed), _rms(left), _rms(right)

    if rms_sum < 0.5 * max(rms_l, rms_r):
        mono = left if rms_l >= rms_r else right
        logger.warning(
            "Anti-phase stereo detected: (L+R)/2 is {:.1f} dB vs channel {:.1f} dB — "
            "using the louder channel instead".format(
                20 * math.log10(rms_sum / 32768.0),
                20 * math.log10(max(rms_l, rms_r) / 32768.0),
            )
        )
    else:
        mono = summed

    rms_dbfs = 20 * math.log10(_rms(mono) / 32768.0)
    if rms_dbfs < -40:
        logger.warning(
            f"Extracted audio is very quiet (mean {rms_dbfs:.1f} dBFS) — "
            "transcription may return few or no segments"
        )

    with wave.open(str(audio_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(np.clip(mono, -32768, 32767).astype(np.int16).tobytes())
