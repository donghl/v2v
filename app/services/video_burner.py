"""Burn subtitles into video using ffmpeg overlay filter.

Renders each subtitle as a transparent PNG (Pillow), then chains them
onto the video using ffmpeg's built-in overlay filter with enable='between(t,s,e)'.

No libass or drawtext required — works with any ffmpeg that has 'overlay'.
"""

import re
import subprocess
import tempfile
from pathlib import Path

from app.config.settings import settings
from app.utils.logger import logger


class BurnError(Exception):
    """Raised when subtitle burning fails."""


def burn_subtitles(
    video_path: Path,
    srt_path: Path,
    output_path: Path,
    original_srt_path: Path | None = None,
) -> Path:
    """
    Burn subtitles into video.
    
    If original_srt_path is provided, renders bilingual subtitles
    (original on top, translated below).
    """
    if not video_path.exists():
        raise BurnError(f"Video not found: {video_path}")
    if not srt_path.exists():
        raise BurnError(f"SRT not found: {srt_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    lang_tag = "双语" if original_srt_path else srt_path.stem
    logger.info(f"Burning subtitles ({lang_tag}): {video_path.name}")

    subtitles = _parse_srt(srt_path)
    if not subtitles:
        import shutil
        shutil.copy2(video_path, output_path)
        return output_path

    # Parse original SRT for bilingual mode
    orig_subtitles = _parse_srt(original_srt_path) if original_srt_path else None

    work_dir = Path(tempfile.mkdtemp(prefix="v2v_burn_"))

    # ── Get video dimensions for font scaling ──
    vw, vh = _get_video_dimensions(video_path)
    ref = min(vw, vh)  # use smaller dimension for scaling (handles portrait/landscape)

    # ── Render each subtitle as a transparent PNG ──
    png_paths = []
    for i, (start, end, text) in enumerate(subtitles):
        png = work_dir / f"sub_{i:04d}.png"
        orig_text = orig_subtitles[i][2] if orig_subtitles and i < len(orig_subtitles) else None
        _render_text_png(text, png, original_text=orig_text, video_ref=ref)
        png_paths.append(png)

    # ── Build ffmpeg command ──
    # Input 0 = video, Input 1..N = PNG images
    # Chain: [0:v][1:v]overlay=enable='between(t,s,e)'[o0];
    #        [o0][2:v]overlay=enable='between(t,s,e)'[o1]; ...
    # Input 0 = video, Input 1..N = PNG images
    inputs = ["-i", str(video_path.resolve())]
    for png in png_paths:
        inputs.extend(["-i", str(png)])

    filter_parts = []
    prev = "0:v"
    for i, (start, end, _) in enumerate(subtitles):
        label = f"o{i}" if i < len(subtitles) - 1 else "final"
        # Use H-h-30 to position from bottom (handles single & bilingual height)
        filter_parts.append(
            f"[{prev}][{i+1}:v]overlay=(W-w)/2:H-h-30:enable='between(t,{start},{end})'[{label}]"
        )
        prev = label

    filter_complex = ";".join(filter_parts)

    cmd = [
        settings.FFMPEG_PATH,
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[final]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-crf", "23",
        "-preset", "medium",
        "-c:a", "aac",
        "-b:a", "128k",
        "-y",
        str(output_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
        if result.returncode != 0:
            raise BurnError(f"ffmpeg failed: {result.stderr[-800:]}")
    except subprocess.TimeoutExpired:
        raise BurnError("Timed out (2h)")
    except FileNotFoundError:
        raise BurnError(f"ffmpeg not found")

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise BurnError("Output file empty")

    logger.info(f"Done: {output_path}")
    return output_path



def _get_video_dimensions(video_path: Path) -> tuple[int, int]:
    """Get video width and height via ffprobe."""
    try:
        import json
        cmd = [
            settings.FFPROBE_PATH,
            "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            str(video_path.resolve()),
        ]
        import subprocess as _sp
        result = _sp.run(cmd, capture_output=True, text=True, timeout=15)
        info = json.loads(result.stdout)
        for s in info.get("streams", []):
            if s.get("codec_type") == "video":
                return (s.get("width", 1920), s.get("height", 1080))
    except Exception:
        pass
    return (1920, 1080)


def _render_text_png(text: str, output_path: Path, original_text: str | None = None, video_ref: int | None = None) -> None:
    """
    Render subtitle text to a transparent PNG.
    
    Font size scales with video_ref (min of width/height) to handle
    both landscape (1920x1080) and portrait (1080x1920) videos uniformly.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise BurnError("Pillow not installed: pip install Pillow")

    # Scale font size based on video ref (min of w/h, reference: 1080)
    base_size = settings.SUBTITLE_FONT_SIZE
    if video_ref:
        scale = video_ref / 1080.0
        size = max(8, int(base_size * scale))
    else:
        size = base_size
    logger.debug(f"Font size: base={base_size}, scaled={size} (video_ref={video_ref})")
    
    # Max PNG width: 85% of video ref, to prevent text going off-screen
    max_width = int(video_ref * 0.85) if video_ref else 1600

    font = _load_font(size)

    if original_text:
        # Bilingual mode: original on top, translated below
        orig_size = max(6, int(size * 0.75))  # 75% size for original
        orig_font = _load_font(orig_size)
        line_gap = 6

        # Wrap text to max_width
        wrapped_orig = _wrap_text(original_text, orig_font, max_width)
        wrapped_trans = _wrap_text(text, font, max_width)
        lines_orig = wrapped_orig.split("\n")
        lines_trans = wrapped_trans.split("\n")

        # Measure
        dummy = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        d = ImageDraw.Draw(dummy)
        lh_orig = max(d.textbbox((0, 0), l, font=orig_font)[3] - d.textbbox((0, 0), l, font=orig_font)[1] for l in lines_orig)
        lh_trans = max(d.textbbox((0, 0), l, font=font)[3] - d.textbbox((0, 0), l, font=font)[1] for l in lines_trans)
        tw = min(max_width, max(
            max(d.textbbox((0, 0), l, font=orig_font)[2] - d.textbbox((0, 0), l, font=orig_font)[0] for l in lines_orig),
            max(d.textbbox((0, 0), l, font=font)[2] - d.textbbox((0, 0), l, font=font)[0] for l in lines_trans),
        ))
        th = lh_orig * len(lines_orig) + line_gap + lh_trans * len(lines_trans)
        h1 = lh_orig * len(lines_orig) + line_gap

        m = 20
        img = Image.new("RGBA", (tw + m * 2, th + m * 2 + 4), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        ow = settings.SUBTITLE_OUTLINE
        oc = settings.SUBTITLE_OUTLINE_COLOR
        fc = settings.SUBTITLE_FONT_COLOR

        # Draw original (top) — slightly dimmer
        orig_color = "#BBBBBB"
        y_off = m
        for line in lines_orig:
            if ow > 0:
                for dx in range(-ow, ow + 1):
                    for dy in range(-ow, ow + 1):
                        if dx != 0 or dy != 0:
                            draw.text((m + dx, y_off + dy), line, fill=oc, font=orig_font)
            draw.text((m, y_off), line, fill=orig_color, font=orig_font)
            y_off += lh_orig

        # Draw translated (bottom)
        y_off += line_gap
        for line in lines_trans:
            if ow > 0:
                for dx in range(-ow, ow + 1):
                    for dy in range(-ow, ow + 1):
                        if dx != 0 or dy != 0:
                            draw.text((m + dx, y_off + dy), line, fill=oc, font=font)
            draw.text((m, y_off), line, fill=fc, font=font)
            y_off += lh_trans
    else:
        # Single language mode
        wrapped = _wrap_text(text, font, max_width)
        lines = wrapped.split("\n")
        dummy = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        d = ImageDraw.Draw(dummy)
        lh = max(d.textbbox((0, 0), l, font=font)[3] - d.textbbox((0, 0), l, font=font)[1] for l in lines)
        tw = min(max_width, max(d.textbbox((0, 0), l, font=font)[2] - d.textbbox((0, 0), l, font=font)[0] for l in lines))
        th = lh * len(lines)

        m = 20
        img = Image.new("RGBA", (tw + m * 2, th + m * 2), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        ow = settings.SUBTITLE_OUTLINE
        oc = settings.SUBTITLE_OUTLINE_COLOR
        y_off = m
        for line in lines:
            if ow > 0:
                for dx in range(-ow, ow + 1):
                    for dy in range(-ow, ow + 1):
                        if dx != 0 or dy != 0:
                            draw.text((m + dx, y_off + dy), line, fill=oc, font=font)
            draw.text((m, y_off), line, fill=settings.SUBTITLE_FONT_COLOR, font=font)
            y_off += lh

    img.save(output_path, "PNG")


def _wrap_text(text: str, font, max_px: int) -> str:
    """Wrap text to fit within max_px pixels wide."""
    from PIL import ImageDraw, Image
    dummy = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    d = ImageDraw.Draw(dummy)
    words = text.split()
    if not words:
        return text
    lines = []
    current = words[0]
    for w in words[1:]:
        test = current + " " + w
        tw = d.textbbox((0, 0), test, font=font)[2]
        if tw <= max_px:
            current = test
        else:
            lines.append(current)
            current = w
    lines.append(current)

    # A single token can still exceed max_px — e.g. Chinese text has no
    # spaces, so the whole subtitle is one token and never wraps above.
    # Break such over-long tokens by character so the text never
    # overflows the canvas (which would clip the end of the subtitle).
    wrapped = []
    for line in lines:
        if len(line) <= 1 or d.textbbox((0, 0), line, font=font)[2] <= max_px:
            wrapped.append(line)
            continue
        chunk = ""
        for ch in line:
            if chunk and d.textbbox((0, 0), chunk + ch, font=font)[2] > max_px:
                wrapped.append(chunk)
                chunk = ch
            else:
                chunk += ch
        wrapped.append(chunk)
    return "\n".join(wrapped)


def _load_font(size: int):
    """Load a font at the given size. Returns None on failure."""
    from PIL import ImageFont
    # PingFang.ttc index 0 = PingFang SC Regular (supports CJK)
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in candidates:
        try:
            font = ImageFont.truetype(path, size)
            logger.debug(f"Loaded font: {path} size={size}")
            return font
        except Exception as e:
            logger.debug(f"Font load failed: {path}: {e}")
            continue
    logger.warning(f"No font found for size {size}, using default")
    return ImageFont.load_default()


def _parse_srt(srt_path: Path) -> list[tuple[float, float, str]]:
    content = srt_path.read_text(encoding="utf-8")
    entries = []
    for block in re.split(r"\n\n+", content.strip()):
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        ti = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if ti is None:
            continue
        parts = lines[ti].split("-->")
        start = _srt_time_to_sec(parts[0].strip())
        end = _srt_time_to_sec(parts[1].strip())
        text = " ".join(lines[ti + 1:]).strip()
        if text:
            entries.append((start, end, text))
    return entries


def _srt_time_to_sec(t: str) -> float:
    import re
    m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", t)
    if m:
        return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4]) / 1000
    return 0.0
