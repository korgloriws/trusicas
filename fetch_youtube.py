from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from config import ensure_env_loaded, get_youtube_settings

YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
USER_AGENT = "Trusicas/1.0 (educational lyrics lesson app)"

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


@dataclass
class YoutubeVideoHit:
    video_id: str
    title: str
    channel_title: str
    score: int = 0


@dataclass
class YoutubeSearchResult:
    ok: bool
    video_id: str | None = None
    title: str | None = None
    channel_title: str | None = None
    candidates: list[dict[str, Any]] | None = None
    error: str | None = None
    from_cache: bool = False


def extract_youtube_video_id(raw: str | None) -> str | None:
    """Extrai o videoId de URL YouTube ou de um id puro (11 chars)."""
    text = str(raw or "").strip()
    if not text:
        return None
    if _VIDEO_ID_RE.fullmatch(text):
        return text

    # Alguns colam o URL com espaços ou sem protocolo
    if "youtube.com" in text or "youtu.be" in text:
        if not re.match(r"^https?://", text, re.I):
            text = "https://" + text.lstrip("/")

    try:
        parsed = urlparse(text)
    except ValueError:
        return None

    host = (parsed.netloc or "").lower()
    path = parsed.path or ""

    if "youtu.be" in host:
        candidate = path.strip("/").split("/")[0]
        return candidate if _VIDEO_ID_RE.fullmatch(candidate) else None

    if "youtube.com" in host or "youtube-nocookie.com" in host:
        qs = parse_qs(parsed.query or "")
        if "v" in qs and qs["v"]:
            candidate = str(qs["v"][0]).strip()
            if _VIDEO_ID_RE.fullmatch(candidate):
                return candidate
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2 and parts[0] in {"embed", "shorts", "live", "v"}:
            candidate = parts[1]
            if _VIDEO_ID_RE.fullmatch(candidate):
                return candidate

    # Fallback: procura v=… no texto
    m = re.search(r"(?:v=|/embed/|/shorts/|/live/)([A-Za-z0-9_-]{11})", text)
    if m:
        return m.group(1)
    return None


def _score_hit(item: dict[str, Any], *, title: str, artist: str) -> int:
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    vid_title = str(snippet.get("title") or "").strip()
    channel = str(snippet.get("channelTitle") or "").strip()
    title_l = title.strip().lower()
    artist_l = artist.strip().lower()
    vt = vid_title.lower()
    ch = channel.lower()

    score = 0
    if title_l and title_l in vt:
        score += 8
    elif title_l:
        # palavras do título presentes
        words = [w for w in re.split(r"\W+", title_l) if len(w) > 2]
        hits = sum(1 for w in words if w in vt)
        score += min(6, hits * 2)

    if artist_l and artist_l in vt:
        score += 8
    elif artist_l and artist_l in ch:
        score += 6
    elif artist_l:
        words = [w for w in re.split(r"\W+", artist_l) if len(w) > 2]
        hits = sum(1 for w in words if w in vt or w in ch)
        score += min(5, hits * 2)

    # Preferências úteis para estudar letra
    if "official audio" in vt or "official video" in vt:
        score += 5
    if "official" in vt:
        score += 2
    if "topic" in ch or ch.endswith(" - topic"):
        score += 4
    if "lyric" in vt or "lyrics" in vt or "letra" in vt:
        score += 3
    if "vevo" in ch:
        score += 3

    # Penalizações
    for bad in ("karaoke", "instrumental", "cover", "reaction", "remix", "nightcore", "8d audio"):
        if bad in vt:
            score -= 6
    if "live" in vt and "official" not in vt:
        score -= 2

    return score


