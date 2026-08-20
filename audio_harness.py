from __future__ import annotations

"""
Harness de áudio: tools + pipeline (busca YouTube sem API + conversão MP3).

Padrão harness: o código orquestra as tools; um passo LLM opcional escolhe
entre candidatos quando a pontuação heurística empatar.
"""

import json
import re
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from fetch_youtube import (
    convert_share_link_to_mp3,
    extract_youtube_video_id,
    resolve_youtube_audio,
    search_youtube,
    search_youtube_scrape,
    youtube_cookies_configured,
)


@dataclass
class ToolStep:
    tool: str
    ok: bool
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class HarnessResult:
    ok: bool
    video_id: str | None = None
    title: str | None = None
    channel_title: str | None = None
    url: str | None = None
    play_url: str | None = None
    mime: str | None = None
    ext: str | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    steps: list[ToolStep] = field(default_factory=list)
    error: str | None = None
    picker: str | None = None  # "score" | "llm" | "url"


def _tool_resolve_share_link(url_or_id: str) -> dict[str, Any]:
    vid = extract_youtube_video_id(url_or_id)
    if not vid:
        return {"ok": False, "error": "Link de partilha / video_id inválido."}
    return {
        "ok": True,
        "video_id": vid,
        "url": f"https://www.youtube.com/watch?v={vid}",
    }


def _tool_search_tracks(title: str, artist: str, *, max_results: int = 8) -> dict[str, Any]:
    """Busca sem YouTube Data API (yt-dlp ytsearch). Fallback à API se houver chave."""
    scraped = search_youtube_scrape(title, artist, max_results=max_results)
    if scraped.ok and scraped.candidates:
        return {
            "ok": True,
            "source": "ytsearch",
            "video_id": scraped.video_id,
            "title": scraped.title,
            "channel_title": scraped.channel_title,
            "candidates": scraped.candidates,
        }
    api = search_youtube(title, artist, max_results=max_results)
    if api.ok and api.candidates:
        return {
            "ok": True,
            "source": "youtube_api",
            "video_id": api.video_id,
            "title": api.title,
            "channel_title": api.channel_title,
            "candidates": api.candidates,
        }
    err = scraped.error or api.error or "Nenhum resultado."
    return {
        "ok": False,
        "error": err,
        "candidates": scraped.candidates or api.candidates or [],
    }


def _tool_convert_to_mp3(url_or_id: str) -> dict[str, Any]:
    result = convert_share_link_to_mp3(url_or_id)
    if not result.ok:
        # Fallback: cache m4a/webm sem remux se ffmpeg falhar
        vid = extract_youtube_video_id(url_or_id)
        if vid:
            alt = resolve_youtube_audio(vid)
            if alt.ok and alt.local_path:
                result = alt
    if not result.ok:
        return {
            "ok": False,
            "error": result.error or "Conversão falhou.",
            "cookies_configured": youtube_cookies_configured(),
        }
    vid = extract_youtube_video_id(url_or_id) or ""
    return {
        "ok": True,
        "video_id": vid,
        "title": result.title,
        "mime": result.mime or "audio/mpeg",
        "ext": result.ext or "mp3",
        "local_path": result.local_path,
        "play_url": f"/api/youtube/media/{vid}" if vid else None,
        "cookies_configured": youtube_cookies_configured(),
    }


TOOL_SPECS: dict[str, dict[str, Any]] = {
    "resolve_share_link": {
        "description": "Extrai video_id de um link de partilha YouTube (youtu.be ou watch?v=).",
        "handler": _tool_resolve_share_link,
    },
    "search_tracks": {
        "description": "Procura faixas no YouTube por título+artista (scraping ytsearch).",
        "handler": _tool_search_tracks,
    },
    "convert_to_mp3": {
        "description": "Descarrega só o áudio do link/id e converte para MP3 local.",
        "handler": _tool_convert_to_mp3,
    },
}


def run_tool(name: str, **kwargs: Any) -> dict[str, Any]:
    spec = TOOL_SPECS.get(name)
    if not spec:
        return {"ok": False, "error": f"Tool desconhecida: {name}"}
    handler: Callable[..., dict[str, Any]] = spec["handler"]
    try:
        return handler(**kwargs)
    except TypeError:
        # search_tracks precisa title/artist posicionais
        if name == "search_tracks":
            return handler(
                str(kwargs.get("title") or ""),
                str(kwargs.get("artist") or ""),
                max_results=int(kwargs.get("max_results") or 8),
            )
        if name == "resolve_share_link":
            return handler(str(kwargs.get("url_or_id") or kwargs.get("url") or ""))
        if name == "convert_to_mp3":
            return handler(str(kwargs.get("url_or_id") or kwargs.get("url") or kwargs.get("video_id") or ""))
        raise
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _llm_pick_candidate(
    *,
    title: str,
    artist: str,
    candidates: list[dict[str, Any]],
) -> str | None:
    """Pede ao modelo open-source para escolher o video_id oficial mais provável."""
    try:
        from client import complete_chat
        from config import load_settings
    except Exception:
        return None

    slim = [
        {
            "video_id": c.get("video_id"),
            "title": c.get("title"),
            "channel": c.get("channel_title"),
            "score": c.get("score"),
        }
        for c in candidates[:8]
        if c.get("video_id")
    ]
    if not slim:
        return None

    system = (
        "Escolhe o vídeo YouTube oficial da música pedida. "
        "Prefere official audio/video, Topic, VEVO. Evita covers, karaoke, live, remix. "
        'Responde SÓ JSON: {"video_id":"..."}'
    )
    user = (
        f"Música: {title}\nArtista: {artist}\nCandidatos:\n"
        + json.dumps(slim, ensure_ascii=False)
    )
    try:
        settings = replace(
            load_settings(temperature=0.0),
            max_output_tokens=256,
            json_mode=False,
            temperature=0.0,
        )
        if not settings.api_key:
            return None
        text, _model = complete_chat(settings=settings, system=system, user=user)
    except Exception:
        return None

    m = re.search(r"\{[^{}]*\}", text, flags=re.S)
    raw = m.group(0) if m else text
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m2 = re.search(r"[A-Za-z0-9_-]{11}", text)
        return m2.group(0) if m2 else None
    vid = str(data.get("video_id") or "").strip()
    allowed = {str(c["video_id"]) for c in slim}
    return vid if vid in allowed else None


