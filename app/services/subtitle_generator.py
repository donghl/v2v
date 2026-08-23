"""Generate SRT subtitle files from transcription segments."""

import re
from pathlib import Path

from app.services.speech_to_text import Segment
from app.utils.logger import logger


def segments_to_srt(segments: list[Segment], output_path: Path) -> Path:
    """
    Convert transcription segments to a standard SRT file.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    for i, seg in enumerate(segments, start=1):
        start_ts = _seconds_to_srt_time(seg["start"])
        end_ts = _seconds_to_srt_time(seg["end"])
        lines.append(f"{i}")
        lines.append(f"{start_ts} --> {end_ts}")
        lines.append(seg["text"])
        lines.append("")

    content = "\n".join(lines)
    output_path.write_text(content, encoding="utf-8")
    logger.info(f"SRT written: {output_path} ({len(segments)} entries)")
    return output_path


def split_long_segments(segments: list[Segment], video_ref: int = 1080) -> list[Segment]:
    """
    Split segments that contain multiple sentences.

    Max chars per line is computed dynamically:
      max_chars = int(60 * video_ref / 1080)
    so smaller videos get shorter lines, larger videos get longer lines.

    Use BEFORE translation so original and translated share boundaries.
    """
    # Dynamic thresholds based on video size
    scale = video_ref / 1080.0
    max_chars = max(30, int(60 * scale))       # ~60 chars at 1080p
    min_split = max(50, int(80 * scale))        # ~80 chars at 1080p
    min_duration = max(1.0, 1.5 * scale)        # minimum visible time per segment

    logger.debug(f"split: max_chars={max_chars}, min_split={min_split}, video_ref={video_ref}")

    result: list[Segment] = []

    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue

        duration = seg["end"] - seg["start"]
        if duration <= 0:
            result.append(seg)
            continue

        sentences = _split_sentences(text)

        # Only split if text is long enough
        if len(text) < min_split or len(sentences) <= 1:
            result.append(seg)
            continue

        # Merge short sentences to avoid too many tiny segments
        merged = []
        for s in sentences:
            s = s.strip()
            if not s:
                continue
            if merged and len(merged[-1]) + len(s) < max_chars:
                merged[-1] = merged[-1] + " " + s
            else:
                merged.append(s)

        if len(merged) <= 1:
            result.append(seg)
            continue

        total_chars = sum(len(s) for s in merged)
        char_offset = 0
        for sent in merged:
            sent_len = len(sent)
            sub_start = seg["start"] + duration * (char_offset / total_chars)
            sub_end = seg["start"] + duration * ((char_offset + sent_len) / total_chars)
            if sub_end - sub_start < min_duration:
                sub_end = sub_start + min_duration
            result.append(Segment(start=round(sub_start, 3),
                                  end=round(sub_end, 3),
                                  text=sent))
            char_offset += sent_len

    return result


# Common abbreviations that should NOT trigger sentence splitting
_ABBREVIATIONS = {
    "dr", "mr", "mrs", "ms", "st", "ave", "blvd", "dept", "etc", "vs",
    "inc", "ltd", "co", "corp", "jan", "feb", "mar", "apr", "jun", "jul",
    "aug", "sep", "oct", "nov", "dec", "approx", "est", "govt", "jr", "sr",
    "prof", "rev", "hon", "capt", "col", "gen", "lt", "sgt", "cpt", "maj",
}


def _split_sentences(text: str) -> list[str]:
    """
    Split text into sentences at real boundaries.
    
    - Chinese punctuation （。！？）: always split
    - English period (.): only split for full sentence boundaries.
      Avoids splitting abbreviations (Dr., Mr., U.S., etc.) and numbers (1.5).
    - ?!\n: always split
    """
    # First split at unambiguous boundaries
    parts = re.split(r'(?<=[。！？!?\n])\s*', text)
    
    # Then handle periods with abbreviation awareness
    refined = []
    for part in parts:
        if not part.strip():
            continue
        # Split at ". " followed by uppercase letter
        # But NOT if the word before the period is a known abbreviation
        sub = re.split(r'(?<!\.)(\.\s+)(?=[A-Z"\'\u201c\u2018])', part)
        
        # Rebuild: check each potential split point
        segs = [""]
        for chunk in sub:
            if chunk.startswith(". "):
                # Check if previous word is an abbreviation
                prev = segs[-1].strip().split()[-1] if segs[-1].strip() else ""
                prev_word = prev.rstrip(".").lower() if prev else ""
                if prev_word in _ABBREVIATIONS:
                    # Not a real sentence boundary — append period and continue
                    segs[-1] += "."
                else:
                    # Real sentence boundary — start new segment
                    segs.append("")
            else:
                segs[-1] += chunk
        
        # Handle trailing period at end
        refined.extend(s.strip() for s in segs if s.strip())
    
    parts = [p.strip() for p in refined if p.strip()]
    parts = [p + "." if not p.endswith(".") and text.count(p + ".") > 0 else p for p in parts]
    parts = [p for p in parts if p.strip()]
    
    if not parts:
        return [text]

    final = []
    for part in parts:
        if len(part) > 100:
            sub = re.split(r'(?<=[，、,;；])\s*', part)
            sub = [s.strip() for s in sub if s.strip()]
            merged = []
            for s in sub:
                if merged and len(merged[-1]) + len(s) < 70:
                    merged[-1] = merged[-1] + s
                else:
                    merged.append(s)
            final.extend(merged)
        else:
            final.append(part)

    return final if final else [text]


def _seconds_to_srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
