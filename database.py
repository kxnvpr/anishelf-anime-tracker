"""SQLite storage and parameterized queries for the personal anime/manga tracker."""

import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get("TRACKER_DB_PATH", Path(__file__).with_name("tracker.db")))
ALLOWED_STATUSES = {"watching", "reading", "plan", "completed", "paused", "dropped"}
EDITABLE_FIELDS = {"status", "progress", "total_units", "notes"}


def connect():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db():
    with connect() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                media_type TEXT NOT NULL CHECK (media_type IN ('anime', 'manga')),
                external_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                image_url TEXT NOT NULL DEFAULT '',
                synopsis TEXT NOT NULL DEFAULT '',
                score REAL,
                total_units INTEGER,
                status TEXT NOT NULL DEFAULT 'plan'
                    CHECK (status IN ('watching', 'reading', 'plan', 'completed', 'paused', 'dropped')),
                progress INTEGER NOT NULL DEFAULT 0 CHECK (progress >= 0),
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (media_type, external_id),
                CHECK (total_units IS NULL OR total_units >= 0),
                CHECK (total_units IS NULL OR progress <= total_units)
            )"""
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_tracks_status ON tracks(status)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_tracks_title ON tracks(title COLLATE NOCASE)")


def _validate_track(data, partial=False):
    if not isinstance(data, dict):
        raise ValueError("Body must be a JSON object")
    allowed = {"media_type", "external_id", "title", "image_url", "synopsis", "score", "total_units", "status", "progress", "notes"}
    unknown = set(data) - (EDITABLE_FIELDS if partial else allowed)
    if unknown:
        raise ValueError("Unsupported field: " + ", ".join(sorted(unknown)))
    if partial:
        result = dict(data)
    else:
        result = dict(data)
        for key in ("media_type", "external_id", "title"):
            if key not in result:
                raise ValueError(f"{key} is required")
        result.setdefault("image_url", "")
        result.setdefault("synopsis", "")
        result.setdefault("status", "plan")
        result.setdefault("progress", 0)
        result.setdefault("total_units", None)
        result.setdefault("score", None)
        result.setdefault("notes", "")

    if "media_type" in result and result["media_type"] not in {"anime", "manga"}:
        raise ValueError("media_type must be anime or manga")
    if "status" in result and result["status"] not in ALLOWED_STATUSES:
        raise ValueError("Unsupported status")
    if "title" in result:
        result["title"] = str(result["title"]).strip()
        if not result["title"]:
            raise ValueError("title cannot be empty")
    for field in ("external_id", "progress", "total_units"):
        if field in result and result[field] is not None:
            try:
                result[field] = int(result[field])
            except (ValueError, TypeError):
                raise ValueError(f"{field} must be an integer") from None
            if result[field] < (1 if field == "external_id" else 0):
                raise ValueError(f"{field} must not be negative or zero")
    if "score" in result and result["score"] is not None:
        try:
            result["score"] = float(result["score"])
        except (TypeError, ValueError):
            raise ValueError("score must be numeric") from None
        if not 0 <= result["score"] <= 10:
            raise ValueError("score must be between 0 and 10")
    for field in ("image_url", "synopsis", "notes"):
        if field in result:
            result[field] = str(result[field] or "").strip()
    return result


def create_track(data):
    track = _validate_track(data)
    if track["total_units"] is not None and track["progress"] > track["total_units"]:
        raise ValueError("progress cannot exceed total_units")
    fields = ("media_type", "external_id", "title", "image_url", "synopsis", "score", "total_units", "status", "progress", "notes")
    with connect() as connection:
        cursor = connection.execute(
            f"INSERT INTO tracks ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})",
            tuple(track[field] for field in fields),
        )
        row = connection.execute("SELECT * FROM tracks WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)


def list_tracks(q="", status="", type="", sort="recent"):
    order = {"recent": "created_at DESC, id DESC", "title": "title COLLATE NOCASE ASC", "score": "score DESC, title COLLATE NOCASE ASC"}.get(sort, "created_at DESC, id DESC")
    clauses, values = [], []
    if q:
        clauses.append("title LIKE ? COLLATE NOCASE")
        values.append(f"%{q}%")
    if status:
        clauses.append("status = ?")
        values.append(status)
    if type:
        clauses.append("media_type = ?")
        values.append(type)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with connect() as connection:
        rows = connection.execute(f"SELECT * FROM tracks{where} ORDER BY {order}", values).fetchall()
    return [dict(row) for row in rows]


def get_track(track_id):
    with connect() as connection:
        row = connection.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    return dict(row) if row else None


def update_track(track_id, data):
    changes = _validate_track(data, partial=True)
    if not changes:
        raise ValueError("No editable fields provided")
    current = get_track(track_id)
    if current is None:
        return None
    progress = changes.get("progress", current["progress"])
    total = changes.get("total_units", current["total_units"])
    if total is not None and progress > total:
        raise ValueError("progress cannot exceed total_units")
    assignments = ", ".join(f"{field} = ?" for field in changes)
    values = list(changes.values()) + [track_id]
    with connect() as connection:
        connection.execute(f"UPDATE tracks SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?", values)
    return get_track(track_id)


def delete_track(track_id):
    with connect() as connection:
        cursor = connection.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
    return cursor.rowcount > 0


init_db()
