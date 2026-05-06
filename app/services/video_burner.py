"""Burn (embed) subtitles into a video using ffmpeg."""

import subprocess
from pathlib import Path

from app.config.settings import settings
from app.utils.logger import logger


class BurnError(Exception):
    """Raised when subtitle burning fails."""


def burn_subtitles(
    video_path: Path,
    srt_path: Path,
    output_path: Path,
) -> Path:
    """
    Burn subtitles into the video using ffmpeg's subtitles filter.

    Args:
        video_path: Original video.
        srt_path: Subtitle file (.srt).
        output_path: Where to write the output video.

    Returns:
        Path to the final video with embedded subtitles.
    """
    if not video_path.exists():
        raise BurnError(f"Video not found: {video_path}")
    if not srt_path.exists():
        raise BurnError(f"SRT not found: {srt_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Burning subtitles: {srt_path.name} → {video_path.name}")

    # Escape path for ffmpeg subtitles filter (replace \ with \\ and : with \\:)
    srt_escaped = str(srt_path.resolve()).replace("\\", "\\\\").replace(":", "\\:")

    font_size = settings.SUBTITLE_FONT_SIZE
    font_color = settings.SUBTITLE_FONT_COLOR
    outline_color = settings.SUBTITLE_OUTLINE_COLOR
    outline = settings.SUBTITLE_OUTLINE

    # Build subtitle style
    style = (
        f"FontSize={font_size},"
        f"PrimaryColour=&H{_color_to_hex(font_color)},"
        f"OutlineColour=&H{_color_to_hex(outline_color)},"
        f"Outline={outline},"
        f"BorderStyle=1,"
        f"Alignment=2"  # bottom-center
    )

    vf = f"subtitles='{srt_escaped}':force_style='{style}'"

    cmd = [
        settings.FFMPEG_PATH,
        "-i", str(video_path),
        "-vf", vf,
        "-c:v", "libx264",          # H.264 video
        "-crf", "23",                # good quality (lower = better)
        "-preset", "medium",
        "-c:a", "aac",               # AAC audio
        "-b:a", "128k",
        "-y",
        str(output_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if result.returncode != 0:
            raise BurnError(f"ffmpeg burning failed: {result.stderr[-500:]}")
    except subprocess.TimeoutExpired:
        raise BurnError("Subtitle burning timed out (1 hour)")
    except FileNotFoundError:
        raise BurnError(f"ffmpeg not found at '{settings.FFMPEG_PATH}'")

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise BurnError("Burning produced an empty file")

    logger.info(f"Burned video: {output_path} ({output_path.stat().st_size / 1024 / 1024:.1f} MB)")
    return output_path


def _color_to_hex(color_name: str) -> str:
    """Map common color names to BGR hex for ffmpeg (AABBGGRR)."""
    colors = {
        "white": "FFFFFF",
        "black": "000000",
        "red": "0000FF",
        "green": "00FF00",
        "blue": "FF0000",
        "yellow": "00FFFF",
        "cyan": "FFFF00",
        "magenta": "FF00FF",
    }
    return colors.get(color_name.lower(), "FFFFFF")
