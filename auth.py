from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar

from flask import jsonify, session

from users import ensure_admin_user, get_user_by_id

F = TypeVar("F", bound=Callable[..., Any])

USER_SESSION_KEY = "trusicas_uid"
# Legado (sessão antiga só-admin); limpo no logout
ADMIN_SESSION_KEY = "trusicas_admin"


def get_secret_key() -> str:
    from config import ensure_env_loaded
    import os

    ensure_env_loaded()
    key = (os.getenv("TRUSICAS_SECRET_KEY") or "").strip()
    if key:
        return key
    return "dev-insecure-set-TRUSICAS_SECRET_KEY-in-env"


def current_user() -> dict[str, Any] | None:
    ensure_admin_user()
    uid = session.get(USER_SESSION_KEY)
    if uid is None:
        return None
    try:
        user_id = int(uid)
    except (TypeError, ValueError):
        return None
    return get_user_by_id(user_id)


def is_logged_in() -> bool:
    return current_user() is not None


def is_admin_session() -> bool:
    user = current_user()
    return bool(user and user.get("role") == "admin")


def login_user(user: dict[str, Any]) -> None:
    session.permanent = True
    session[USER_SESSION_KEY] = int(user["id"])
    session.pop(ADMIN_SESSION_KEY, None)


def logout_user() -> None:
    session.pop(USER_SESSION_KEY, None)
    session.pop(ADMIN_SESSION_KEY, None)


def require_login(f: F) -> F:
    @wraps(f)
    def wrapped(*args: Any, **kwargs: Any):
        if current_user() is None:
            return jsonify({"ok": False, "error": "É necessário entrar na sua conta."}), 401
        return f(*args, **kwargs)

    return wrapped  # type: ignore[return-value]


def require_admin(f: F) -> F:
    @wraps(f)
    def wrapped(*args: Any, **kwargs: Any):
        user = current_user()
        if user is None:
            return jsonify({"ok": False, "error": "É necessário entrar na sua conta."}), 401
        if user.get("role") != "admin":
            return jsonify({"ok": False, "error": "Apenas administradores."}), 403
        return f(*args, **kwargs)

    return wrapped  # type: ignore[return-value]
