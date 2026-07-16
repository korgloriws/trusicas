from __future__ import annotations

import os
import re
from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash

from config import ensure_env_loaded
from store import connect, init_db

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")


def normalize_username(raw: str) -> str:
    return str(raw or "").strip().lower()


def validate_username(username: str) -> str | None:
    u = normalize_username(username)
    if not _USERNAME_RE.match(u):
        return "Utilizador: 3–32 caracteres (letras, números, . _ -)."
    return None


def validate_password(password: str) -> str | None:
    if len(password) < 6:
        return "A senha deve ter pelo menos 6 caracteres."
    return None


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    if not password_hash or password is None:
        return False
    return check_password_hash(password_hash, password)


def _row_to_user(row: Any, *, include_hash: bool = False) -> dict[str, Any]:
    out = {
        "id": int(row["id"]),
        "username": str(row["username"]),
        "display_name": str(row["display_name"]),
        "role": str(row["role"]),
        "created_at": str(row["created_at"]),
    }
    if include_hash:
        out["password_hash"] = str(row["password_hash"])
    return out


def ensure_admin_user() -> None:
    """Create bootstrap admin from .env if no admin exists yet."""
    ensure_env_loaded()
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE role = 'admin' LIMIT 1"
        ).fetchone()
        if row is not None:
            return
        username = normalize_username(os.getenv("TRUSICAS_ADMIN_USERNAME") or "admin")
        password = (os.getenv("TRUSICAS_ADMIN_PASSWORD") or "").strip()
        if not password:
            password = "admin"
        display = (os.getenv("TRUSICAS_ADMIN_DISPLAY_NAME") or "Administrador").strip()
        err = validate_username(username)
        if err:
            username = "admin"
        cur = conn.execute(
            """
            INSERT INTO users (username, display_name, password_hash, role)
            VALUES (?, ?, ?, 'admin')
            """,
            (username, display or "Administrador", hash_password(password)),
        )
        admin_id = int(cur.lastrowid)
        conn.execute(
            "UPDATE lessons SET user_id = ? WHERE user_id IS NULL",
            (admin_id,),
        )
        conn.commit()


def get_user_by_id(user_id: int, *, include_hash: bool = False) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        return None
    return _row_to_user(row, include_hash=include_hash)


def get_user_by_username(username: str, *, include_hash: bool = False) -> dict[str, Any] | None:
    init_db()
    u = normalize_username(username)
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (u,)).fetchone()
    if row is None:
        return None
    return _row_to_user(row, include_hash=include_hash)


def authenticate_user(username: str, password: str) -> dict[str, Any] | None:
    user = get_user_by_username(username, include_hash=True)
    if user is None:
        return None
    if not verify_password(user["password_hash"], password):
        return None
    user.pop("password_hash", None)
    return user


def list_users() -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, username, display_name, role, created_at
            FROM users
            ORDER BY role DESC, LOWER(username) ASC
            """
        ).fetchall()
    return [_row_to_user(r) for r in rows]


def create_user(
    *,
    username: str,
    display_name: str,
    password: str,
    role: str = "user",
) -> dict[str, Any]:
    init_db()
    u = normalize_username(username)
    err = validate_username(u)
    if err:
        raise ValueError(err)
    err = validate_password(password)
    if err:
        raise ValueError(err)
    role = "admin" if str(role).strip().lower() == "admin" else "user"
    name = (display_name or "").strip() or u
    with connect() as conn:
        existing = conn.execute("SELECT id FROM users WHERE username = ?", (u,)).fetchone()
        if existing is not None:
            raise ValueError("Já existe um utilizador com esse nome.")
        cur = conn.execute(
            """
            INSERT INTO users (username, display_name, password_hash, role)
            VALUES (?, ?, ?, ?)
            """,
            (u, name, hash_password(password), role),
        )
        kid = int(cur.lastrowid)
        row = conn.execute("SELECT * FROM users WHERE id = ?", (kid,)).fetchone()
        conn.commit()
    return _row_to_user(row)


def update_user(
    user_id: int,
    *,
    display_name: str | None = None,
    password: str | None = None,
    role: str | None = None,
) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            return None
        name = row["display_name"]
        pw_hash = row["password_hash"]
        new_role = row["role"]
        if display_name is not None:
            name = str(display_name).strip() or row["username"]
        if password is not None and str(password) != "":
            err = validate_password(password)
            if err:
                raise ValueError(err)
            pw_hash = hash_password(password)
        if role is not None:
            new_role = "admin" if str(role).strip().lower() == "admin" else "user"
            if new_role != "admin" and row["role"] == "admin":
                admins = conn.execute(
                    "SELECT COUNT(*) AS n FROM users WHERE role = 'admin'"
                ).fetchone()
                if int(admins["n"]) <= 1:
                    raise ValueError("Não é possível remover o último administrador.")
        conn.execute(
            """
            UPDATE users
            SET display_name = ?, password_hash = ?, role = ?
            WHERE id = ?
            """,
            (name, pw_hash, new_role, user_id),
        )
        updated = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.commit()
    return _row_to_user(updated)


def delete_user(user_id: int, *, actor_id: int | None = None) -> None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise ValueError("Utilizador não encontrado.")
        if actor_id is not None and int(user_id) == int(actor_id):
            raise ValueError("Não pode apagar a sua própria conta.")
        if row["role"] == "admin":
            admins = conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE role = 'admin'"
            ).fetchone()
            if int(admins["n"]) <= 1:
                raise ValueError("Não é possível apagar o último administrador.")
        conn.execute("DELETE FROM lessons WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()


def count_users() -> int:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return int(row["n"]) if row else 0
