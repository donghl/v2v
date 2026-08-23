"""Subscription checker — periodically scans YouTube/Facebook channels for new videos."""

import threading
import time
from datetime import datetime, timezone

from app.config.settings import settings
from app.utils.logger import logger
from app import database
from app.services.video_downloader import SUPPORTED_DOMAINS


class SubscriptionScheduler:
    """Background thread that periodically checks subscriptions for new videos."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("Subscription scheduler started")

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        interval = getattr(settings, "SUBSCRIPTION_CHECK_INTERVAL", 3600)
        while not self._stop.is_set():
            try:
                self._check_all()
            except Exception:
                logger.exception("Subscription check error")
            self._stop.wait(interval)

    def _check_all(self) -> None:
        subs = database.list_subscriptions()
        if not subs:
            return
        logger.info(f"Checking {len(subs)} subscription(s) ...")
        for sub in subs:
            try:
                check_subscription(sub["id"])
            except Exception:
                logger.exception(f"Check failed for subscription {sub['id']}")


_scheduler = SubscriptionScheduler()


def start_scheduler() -> None:
    _scheduler.start()


def check_subscription(sub_id: int) -> list[str]:
    """Check a subscription for new videos, return list of newly created task_ids."""
    import yt_dlp

    sub = database.get_subscription(sub_id)
    if sub is None:
        return []

    channel_url = sub["channel_url"]
    target_lang = sub["target_lang"]

    logger.info(f"Checking subscription #{sub_id}: {channel_url}")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "playlistend": 20,
    }

    new_task_ids: list[str] = []
    channel_name: str | None = None
    latest_video_id: str | None = None

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(channel_url, download=False)
            if info is None:
                return []

            channel_name = info.get("channel") or info.get("uploader") or channel_url

            entries = info.get("entries") or []
            for entry in entries:
                if entry is None:
                    continue
                vid = entry.get("id") or entry.get("url")
                if vid is None:
                    continue
                if latest_video_id is None:
                    latest_video_id = vid
                # Stop at previously seen video
                if vid == sub.get("last_video_id"):
                    break
                # Submit pipeline for this video
                video_url = entry.get("url") or entry.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}"
                title = entry.get("title") or vid
                _submit_video(video_url, target_lang, title, new_task_ids)

    except Exception:
        logger.exception(f"yt-dlp failed for {channel_url}")
        return new_task_ids

    # Update subscription metadata
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    database.update_subscription(
        sub_id,
        channel_name=channel_name,
        last_video_id=latest_video_id,
        last_checked=now,
    )

    if new_task_ids:
        logger.info(f"Subscription #{sub_id}: {len(new_task_ids)} new video(s) queued")
    return new_task_ids


def _submit_video(url: str, target_lang: str, title: str, task_ids: list[str]) -> None:
    """Download a video and submit it to the pipeline."""
    from pathlib import Path
    from app.services.pipeline import submit_task, run_pipeline

    try:
        from app.services.video_downloader import download_video, DownloadError
    except ImportError:
        logger.warning("video_downloader not available, creating placeholder task")
        placeholder = settings.INPUT_DIR / f"_{url.split('/')[-1][:30] or 'video'}"
        task = submit_task(Path(placeholder), target_lang, source_url=url, source_title=title)
        task_ids.append(task.task_id)
        return

    try:
        video_path, _ = download_video(url, settings.INPUT_DIR)
        task = submit_task(video_path, target_lang, source_url=url, source_title=title)
        task_ids.append(task.task_id)
        # Run in background thread
        thread = threading.Thread(target=run_pipeline, args=(task.task_id,), daemon=True)
        thread.start()
    except DownloadError as e:
        logger.error(f"Download failed for {url}: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error downloading {url}: {e}")
