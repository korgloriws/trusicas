from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import threading
import time
import unicodedata
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
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at TEXT NOT NULL DEFAULT (datetime('now')),
              username TEXT NOT NULL UNIQUE,
              display_name TEXT NOT NULL,
              password_hash TEXT NOT NULL,
              role TEXT NOT NULL DEFAULT 'user'
                CHECK (role IN ('admin', 'user'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)"
        )
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
              raw_response TEXT,
              user_id INTEGER
            )
            """
        )
        cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(lessons)").fetchall()}
        if "user_id" not in cols:
            conn.execute("ALTER TABLE lessons ADD COLUMN user_id INTEGER")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lessons_created_at ON lessons(created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lessons_user_id ON lessons(user_id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lyrics_cache (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at TEXT NOT NULL DEFAULT (datetime('now')),
              updated_at TEXT NOT NULL DEFAULT (datetime('now')),
              title_norm TEXT NOT NULL,
              artist_norm TEXT NOT NULL,
              title TEXT NOT NULL,
              artist TEXT NOT NULL,
              lyrics_en TEXT NOT NULL,
              UNIQUE(title_norm, artist_norm)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lyrics_cache_norm "
            "ON lyrics_cache(title_norm, artist_norm)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cifra_cache (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at TEXT NOT NULL DEFAULT (datetime('now')),
              updated_at TEXT NOT NULL DEFAULT (datetime('now')),
              title_norm TEXT NOT NULL,
              artist_norm TEXT NOT NULL,
              title TEXT NOT NULL,
              artist TEXT NOT NULL,
              cifra_text TEXT NOT NULL,
              UNIQUE(title_norm, artist_norm)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cifra_cache_norm "
            "ON cifra_cache(title_norm, artist_norm)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS playlists (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at TEXT NOT NULL DEFAULT (datetime('now')),
              user_id INTEGER NOT NULL,
              name TEXT NOT NULL,
              sort_order INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_playlists_user_id ON playlists(user_id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS playlist_lessons (
              playlist_id INTEGER NOT NULL,
              lesson_id INTEGER NOT NULL,
              position INTEGER NOT NULL DEFAULT 0,
              added_at TEXT NOT NULL DEFAULT (datetime('now')),
              PRIMARY KEY (playlist_id, lesson_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_playlist_lessons_lesson "
            "ON playlist_lessons(lesson_id)"
        )
        # Migração: lições órfãs passam para o primeiro admin (se existir)
        admin = conn.execute(
            "SELECT id FROM users WHERE role = 'admin' ORDER BY id ASC LIMIT 1"
        ).fetchone()
        if admin is not None:
            conn.execute(
                "UPDATE lessons SET user_id = ? WHERE user_id IS NULL",
                (int(admin["id"]),),
            )
        conn.commit()


def _normalize_song_part(value: str | None) -> str:
    s = unicodedata.normalize("NFKD", str(value or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.casefold().strip()
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_song_key(title: str | None, artist: str | None) -> tuple[str, str] | None:
    title_norm = _normalize_song_part(title)
    artist_norm = _normalize_song_part(artist)
    if not title_norm or not artist_norm:
        return None
    return title_norm, artist_norm


def save_shared_lyrics(
    *,
    title: str | None,
    artist: str | None,
    lyrics_en: str,
) -> bool:
    """Guarda letra partilhada por título+artista (todos os utilizadores)."""
    key = normalize_song_key(title, artist)
    lyrics = str(lyrics_en or "").strip()
    if key is None or not lyrics:
        return False
    title_norm, artist_norm = key
    title_s = str(title or "").strip() or title_norm
    artist_s = str(artist or "").strip() or artist_norm
    init_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO lyrics_cache (
              title_norm, artist_norm, title, artist, lyrics_en, updated_at
            )
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(title_norm, artist_norm) DO UPDATE SET
              title = excluded.title,
              artist = excluded.artist,
              lyrics_en = excluded.lyrics_en,
              updated_at = datetime('now')
            """,
            (title_norm, artist_norm, title_s, artist_s, lyrics),
        )
        conn.commit()
    return True


