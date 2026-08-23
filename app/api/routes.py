"""FastAPI routes for video upload, status, and download."""

import shutil
import threading
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from app.config.settings import settings
from app.services.pipeline import submit_task, get_task, run_pipeline, PipelineStatus
from app.services.video_downloader import download_video, DownloadError
from app.utils.logger import logger

router = APIRouter()


@router.post("/upload")
async def upload_video(
    video: UploadFile = File(...),
    target_language: str = Form(default="zh"),
):
    """
    Upload a video file and start the subtitle translation pipeline.

    Args:
        video: Video file (mp4, mov, mkv, etc.).
        target_language: Target language code (default: zh).

    Returns:
        JSON with task_id for tracking.
    """
    if not video.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    # Validate file extension
    allowed = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".wmv"}
    suffix = Path(video.filename).suffix.lower()
    if suffix not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format '{suffix}'. Allowed: {', '.join(allowed)}",
        )

    # Save uploaded file to input directory
    stem = Path(video.filename).stem
    suffix = Path(video.filename).suffix
    input_path = settings.INPUT_DIR / f"{stem}{suffix}"
    # Avoid overwriting — append number if exists
    counter = 1
    while input_path.exists():
        input_path = settings.INPUT_DIR / f"{stem}_{counter}{suffix}"
        counter += 1

    try:
        with input_path.open("wb") as f:
            shutil.copyfileobj(video.file, f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {e}")
    finally:
        video.file.close()

    logger.info(f"Uploaded: {video.filename} → {input_path}")

    # Create task
    task = submit_task(input_path, target_language)

    # Run pipeline in background thread
    thread = threading.Thread(target=run_pipeline, args=(task.task_id,), daemon=True)
    thread.start()

    return {"task_id": task.task_id}


@router.post("/process")
async def process_local(
    filename: str = Form(...),
    target_language: str = Form(default="zh"),
):
    """
    Process a file that already exists in the input/ directory.
    No file copy — uses the file in-place.

    Args:
        filename: Name of the file in input/ directory.
        target_language: Target language code (default: zh).

    Returns:
        JSON with task_id for tracking.
    """
    input_path = settings.INPUT_DIR / filename
    if not input_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found in input/: {filename}")

    logger.info(f"Processing local: {filename}")

    task = submit_task(input_path, target_language)
    thread = threading.Thread(target=run_pipeline, args=(task.task_id,), daemon=True)
    thread.start()

    return {"task_id": task.task_id}


@router.post("/process-all")
async def process_all(
    target_language: str = Form(default="zh"),
):
    """
    Process all unprocessed video files in the input/ directory sequentially.
    Skips files with _subtitled_ in the name and files already in output/.

    Returns a list of task_ids in processing order.
    """
    input_dir = settings.INPUT_DIR
    video_files = sorted(
        f for f in input_dir.iterdir()
        if f.suffix.lower() in {".mp4", ".mov", ".mkv", ".avi", ".webm"}
        and "_subtitled_" not in f.stem
    )

    # Filter out files that already have output
    pending: list[Path] = []
    for vf in video_files:
        if not list(settings.OUTPUT_DIR.glob(f"{vf.stem}_subtitled_*")):
            pending.append(vf)

    if not pending:
        return {"message": "No files to process", "task_ids": []}

    logger.info(f"Batch processing {len(pending)} files")

    # Create all tasks upfront so the caller gets all task_ids immediately
    task_list: list[str] = []
    for vf in pending:
        t = submit_task(vf, target_language)
        task_list.append(t.task_id)
        logger.info(f"[batch] Queued: {t.task_id} → {vf.name}")

    def run_sequential():
        for tid in task_list:
            run_pipeline(tid)

    thread = threading.Thread(target=run_sequential, daemon=True)
    thread.start()

    return {"files": len(pending), "task_ids": task_list}


@router.post("/download-url")
async def download_from_url(
    url: str = Form(...),
    target_language: str = Form(default="zh"),
):
    """
    Download a video from a URL and start the subtitle translation pipeline.

    Supported platforms: YouTube, Facebook, TikTok, X (Twitter),
    Instagram, Vimeo, Bilibili, and more.

    Args:
        url: Video URL to download.
        target_language: Target language code (default: zh).

    Returns:
        JSON with task_id for tracking.
    """
    url = url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="No URL provided")

    target_lang = target_language.strip() or "zh"

    # Validate URL format
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(
            status_code=400, detail="Invalid URL: must start with http:// or https://"
        )

    # Create task first so user gets a task_id immediately
    # Use a placeholder path — replaced after download
    placeholder = settings.INPUT_DIR / f"_{url.split('/')[-1][:30] or 'video'}"
    task = submit_task(placeholder, target_lang)

    def download_then_run():
        try:
            logger.info(f"[{task.task_id}] Downloading from URL: {url}")
            results = download_video(url, settings.INPUT_DIR)
            if not results:
                raise DownloadError(f"No media downloaded from {url}")
            video_path, title = results[0]
            # Update task with the real video path
            task.video_path = video_path
            logger.info(f"[{task.task_id}] Download complete: {title}")
            # Now run the normal pipeline
            run_pipeline(task.task_id)
        except DownloadError as e:
            from app.services.pipeline import _tasks

            task.status = PipelineStatus.FAILED
            task.error = str(e)
            logger.error(f"[{task.task_id}] Download failed: {e}")
        except Exception as e:
            from app.services.pipeline import _tasks

            task.status = PipelineStatus.FAILED
            task.error = f"Unexpected error: {e}"
            logger.exception(f"[{task.task_id}] Unexpected error during download")

    thread = threading.Thread(target=download_then_run, daemon=True)
    thread.start()

    return {"task_id": task.task_id}


