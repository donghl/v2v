"""Download videos from popular platforms using yt-dlp."""

from pathlib import Path

from app.config.settings import settings
from app.utils.logger import logger


class DownloadError(Exception):
    """Raised when video download fails."""


# Platforms that yt-dlp supports
SUPPORTED_DOMAINS = {
    "youtube.com",
    "youtu.be",
    "facebook.com",
    "fb.com",
    "fb.watch",
    "tiktok.com",
    "x.com",
    "twitter.com",
    "instagram.com",
    "vimeo.com",
    "dailymotion.com",
    "bilibili.com",
    "reddit.com",
}


def download_video(url: str, output_dir: Path) -> tuple[Path, str]:
    """
    Download a video from a URL using yt-dlp.

    Args:
        url: Video URL to download.
        output_dir: Directory to save the downloaded video.

    Returns:
        (video_path, title) — path to the downloaded file and its title.

    Raises:
        DownloadError: If download fails or URL is unsupported.
    """
    try:
        import yt_dlp
    except ImportError:
        raise DownloadError(
            "yt-dlp not installed. Run: pip install yt-dlp"
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    output_template = str(output_dir / "%(title).100s_%(id)s.%(ext)s")

    ydl_opts = {
        "format": "mp4/best[ext=mp4]/best[height<=1080]",  # prefer mp4, max 1080p
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
        "nocheckcertificate": True,
        "restrictfilenames": True,
        # Don't download playlists / channels — single video only
        "noplaylist": True,
        "playlistend": 1,
    }

    logger.info(f"Downloading: {url}")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                raise DownloadError("yt-dlp returned no info")

            title = info.get("title", "unknown")
            # yt-dlp may have downloaded a playlist; get the single entry
            if "entries" in info:
                entries = info["entries"]
                if not entries:
                    raise DownloadError("No video found at this URL")
                entry = entries[0]
                filename = ydl.prepare_filename(entry)
            else:
                filename = ydl.prepare_filename(info)

    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(f"Download failed: {e}")
    except Exception as e:
        raise DownloadError(f"Unexpected download error: {e}")

    video_path = Path(filename)
    if not video_path.exists():
        # yt-dlp may have changed the extension — try to find it
        candidates = list(output_dir.glob(f"{video_path.stem}.*"))
        if candidates:
            video_path = candidates[0]
        else:
            raise DownloadError(f"Downloaded file not found: {filename}")

    size_mb = video_path.stat().st_size / 1024 / 1024
    logger.info(f"Downloaded: {title} → {video_path.name} ({size_mb:.1f} MB)")

    return video_path, title


def is_url_supported(url: str) -> bool:
    """Quick check whether a URL is likely supported by yt-dlp."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain in SUPPORTED_DOMAINS or parsed.scheme in ("http", "https")
    except Exception:
        return False