def cifra_text_from_lesson(lesson: dict[str, Any] | None) -> str:
    if not isinstance(lesson, dict):
        return ""
    raw = lesson.get("cifra")
    if isinstance(raw, str):
        return raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if isinstance(raw, dict):
        return str(raw.get("text") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return ""


def save_shared_cifra(
    *,
    title: str | None,
    artist: str | None,
    cifra_text: str,
) -> bool:
    key = normalize_song_key(title, artist)
    text = str(cifra_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if key is None or not text:
        return False
    title_norm, artist_norm = key
    title_s = str(title or "").strip() or title_norm
    artist_s = str(artist or "").strip() or artist_norm
    init_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO cifra_cache (
              title_norm, artist_norm, title, artist, cifra_text, updated_at
            )
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(title_norm, artist_norm) DO UPDATE SET
              title = excluded.title,
              artist = excluded.artist,
              cifra_text = excluded.cifra_text,
              updated_at = datetime('now')
            """,
            (title_norm, artist_norm, title_s, artist_s, text),
        )
        conn.commit()
    return True


def find_shared_cifra(title: str | None, artist: str | None) -> str | None:
    key = normalize_song_key(title, artist)
    if key is None:
        return None
    title_norm, artist_norm = key
    init_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT cifra_text FROM cifra_cache
            WHERE title_norm = ? AND artist_norm = ?
            LIMIT 1
            """,
            (title_norm, artist_norm),
        ).fetchone()
        if row is not None and str(row["cifra_text"] or "").strip():
            return str(row["cifra_text"]).strip()

        rows = conn.execute(
            """
            SELECT title_hint, artist_hint, lesson_json
            FROM lessons
            WHERE TRIM(COALESCE(lesson_json, '')) NOT IN ('', '{}')
              AND TRIM(COALESCE(title_hint, '')) != ''
              AND TRIM(COALESCE(artist_hint, '')) != ''
            ORDER BY id DESC
            LIMIT 400
            """
        ).fetchall()
        for r in rows:
            if normalize_song_key(r["title_hint"], r["artist_hint"]) != key:
                continue
            try:
                lesson = json.loads(r["lesson_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            text = cifra_text_from_lesson(lesson if isinstance(lesson, dict) else None)
            if not text:
                continue
            conn.execute(
                """
                INSERT INTO cifra_cache (
                  title_norm, artist_norm, title, artist, cifra_text, updated_at
                )
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(title_norm, artist_norm) DO UPDATE SET
                  title = excluded.title,
                  artist = excluded.artist,
                  cifra_text = excluded.cifra_text,
                  updated_at = datetime('now')
                """,
                (
                    title_norm,
                    artist_norm,
                    str(r["title_hint"] or title or "").strip(),
                    str(r["artist_hint"] or artist or "").strip(),
                    text,
                ),
            )
            conn.commit()
            return text
    return None


def find_shared_lyrics(title: str | None, artist: str | None) -> dict[str, str] | None:
    """
    Procura letra já conhecida na cache partilhada e nas lições de qualquer utilizador.
    Devolve só título/artista/letra — nunca o conteúdo da lição gerada.
    """
    key = normalize_song_key(title, artist)
    if key is None:
        return None
    title_norm, artist_norm = key
    init_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT title, artist, lyrics_en
            FROM lyrics_cache
            WHERE title_norm = ? AND artist_norm = ?
            LIMIT 1
            """,
            (title_norm, artist_norm),
        ).fetchone()
        if row is not None and str(row["lyrics_en"] or "").strip():
            return {
                "title": str(row["title"] or title or "").strip(),
                "artist": str(row["artist"] or artist or "").strip(),
                "lyrics_en": str(row["lyrics_en"]).strip(),
            }

        # Match rápido nas lições (case-insensitive, sem normalizar acentos)
        row = conn.execute(
            """
            SELECT title_hint, artist_hint, lyrics_en
            FROM lessons
            WHERE TRIM(COALESCE(lyrics_en, '')) != ''
              AND LOWER(TRIM(COALESCE(title_hint, ''))) = LOWER(TRIM(?))
              AND LOWER(TRIM(COALESCE(artist_hint, ''))) = LOWER(TRIM(?))
            ORDER BY id DESC
            LIMIT 1
            """,
            (str(title or ""), str(artist or "")),
        ).fetchone()
        if row is not None and str(row["lyrics_en"] or "").strip():
            hit = {
                "title": str(row["title_hint"] or title or "").strip(),
                "artist": str(row["artist_hint"] or artist or "").strip(),
                "lyrics_en": str(row["lyrics_en"]).strip(),
            }
            conn.execute(
                """
                INSERT INTO lyrics_cache (
                  title_norm, artist_norm, title, artist, lyrics_en, updated_at
                )
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(title_norm, artist_norm) DO UPDATE SET
                  title = excluded.title,
                  artist = excluded.artist,
                  lyrics_en = excluded.lyrics_en,
                  updated_at = datetime('now')
                """,
                (
                    title_norm,
                    artist_norm,
                    hit["title"],
                    hit["artist"],
                    hit["lyrics_en"],
                ),
            )
            conn.commit()
            return hit

        rows = conn.execute(
            """
            SELECT title_hint, artist_hint, lyrics_en
            FROM lessons
            WHERE TRIM(COALESCE(lyrics_en, '')) != ''
              AND TRIM(COALESCE(title_hint, '')) != ''
              AND TRIM(COALESCE(artist_hint, '')) != ''
            ORDER BY id DESC
            LIMIT 400
            """
        ).fetchall()
        for r in rows:
            if normalize_song_key(r["title_hint"], r["artist_hint"]) != key:
                continue
            lyrics = str(r["lyrics_en"] or "").strip()
            if not lyrics:
                continue
            hit = {
                "title": str(r["title_hint"] or title or "").strip(),
                "artist": str(r["artist_hint"] or artist or "").strip(),
                "lyrics_en": lyrics,
            }
            conn.execute(
                """
                INSERT INTO lyrics_cache (
                  title_norm, artist_norm, title, artist, lyrics_en, updated_at
                )
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(title_norm, artist_norm) DO UPDATE SET
                  title = excluded.title,
                  artist = excluded.artist,
                  lyrics_en = excluded.lyrics_en,
                  updated_at = datetime('now')
                """,
                (
                    title_norm,
                    artist_norm,
                    hit["title"],
                    hit["artist"],
                    hit["lyrics_en"],
                ),
            )
            conn.commit()
            return hit
    return None


def _lyrics_fingerprint(lyrics: str | None) -> str:
    s = re.sub(r"\s+", " ", str(lyrics or "").strip().casefold())
    return s[:500]


def _lesson_from_row(row: sqlite3.Row) -> dict[str, Any] | None:
    try:
        lesson = json.loads(row["lesson_json"])
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(lesson, dict) or not lesson:
        return None
    # Lição mínima utilizável
    if not (lesson.get("translation") or lesson.get("vocabulary") or lesson.get("structures")):
        return None
    return {
        "id": int(row["id"]),
        "created_at": str(row["created_at"]),
        "user_id": int(row["user_id"]) if row["user_id"] is not None else None,
        "title_hint": row["title_hint"],
        "artist_hint": row["artist_hint"],
        "lyrics_en": str(row["lyrics_en"] or ""),
        "model": row["model"],
        "lesson": lesson,
        "raw_response": row["raw_response"] or "",
    }


def find_shared_lesson(
    *,
    title: str | None,
    artist: str | None,
    lyrics_en: str | None = None,
    prefer_user_id: int | None = None,
    exclude_lesson_id: int | None = None,
) -> dict[str, Any] | None:
    """
    Procura uma lição já gerada (qualquer utilizador) pelo título+artista
    e, se possível, pela letra. Prefere a do próprio utilizador.
    """
    key = normalize_song_key(title, artist)
    fp = _lyrics_fingerprint(lyrics_en) if lyrics_en else ""
    if key is None and len(fp) < 40:
        return None

    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, user_id, title_hint, artist_hint,
                   lyrics_en, model, lesson_json, raw_response
            FROM lessons
            WHERE TRIM(COALESCE(lesson_json, '')) NOT IN ('', '{}')
              AND TRIM(COALESCE(lyrics_en, '')) != ''
            ORDER BY id DESC
            LIMIT 500
            """
        ).fetchall()

    scored: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        if exclude_lesson_id is not None and int(row["id"]) == exclude_lesson_id:
            continue
        parsed = _lesson_from_row(row)
        if parsed is None:
            continue
        score = 0
        row_key = normalize_song_key(row["title_hint"], row["artist_hint"])
        if key is not None and row_key == key:
            score += 100
        elif key is not None:
            continue  # título+artista pedidos: só aceitar match de chave
        row_fp = _lyrics_fingerprint(row["lyrics_en"])
        if fp and row_fp:
            if row_fp == fp:
                score += 50
            elif fp[:120] and (fp[:120] in row_fp or row_fp[:120] in fp):
                score += 25
            elif key is None:
                continue  # sem título: exige overlap de letra
        if score <= 0:
            continue
        if prefer_user_id is not None and parsed["user_id"] == prefer_user_id:
            score += 20
        scored.append((score, parsed))

    if not scored:
        return None
    scored.sort(key=lambda x: (x[0], x[1]["id"]), reverse=True)
    return scored[0][1]


def insert_lesson(
    *,
    user_id: int,
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
            INSERT INTO lessons (
              title_hint, artist_hint, lyrics_en, model, lesson_json, raw_response, user_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title_hint,
                artist_hint,
                lyrics_en,
                model,
                payload,
                raw_response,
                user_id,
            ),
        )
        kid = int(cur.lastrowid)
        row = conn.execute("SELECT created_at FROM lessons WHERE id = ?", (kid,)).fetchone()
        conn.commit()
    save_shared_lyrics(title=title_hint, artist=artist_hint, lyrics_en=lyrics_en)
    cifra = cifra_text_from_lesson(lesson)
    if cifra:
        save_shared_cifra(title=title_hint, artist=artist_hint, cifra_text=cifra)
    return {"id": kid, "created_at": str(row["created_at"]) if row else ""}


def update_lesson(
    lesson_id: int,
    *,
    user_id: int,
    lyrics_en: str,
    title_hint: str | None,
    artist_hint: str | None,
    model: str | None,
    lesson: dict[str, Any],
    raw_response: str,
) -> dict[str, Any] | None:
    """Overwrite an existing row owned by user_id. Returns {id, created_at} or None."""
    init_db()
    payload = json.dumps(lesson, ensure_ascii=False)
    with connect() as conn:
        cur = conn.execute(
            """
            UPDATE lessons
            SET title_hint = ?, artist_hint = ?, lyrics_en = ?, model = ?,
                lesson_json = ?, raw_response = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                title_hint,
                artist_hint,
                lyrics_en,
                model,
                payload,
                raw_response,
                lesson_id,
                user_id,
            ),
        )
        if cur.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT created_at FROM lessons WHERE id = ? AND user_id = ?",
            (lesson_id, user_id),
        ).fetchone()
        conn.commit()
    save_shared_lyrics(title=title_hint, artist=artist_hint, lyrics_en=lyrics_en)
    cifra = cifra_text_from_lesson(lesson)
    if cifra:
        save_shared_cifra(title=title_hint, artist=artist_hint, cifra_text=cifra)
    return {"id": lesson_id, "created_at": str(row["created_at"]) if row else ""}


def patch_lesson_metadata(
    lesson_id: int,
    *,
    user_id: int,
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
                WHERE id = ? AND user_id = ?
                """,
                (lyrics_en, title_hint, artist_hint, payload, lesson_id, user_id),
            )
        else:
            cur = conn.execute(
                """
                UPDATE lessons
                SET lyrics_en = ?, title_hint = ?, artist_hint = ?
                WHERE id = ? AND user_id = ?
                """,
                (lyrics_en, title_hint, artist_hint, lesson_id, user_id),
            )
        if cur.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT created_at FROM lessons WHERE id = ? AND user_id = ?",
            (lesson_id, user_id),
        ).fetchone()
        conn.commit()
    save_shared_lyrics(title=title_hint, artist=artist_hint, lyrics_en=lyrics_en)
    if lesson is not None:
        cifra = cifra_text_from_lesson(lesson)
        if cifra:
            save_shared_cifra(title=title_hint, artist=artist_hint, cifra_text=cifra)
    return {"id": lesson_id, "created_at": str(row["created_at"]) if row else ""}


@dataclass(frozen=True)
class LessonSummary:
    id: int
    created_at: str
    title_hint: str | None
    artist_hint: str | None
    model: str | None
    lyrics_preview: str


@dataclass(frozen=True)
class PlaylistSummary:
    id: int
    name: str
    created_at: str
    lesson_count: int
    sort_order: int


def list_playlists(*, user_id: int) -> list[PlaylistSummary]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.name, p.created_at, p.sort_order,
                   COUNT(pl.lesson_id) AS lesson_count
            FROM playlists p
            LEFT JOIN playlist_lessons pl ON pl.playlist_id = p.id
            WHERE p.user_id = ?
            GROUP BY p.id
            ORDER BY p.sort_order ASC, LOWER(p.name) COLLATE NOCASE ASC, p.id ASC
            """,
            (user_id,),
        ).fetchall()
    return [
        PlaylistSummary(
            id=int(r["id"]),
            name=str(r["name"]),
            created_at=str(r["created_at"]),
            lesson_count=int(r["lesson_count"] or 0),
            sort_order=int(r["sort_order"] or 0),
        )
        for r in rows
    ]


def create_playlist(*, user_id: int, name: str) -> PlaylistSummary:
    init_db()
    clean = str(name or "").strip()
    if not clean:
        raise ValueError("Indique um nome para a lista.")
    if len(clean) > 80:
        raise ValueError("O nome da lista é demasiado longo (máx. 80).")
    with connect() as conn:
        exists = conn.execute(
            """
            SELECT id FROM playlists
            WHERE user_id = ? AND LOWER(TRIM(name)) = LOWER(?)
            LIMIT 1
            """,
            (user_id, clean),
        ).fetchone()
        if exists is not None:
            raise ValueError("Já existe uma lista com este nome.")
        max_ord = conn.execute(
            "SELECT COALESCE(MAX(sort_order), 0) AS m FROM playlists WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        sort_order = int(max_ord["m"] if max_ord else 0) + 1
        cur = conn.execute(
            """
            INSERT INTO playlists (user_id, name, sort_order)
            VALUES (?, ?, ?)
            """,
            (user_id, clean, sort_order),
        )
        pid = int(cur.lastrowid)
        row = conn.execute(
            "SELECT id, name, created_at, sort_order FROM playlists WHERE id = ?",
            (pid,),
        ).fetchone()
        conn.commit()
    return PlaylistSummary(
        id=int(row["id"]),
        name=str(row["name"]),
        created_at=str(row["created_at"]),
        lesson_count=0,
        sort_order=int(row["sort_order"] or 0),
    )


def rename_playlist(
    playlist_id: int, *, user_id: int, name: str
) -> PlaylistSummary | None:
    init_db()
    clean = str(name or "").strip()
    if not clean:
        raise ValueError("Indique um nome para a lista.")
    if len(clean) > 80:
        raise ValueError("O nome da lista é demasiado longo (máx. 80).")
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM playlists WHERE id = ? AND user_id = ?",
            (playlist_id, user_id),
        ).fetchone()
        if row is None:
            return None
        clash = conn.execute(
            """
            SELECT id FROM playlists
            WHERE user_id = ? AND LOWER(TRIM(name)) = LOWER(?) AND id != ?
            LIMIT 1
            """,
            (user_id, clean, playlist_id),
        ).fetchone()
        if clash is not None:
            raise ValueError("Já existe uma lista com este nome.")
        conn.execute(
            "UPDATE playlists SET name = ? WHERE id = ? AND user_id = ?",
            (clean, playlist_id, user_id),
        )
        conn.commit()
    items = [p for p in list_playlists(user_id=user_id) if p.id == playlist_id]
    return items[0] if items else None


def delete_playlist(playlist_id: int, *, user_id: int) -> bool:
    """Apaga a lista (não apaga as lições)."""
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM playlists WHERE id = ? AND user_id = ?",
            (playlist_id, user_id),
        ).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM playlist_lessons WHERE playlist_id = ?", (playlist_id,))
        conn.execute(
            "DELETE FROM playlists WHERE id = ? AND user_id = ?",
            (playlist_id, user_id),
        )
        conn.commit()
    return True


def get_playlist(playlist_id: int, *, user_id: int) -> PlaylistSummary | None:
    for p in list_playlists(user_id=user_id):
        if p.id == playlist_id:
            return p
    return None


def add_lesson_to_playlist(
    playlist_id: int, *, user_id: int, lesson_id: int
) -> bool:
    init_db()
    with connect() as conn:
        pl = conn.execute(
            "SELECT id FROM playlists WHERE id = ? AND user_id = ?",
            (playlist_id, user_id),
        ).fetchone()
        if pl is None:
            return False
        lesson = conn.execute(
            "SELECT id FROM lessons WHERE id = ? AND user_id = ?",
            (lesson_id, user_id),
        ).fetchone()
        if lesson is None:
            return False
        max_pos = conn.execute(
            """
            SELECT COALESCE(MAX(position), 0) AS m
            FROM playlist_lessons WHERE playlist_id = ?
            """,
            (playlist_id,),
        ).fetchone()
        position = int(max_pos["m"] if max_pos else 0) + 1
        conn.execute(
            """
            INSERT OR IGNORE INTO playlist_lessons (playlist_id, lesson_id, position)
            VALUES (?, ?, ?)
            """,
            (playlist_id, lesson_id, position),
        )
        conn.commit()
    return True


def remove_lesson_from_playlist(
    playlist_id: int, *, user_id: int, lesson_id: int
) -> bool:
    init_db()
    with connect() as conn:
        pl = conn.execute(
            "SELECT id FROM playlists WHERE id = ? AND user_id = ?",
            (playlist_id, user_id),
        ).fetchone()
        if pl is None:
            return False
        cur = conn.execute(
            """
            DELETE FROM playlist_lessons
            WHERE playlist_id = ? AND lesson_id = ?
            """,
            (playlist_id, lesson_id),
        )
        conn.commit()
        return cur.rowcount > 0


def move_lesson_between_playlists(
    *,
    user_id: int,
    lesson_id: int,
    to_playlist_id: int,
    from_playlist_id: int | None = None,
) -> bool:
    """
    Adiciona a lição à lista destino.
    Se from_playlist_id for indicado, remove da origem (migração).
    """
    if not add_lesson_to_playlist(
        to_playlist_id, user_id=user_id, lesson_id=lesson_id
    ):
        return False
    if from_playlist_id is not None and from_playlist_id != to_playlist_id:
        remove_lesson_from_playlist(
            from_playlist_id, user_id=user_id, lesson_id=lesson_id
        )
    return True


def list_playlists_for_lesson(*, user_id: int, lesson_id: int) -> list[int]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT pl.playlist_id
            FROM playlist_lessons pl
            INNER JOIN playlists p ON p.id = pl.playlist_id
            WHERE pl.lesson_id = ? AND p.user_id = ?
            """,
            (lesson_id, user_id),
        ).fetchall()
    return [int(r["playlist_id"]) for r in rows]