@router.get("/status/{task_id}")
async def task_status(task_id: str):
    """
    Get the current status of a pipeline task.

    Args:
        task_id: The task ID returned by POST /upload.

    Returns:
        JSON with status, progress, and error info.
    """
    task = get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task.to_dict()


@router.get("/download/{task_id}")
async def download_result(task_id: str):
    """
    Download the final subtitled video.

    Args:
        task_id: The task ID returned by POST /upload.

    Returns:
        The processed video file.
    """
    task = get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status != PipelineStatus.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail=f"Task not completed yet. Current status: {task.status.value}",
        )

    if task.output_path is None or not task.output_path.exists():
        raise HTTPException(status_code=404, detail="Output file not found")

    return FileResponse(
        path=str(task.output_path),
        media_type="video/mp4",
        filename=task.output_path.name,
    )


@router.get("/", response_class=HTMLResponse)
async def index():
    """Serve the web UI."""
    html_path = __import__("pathlib").Path(__file__).resolve().parent.parent / "templates" / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


# ── History ──────────────────────────────────────────────────────────────

@router.get("/history")
async def list_history(limit: int = 50, offset: int = 0):
    """List all tasks, newest first."""
    from app import database

    tasks = database.list_tasks(limit=limit, offset=offset)
    total = database.count_tasks()
    return {"tasks": tasks, "total": total, "limit": limit, "offset": offset}


@router.get("/history/{task_id}")
async def get_history_task(task_id: str):
    """Get full details of a single task."""
    from app import database

    task = database.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


# ── Subscriptions ────────────────────────────────────────────────────────

@router.post("/subscriptions")
async def create_subscription(
    channel_url: str = Form(...),
    platform: str = Form(...),
    target_language: str = Form(default="zh"),
):
    """Add a new channel subscription."""
    from app import database

    channel_url = channel_url.strip()
    if not channel_url:
        raise HTTPException(status_code=400, detail="channel_url required")

    sub = database.add_subscription(channel_url, platform, target_language)
    return {"subscription": sub}


@router.get("/subscriptions")
async def list_subscriptions_endpoint():
    """List all active subscriptions."""
    from app import database

    subs = database.list_subscriptions()
    return {"subscriptions": subs}


@router.delete("/subscriptions/{sub_id}")
async def delete_subscription(sub_id: int):
    """Delete a subscription."""
    from app import database

    ok = database.remove_subscription(sub_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return {"deleted": sub_id}


@router.post("/subscriptions/{sub_id}/check")
async def trigger_subscription_check(sub_id: int):
    """Manually trigger a check for new videos from a subscription."""
    from app.services.subscription_checker import check_subscription

    task_ids = check_subscription(sub_id)
    return {"subscription_id": sub_id, "new_tasks": len(task_ids), "task_ids": task_ids}


@router.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}
