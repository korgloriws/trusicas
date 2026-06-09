from __future__ import annotations

import os
import secrets
from functools import wraps
from typing import Any, Callable, TypeVar

from flask import jsonify, session

F = TypeVar("F", bound=Callable[..., Any])

ADMIN_SESSION_KEY = "trusicas_admin"


def get_secret_key() -> str:
    key = (os.getenv("TRUSICAS_SECRET_KEY") or "").strip()
    if key:
        return key
    return "dev-insecure-set-TRUSICAS_SECRET_KEY-in-env"


def get_admin_password() -> str | None:
    p = (os.getenv("TRUSICAS_ADMIN_PASSWORD") or "").strip()
    return p or None


def admin_enabled() -> bool:
    return bool(get_admin_password())


def is_admin_session() -> bool:
    return session.get(ADMIN_SESSION_KEY) is True


def require_admin(f: F) -> F:
    @wraps(f)
    def wrapped(*args: Any, **kwargs: Any):
        if not admin_enabled():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Edição desativada: defina TRUSICAS_ADMIN_PASSWORD no .env.",
                    }
                ),
                503,
            )
        if not is_admin_session():
            return (
                jsonify({"ok": False, "error": "É necessário entrar como admin."}),
                401,
            )
        return f(*args, **kwargs)

    return wrapped  # type: ignore[return-value]


def verify_admin_password(password: str) -> bool:
    expected = get_admin_password()
    if not expected:
        return False
    return secrets.compare_digest(password, expected)
