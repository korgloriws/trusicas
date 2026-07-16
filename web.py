from __future__ import annotations

import io
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request, send_file

from auth import (
    current_user,
    get_secret_key,
    login_user,
    logout_user,
    require_admin,
    require_login,
)
from generate import generate_lesson
from store import (
    delete_lesson,
    export_db_bytes,
    get_lesson,
    init_db,
    insert_lesson,
    list_lessons,
    list_lessons_grouped_by_artist,
    patch_lesson_metadata,
    restore_db_bytes,
    update_lesson,
)
from users import (
    authenticate_user,
    create_user,
    delete_user,
    ensure_admin_user,
    get_user_by_id,
    list_users,
    update_user,
    verify_password,
)

_ROOT = Path(__file__).resolve().parent


def create_app() -> Flask:
    from config import ensure_env_loaded

    ensure_env_loaded()
    app = Flask(
        __name__,
        template_folder=str(_ROOT / "templates"),
        static_folder=str(_ROOT / "static"),
    )
    app.secret_key = get_secret_key()
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    lifetime_h = float(os.getenv("TRUSICAS_ADMIN_SESSION_HOURS", "168"))
    app.permanent_session_lifetime = timedelta(hours=lifetime_h)
    init_db()
    ensure_admin_user()

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            app_name="Trusicas",
            app_tagline="Inglês com música",
        )

    @app.get("/api/auth/me")
    def api_auth_me():
        ensure_admin_user()
        user = current_user()
        return jsonify(
            {
                "ok": True,
                "authenticated": user is not None,
                "user": user,
                "is_admin": bool(user and user.get("role") == "admin"),
            }
        )

    @app.post("/api/auth/login")
    def api_auth_login():
        ensure_admin_user()
        payload = request.get_json(silent=True) or {}
        username = str(payload.get("username") or "")
        password = str(payload.get("password") or "")
        user = authenticate_user(username, password)
        if user is None:
            return jsonify({"ok": False, "error": "Utilizador ou senha incorrectos."}), 401
        login_user(user)
        return jsonify(
            {
                "ok": True,
                "authenticated": True,
                "user": user,
                "is_admin": user.get("role") == "admin",
            }
        )

    @app.post("/api/auth/logout")
    def api_auth_logout():
        logout_user()
        return jsonify({"ok": True, "authenticated": False})

    @app.patch("/api/auth/me")
    @require_login
    def api_auth_me_update():
        user = current_user()
        assert user is not None
        payload = request.get_json(silent=True) or {}
        display_name = payload.get("display_name")
        new_password = payload.get("password")
        current_password = str(payload.get("current_password") or "")

        full = get_user_by_id(int(user["id"]), include_hash=True)
        if full is None:
            return jsonify({"ok": False, "error": "Utilizador não encontrado."}), 404

        wants_password = new_password is not None and str(new_password).strip() != ""
        if wants_password or (display_name is not None and str(display_name).strip() != user["display_name"]):
            if not verify_password(full["password_hash"], current_password):
                return jsonify({"ok": False, "error": "Senha actual incorrecta."}), 401

        try:
            updated = update_user(
                int(user["id"]),
                display_name=str(display_name) if display_name is not None else None,
                password=str(new_password) if wants_password else None,
            )
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        if updated is None:
            return jsonify({"ok": False, "error": "Não foi possível actualizar."}), 500
        return jsonify({"ok": True, "user": updated, "is_admin": updated.get("role") == "admin"})

    @app.get("/api/users")
    @require_admin
    def api_list_users():
        return jsonify({"ok": True, "users": list_users()})

    @app.post("/api/users")
    @require_admin
    def api_create_user():
        payload = request.get_json(silent=True) or {}
        try:
            user = create_user(
                username=str(payload.get("username") or ""),
                display_name=str(payload.get("display_name") or ""),
                password=str(payload.get("password") or ""),
                role=str(payload.get("role") or "user"),
            )
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True, "user": user})

    @app.patch("/api/users/<int:user_id>")
    @require_admin
    def api_patch_user(user_id: int):
        payload = request.get_json(silent=True) or {}
        display_name = payload.get("display_name")
        password = payload.get("password")
        role = payload.get("role")
        try:
            updated = update_user(
                user_id,
                display_name=str(display_name) if display_name is not None else None,
                password=str(password) if password is not None and str(password) != "" else None,
                role=str(role) if role is not None else None,
            )
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        if updated is None:
            return jsonify({"ok": False, "error": "Utilizador não encontrado."}), 404
        return jsonify({"ok": True, "user": updated})

    @app.delete("/api/users/<int:user_id>")
    @require_admin
    def api_delete_user(user_id: int):
        actor = current_user()
        assert actor is not None
        try:
            delete_user(user_id, actor_id=int(actor["id"]))
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True})

    def _uid() -> int:
        user = current_user()
        assert user is not None
        return int(user["id"])

    @app.post("/api/generate")
    @require_login
    def api_generate():
        payload = request.get_json(silent=True) or {}
        lyrics = str(payload.get("lyrics") or "").strip()
        title = payload.get("title")
        artist = payload.get("artist")
        title_hint = str(title).strip() if title else None
        artist_hint = str(artist).strip() if artist else None
        model = payload.get("model")
        model_override = str(model).strip() if model else None
        temp = payload.get("temperature")
        temperature = float(temp) if temp is not None and str(temp).strip() != "" else None
        uid = _uid()

        result = generate_lesson(
            lyrics,
            title_hint=title_hint or None,
            artist_hint=artist_hint or None,
            temperature=temperature,
            model=model_override,
        )
        if not result.ok or result.lesson is None:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": result.error,
                        "raw": result.raw,
                    }
                ),
                422,
            )
        replace_raw = payload.get("replace_lesson_id")
        replace_id: int | None = None
        if replace_raw is not None and str(replace_raw).strip() != "":
            try:
                replace_id = int(replace_raw)
            except (TypeError, ValueError):
                replace_id = None
        if replace_id is not None:
            if get_lesson(replace_id, user_id=uid) is None:
                return jsonify({"ok": False, "error": "Lição a substituir não encontrada."}), 404
            saved = update_lesson(
                replace_id,
                user_id=uid,
                lyrics_en=lyrics,
                title_hint=title_hint,
                artist_hint=artist_hint,
                model=result.model_used,
                lesson=result.lesson,
                raw_response=result.raw,
            )
            if saved is None:
                return jsonify({"ok": False, "error": "Não foi possível atualizar a lição."}), 500
        else:
            saved = insert_lesson(
                user_id=uid,
                lyrics_en=lyrics,
                title_hint=title_hint,
                artist_hint=artist_hint,
                model=result.model_used,
                lesson=result.lesson,
                raw_response=result.raw,
            )
        return jsonify(
            {
                "ok": True,
                "lesson": result.lesson,
                "raw": result.raw,
                "saved": saved,
                "replaced": replace_id is not None,
            }
        )

    @app.get("/api/lessons")
    @require_login
    def api_list_lessons():
        uid = _uid()
        try:
            limit = int(request.args.get("limit", "100"))
        except ValueError:
            limit = 100
        flat = request.args.get("flat", "").strip().lower() in {"1", "true", "yes"}
        if flat:
            try:
                offset = int(request.args.get("offset", "0"))
            except ValueError:
                offset = 0
            rows = list_lessons(user_id=uid, limit=limit, offset=offset)
            return jsonify(
                {
                    "ok": True,
                    "lessons": [
                        {
                            "id": r.id,
                            "created_at": r.created_at,
                            "title_hint": r.title_hint,
                            "artist_hint": r.artist_hint,
                            "model": r.model,
                            "lyrics_preview": r.lyrics_preview,
                        }
                        for r in rows
                    ],
                }
            )
        q = request.args.get("q", "").strip()
        groups, total = list_lessons_grouped_by_artist(
            user_id=uid, limit=limit, search=q or None
        )
        return jsonify({"ok": True, "groups": groups, "total": total, "query": q})

    @app.get("/api/lessons/<int:lesson_id>")
    @require_login
    def api_get_lesson(lesson_id: int):
        row = get_lesson(lesson_id, user_id=_uid())
        if row is None:
            return jsonify({"ok": False, "error": "Lição não encontrada."}), 404
        return jsonify({"ok": True, **row})

    @app.delete("/api/lessons/<int:lesson_id>")
    @require_login
    def api_delete_lesson(lesson_id: int):
        if not delete_lesson(lesson_id, user_id=_uid()):
            return jsonify({"ok": False, "error": "Lição não encontrada."}), 404
        return jsonify({"ok": True})

    @app.patch("/api/lessons/<int:lesson_id>")
    @require_login
    def api_patch_lesson(lesson_id: int):
        payload = request.get_json(silent=True) or {}
        lyrics = str(payload.get("lyrics") or "").strip()
        if not lyrics:
            return jsonify({"ok": False, "error": "A letra não pode ficar vazia."}), 400
        title = payload.get("title")
        artist = payload.get("artist")
        title_hint = str(title).strip() if title else None
        artist_hint = str(artist).strip() if artist else None
        lesson_payload = payload.get("lesson")
        lesson: dict[str, Any] | None = None
        if lesson_payload is not None:
            if not isinstance(lesson_payload, dict):
                return jsonify({"ok": False, "error": "O campo «lesson» tem de ser um objeto JSON."}), 400
            lesson = lesson_payload
        uid = _uid()
        if get_lesson(lesson_id, user_id=uid) is None:
            return jsonify({"ok": False, "error": "Lição não encontrada."}), 404
        saved = patch_lesson_metadata(
            lesson_id,
            user_id=uid,
            lyrics_en=lyrics,
            title_hint=title_hint,
            artist_hint=artist_hint,
            lesson=lesson,
        )
        if saved is None:
            return jsonify({"ok": False, "error": "Não foi possível guardar."}), 500
        return jsonify({"ok": True, "saved": saved})

    @app.get("/api/backup")
    @require_admin
    def api_backup_download():
        try:
            data = export_db_bytes()
        except FileNotFoundError as e:
            return jsonify({"ok": False, "error": str(e)}), 404
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        filename = f"trusicas-backup-{stamp}.sqlite"
        return send_file(
            io.BytesIO(data),
            mimetype="application/x-sqlite3",
            as_attachment=True,
            download_name=filename,
        )

    @app.post("/api/backup")
    @require_admin
    def api_backup_restore():
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return jsonify({"ok": False, "error": "Envie um ficheiro .sqlite de backup."}), 400
        data = upload.read()
        if not data:
            return jsonify({"ok": False, "error": "Ficheiro vazio."}), 400
        try:
            stats = restore_db_bytes(data)
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except OSError as e:
            return jsonify({"ok": False, "error": f"Não foi possível gravar o backup: {e}"}), 500
        ensure_admin_user()
        return jsonify({"ok": True, **stats})

    return app


app = create_app()


def main() -> None:
    host = os.getenv("FLASK_HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5050"))
    debug = os.getenv("FLASK_DEBUG", "0").strip() in {"1", "true", "yes", "on"}
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
