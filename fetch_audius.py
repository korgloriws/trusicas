from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

AUDIUS_API = "https://api.audius.co/v1"
USER_AGENT = "Trusicas/1.0 (educational lyrics lesson app)"
APP_NAME = "trusicas"

_TRACK_ID_RE = re.compile(r"^[A-Za-z0-9]+$")


@dataclass
class AudiusTrackHit:
    track_id: str
    title: str
    artist: str
    duration: int | None = None
    score: int = 0


@dataclass
class AudiusSearchResult:
    ok: bool
    track_id: str | None = None
    title: str | None = None
    artist: str | None = None
    candidates: list[dict[str, Any]] | None = None
    error: str | None = None


def is_audius_track_id(raw: str | None) -> bool:
    text = str(raw or "").strip()
    return bool(text) and bool(_TRACK_ID_RE.fullmatch(text)) and 4 <= len(text) <= 32


def audius_stream_url(track_id: str) -> str:
    tid = str(track_id or "").strip()
    return f"{AUDIUS_API}/tracks/{tid}/stream?app_name={APP_NAME}"


def _score_track(item: dict[str, Any], *, title: str, artist: str) -> int:
    track_title = str(item.get("title") or "").strip()
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    track_artist = str(user.get("name") or "").strip()
    title_l = title.strip().lower()
    artist_l = artist.strip().lower()
    tt = track_title.lower()
    ta = track_artist.lower()

    score = 0
    if title_l and tt == title_l:
        score += 10
    elif title_l and title_l in tt:
        score += 6
    elif title_l and tt in title_l:
        score += 4
    elif title_l:
        words = [w for w in re.split(r"\W+", title_l) if len(w) > 2]
        hits = sum(1 for w in words if w in tt)
        score += min(5, hits * 2)

    if artist_l and ta == artist_l:
        score += 10
    elif artist_l and artist_l in ta:
        score += 6
    elif artist_l and ta in artist_l:
        score += 4
    elif artist_l:
        words = [w for w in re.split(r"\W+", artist_l) if len(w) > 2]
        hits = sum(1 for w in words if w in ta or w in tt)
        score += min(5, hits * 2)

    # Preferências leves
    if "cover" in tt:
        score += 1  # covers são comuns e úteis para estudar
    if "karaoke" in tt or "instrumental" in tt:
        score -= 6
    if item.get("is_streamable") is False:
        score -= 20

    duration = item.get("duration")
    try:
        dur = int(duration) if duration is not None else 0
    except (TypeError, ValueError):
        dur = 0
    if 60 <= dur <= 600:
        score += 2

    return score


def search_audius(title: str, artist: str, *, max_results: int = 8) -> AudiusSearchResult:
    """
    Busca faixa no Audius (API aberta) — equivalente à LRCLIB para áudio.
    Funciona a partir de VPS/datacenter sem cookies.
    """
    t = str(title or "").strip()
    a = str(artist or "").strip()
    if not t or not a:
        return AudiusSearchResult(
            ok=False,
            error="Indique o título e o artista para buscar o áudio.",
        )

    query = f"{a} {t}".strip()
    params = {
        "query": query,
        "app_name": APP_NAME,
        "limit": max(3, min(int(max_results), 20)),
    }
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    scored: list[AudiusTrackHit] = []
    try:
        with httpx.Client(timeout=25.0, headers=headers, follow_redirects=True) as client:
            r = client.get(f"{AUDIUS_API}/tracks/search", params=params)
            if r.status_code >= 400:
                return AudiusSearchResult(
                    ok=False,
                    error=f"Audius API devolveu HTTP {r.status_code}.",
                )
            try:
                payload = r.json()
            except Exception:
                return AudiusSearchResult(ok=False, error="Resposta inválida da Audius API.")

            items = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(items, list) or not items:
                return AudiusSearchResult(
                    ok=False,
                    error="Nenhuma faixa encontrada no Audius para esta música.",
                    candidates=[],
                )

            for item in items:
                if not isinstance(item, dict):
                    continue
                track_id = str(item.get("id") or "").strip()
                if not is_audius_track_id(track_id):
                    continue
                if item.get("is_streamable") is False:
                    continue
                user = item.get("user") if isinstance(item.get("user"), dict) else {}
                dur_raw = item.get("duration")
                try:
                    duration = int(dur_raw) if dur_raw is not None else None
                except (TypeError, ValueError):
                    duration = None
                hit = AudiusTrackHit(
                    track_id=track_id,
                    title=str(item.get("title") or "").strip() or track_id,
                    artist=str(user.get("name") or "").strip(),
                    duration=duration,
                    score=_score_track(item, title=t, artist=a),
                )
                scored.append(hit)
    except httpx.HTTPError as e:
        return AudiusSearchResult(ok=False, error=f"Falha de rede ao contactar o Audius: {e}")

    if not scored:
        return AudiusSearchResult(
            ok=False,
            error="Nenhuma faixa streamable encontrada no Audius.",
            candidates=[],
        )

    scored.sort(key=lambda h: h.score, reverse=True)
    best = scored[0]
    candidates = [
        {
            "track_id": h.track_id,
            "title": h.title,
            "artist": h.artist,
            "duration": h.duration,
            "score": h.score,
            "source": "audius",
            "play_url": f"/api/audio/stream/{h.track_id}",
        }
        for h in scored
    ]
    return AudiusSearchResult(
        ok=True,
        track_id=best.track_id,
        title=best.title,
        artist=best.artist,
        candidates=candidates,
    )
