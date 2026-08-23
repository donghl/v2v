"""Download videos using yt-dlp CLI (bypasses Python SSL issues)."""

import subprocess
import sys
from pathlib import Path

from app.config.settings import settings
from app.utils.logger import logger


class DownloadError(Exception):
    """Raised when video download fails."""


def download_video(url: str, output_dir: Path) -> list[tuple[Path, str]]:
    """
    Download video(s) from a URL using yt-dlp CLI.

    Returns a list of (video_path, title) for ALL downloaded media
    (handles playlists / multi-video tweets).

    Args:
        url: Video URL to download.
        output_dir: Directory to save the downloaded video.

    Returns:
        List of (video_path, title) tuples.
    """
    import shutil

    yt_dlp_path = shutil.which("yt-dlp")
    if not yt_dlp_path:
        venv_bin = Path(sys.executable).parent / "yt-dlp"
        if venv_bin.exists():
            yt_dlp_path = str(venv_bin)
        else:
            yt_dlp_path = None

    output_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(output_dir / "%(title).100s_%(id)s.%(ext)s")

    def _run_ytdlp(extra_args: list[str] | None = None) -> list[tuple[Path, str]]:
        """Run yt-dlp and return list of (video_path, title)."""
        cmd = []
        if yt_dlp_path:
            cmd = [yt_dlp_path]
        else:
            cmd = [sys.executable, "-m", "yt_dlp"]

        cmd.extend([
            "--format", "mp4/best[ext=mp4]/best[height<=1080]",
            "--output", output_template,
            "--no-check-certificates",
            "--no-warnings",
            "--restrict-filenames",
            "--extractor-retries", "5",
            "--print", "after_move:filepath",
        ])

        if extra_args:
            cmd.extend(extra_args)

        cmd.append(url)

        logger.info(f"Running: yt-dlp ...")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            raise DownloadError("yt-dlp timed out (5 min)")

        if result.returncode != 0:
            raise DownloadError(result.stderr.strip() or f"Exit code {result.returncode}")

        # Parse all file paths from output
        files: list[tuple[Path, str]] = []
        for line in result.stdout.split("\n"):
            line = line.strip()
            if line and not line.startswith("[") and not line.startswith(" "):
                p = Path(line)
                if p.exists() and p.stat().st_size > 0:
                    # Extract a short title from filename
                    title = p.stem
                    files.append((p, title))

        if not files:
            raise DownloadError("No files downloaded")

        for p, t in files:
            logger.info(f"Downloaded: {p.name} ({p.stat().st_size / 1024 / 1024:.1f} MB)")

        return files

    strategies = [
        None,
        ["--downloader", "curl_cffi"],
        ["--format", "worst[ext=mp4]/worst"],
        ["--socket-timeout", "30", "--retries", "10", "--fragment-retries", "10"],
    ]

    last_error = None
    for i, extra in enumerate(strategies):
        try:
            logger.info(f"Download attempt {i+1}/{len(strategies)}: {url}")
            return _run_ytdlp(extra)
        except Exception as e:
            last_error = e
            logger.warning(f"Attempt {i+1} failed: {str(e)[:100]}")
            continue

    raise DownloadError(f"All download strategies failed. Last error: {last_error}")