def list_lessons(
    *, user_id: int, limit: int = 100, offset: int = 0, playlist_id: int | None = None
) -> list[LessonSummary]:
    init_db()
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    with connect() as conn:
        if playlist_id is not None:
            pl = conn.execute(
                "SELECT id FROM playlists WHERE id = ? AND user_id = ?",
                (playlist_id, user_id),
            ).fetchone()
            if pl is None:
                return []
            rows = conn.execute(
                """
                SELECT l.id, l.created_at, l.title_hint, l.artist_hint, l.model,
                       substr(l.lyrics_en, 1, 160) AS lyrics_preview
                FROM lessons l
                INNER JOIN playlist_lessons pl ON pl.lesson_id = l.id
                WHERE l.user_id = ? AND pl.playlist_id = ?
                ORDER BY pl.position ASC, datetime(l.created_at) DESC, l.id DESC
                LIMIT ? OFFSET ?
                """,
                (user_id, playlist_id, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, created_at, title_hint, artist_hint, model,
                       substr(lyrics_en, 1, 160) AS lyrics_preview
                FROM lessons
                WHERE user_id = ?
                ORDER BY datetime(created_at) DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (user_id, limit, offset),
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
    *,
    user_id: int,
    limit: int = 500,
    search: str | None = None,
    playlist_id: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """
    Lessons ordered alphabetically by artist (case-insensitive), then by date desc within each artist.
    Rows without artist are grouped under '(sem artista)' at the end.
    Optional search: each word must match (substring, case-insensitive) in title_hint OR artist_hint OR lyrics_en.
    Optional playlist_id: only lessons in that playlist.
    Returns (groups, total_count) where each group is {"artist": str, "lessons": [dict, ...]}.
    """
    init_db()
    limit = max(1, min(limit, 2000))
    terms = _library_search_terms(search)
    where_parts = ["l.user_id = ?"]
    args: list[Any] = [user_id]
    join_sql = ""
    if playlist_id is not None:
        join_sql = "INNER JOIN playlist_lessons pl ON pl.lesson_id = l.id"
        where_parts.append("pl.playlist_id = ?")
        args.append(playlist_id)
    if terms:
        for term in terms:
            where_parts.append(
                "("
                "INSTR(LOWER(COALESCE(l.title_hint, '')), ?) > 0 OR "
                "INSTR(LOWER(COALESCE(l.artist_hint, '')), ?) > 0 OR "
                "INSTR(LOWER(l.lyrics_en), ?) > 0"
                ")"
            )
            args.extend([term, term, term])
    where_sql = "WHERE " + " AND ".join(where_parts)
    args.append(limit)
    with connect() as conn:
        if playlist_id is not None:
            pl = conn.execute(
                "SELECT id FROM playlists WHERE id = ? AND user_id = ?",
                (playlist_id, user_id),
            ).fetchone()
            if pl is None:
                return [], 0
        rows = conn.execute(
            f"""
            SELECT l.id, l.created_at, l.title_hint, l.artist_hint, l.model,
                   substr(l.lyrics_en, 1, 160) AS lyrics_preview
            FROM lessons l
            {join_sql}
            {where_sql}
            ORDER BY
              (CASE WHEN TRIM(COALESCE(l.artist_hint, '')) = '' THEN 1 ELSE 0 END) ASC,
              LOWER(TRIM(COALESCE(l.artist_hint, ''))) COLLATE NOCASE ASC,
              datetime(l.created_at) DESC,
              l.id DESC
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


def get_lesson(lesson_id: int, *, user_id: int) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id, created_at, title_hint, artist_hint, lyrics_en, model, lesson_json, raw_response
            FROM lessons WHERE id = ? AND user_id = ?
            """,
            (lesson_id, user_id),
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


def delete_lesson(lesson_id: int, *, user_id: int) -> bool:
    init_db()
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM lessons WHERE id = ? AND user_id = ?",
            (lesson_id, user_id),
        )
        if cur.rowcount > 0:
            conn.execute("DELETE FROM playlist_lessons WHERE lesson_id = ?", (lesson_id,))
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
                CREATE TABLE IF NOT EXISTS users (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  created_at TEXT NOT NULL DEFAULT (datetime('now')),
                  username TEXT NOT NULL UNIQUE,
                  display_name TEXT NOT NULL,
                  password_hash TEXT NOT NULL,
                  role TEXT NOT NULL DEFAULT 'user'
                    CHECK (role IN ('admin', 'user'))
                )
                """
            )
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
                  raw_response TEXT,
                  user_id INTEGER
                )
                """
            )
            cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(lessons)").fetchall()}
            if "user_id" not in cols:
                conn.execute("ALTER TABLE lessons ADD COLUMN user_id INTEGER")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS lyrics_cache (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  created_at TEXT NOT NULL DEFAULT (datetime('now')),
                  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                  title_norm TEXT NOT NULL,
                  artist_norm TEXT NOT NULL,
                  title TEXT NOT NULL,
                  artist TEXT NOT NULL,
                  lyrics_en TEXT NOT NULL,
                  UNIQUE(title_norm, artist_norm)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cifra_cache (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  created_at TEXT NOT NULL DEFAULT (datetime('now')),
                  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                  title_norm TEXT NOT NULL,
                  artist_norm TEXT NOT NULL,
                  title TEXT NOT NULL,
                  artist TEXT NOT NULL,
                  cifra_text TEXT NOT NULL,
                  UNIQUE(title_norm, artist_norm)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS playlists (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  created_at TEXT NOT NULL DEFAULT (datetime('now')),
                  user_id INTEGER NOT NULL,
                  name TEXT NOT NULL,
                  sort_order INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_playlists_user_id ON playlists(user_id)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS playlist_lessons (
                  playlist_id INTEGER NOT NULL,
                  lesson_id INTEGER NOT NULL,
                  position INTEGER NOT NULL DEFAULT 0,
                  added_at TEXT NOT NULL DEFAULT (datetime('now')),
                  PRIMARY KEY (playlist_id, lesson_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_playlist_lessons_lesson "
                "ON playlist_lessons(lesson_id)"
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
