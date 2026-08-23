"""SQLite persistence layer for tasks and subscriptions."""

import sqlite3
import threading
from pathlib import Path

from app.config.settings import settings

_conn: sqlite3.Connection | None = None
_lock = threading.Lock()


def get_db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        db_path = Path(settings.DATABASE_PATH)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(db_path), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
    return _conn


def init_db() -> None:
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            task_id         TEXT PRIMARY KEY,
            status          TEXT NOT NULL DEFAULT 'waiting',
            progress        REAL NOT NULL DEFAULT 0,
            source_url      TEXT,
            source_title    TEXT,
            source_lang     TEXT,
            target_lang     TEXT NOT NULL DEFAULT 'zh',
            video_path      TEXT,
            output_path     TEXT,
            error           TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at    TEXT
        );

        CREATE TABLE IF NOT EXISTS subscriptions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_url     TEXT NOT NULL,
            channel_name    TEXT,
            platform        TEXT NOT NULL,
            target_lang     TEXT NOT NULL DEFAULT 'zh',
            last_video_id   TEXT,
            last_checked    TEXT,
            is_active       INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    db.commit()


# ── Task CRUD ──

def save_task(
    task_id: str,
    status: str = "waiting",
    progress: float = 0,
    source_url: str | None = None,
    source_title: str | None = None,
    source_lang: str | None = None,
    target_lang: str = "zh",
    video_path: str | None = None,
    output_path: str | None = None,
    error: str | None = None,
) -> None:
    with _lock:
        db = get_db()
        db.execute(
            """INSERT INTO tasks (task_id, status, progress, source_url, source_title,
               source_lang, target_lang, video_path, output_path, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(task_id) DO UPDATE SET
               status=excluded.status, progress=excluded.progress,
               source_url=excluded.source_url, source_title=excluded.source_title,
               source_lang=excluded.source_lang, target_lang=excluded.target_lang,
               video_path=excluded.video_path, output_path=excluded.output_path,
               error=excluded.error""",
            (task_id, status, progress, source_url, source_title,
             source_lang, target_lang,
             str(video_path) if video_path else None,
             str(output_path) if output_path else None,
             error),
        )
        db.commit()


def update_task(
    task_id: str,
    *,
    status: str | None = None,
    progress: float | None = None,
    source_lang: str | None = None,
    output_path: str | None = None,
    error: str | None = None,
    completed_at: str | None = None,
) -> None:
    with _lock:
        db = get_db()
        sets: list[str] = []
        params: list = []
        if status is not None:
            sets.append("status=?")
            params.append(status)
        if progress is not None:
            sets.append("progress=?")
            params.append(progress)
        if source_lang is not None:
            sets.append("source_lang=?")
            params.append(source_lang)
        if output_path is not None:
            sets.append("output_path=?")
            params.append(output_path)
        if error is not None:
            sets.append("error=?")
            params.append(error)
        if completed_at is not None:
            sets.append("completed_at=?")
            params.append(completed_at)
        if not sets:
            return
        params.append(task_id)
        db.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE task_id=?", params)
        db.commit()


def get_task(task_id: str) -> dict | None:
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    return dict(row) if row else None


def list_tasks(limit: int = 50, offset: int = 0) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


def count_tasks() -> int:
    db = get_db()
    row = db.execute("SELECT COUNT(*) as cnt FROM tasks").fetchone()
    return row["cnt"]


# ── Subscription CRUD ──

def add_subscription(channel_url: str, platform: str, target_lang: str = "zh") -> dict:
    with _lock:
        db = get_db()
        cur = db.execute(
            """INSERT INTO subscriptions (channel_url, platform, target_lang)
               VALUES (?, ?, ?)""",
            (channel_url, platform, target_lang),
        )
        db.commit()
        row = db.execute(
            "SELECT * FROM subscriptions WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)


def remove_subscription(sub_id: int) -> bool:
    with _lock:
        db = get_db()
        cur = db.execute("DELETE FROM subscriptions WHERE id=?", (sub_id,))
        db.commit()
        return cur.rowcount > 0


def list_subscriptions() -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM subscriptions WHERE is_active=1 ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_subscription(sub_id: int) -> dict | None:
    db = get_db()
    row = db.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    return dict(row) if row else None


def update_subscription(
    sub_id: int,
    *,
    channel_name: str | None = None,
    last_video_id: str | None = None,
    last_checked: str | None = None,
) -> None:
    with _lock:
        db = get_db()
        sets: list[str] = []
        params: list = []
        if channel_name is not None:
            sets.append("channel_name=?")
            params.append(channel_name)
        if last_video_id is not None:
            sets.append("last_video_id=?")
            params.append(last_video_id)
        if last_checked is not None:
            sets.append("last_checked=?")
            params.append(last_checked)
        if not sets:
            return
        params.append(sub_id)
        db.execute(f"UPDATE subscriptions SET {', '.join(sets)} WHERE id=?", params)
        db.commit()
