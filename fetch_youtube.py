from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from config import ensure_env_loaded, get_youtube_settings

YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
USER_AGENT = "Trusicas/1.0 (educational lyrics lesson app)"

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PROJECT_ROOT = Path(__file__).resolve().parent
_AUDIO_CACHE_DIR = _PROJECT_ROOT / "data" / "yt_audio"
_ENV_COOKIES_PATH = _PROJECT_ROOT / "data" / ".youtube.cookies.from_env.txt"

# Clientes yt-dlp — em VPS o «web» costuma pedir login anti-bot; android/ios/tv falham menos.
_PLAYER_CLIENT_ATTEMPTS: tuple[tuple[str, ...], ...] = (
    ("android_music", "android", "ios"),
    ("tv_embedded", "tv"),
    ("mweb", "web"),
)

# Instâncias públicas (falham com frequência; só fallback).
_PIPED_STREAM_APIS = (
    "https://pipedapi.kavin.rocks/streams/{vid}",
    "https://api.piped.private.coffee/streams/{vid}",
    "https://pipedapi.reallyaweso.me/streams/{vid}",
)

_BOT_BLOCK_HINT = (
    "O YouTube bloqueou o IP deste servidor. Cole abaixo o conteúdo do cookies.txt "
    "(no PC: extensão «Get cookies.txt LOCALLY» em youtube.com → Export → abrir o ficheiro "
    "no Bloco de notas → copiar tudo → colar aqui) e carregue em Guardar. "
    "Depois volte a pedir o áudio."
)


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
    local_path: str | None = None
    mime: str | None = None
    title: str | None = None
    ext: str | None = None
    error: str | None = None


_audio_url_cache: dict[str, tuple[float, YoutubeAudioResult]] = {}
_AUDIO_CACHE_TTL_S = 45 * 60


def _cookies_text_from_env() -> str | None:
    """Lê cookies Netscape a partir do .env (B64 ou texto com \\n)."""
    ensure_env_loaded()
    b64 = (os.getenv("YOUTUBE_COOKIES_B64") or "").strip().strip('"').strip("'")
    if b64:
        compact = "".join(b64.split())
        try:
            decoded = base64.b64decode(compact, validate=False)
            text = decoded.decode("utf-8", errors="replace").strip()
            if text:
                return text
        except Exception:
            pass
    raw = (os.getenv("YOUTUBE_COOKIES") or "").strip().strip('"').strip("'")
    if raw:
        return raw.replace("\\n", "\n").strip()
    return None


def _materialize_env_cookies() -> str | None:
    """Escreve cookies do .env para um ficheiro interno (o utilizador não cria nada na VPS)."""
    text = _cookies_text_from_env()
    if not text:
        return None
    if "youtube.com" not in text.lower() and "# netscape" not in text.lower():
        # Ainda assim tentar — alguns exports mínimos podem variar
        pass
    try:
        _ENV_COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
        prev = (
            _ENV_COOKIES_PATH.read_text(encoding="utf-8")
            if _ENV_COOKIES_PATH.is_file()
            else None
        )
        if prev != text:
            _ENV_COOKIES_PATH.write_text(text, encoding="utf-8")
            try:
                os.chmod(_ENV_COOKIES_PATH, 0o600)
            except OSError:
                pass
        return str(_ENV_COOKIES_PATH.resolve())
    except OSError:
        return None


