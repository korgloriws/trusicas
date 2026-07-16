from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

LRCLIB_SEARCH = "https://lrclib.net/api/search"
LYRICS_OVH = "https://api.lyrics.ovh/v1"
USER_AGENT = "Trusicas/1.0 (https://github.com/local/trusicas; educational lyrics lesson app)"


@dataclass
class LyricsFetchResult:
    ok: bool
    lyrics: str
    title: str | None = None
    artist: str | None = None
    source: str | None = None
    candidates: list[dict[str, Any]] | None = None
    error: str | None = None


def _clean_lyrics(text: str) -> str:
    lines = [ln.rstrip() for ln in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    # Remove leading/trailing empty lines; keep internal stanza breaks
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()


def _pick_best_lrclib(rows: list[dict[str, Any]], *, title: str, artist: str) -> dict[str, Any] | None:
    if not rows:
        return None
    title_l = title.strip().lower()
    artist_l = artist.strip().lower()
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("instrumental"):
            continue
        plain = str(row.get("plainLyrics") or "").strip()
        if not plain:
            continue
        track = str(row.get("trackName") or row.get("name") or "").strip().lower()
        art = str(row.get("artistName") or "").strip().lower()
        score = 0
        if title_l and track == title_l:
            score += 5
        elif title_l and title_l in track:
            score += 3
        elif title_l and track in title_l:
            score += 2
        if artist_l and art == artist_l:
            score += 5
        elif artist_l and artist_l in art:
            score += 3
        elif artist_l and art in artist_l:
            score += 2
        scored.append((score, row))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def _fetch_lrclib(title: str, artist: str, *, timeout_s: float = 25.0) -> LyricsFetchResult:
    params = {
        "track_name": title.strip(),
        "artist_name": artist.strip(),
    }
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    with httpx.Client(timeout=timeout_s, headers=headers, follow_redirects=True) as client:
        r = client.get(LRCLIB_SEARCH, params=params)
        if r.status_code >= 400:
            # Fallback: general query
            r = client.get(LRCLIB_SEARCH, params={"q": f"{artist} {title}".strip()})
        if r.status_code >= 400:
            return LyricsFetchResult(
                ok=False,
                lyrics="",
                error=f"LRCLIB HTTP {r.status_code}",
            )
        data = r.json()
    if not isinstance(data, list) or not data:
        return LyricsFetchResult(ok=False, lyrics="", error="LRCLIB sem resultados.")

    best = _pick_best_lrclib(data, title=title, artist=artist)
    if best is None:
        return LyricsFetchResult(ok=False, lyrics="", error="LRCLIB: resultados sem letra utilizável.")

    lyrics = _clean_lyrics(str(best.get("plainLyrics") or ""))
    if not lyrics:
        return LyricsFetchResult(ok=False, lyrics="", error="LRCLIB: letra vazia.")

    candidates: list[dict[str, Any]] = []
    for row in data[:8]:
        if not isinstance(row, dict):
            continue
        plain = str(row.get("plainLyrics") or "").strip()
        if not plain:
            continue
        candidates.append(
            {
                "title": row.get("trackName") or row.get("name"),
                "artist": row.get("artistName"),
                "album": row.get("albumName"),
                "source": "lrclib",
                "preview": _clean_lyrics(plain)[:240],
            }
        )

    return LyricsFetchResult(
        ok=True,
        lyrics=lyrics,
        title=str(best.get("trackName") or best.get("name") or title).strip() or title,
        artist=str(best.get("artistName") or artist).strip() or artist,
        source="lrclib",
        candidates=candidates or None,
    )


def _fetch_lyrics_ovh(title: str, artist: str, *, timeout_s: float = 25.0) -> LyricsFetchResult:
    url = f"{LYRICS_OVH}/{quote(artist.strip())}/{quote(title.strip())}"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    with httpx.Client(timeout=timeout_s, headers=headers, follow_redirects=True) as client:
        r = client.get(url)
    if r.status_code == 404:
        return LyricsFetchResult(ok=False, lyrics="", error="lyrics.ovh: não encontrado.")
    if r.status_code >= 400:
        return LyricsFetchResult(ok=False, lyrics="", error=f"lyrics.ovh HTTP {r.status_code}")
    try:
        data = r.json()
    except Exception:
        return LyricsFetchResult(ok=False, lyrics="", error="lyrics.ovh: resposta inválida.")
    lyrics = _clean_lyrics(str((data or {}).get("lyrics") or ""))
    if not lyrics:
        return LyricsFetchResult(ok=False, lyrics="", error="lyrics.ovh: letra vazia.")
    return LyricsFetchResult(
        ok=True,
        lyrics=lyrics,
        title=title.strip(),
        artist=artist.strip(),
        source="lyrics.ovh",
    )


def fetch_lyrics(title: str, artist: str) -> LyricsFetchResult:
    """
    Fetch plain lyrics using free public APIs only (no paid keys, no browser).
    Primary: LRCLIB (open-source). Fallback: lyrics.ovh.
    """
    t = (title or "").strip()
    a = (artist or "").strip()
    if not t or not a:
        return LyricsFetchResult(
            ok=False,
            lyrics="",
            error="Indique o título e o artista para buscar a letra.",
        )

    primary = _fetch_lrclib(t, a)
    if primary.ok:
        return primary

    fallback = _fetch_lyrics_ovh(t, a)
    if fallback.ok:
        fallback.candidates = primary.candidates
        return fallback

    return LyricsFetchResult(
        ok=False,
        lyrics="",
        error=(
            "Não foi possível encontrar a letra automaticamente. "
            f"({primary.error or 'lrclib falhou'}; {fallback.error or 'lyrics.ovh falhou'}). "
            "Cole a letra manualmente."
        ),
        candidates=primary.candidates,
    )
