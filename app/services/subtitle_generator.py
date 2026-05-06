"""Generate SRT subtitle files from transcription segments."""

from pathlib import Path

from app.services.speech_to_text import Segment
from app.utils.logger import logger


def segments_to_srt(segments: list[Segment], output_path: Path) -> Path:
    """
    Convert transcription segments to a standard SRT file.

    Args:
        segments: List of {start, end, text} dicts.
        output_path: Where to write the .srt file.

    Returns:
        Path to the generated SRT file.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    for i, seg in enumerate(segments, start=1):
        start_ts = _seconds_to_srt_time(seg["start"])
        end_ts = _seconds_to_srt_time(seg["end"])
        lines.append(f"{i}")
        lines.append(f"{start_ts} --> {end_ts}")
        lines.append(seg["text"])
        lines.append("")  # blank line between entries

    content = "\n".join(lines)
    output_path.write_text(content, encoding="utf-8")
    logger.info(f"SRT written: {output_path} ({len(segments)} entries)")
    return output_path


def _seconds_to_srt_time(seconds: float) -> str:
    """Convert float seconds to SRT timestamp HH:MM:SS,mmm."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
