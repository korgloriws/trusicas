from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import ensure_env_loaded

_SQLITE_MAGIC = b"SQLite format 3\x00"
_db_lock = threading.RLock()


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def get_db_path() -> Path:
    ensure_env_loaded()
    raw = (os.getenv("TRUSICAS_DB") or os.getenv("LYRICS_LESSON_DB") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    data_dir = _project_root() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "lessons.sqlite"


class _DbSession:
    """Uma conexão por vez — necessário para restore no Windows (WAL bloqueado)."""

    def __enter__(self) -> sqlite3.Connection:
        _db_lock.acquire()
        path = get_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        try:
            self._conn.close()
        finally:
            _db_lock.release()


def connect() -> _DbSession:
    return _DbSession()


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


def _sqlite_sidecars(path: Path) -> list[Path]:
    return [Path(f"{path}-wal"), Path(f"{path}-shm")]


def _unlink_with_retry(path: Path, *, attempts: int = 8) -> None:
    last_err: OSError | None = None
    for i in range(attempts):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except OSError as e:
            last_err = e
            if i < attempts - 1:
                time.sleep(0.05 * (2**i))
    if last_err is not None:
        raise last_err


def _checkpoint_and_close(path: Path) -> None:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    try:
        conn.execute("PRAGMA wal_checkpoint(FULL)")
    finally:
        conn.close()


def export_db_bytes() -> bytes:
    """Consolidate WAL and return a portable SQLite file snapshot."""
    init_db()
    path = get_db_path()
    with connect() as conn:
        conn.execute("PRAGMA wal_checkpoint(FULL)")
    if not path.is_file():
        raise FileNotFoundError("Base de dados não encontrada.")
    return path.read_bytes()


def restore_db_bytes(data: bytes) -> dict[str, Any]:
    if len(data) < 16 or not data.startswith(_SQLITE_MAGIC):
        raise ValueError("Ficheiro inválido: não é uma base SQLite.")

    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    with _db_lock:
        if path.is_file():
            _checkpoint_and_close(path)
            backup_copy = path.with_name(f"{path.stem}.before-restore-{stamp}{path.suffix}")
            shutil.copy2(path, backup_copy)
            for sidecar in _sqlite_sidecars(path):
                if sidecar.is_file():
                    _unlink_with_retry(sidecar)

        path.write_bytes(data)
        for sidecar in _sqlite_sidecars(path):
            if sidecar.is_file():
                _unlink_with_retry(sidecar)

        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
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
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='lessons'"
            ).fetchone()
            if row is None:
                raise ValueError(
                    "Backup sem tabela «lessons» — não é um backup Trusicas válido."
                )
            count_row = conn.execute("SELECT COUNT(*) AS n FROM lessons").fetchone()
            lesson_count = int(count_row["n"]) if count_row else 0
            conn.commit()
        finally:
            conn.close()

    return {"lessons": lesson_count, "path": str(path)}
