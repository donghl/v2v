"""Separate vocals from background music using Demucs (Meta)."""

from pathlib import Path

from app.config.settings import settings
from app.utils.logger import logger


class VocalSeparationError(Exception):
    """Raised when vocal separation fails."""


def separate_vocals(audio_path: Path, output_dir: Path) -> Path:
    """
    Run Demucs to extract vocals from an audio file.

    Args:
        audio_path: Path to the input audio (WAV).
        output_dir: Directory to write the separated vocals.

    Returns:
        Path to the vocals-only WAV file.

    Raises:
        VocalSeparationError: If Demucs fails or is not installed.
    """
    if not audio_path.exists():
        raise VocalSeparationError(f"Audio file not found: {audio_path}")

    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        from demucs import separate
    except ImportError:
        raise VocalSeparationError(
            "demucs not installed. Run: pip install demucs"
        )

    model_name = settings.DEMUCS_MODEL
    logger.info(f"Separating vocals with Demucs ({model_name}) ...")

    try:
        # Run Demucs separation
        separate.main([
            "--two-stems", "vocals",      # vocals + instrumental
            "-n", model_name,
            "-o", str(output_dir),
            str(audio_path),
        ])
    except Exception as e:
        raise VocalSeparationError(f"Demucs separation failed: {e}")

    # Demucs output path: output_dir/{model_name}/{stem}/vocals.wav
    vocals_path = output_dir / model_name / audio_path.stem / "vocals.wav"
    if not vocals_path.exists():
        # Try alternate naming patterns
        candidates = list(output_dir.glob(f"{model_name}/**/vocals.wav"))
        if candidates:
            vocals_path = candidates[0]
        else:
            raise VocalSeparationError(
                "Demucs completed but vocals file not found"
            )

    logger.info(f"Vocals extracted: {vocals_path} ({vocals_path.stat().st_size / 1024:.0f} KB)")
    return vocals_path