def _resolve_cookies_file() -> str | None:
    """Prioridade: .env → ficheiro guardado na app → caminhos configurados."""
    from_env = _materialize_env_cookies()
    if from_env:
        return from_env

    if _ENV_COOKIES_PATH.is_file() and _ENV_COOKIES_PATH.stat().st_size > 0:
        return str(_ENV_COOKIES_PATH.resolve())

    settings = get_youtube_settings()
    configured = (settings.get("cookies_file") or "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    candidates.append(_PROJECT_ROOT / "data" / "youtube.cookies.txt")
    candidates.append(Path("/app/data/youtube.cookies.txt"))
    seen: set[str] = set()
    for path in candidates:
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        if resolved.is_file() and resolved.stat().st_size > 0:
            return key
    return None


def youtube_cookies_configured() -> bool:
    return _resolve_cookies_file() is not None


def clear_youtube_audio_mem_cache() -> None:
    _audio_url_cache.clear()


def save_youtube_cookies_text(raw: str) -> tuple[bool, str]:
    """
    Guarda cookies Netscape colados na UI (sem criar ficheiros à mão na VPS).
    """
    text = str(raw or "").strip()
    if len(text) < 40:
        return False, "Cole o conteúdo completo do cookies.txt (está demasiado curto)."
    low = text.lower()
    if "youtube.com" not in low and ".youtube.com" not in low:
        return (
            False,
            "O texto não parece cookies do YouTube. Exporte em youtube.com com a extensão.",
        )
    try:
        _ENV_COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ENV_COOKIES_PATH.write_text(text + ("" if text.endswith("\n") else "\n"), encoding="utf-8")
        try:
            os.chmod(_ENV_COOKIES_PATH, 0o600)
        except OSError:
            pass
    except OSError as e:
        return False, f"Não foi possível guardar os cookies: {e}"
    clear_youtube_audio_mem_cache()
    return True, "Cookies guardados. Pode carregar o áudio outra vez."


def clear_youtube_cookies_file() -> tuple[bool, str]:
    try:
        if _ENV_COOKIES_PATH.is_file():
            _ENV_COOKIES_PATH.unlink()
    except OSError as e:
        return False, f"Não foi possível remover: {e}"
    clear_youtube_audio_mem_cache()
    return True, "Cookies removidos."


def _youtube_proxy() -> str | None:
    proxy = (get_youtube_settings().get("proxy") or "").strip()
    return proxy or None


def _is_bot_block_error(message: str) -> bool:
    low = message.lower()
    return (
        "sign in to confirm" in low
        or "not a bot" in low
        or "cookies-from-browser" in low
        or "use --cookies" in low
        or "login_required" in low
    )


def _pick_audio_url(info: dict[str, Any]) -> tuple[str | None, str | None]:
    """Devolve (url, ext) a partir do info do yt-dlp."""
    audio_url = str(info.get("url") or "").strip() or None
    ext = str(info.get("ext") or "").strip().lower() or None
    if audio_url:
        return audio_url, ext

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
    if not audio_formats:
        return None, ext
    best = audio_formats[0]
    audio_url = str(best.get("url") or "").strip() or None
    fmt_ext = str(best.get("ext") or "").strip().lower() or ext
    return audio_url, fmt_ext


def _mime_for_ext(ext: str | None) -> str | None:
    if ext in {"m4a", "mp4"}:
        return "audio/mp4"
    if ext == "webm":
        return "audio/webm"
    if ext == "mp3":
        return "audio/mpeg"
    if ext in {"opus", "ogg"}:
        return "audio/ogg"
    return None


def _find_cached_audio(vid: str) -> Path | None:
    if not _AUDIO_CACHE_DIR.is_dir():
        return None
    matches = sorted(
        (
            p
            for p in _AUDIO_CACHE_DIR.glob(f"{vid}.*")
            if p.is_file() and p.stat().st_size > 1024 and not p.name.endswith(".part")
        ),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None


def _result_from_cache(path: Path, *, title: str | None = None) -> YoutubeAudioResult:
    ext = path.suffix.lstrip(".").lower() or None
    return YoutubeAudioResult(
        ok=True,
        local_path=str(path.resolve()),
        mime=_mime_for_ext(ext),
        title=title,
        ext=ext,
    )


def _download_with_ytdlp(vid: str) -> YoutubeAudioResult:
    try:
        import yt_dlp
    except ImportError:
        return YoutubeAudioResult(
            ok=False,
            error="Dependência yt-dlp em falta. Instale com: pip install yt-dlp",
        )

    page_url = f"https://www.youtube.com/watch?v={vid}"
    cookies_file = _resolve_cookies_file()
    proxy = _youtube_proxy()
    last_error = ""
    saw_bot_block = False

    _AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    outtmpl = str(_AUDIO_CACHE_DIR / f"{vid}.%(ext)s")

    for clients in _PLAYER_CLIENT_ATTEMPTS:
        ydl_opts: dict[str, Any] = {
            "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "outtmpl": outtmpl,
            "overwrites": True,
            "extractor_args": {"youtube": {"player_client": list(clients)}},
        }
        if cookies_file:
            ydl_opts["cookiefile"] = cookies_file
        if proxy:
            ydl_opts["proxy"] = proxy
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(page_url, download=True)
        except Exception as e:
            last_error = str(e)
            if _is_bot_block_error(last_error):
                saw_bot_block = True
            # Limpar leftovers .part
            for junk in _AUDIO_CACHE_DIR.glob(f"{vid}.*"):
                if junk.suffix == ".part" or junk.stat().st_size < 512:
                    try:
                        junk.unlink(missing_ok=True)
                    except OSError:
                        pass
            continue

        cached = _find_cached_audio(vid)
        if cached:
            title = None
            if isinstance(info, dict):
                title = str(info.get("title") or "").strip() or None
            return _result_from_cache(cached, title=title)

        # download=True por vezes só devolve info; tentar URL + httpx
        if isinstance(info, dict):
            audio_url, ext = _pick_audio_url(info)
            title = str(info.get("title") or "").strip() or None
            if audio_url:
                saved = _http_download_audio(vid, audio_url, ext_hint=ext)
                if saved.ok:
                    saved.title = title or saved.title
                    return saved
        last_error = "yt-dlp não gravou o ficheiro de áudio."

    if saw_bot_block and not cookies_file and not proxy:
        return YoutubeAudioResult(ok=False, error=_BOT_BLOCK_HINT)
    if saw_bot_block:
        return YoutubeAudioResult(
            ok=False,
            error=(
                "O YouTube ainda bloqueia o áudio. Actualize YOUTUBE_COOKIES_B64 "
                "com cookies frescos (conta logada no YouTube) e faça redeploy. "
                f"Detalhe: {last_error}"
            ),
        )
    return YoutubeAudioResult(
        ok=False,
        error=f"Não foi possível obter o áudio deste vídeo: {last_error or 'erro desconhecido'}",
    )


def _http_download_audio(
    vid: str, audio_url: str, *, ext_hint: str | None = None
) -> YoutubeAudioResult:
    ext = (ext_hint or "m4a").lstrip(".").lower() or "m4a"
    if ext == "mp4":
        ext = "m4a"
    dest = _AUDIO_CACHE_DIR / f"{vid}.{ext}"
    _AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    try:
        with httpx.Client(timeout=120.0, follow_redirects=True, headers=headers) as client:
            with client.stream("GET", audio_url) as resp:
                if resp.status_code >= 400:
                    return YoutubeAudioResult(
                        ok=False,
                        error=f"Falha ao descarregar áudio (HTTP {resp.status_code}).",
                    )
                tmp = dest.with_suffix(dest.suffix + ".part")
                with tmp.open("wb") as fh:
                    for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                        if chunk:
                            fh.write(chunk)
                tmp.replace(dest)
    except httpx.HTTPError as e:
        return YoutubeAudioResult(ok=False, error=f"Falha de rede ao descarregar áudio: {e}")
    if not dest.is_file() or dest.stat().st_size < 1024:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        return YoutubeAudioResult(ok=False, error="Áudio descarregado está vazio.")
    return _result_from_cache(dest)


def _download_via_piped(vid: str) -> YoutubeAudioResult:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    last_error = ""
    with httpx.Client(timeout=25.0, follow_redirects=True, headers=headers) as client:
        for template in _PIPED_STREAM_APIS:
            url = template.format(vid=vid)
            try:
                r = client.get(url)
            except httpx.HTTPError as e:
                last_error = str(e)
                continue
            if r.status_code >= 400:
                last_error = f"HTTP {r.status_code} em {url}"
                continue
            try:
                payload = r.json()
            except Exception:
                last_error = "JSON inválido (Piped)"
                continue
            streams = payload.get("audioStreams") if isinstance(payload, dict) else None
            if not isinstance(streams, list) or not streams:
                last_error = "Piped sem audioStreams"
                continue
            usable = [
                s
                for s in streams
                if isinstance(s, dict) and str(s.get("url") or "").startswith("http")
            ]
            usable.sort(
                key=lambda s: int(s.get("bitrate") or s.get("bitRate") or 0),
                reverse=True,
            )
            if not usable:
                continue
            best = usable[0]
            audio_url = str(best.get("url") or "").strip()
            mime = str(best.get("mimeType") or best.get("type") or "").lower()
            ext = "m4a"
            if "webm" in mime or "opus" in mime:
                ext = "webm"
            elif "mpeg" in mime or "mp3" in mime:
                ext = "mp3"
            title = str(payload.get("title") or "").strip() or None
            saved = _http_download_audio(vid, audio_url, ext_hint=ext)
            if saved.ok:
                saved.title = title or saved.title
                if mime.startswith("audio/"):
                    saved.mime = mime.split(";")[0].strip()
                return saved
            last_error = saved.error or "download Piped falhou"
    return YoutubeAudioResult(
        ok=False,
        error=last_error or "Nenhuma instância Piped disponível.",
    )


def resolve_youtube_audio(video_id: str) -> YoutubeAudioResult:
    """
    Garante áudio em cache local (data/yt_audio/) para o videoId.
    Em VPS: defina YOUTUBE_COOKIES_B64 no .env (colar base64) — sem criar ficheiros à mão.
    O browser recebe o ficheiro via /api/youtube/media (não depende do IP do CDN).
    """
    import time

    vid = str(video_id or "").strip()
    if not _VIDEO_ID_RE.fullmatch(vid):
        return YoutubeAudioResult(ok=False, error="video_id inválido.")

    now = time.time()
    cached_mem = _audio_url_cache.get(vid)
    if (
        cached_mem
        and cached_mem[0] > now
        and cached_mem[1].ok
        and cached_mem[1].local_path
        and Path(cached_mem[1].local_path).is_file()
    ):
        return cached_mem[1]

    on_disk = _find_cached_audio(vid)
    if on_disk:
        result = _result_from_cache(on_disk)
        _audio_url_cache[vid] = (now + _AUDIO_CACHE_TTL_S, result)
        return result

    result = _download_with_ytdlp(vid)
    if not result.ok:
        piped = _download_via_piped(vid)
        if piped.ok:
            result = piped
        elif _is_bot_block_error(result.error or "") and not _resolve_cookies_file():
            result = YoutubeAudioResult(ok=False, error=_BOT_BLOCK_HINT)
        elif not result.error:
            result = piped

    if result.ok and result.local_path:
        _audio_url_cache[vid] = (now + _AUDIO_CACHE_TTL_S, result)
    return result

