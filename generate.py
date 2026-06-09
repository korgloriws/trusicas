from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from client import complete_chat
from config import load_settings
from json_extract import extract_json_object
from prompt import SYSTEM_PROMPT, build_user_prompt


@dataclass
class GenerateResult:
    ok: bool
    lesson: dict[str, Any] | None
    raw: str
    error: str | None = None
    model_used: str = ""


def generate_lesson(
    lyrics: str,
    *,
    title_hint: str | None = None,
    artist_hint: str | None = None,
    temperature: float | None = None,
    model: str | None = None,
) -> GenerateResult:
    text = lyrics.strip()
    if not text:
        return GenerateResult(ok=False, lesson=None, raw="", error="Lyrics are empty.", model_used="")

    settings = load_settings(temperature=temperature)
    if model:
        from dataclasses import replace

        settings = replace(settings, model=model.strip())
    model_used = settings.model

    user = build_user_prompt(text, title_hint=title_hint, artist_hint=artist_hint)
    try:
        raw = complete_chat(settings=settings, system=SYSTEM_PROMPT, user=user)
    except RuntimeError as e:
        return GenerateResult(ok=False, lesson=None, raw="", error=str(e), model_used=model_used)
    try:
        lesson = extract_json_object(raw)
    except Exception as e:
        return GenerateResult(
            ok=False, lesson=None, raw=raw, error=f"{type(e).__name__}: {e}", model_used=model_used
        )
    return GenerateResult(ok=True, lesson=lesson, raw=raw, error=None, model_used=model_used)
