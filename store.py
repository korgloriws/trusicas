from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def get_db_path() -> Path:
    load_dotenv()
    raw = (os.getenv("TRUSICAS_DB") or os.getenv("LYRICS_LESSON_DB") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    data_dir = _project_root() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "lessons.sqlite"


def connect() -> sqlite3.Connection:
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lessons (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at TEXT NOT NULL DEFAULT (datetime('now')),
              title_hint TEXT,
              artist_hint TEXT,
              lyrics_en TEXT NOT NULL,
              model TEXT,
              lesson_json TEXT NOT NULL,
              raw_response TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lessons_created_at ON lessons(created_at DESC)"
        )
        conn.commit()


def insert_lesson(
    *,
    lyrics_en: str,
    title_hint: str | None,
    artist_hint: str | None,
    model: str | None,
    lesson: dict[str, Any],
    raw_response: str,
) -> dict[str, Any]:
    init_db()
    payload = json.dumps(lesson, ensure_ascii=False)
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO lessons (title_hint, artist_hint, lyrics_en, model, lesson_json, raw_response)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                title_hint,
                artist_hint,
                lyrics_en,
                model,
                payload,
                raw_response,
            ),
        )
        kid = int(cur.lastrowid)
        row = conn.execute("SELECT created_at FROM lessons WHERE id = ?", (kid,)).fetchone()
        conn.commit()
    return {"id": kid, "created_at": str(row["created_at"]) if row else ""}


def update_lesson(
    lesson_id: int,
    *,
    lyrics_en: str,
    title_hint: str | None,
    artist_hint: str | None,
    model: str | None,
    lesson: dict[str, Any],
    raw_response: str,
) -> dict[str, Any] | None:
    """Overwrite an existing row. Returns {id, created_at} or None if id missing."""
    init_db()
    payload = json.dumps(lesson, ensure_ascii=False)
    with connect() as conn:
        cur = conn.execute(
            """
            UPDATE lessons
            SET title_hint = ?, artist_hint = ?, lyrics_en = ?, model = ?,
                lesson_json = ?, raw_response = ?
            WHERE id = ?
            """,
            (title_hint, artist_hint, lyrics_en, model, payload, raw_response, lesson_id),
        )
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT created_at FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
        conn.commit()
    return {"id": lesson_id, "created_at": str(row["created_at"]) if row else ""}


def patch_lesson_metadata(
    lesson_id: int,
    *,
    lyrics_en: str,
    title_hint: str | None,
    artist_hint: str | None,
    lesson: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Update lyrics and hints; optionally replace lesson_json (generated fields)."""
    init_db()
    with connect() as conn:
        if lesson is not None:
            payload = json.dumps(lesson, ensure_ascii=False)
            cur = conn.execute(
                """
                UPDATE lessons
                SET lyrics_en = ?, title_hint = ?, artist_hint = ?,
                    lesson_json = ?
                WHERE id = ?
                """,
                (lyrics_en, title_hint, artist_hint, payload, lesson_id),
            )
        else:
            cur = conn.execute(
                """
                UPDATE lessons
                SET lyrics_en = ?, title_hint = ?, artist_hint = ?
                WHERE id = ?
                """,
                (lyrics_en, title_hint, artist_hint, lesson_id),
            )
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT created_at FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
        conn.commit()
    return {"id": lesson_id, "created_at": str(row["created_at"]) if row else ""}


@dataclass(frozen=True)
class LessonSummary:
    id: int
    created_at: str
    title_hint: str | None
    artist_hint: str | None
    model: str | None
    lyrics_preview: str


def list_lessons(*, limit: int = 100, offset: int = 0) -> list[LessonSummary]:
    init_db()
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, title_hint, artist_hint, model,
                   substr(lyrics_en, 1, 160) AS lyrics_preview
            FROM lessons
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
    return _rows_to_summaries(rows)


def _rows_to_summaries(rows: list[sqlite3.Row]) -> list[LessonSummary]:
    out: list[LessonSummary] = []
    for r in rows:
        out.append(
            LessonSummary(
                id=int(r["id"]),
                created_at=str(r["created_at"]),
                title_hint=r["title_hint"],
                artist_hint=r["artist_hint"],
                model=r["model"],
                lyrics_preview=str(r["lyrics_preview"] or ""),
            )
        )
    return out


def _library_search_terms(search: str | None) -> list[str]:
    if not search or not str(search).strip():
        return []
    return [t.strip().lower() for t in str(search).split() if t.strip()][:8]


def list_lessons_grouped_by_artist(
    *, limit: int = 500, search: str | None = None
) -> tuple[list[dict[str, Any]], int]:
    """
    Lessons ordered alphabetically by artist (case-insensitive), then by date desc within each artist.
    Rows without artist are grouped under '(sem artista)' at the end.
    Optional search: each word must match (substring, case-insensitive) in title_hint OR artist_hint OR lyrics_en.
    Returns (groups, total_count) where each group is {"artist": str, "lessons": [dict, ...]}.
    """
    init_db()
    limit = max(1, min(limit, 2000))
    terms = _library_search_terms(search)
    where_sql = ""
    args: list[Any] = []
    if terms:
        parts = []
        for term in terms:
            parts.append(
                "("
                "INSTR(LOWER(COALESCE(title_hint, '')), ?) > 0 OR "
                "INSTR(LOWER(COALESCE(artist_hint, '')), ?) > 0 OR "
                "INSTR(LOWER(lyrics_en), ?) > 0"
                ")"
            )
            args.extend([term, term, term])
        where_sql = "WHERE " + " AND ".join(parts)
    args.append(limit)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, created_at, title_hint, artist_hint, model,
                   substr(lyrics_en, 1, 160) AS lyrics_preview
            FROM lessons
            {where_sql}
            ORDER BY
              (CASE WHEN TRIM(COALESCE(artist_hint, '')) = '' THEN 1 ELSE 0 END) ASC,
              LOWER(TRIM(COALESCE(artist_hint, ''))) COLLATE NOCASE ASC,
              datetime(created_at) DESC,
              id DESC
            LIMIT ?
            """,
            args,
        ).fetchall()
    summaries = _rows_to_summaries(rows)
    groups: list[dict[str, Any]] = []
    for s in summaries:
        label = (s.artist_hint or "").strip() or "(sem artista)"
        d = {
            "id": s.id,
            "created_at": s.created_at,
            "title_hint": s.title_hint,
            "artist_hint": s.artist_hint,
            "model": s.model,
            "lyrics_preview": s.lyrics_preview,
        }
        if not groups or groups[-1]["artist"] != label:
            groups.append({"artist": label, "lessons": []})
        groups[-1]["lessons"].append(d)
    return groups, len(summaries)


def get_lesson(lesson_id: int) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id, created_at, title_hint, artist_hint, lyrics_en, model, lesson_json, raw_response
            FROM lessons WHERE id = ?
            """,
            (lesson_id,),
        ).fetchone()
    if row is None:
        return None
    try:
        lesson = json.loads(row["lesson_json"])
    except json.JSONDecodeError:
        lesson = {}
    return {
        "id": int(row["id"]),
        "created_at": str(row["created_at"]),
        "title_hint": row["title_hint"],
        "artist_hint": row["artist_hint"],
        "lyrics_en": row["lyrics_en"],
        "model": row["model"],
        "lesson": lesson,
        "raw_response": row["raw_response"],
    }


def delete_lesson(lesson_id: int) -> bool:
    init_db()
    with connect() as conn:
        cur = conn.execute("DELETE FROM lessons WHERE id = ?", (lesson_id,))
        conn.commit()
        return cur.rowcount > 0
