from __future__ import annotations

import os
import re
from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash

from config import ensure_env_loaded
from store import connect, init_db

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")

SECURITY_QUESTION_PRESETS: tuple[str, ...] = (
    "Qual o nome da sua primeira escola?",
    "Qual a cidade onde nasceu?",
    "Qual o nome do seu primeiro animal de estimação?",
    "Qual o apelido de infância?",
    "Qual o nome da sua mãe (só o primeiro)?",
)


def normalize_username(raw: str) -> str:
    return str(raw or "").strip().lower()


def normalize_security_answer(raw: str) -> str:
    """Normaliza a resposta para comparação estável (sem distinguir maiúsculas)."""
    s = str(raw or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def validate_username(username: str) -> str | None:
    u = normalize_username(username)
    if not _USERNAME_RE.match(u):
        return "Utilizador: 3–32 caracteres (letras, números, . _ -)."
    return None


def validate_password(password: str) -> str | None:
    if len(password) < 6:
        return "A senha deve ter pelo menos 6 caracteres."
    return None


def validate_security_question(question: str) -> str | None:
    q = str(question or "").strip()
    if len(q) < 8:
        return "A pergunta de segurança é demasiado curta."
    if len(q) > 160:
        return "A pergunta de segurança é demasiado longa (máx. 160)."
    return None


def validate_security_answer(answer: str) -> str | None:
    a = normalize_security_answer(answer)
    if len(a) < 2:
        return "A resposta de segurança deve ter pelo menos 2 caracteres."
    if len(a) > 120:
        return "A resposta de segurança é demasiado longa."
    return None


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    if not password_hash or password is None:
        return False
    return check_password_hash(password_hash, password)


def hash_security_answer(answer: str) -> str:
    return generate_password_hash(normalize_security_answer(answer))


def verify_security_answer(answer_hash: str | None, answer: str) -> bool:
    if not answer_hash:
        return False
    return check_password_hash(answer_hash, normalize_security_answer(answer))


def _row_has(row: Any, key: str) -> bool:
    try:
        return key in row.keys()
    except Exception:
        return False


def _row_to_user(row: Any, *, include_hash: bool = False) -> dict[str, Any]:
    sq = row["security_question"] if _row_has(row, "security_question") else None
    sah = row["security_answer_hash"] if _row_has(row, "security_answer_hash") else None
    out = {
        "id": int(row["id"]),
        "username": str(row["username"]),
        "display_name": str(row["display_name"]),
        "role": str(row["role"]),
        "created_at": str(row["created_at"]),
        "has_security": bool(sq and sah),
    }
    if include_hash:
        out["password_hash"] = str(row["password_hash"])
        out["security_question"] = str(sq) if sq else None
        out["security_answer_hash"] = str(sah) if sah else None
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
    user.pop("security_answer_hash", None)
    user.pop("security_question", None)
    return user


def list_users() -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, username, display_name, role, created_at,
                   security_question, security_answer_hash
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
    security_question: str | None = None,
    security_answer: str | None = None,
    require_security: bool = False,
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

    sq: str | None = None
    sah: str | None = None
    q_raw = (security_question or "").strip()
    a_raw = security_answer or ""
    if require_security or q_raw or str(a_raw).strip():
        err = validate_security_question(q_raw)
        if err:
            raise ValueError(err)
        err = validate_security_answer(a_raw)
        if err:
            raise ValueError(err)
        sq = q_raw
        sah = hash_security_answer(a_raw)

    with connect() as conn:
        existing = conn.execute("SELECT id FROM users WHERE username = ?", (u,)).fetchone()
        if existing is not None:
            raise ValueError("Já existe um utilizador com esse nome.")
        cur = conn.execute(
            """
            INSERT INTO users (
              username, display_name, password_hash, role,
              security_question, security_answer_hash
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (u, name, hash_password(password), role, sq, sah),
        )
        kid = int(cur.lastrowid)
        row = conn.execute("SELECT * FROM users WHERE id = ?", (kid,)).fetchone()
        conn.commit()
    return _row_to_user(row)


def register_user(
    *,
    username: str,
    display_name: str,
    password: str,
    security_question: str,
    security_answer: str,
) -> dict[str, Any]:
    """Auto-cadastro público — sempre role=user, com pergunta de segurança obrigatória."""
    return create_user(
        username=username,
        display_name=display_name,
        password=password,
        role="user",
        security_question=security_question,
        security_answer=security_answer,
        require_security=True,
    )


def get_recovery_question(username: str) -> dict[str, Any] | None:
    """Devolve estado da pergunta de segurança do utilizador (ou None se não existir)."""
    user = get_user_by_username(username, include_hash=True)
    if user is None:
        return None
    q = user.get("security_question")
    h = user.get("security_answer_hash")
    if not q or not h:
        return {"username": user["username"], "question": None, "configured": False}
    return {
        "username": user["username"],
        "question": q,
        "configured": True,
    }


def reset_password_with_security(
    *,
    username: str,
    security_answer: str,
    new_password: str,
) -> dict[str, Any]:
    init_db()
    err = validate_password(new_password)
    if err:
        raise ValueError(err)
    u = normalize_username(username)
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (u,)).fetchone()
        if row is None:
            raise ValueError("Utilizador ou resposta incorrectos.")
        q = row["security_question"] if _row_has(row, "security_question") else None
        sah = row["security_answer_hash"] if _row_has(row, "security_answer_hash") else None
        if not q or not sah:
            raise ValueError(
                "Esta conta não tem pergunta de segurança. "
                "Peça ao administrador para repor a senha."
            )
        if not verify_security_answer(str(sah), security_answer):
            raise ValueError("Utilizador ou resposta incorrectos.")
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(new_password), int(row["id"])),
        )
        updated = conn.execute(
            "SELECT * FROM users WHERE id = ?", (int(row["id"]),)
        ).fetchone()
        conn.commit()
    return _row_to_user(updated)


def update_user(
    user_id: int,
    *,
    display_name: str | None = None,
    password: str | None = None,
    role: str | None = None,
    security_question: str | None = None,
    security_answer: str | None = None,
) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            return None
        name = row["display_name"]
        pw_hash = row["password_hash"]
        new_role = row["role"]
        sq = row["security_question"] if _row_has(row, "security_question") else None
        sah = row["security_answer_hash"] if _row_has(row, "security_answer_hash") else None
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
        if security_question is not None or security_answer is not None:
            q_raw = (
                str(security_question).strip()
                if security_question is not None
                else (str(sq) if sq else "")
            )
            if security_answer is not None and str(security_answer).strip() != "":
                err = validate_security_question(q_raw)
                if err:
                    raise ValueError(err)
                err = validate_security_answer(security_answer)
                if err:
                    raise ValueError(err)
                sq = q_raw
                sah = hash_security_answer(security_answer)
            elif security_question is not None:
                err = validate_security_question(q_raw)
                if err:
                    raise ValueError(err)
                if not sah:
                    raise ValueError("Indique também a resposta de segurança.")
                sq = q_raw
        conn.execute(
            """
            UPDATE users
            SET display_name = ?, password_hash = ?, role = ?,
                security_question = ?, security_answer_hash = ?
            WHERE id = ?
            """,
            (name, pw_hash, new_role, sq, sah, user_id),
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
        conn.execute(
            "DELETE FROM playlist_lessons WHERE playlist_id IN "
            "(SELECT id FROM playlists WHERE user_id = ?)",
            (user_id,),
        )
        conn.execute("DELETE FROM playlists WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM lessons WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()


def count_users() -> int:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return int(row["n"]) if row else 0