def search_youtube(title: str, artist: str, *, max_results: int = 8) -> YoutubeSearchResult:
    """
    Busca o vídeo mais provável da faixa via YouTube Data API v3.
    Requer YOUTUBE_API_KEY no .env.
    """
    ensure_env_loaded()
    t = str(title or "").strip()
    a = str(artist or "").strip()
    if not t or not a:
        return YoutubeSearchResult(
            ok=False,
            error="Indique o título e o artista para buscar no YouTube.",
        )

    settings = get_youtube_settings()
    api_key = settings["api_key"]
    if not api_key:
        return YoutubeSearchResult(
            ok=False,
            error="YOUTUBE_API_KEY não configurada. Adicione a chave no ficheiro .env.",
        )

    query = f"{a} {t} official audio"
    params: dict[str, Any] = {
        "part": "snippet",
        "type": "video",
        "q": query,
        "maxResults": max(3, min(int(max_results), 15)),
        "key": api_key,
        "safeSearch": "none",
    }
    region = settings.get("region_code") or ""
    lang = settings.get("relevance_language") or ""
    if region:
        params["regionCode"] = region
    if lang:
        params["relevanceLanguage"] = lang

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    scored: list[YoutubeVideoHit] = []
    try:
        with httpx.Client(timeout=25.0, headers=headers, follow_redirects=True) as client:
            r = client.get(YOUTUBE_SEARCH_URL, params=params)
            if r.status_code == 403:
                detail = ""
                try:
                    err = r.json().get("error", {})
                    detail = str(err.get("message") or "")
                except Exception:
                    detail = r.text[:200]
                return YoutubeSearchResult(
                    ok=False,
                    error=(
                        "YouTube API recusou o pedido (403). Confirme que a YouTube Data API v3 "
                        f"está activa e que a chave é válida. {detail}"
                    ).strip(),
                )
            if r.status_code == 400:
                return YoutubeSearchResult(
                    ok=False,
                    error="Pedido inválido à YouTube API. Verifique a chave e os parâmetros.",
                )
            if r.status_code >= 400:
                return YoutubeSearchResult(
                    ok=False,
                    error=f"YouTube API devolveu HTTP {r.status_code}.",
                )

            try:
                payload = r.json()
            except Exception:
                return YoutubeSearchResult(ok=False, error="Resposta inválida da YouTube API.")

            items = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(items, list) or not items:
                return YoutubeSearchResult(
                    ok=False,
                    error="Nenhum vídeo encontrado para esta música.",
                    candidates=[],
                )

            for item in items:
                if not isinstance(item, dict):
                    continue
                id_obj = item.get("id") if isinstance(item.get("id"), dict) else {}
                video_id = str(id_obj.get("videoId") or "").strip()
                if not _VIDEO_ID_RE.fullmatch(video_id):
                    continue
                snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
                hit = YoutubeVideoHit(
                    video_id=video_id,
                    title=str(snippet.get("title") or "").strip() or video_id,
                    channel_title=str(snippet.get("channelTitle") or "").strip(),
                    score=_score_hit(item, title=t, artist=a),
                )
                scored.append(hit)
    except httpx.HTTPError as e:
        return YoutubeSearchResult(ok=False, error=f"Falha de rede ao contactar o YouTube: {e}")

    if not scored:
        return YoutubeSearchResult(
            ok=False,
            error="Nenhum resultado encontrado para esta música.",
            candidates=[],
        )

    scored.sort(key=lambda h: h.score, reverse=True)

    best = scored[0]
    candidates = [
        {
            "video_id": h.video_id,
            "title": h.title,
            "channel_title": h.channel_title,
            "url": f"https://www.youtube.com/watch?v={h.video_id}",
            "score": h.score,
        }
        for h in scored
    ]
    return YoutubeSearchResult(
        ok=True,
        video_id=best.video_id,
        title=best.title,
        channel_title=best.channel_title,
        candidates=candidates,
    )


@dataclass
class YoutubeAudioResult:
    ok: bool
    audio_url: str | None = None
    mime: str | None = None
    title: str | None = None
    ext: str | None = None
    error: str | None = None


_audio_url_cache: dict[str, tuple[float, YoutubeAudioResult]] = {}
_AUDIO_CACHE_TTL_S = 45 * 60


def resolve_youtube_audio(video_id: str) -> YoutubeAudioResult:
    """
    Resolve um URL directo de áudio (m4a/webm) para o videoId, via yt-dlp.
    Usado pelo player HTML5 na Aula — sem iframe do YouTube.
    """
    import time

    vid = str(video_id or "").strip()
    if not _VIDEO_ID_RE.fullmatch(vid):
        return YoutubeAudioResult(ok=False, error="video_id inválido.")

    now = time.time()
    cached = _audio_url_cache.get(vid)
    if cached and cached[0] > now and cached[1].ok and cached[1].audio_url:
        return cached[1]

    try:
        import yt_dlp
    except ImportError:
        return YoutubeAudioResult(
            ok=False,
            error="Dependência yt-dlp em falta. Instale com: pip install yt-dlp",
        )

    page_url = f"https://www.youtube.com/watch?v={vid}"
    ydl_opts: dict[str, Any] = {
        "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        # Menos ruído / mais estável em servidores
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(page_url, download=False)
    except Exception as e:
        return YoutubeAudioResult(
            ok=False,
            error=f"Não foi possível obter o áudio deste vídeo: {e}",
        )

    if not isinstance(info, dict):
        return YoutubeAudioResult(ok=False, error="Resposta inválida ao resolver o áudio.")

    audio_url = str(info.get("url") or "").strip()
    # Às vezes o formato escolhido vem em «requested_formats» / «formats»
    if not audio_url:
        formats = info.get("formats") if isinstance(info.get("formats"), list) else []
        audio_formats = [
            f
            for f in formats
            if isinstance(f, dict)
            and f.get("url")
            and (f.get("acodec") not in (None, "none"))
            and (f.get("vcodec") in (None, "none"))
        ]
        audio_formats.sort(
            key=lambda f: (
                int(f.get("abr") or 0),
                int(f.get("tbr") or 0),
            ),
            reverse=True,
        )
        if audio_formats:
            audio_url = str(audio_formats[0].get("url") or "").strip()

    if not audio_url:
        return YoutubeAudioResult(
            ok=False,
            error="Não foi encontrado um stream de áudio para este vídeo.",
        )

    ext = str(info.get("ext") or "").strip().lower() or None
    mime = None
    if ext in {"m4a", "mp4"}:
        mime = "audio/mp4"
    elif ext == "webm":
        mime = "audio/webm"
    elif ext == "mp3":
        mime = "audio/mpeg"
    elif ext in {"opus", "ogg"}:
        mime = "audio/ogg"

    title = str(info.get("title") or "").strip() or None
    result = YoutubeAudioResult(
        ok=True,
        audio_url=audio_url,
        mime=mime,
        title=title,
        ext=ext,
    )
    _audio_url_cache[vid] = (now + _AUDIO_CACHE_TTL_S, result)
    return result