def run_audio_harness(
    *,
    title: str = "",
    artist: str = "",
    share_url: str | None = None,
    video_id: str | None = None,
    use_llm_picker: bool = True,
) -> HarnessResult:
    """
    Pipeline harness:
      1) resolve link (se houver) OU search_tracks(title, artist)
      2) (opcional) LLM escolhe entre candidatos
      3) convert_to_mp3
    """
    steps: list[ToolStep] = []
    t = str(title or "").strip()
    a = str(artist or "").strip()
    url = str(share_url or "").strip() or None
    forced_id = extract_youtube_video_id(video_id) if video_id else None

    chosen_id: str | None = forced_id
    chosen_title: str | None = None
    chosen_channel: str | None = None
    candidates: list[dict[str, Any]] = []
    picker = "url" if (url or forced_id) else "score"

    if url and not chosen_id:
        resolved = run_tool("resolve_share_link", url_or_id=url)
        steps.append(
            ToolStep(
                tool="resolve_share_link",
                ok=bool(resolved.get("ok")),
                detail=str(resolved.get("error") or resolved.get("video_id") or ""),
                data={k: v for k, v in resolved.items() if k != "error"},
            )
        )
        if not resolved.get("ok"):
            return HarnessResult(
                ok=False,
                error=str(resolved.get("error") or "Link inválido."),
                steps=steps,
            )
        chosen_id = str(resolved.get("video_id"))

    if not chosen_id:
        if not t or not a:
            return HarnessResult(
                ok=False,
                error="Indique título e artista, ou um link de partilha YouTube.",
                steps=steps,
            )
        searched = run_tool("search_tracks", title=t, artist=a, max_results=8)
        steps.append(
            ToolStep(
                tool="search_tracks",
                ok=bool(searched.get("ok")),
                detail=str(
                    searched.get("source")
                    or searched.get("error")
                    or f"{len(searched.get('candidates') or [])} resultados"
                ),
                data={
                    "source": searched.get("source"),
                    "count": len(searched.get("candidates") or []),
                },
            )
        )
        candidates = list(searched.get("candidates") or [])
        if not searched.get("ok") or not candidates:
            return HarnessResult(
                ok=False,
                error=str(searched.get("error") or "Nenhum vídeo encontrado."),
                candidates=candidates,
                steps=steps,
            )

        chosen_id = str(searched.get("video_id") or candidates[0].get("video_id") or "")
        chosen_title = str(searched.get("title") or candidates[0].get("title") or "") or None
        chosen_channel = (
            str(searched.get("channel_title") or candidates[0].get("channel_title") or "")
            or None
        )

        if use_llm_picker and len(candidates) > 1:
            picked = _llm_pick_candidate(title=t, artist=a, candidates=candidates)
            steps.append(
                ToolStep(
                    tool="llm_pick",
                    ok=bool(picked),
                    detail=picked or "fallback score",
                )
            )
            if picked:
                chosen_id = picked
                picker = "llm"
                for c in candidates:
                    if str(c.get("video_id")) == picked:
                        chosen_title = str(c.get("title") or "") or chosen_title
                        chosen_channel = str(c.get("channel_title") or "") or chosen_channel
                        break

    assert chosen_id
    converted = run_tool("convert_to_mp3", url_or_id=chosen_id)
    steps.append(
        ToolStep(
            tool="convert_to_mp3",
            ok=bool(converted.get("ok")),
            detail=str(converted.get("ext") or converted.get("error") or ""),
            data={
                "mime": converted.get("mime"),
                "ext": converted.get("ext"),
                "cookies_configured": converted.get("cookies_configured"),
            },
        )
    )
    if not converted.get("ok"):
        return HarnessResult(
            ok=False,
            video_id=chosen_id,
            title=chosen_title or converted.get("title"),
            channel_title=chosen_channel,
            url=f"https://www.youtube.com/watch?v={chosen_id}",
            candidates=candidates,
            steps=steps,
            error=str(converted.get("error") or "Falha ao converter para MP3."),
            picker=picker,
        )

    return HarnessResult(
        ok=True,
        video_id=chosen_id,
        title=chosen_title or converted.get("title"),
        channel_title=chosen_channel,
        url=f"https://www.youtube.com/watch?v={chosen_id}",
        play_url=str(converted.get("play_url") or f"/api/youtube/media/{chosen_id}"),
        mime=str(converted.get("mime") or "audio/mpeg"),
        ext=str(converted.get("ext") or "mp3"),
        candidates=candidates,
        steps=steps,
        picker=picker,
    )
