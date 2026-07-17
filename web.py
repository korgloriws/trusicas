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
from fetch_cifra import fetch_cifra
from fetch_lyrics import fetch_lyrics
from generate import generate_lesson
from store import (
    add_lesson_to_playlist,
    create_playlist,
    delete_lesson,
    delete_playlist,
    export_db_bytes,
    find_shared_cifra,
    find_shared_lesson,
    find_shared_lyrics,
    get_lesson,
    get_playlist,
    init_db,
    insert_lesson,
    list_lessons,
    list_lessons_grouped_by_artist,
    list_playlists,
    move_lesson_between_playlists,
    patch_lesson_metadata,
    remove_lesson_from_playlist,
    rename_playlist,
    restore_db_bytes,
    save_shared_cifra,
    save_shared_lyrics,
    update_lesson,
)
from users import (
    authenticate_user,
    create_user,
    delete_user,
    ensure_admin_user,
    get_recovery_question,
    get_user_by_id,
    list_users,
    register_user,
    reset_password_with_security,
    SECURITY_QUESTION_PRESETS,
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

    @app.get("/api/auth/security-presets")
    def api_security_presets():
        return jsonify({"ok": True, "presets": list(SECURITY_QUESTION_PRESETS)})

    @app.post("/api/auth/register")
    def api_auth_register():
        ensure_admin_user()
        payload = request.get_json(silent=True) or {}
        try:
            user = register_user(
                username=str(payload.get("username") or ""),
                display_name=str(payload.get("display_name") or ""),
                password=str(payload.get("password") or ""),
                security_question=str(payload.get("security_question") or ""),
                security_answer=str(payload.get("security_answer") or ""),
            )
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        login_user(user)
        return jsonify(
            {
                "ok": True,
                "authenticated": True,
                "user": user,
                "is_admin": False,
                "registered": True,
            }
        ), 201

    @app.post("/api/auth/recovery-question")
    def api_auth_recovery_question():
        """Devolve a pergunta de segurança (sem revelar se o user não existe)."""
        payload = request.get_json(silent=True) or {}
        username = str(payload.get("username") or "")
        info = get_recovery_question(username)
        # Resposta uniforme para não enumerar contas
        if info is None:
            return jsonify(
                {
                    "ok": True,
                    "found": False,
                    "configured": False,
                    "question": None,
                    "error": "Se a conta existir e tiver recuperação, a pergunta aparece a seguir. "
                    "Caso contrário, confirme o utilizador ou peça ajuda ao administrador.",
                }
            )
        if not info.get("configured"):
            return jsonify(
                {
                    "ok": True,
                    "found": True,
                    "configured": False,
                    "username": info["username"],
                    "question": None,
                    "error": "Esta conta não tem pergunta de segurança. "
                    "Peça ao administrador para repor a senha.",
                }
            )
        return jsonify(
            {
                "ok": True,
                "found": True,
                "configured": True,
                "username": info["username"],
                "question": info["question"],
            }
        )

    @app.post("/api/auth/recover")
    def api_auth_recover():
        payload = request.get_json(silent=True) or {}
        try:
            user = reset_password_with_security(
                username=str(payload.get("username") or ""),
                security_answer=str(payload.get("security_answer") or ""),
                new_password=str(payload.get("new_password") or ""),
            )
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify(
            {
                "ok": True,
                "username": user["username"],
                "message": "Senha actualizada. Já pode entrar com a nova senha.",
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

    def _normalize_cifra(value: Any) -> str:
        return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()

    def _apply_cifra_to_lesson(
        lesson: dict[str, Any],
        *,
        cifra_text: str | None,
        title_hint: str | None,
        artist_hint: str | None,
        keep_existing: bool = True,
    ) -> dict[str, Any]:
        out = dict(lesson)
        text = _normalize_cifra(cifra_text)
        if not text and keep_existing:
            prev = out.get("cifra")
            if isinstance(prev, str):
                text = _normalize_cifra(prev)
            elif isinstance(prev, dict):
                text = _normalize_cifra(prev.get("text"))
        if not text:
            text = _normalize_cifra(find_shared_cifra(title_hint, artist_hint))
        if text:
            out["cifra"] = {"text": text, "source": "user"}
            save_shared_cifra(title=title_hint, artist=artist_hint, cifra_text=text)
        elif "cifra" in out:
            del out["cifra"]
        return out

    @app.post("/api/lyrics/fetch")
    @require_login
    def api_lyrics_fetch():
        payload = request.get_json(silent=True) or {}
        title = str(payload.get("title") or "").strip()
        artist = str(payload.get("artist") or "").strip()
        shared_cifra = find_shared_cifra(title, artist)

        cached = find_shared_lyrics(title, artist)
        if cached:
            return jsonify(
                {
                    "ok": True,
                    "lyrics": cached["lyrics_en"],
                    "title": cached["title"],
                    "artist": cached["artist"],
                    "cifra": shared_cifra,
                    "from_cache": True,
                    "candidates": [],
                }
            )

        result = fetch_lyrics(title, artist)
        if not result.ok:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": result.error,
                        "candidates": result.candidates or [],
                    }
                ),
                404,
            )
        save_shared_lyrics(
            title=result.title or title,
            artist=result.artist or artist,
            lyrics_en=result.lyrics,
        )
        return jsonify(
            {
                "ok": True,
                "lyrics": result.lyrics,
                "title": result.title,
                "artist": result.artist,
                "cifra": shared_cifra or find_shared_cifra(result.title or title, result.artist or artist),
                "from_cache": False,
                "candidates": result.candidates or [],
            }
        )

    @app.post("/api/cifra/fetch")
    @require_login
    def api_cifra_fetch():
        payload = request.get_json(silent=True) or {}
        title = str(payload.get("title") or "").strip()
        artist = str(payload.get("artist") or "").strip()
        uid = _uid()
        lesson_id: int | None = None
        raw_id = payload.get("lesson_id")
        if raw_id is not None and str(raw_id).strip() != "":
            try:
                lesson_id = int(raw_id)
            except (TypeError, ValueError):
                lesson_id = None

        cifra_text = find_shared_cifra(title, artist)
        from_cache = bool(cifra_text)
        title_out, artist_out = title, artist
        source = "cache" if from_cache else None
        candidates: list[Any] = []

        if not cifra_text:
            result = fetch_cifra(title, artist)
            if not result.ok:
                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": result.error,
                            "candidates": result.candidates or [],
                        }
                    ),
                    404,
                )
            cifra_text = result.cifra
            title_out = result.title or title
            artist_out = result.artist or artist
            source = result.source
            candidates = result.candidates or []
            save_shared_cifra(
                title=title_out,
                artist=artist_out,
                cifra_text=cifra_text,
            )
        else:
            save_shared_cifra(title=title_out, artist=artist_out, cifra_text=cifra_text)

        saved = None
        lesson_out = None
        if lesson_id is not None:
            existing = get_lesson(lesson_id, user_id=uid)
            if existing is None:
                return jsonify({"ok": False, "error": "Lição não encontrada para guardar a cifra."}), 404
            lesson_obj = existing.get("lesson") if isinstance(existing.get("lesson"), dict) else {}
            lesson_obj = dict(lesson_obj)
            lesson_obj["cifra"] = {
                "text": cifra_text,
                "source": source or "user",
            }
            saved = patch_lesson_metadata(
                lesson_id,
                user_id=uid,
                lyrics_en=str(existing.get("lyrics_en") or ""),
                title_hint=existing.get("title_hint") or title_out or None,
                artist_hint=existing.get("artist_hint") or artist_out or None,
                lesson=lesson_obj,
            )
            lesson_out = lesson_obj
            if saved is None:
                return jsonify({"ok": False, "error": "Cifra encontrada, mas falhou ao guardar na lição."}), 500

        return jsonify(
            {
                "ok": True,
                "cifra": cifra_text,
                "title": title_out,
                "artist": artist_out,
                "from_cache": from_cache,
                "source": source,
                "candidates": candidates,
                "saved": saved,
                "lesson": lesson_out,
            }
        )

    @app.post("/api/generate")
    @require_login
    def api_generate():
        payload = request.get_json(silent=True) or {}
        lyrics = str(payload.get("lyrics") or "").strip()
        title = payload.get("title")
        artist = payload.get("artist")
        title_hint = str(title).strip() if title else None
        artist_hint = str(artist).strip() if artist else None
        cifra_in = _normalize_cifra(payload.get("cifra"))
        model = payload.get("model")
        model_override = str(model).strip() if model else None
        temp = payload.get("temperature")
        temperature = float(temp) if temp is not None and str(temp).strip() != "" else None
        uid = _uid()
        force = bool(payload.get("force"))

        replace_raw = payload.get("replace_lesson_id")
        replace_id: int | None = None
        if replace_raw is not None and str(replace_raw).strip() != "":
            try:
                replace_id = int(replace_raw)
            except (TypeError, ValueError):
                replace_id = None

        # Reutilizar lição já gerada (salvo regeneração forçada / substituição explícita)
        if not force and replace_id is None and lyrics:
            shared = find_shared_lesson(
                title=title_hint,
                artist=artist_hint,
                lyrics_en=lyrics,
                prefer_user_id=uid,
            )
            if shared is not None:
                lesson = _apply_cifra_to_lesson(
                    shared["lesson"],
                    cifra_text=cifra_in,
                    title_hint=shared.get("title_hint") or title_hint,
                    artist_hint=shared.get("artist_hint") or artist_hint,
                    keep_existing=True,
                )
                # Já é do próprio utilizador: devolve a existente sem duplicar
                if shared.get("user_id") == uid:
                    saved = {
                        "id": shared["id"],
                        "created_at": shared["created_at"],
                    }
                    return jsonify(
                        {
                            "ok": True,
                            "lesson": lesson,
                            "raw": shared.get("raw_response") or "",
                            "saved": saved,
                            "replaced": False,
                            "from_cache": True,
                            "reused_own": True,
                        }
                    )
                # De outro utilizador: clona para a conta atual
                saved = insert_lesson(
                    user_id=uid,
                    lyrics_en=shared.get("lyrics_en") or lyrics,
                    title_hint=shared.get("title_hint") or title_hint,
                    artist_hint=shared.get("artist_hint") or artist_hint,
                    model=shared.get("model") or "reutilizado",
                    lesson=lesson,
                    raw_response=shared.get("raw_response")
                    or "(lição reutilizada da coleção partilhada)",
                )
                return jsonify(
                    {
                        "ok": True,
                        "lesson": lesson,
                        "raw": shared.get("raw_response") or "",
                        "saved": saved,
                        "replaced": False,
                        "from_cache": True,
                        "reused_own": False,
                    }
                )

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

        prev_lesson = None
        if replace_id is not None:
            prev = get_lesson(replace_id, user_id=uid)
            if prev is None:
                return jsonify({"ok": False, "error": "Lição a substituir não encontrada."}), 404
            prev_lesson = prev.get("lesson") if isinstance(prev.get("lesson"), dict) else None

        lesson = dict(result.lesson)
        cifra_use = cifra_in
        if not cifra_use and prev_lesson:
            prev_c = prev_lesson.get("cifra")
            if isinstance(prev_c, str):
                cifra_use = _normalize_cifra(prev_c)
            elif isinstance(prev_c, dict):
                cifra_use = _normalize_cifra(prev_c.get("text"))
        lesson = _apply_cifra_to_lesson(
            lesson,
            cifra_text=cifra_use,
            title_hint=title_hint,
            artist_hint=artist_hint,
            keep_existing=False,
        )

        if replace_id is not None:
            saved = update_lesson(
                replace_id,
                user_id=uid,
                lyrics_en=lyrics,
                title_hint=title_hint,
                artist_hint=artist_hint,
                model=result.model_used,
                lesson=lesson,
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
                lesson=lesson,
                raw_response=result.raw,
            )
        return jsonify(
            {
                "ok": True,
                "lesson": lesson,
                "raw": result.raw,
                "saved": saved,
                "replaced": replace_id is not None,
                "from_cache": False,
                "model_used": result.model_used,
            }
        )

    def _parse_playlist_id_arg() -> int | None:
        raw = request.args.get("playlist_id", "").strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    def _playlist_json(p: Any) -> dict[str, Any]:
        return {
            "id": p.id,
            "name": p.name,
            "created_at": p.created_at,
            "lesson_count": p.lesson_count,
            "sort_order": p.sort_order,
        }

    @app.get("/api/lessons")
    @require_login
    def api_list_lessons():
        uid = _uid()
        try:
            limit = int(request.args.get("limit", "100"))
        except ValueError:
            limit = 100
        playlist_id = _parse_playlist_id_arg()
        if playlist_id is not None and get_playlist(playlist_id, user_id=uid) is None:
            return jsonify({"ok": False, "error": "Lista não encontrada."}), 404
        flat = request.args.get("flat", "").strip().lower() in {"1", "true", "yes"}
        if flat:
            try:
                offset = int(request.args.get("offset", "0"))
            except ValueError:
                offset = 0
            rows = list_lessons(
                user_id=uid, limit=limit, offset=offset, playlist_id=playlist_id
            )
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
                    "playlist_id": playlist_id,
                }
            )
        q = request.args.get("q", "").strip()
        groups, total = list_lessons_grouped_by_artist(
            user_id=uid,
            limit=limit,
            search=q or None,
            playlist_id=playlist_id,
        )
        return jsonify(
            {
                "ok": True,
                "groups": groups,
                "total": total,
                "query": q,
                "playlist_id": playlist_id,
            }
        )

    @app.get("/api/playlists")
    @require_login
    def api_list_playlists():
        rows = list_playlists(user_id=_uid())
        return jsonify({"ok": True, "playlists": [_playlist_json(p) for p in rows]})

    @app.post("/api/playlists")
    @require_login
    def api_create_playlist():
        payload = request.get_json(silent=True) or {}
        try:
            created = create_playlist(
                user_id=_uid(), name=str(payload.get("name") or "")
            )
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True, "playlist": _playlist_json(created)}), 201

    @app.patch("/api/playlists/<int:playlist_id>")
    @require_login
    def api_rename_playlist(playlist_id: int):
        payload = request.get_json(silent=True) or {}
        try:
            updated = rename_playlist(
                playlist_id, user_id=_uid(), name=str(payload.get("name") or "")
            )
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        if updated is None:
            return jsonify({"ok": False, "error": "Lista não encontrada."}), 404
        return jsonify({"ok": True, "playlist": _playlist_json(updated)})

    @app.delete("/api/playlists/<int:playlist_id>")
    @require_login
    def api_delete_playlist(playlist_id: int):
        if not delete_playlist(playlist_id, user_id=_uid()):
            return jsonify({"ok": False, "error": "Lista não encontrada."}), 404
        return jsonify({"ok": True})

    @app.post("/api/playlists/<int:playlist_id>/lessons")
    @require_login
    def api_add_lesson_to_playlist(playlist_id: int):
        payload = request.get_json(silent=True) or {}
        try:
            lesson_id = int(payload.get("lesson_id"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Indique o lesson_id."}), 400
        uid = _uid()
        if get_playlist(playlist_id, user_id=uid) is None:
            return jsonify({"ok": False, "error": "Lista não encontrada."}), 404
        if get_lesson(lesson_id, user_id=uid) is None:
            return jsonify({"ok": False, "error": "Lição não encontrada."}), 404
        add_lesson_to_playlist(playlist_id, user_id=uid, lesson_id=lesson_id)
        return jsonify({"ok": True})

    @app.delete("/api/playlists/<int:playlist_id>/lessons/<int:lesson_id>")
    @require_login
    def api_remove_lesson_from_playlist(playlist_id: int, lesson_id: int):
        uid = _uid()
        if get_playlist(playlist_id, user_id=uid) is None:
            return jsonify({"ok": False, "error": "Lista não encontrada."}), 404
        if not remove_lesson_from_playlist(
            playlist_id, user_id=uid, lesson_id=lesson_id
        ):
            return jsonify(
                {"ok": False, "error": "A música não está nesta lista."}
            ), 404
        return jsonify({"ok": True})

    @app.post("/api/lessons/<int:lesson_id>/move")
    @require_login
    def api_move_lesson(lesson_id: int):
        payload = request.get_json(silent=True) or {}
        try:
            to_playlist_id = int(payload.get("to_playlist_id"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Indique to_playlist_id."}), 400
        from_raw = payload.get("from_playlist_id", None)
        from_playlist_id: int | None
        if from_raw is None or from_raw == "":
            from_playlist_id = None
        else:
            try:
                from_playlist_id = int(from_raw)
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "from_playlist_id inválido."}), 400
        uid = _uid()
        if get_lesson(lesson_id, user_id=uid) is None:
            return jsonify({"ok": False, "error": "Lição não encontrada."}), 404
        if get_playlist(to_playlist_id, user_id=uid) is None:
            return jsonify({"ok": False, "error": "Lista de destino não encontrada."}), 404
        if from_playlist_id is not None and get_playlist(
            from_playlist_id, user_id=uid
        ) is None:
            return jsonify({"ok": False, "error": "Lista de origem não encontrada."}), 404
        if not move_lesson_between_playlists(
            user_id=uid,
            lesson_id=lesson_id,
            to_playlist_id=to_playlist_id,
            from_playlist_id=from_playlist_id,
        ):
            return jsonify({"ok": False, "error": "Não foi possível migrar a música."}), 400
        return jsonify({"ok": True})

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
