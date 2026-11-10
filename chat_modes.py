"""Persist each Telegram chat's default download mode in SQLite."""
import sqlite3
from pathlib import Path

VALID_MODES = frozenset({"video", "mp3"})


def initialize(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS chat_modes ("
            "chat_id INTEGER PRIMARY KEY, "
            "mode TEXT NOT NULL CHECK(mode IN ('video', 'mp3'))"
            ")"
        )


def get_mode(path: Path, chat_id: int) -> str:
    with sqlite3.connect(path) as db:
        row = db.execute(
            "SELECT mode FROM chat_modes WHERE chat_id = ?", (chat_id,)
        ).fetchone()
    return row[0] if row else "video"


def set_mode(path: Path, chat_id: int, mode: str) -> None:
    if mode not in VALID_MODES:
        raise ValueError("Unsupported download mode")
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO chat_modes (chat_id, mode) VALUES (?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET mode = excluded.mode",
            (chat_id, mode),
        )
