from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import unescape
from typing import Any

import httpx

SOLR_CC = "https://solr.sscdn.co/cc/b1/-"
CIFRACLUB_BASE = "https://www.cifraclub.com.br"
CIFRAS_BASE = "https://www.cifras.com.br"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Referer": "https://www.cifraclub.com.br/",
}


@dataclass
class CifraFetchResult:
    ok: bool
    cifra: str
    title: str | None = None
    artist: str | None = None
    source: str | None = None
    url: str | None = None
    candidates: list[dict[str, Any]] | None = None
    error: str | None = None


def clean_cifra_text(raw: str) -> str:
    text = str(raw or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\[ch\](.*?)\[/ch\]", r"\1", text, flags=re.I | re.S)
    text = re.sub(r"\[/?tab\]", "", text, flags=re.I)
    text = text.replace("\xa0", " ")
    text = unescape(text)
    lines = [ln.rstrip() for ln in text.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()


def _html_to_text(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
    text = re.sub(r"</div\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return clean_cifra_text(text)


def _slugify(value: str) -> str:
    s = value.strip().lower()
    s = (
        s.replace("á", "a")
        .replace("à", "a")
        .replace("ã", "a")
        .replace("â", "a")
        .replace("é", "e")
        .replace("ê", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ô", "o")
        .replace("õ", "o")
        .replace("ú", "u")
        .replace("ü", "u")
        .replace("ç", "c")
    )
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _score_name(candidate: str, target: str) -> float:
    a = candidate.strip().lower()
    b = target.strip().lower()
    if not a or not b:
        return 0.0
    if a == b:
        return 8.0
    if b in a or a in b:
        return 4.0
    return 0.0


def _parse_solr(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if text.startswith("suggest_callback(") and text.endswith(")"):
        text = text[len("suggest_callback(") : -1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _search_cifraclub(title: str, artist: str, *, client: httpx.Client) -> list[dict[str, Any]]:
    queries = [
        f'"{artist}" "{title}"',
        f"{artist} {title}",
        title,
    ]
    seen: set[str] = set()
    hits: list[tuple[float, dict[str, Any]]] = []
    for q in queries:
        r = client.get(SOLR_CC, params={"q": q})
        if r.status_code >= 400:
            continue
        data = _parse_solr(r.text)
        if not data:
            continue
        docs = (data.get("response") or {}).get("docs") or []
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            dns = str(doc.get("dns") or "").strip()
            url = str(doc.get("url") or "").strip().strip("/")
            if not dns or not url:
                continue
            key = f"{dns}/{url}"
            if key in seen:
                continue
            seen.add(key)
            song = str(doc.get("txt") or doc.get("full_txt") or "")
            art = str(doc.get("art") or "")
            score = _score_name(song, title) + _score_name(art, artist)
            # prefer exact-ish matches
            if score < 8:
                continue
            hits.append(
                (
                    score + float(doc.get("h") or 0) / 10000.0,
                    {
                        "title": song or title,
                        "artist": art or artist,
                        "dns": dns,
                        "slug": url,
                        "path": f"/{dns}/{url}/",
                        "source": "cifraclub",
                    },
                )
            )
        if hits:
            break
    hits.sort(key=lambda x: x[0], reverse=True)
    return [row for _, row in hits]


def _fetch_cifraclub_print(dns: str, slug: str, *, client: httpx.Client) -> tuple[str, str]:
    path = f"/{dns}/{slug}/imprimir.html"
    url = CIFRACLUB_BASE + path
    r = client.get(url)
    if r.status_code >= 400:
        return "", url
    pres = re.findall(r"<pre[^>]*>(.*?)</pre>", r.text, flags=re.I | re.S)
    if not pres:
        return "", url
    # pick longest pre (main cifra)
    best = max((_html_to_text(p) for p in pres), key=len, default="")
    return best, url


def _fetch_cifras_com(title: str, artist: str, *, client: httpx.Client) -> CifraFetchResult | None:
    a_slug = _slugify(artist)
    t_slug = _slugify(title)
    urls = [
        f"{CIFRAS_BASE}/cifra/{a_slug}/{t_slug}",
        f"{CIFRAS_BASE}/cifra/{a_slug}/{t_slug}/",
    ]
    for url in urls:
        r = client.get(url)
        if r.status_code >= 400:
            continue
        html = r.text
        # common containers
        m = re.search(
            r'<(?:pre|div)[^>]*(?:id|class)="[^"]*(?:cifra|chord|letra)[^"]*"[^>]*>(.*?)</(?:pre|div)>',
            html,
            flags=re.I | re.S,
        )
        if not m:
            # fallback: first large <pre>
            pres = re.findall(r"<pre[^>]*>(.*?)</pre>", html, flags=re.I | re.S)
            texts = [_html_to_text(p) for p in pres]
            text = max(texts, key=len, default="")
        else:
            text = _html_to_text(m.group(1))
        if len(text) < 40:
            continue
        return CifraFetchResult(
            ok=True,
            cifra=text,
            title=title,
            artist=artist,
            source="cifras.com.br",
            url=url,
        )
    return None


def fetch_cifra(title: str, artist: str, *, timeout_s: float = 35.0) -> CifraFetchResult:
    """
    Busca cifra (formato Cifra Club / cifras.com.br) por scraping best-effort.
    Preferência: Cifra Club (página imprimir) → cifras.com.br.
    """
    t = (title or "").strip()
    a = (artist or "").strip()
    if not t or not a:
        return CifraFetchResult(
            ok=False,
            cifra="",
            error="Indique o título e o artista para buscar a cifra.",
        )

    try:
        with httpx.Client(timeout=timeout_s, headers=HEADERS, follow_redirects=True) as client:
            matches = _search_cifraclub(t, a, client=client)
            candidates = [
                {
                    "title": m.get("title"),
                    "artist": m.get("artist"),
                    "url": CIFRACLUB_BASE + str(m.get("path") or ""),
                    "source": "cifraclub",
                }
                for m in matches[:6]
            ]

            for m in matches[:4]:
                text, url = _fetch_cifraclub_print(
                    str(m["dns"]), str(m["slug"]), client=client
                )
                if text and len(text) >= 40:
                    return CifraFetchResult(
                        ok=True,
                        cifra=text,
                        title=str(m.get("title") or t),
                        artist=str(m.get("artist") or a),
                        source="cifraclub",
                        url=url,
                        candidates=candidates or None,
                    )

            # Tentativa direta por slug (sem solr)
            text, url = _fetch_cifraclub_print(_slugify(a), _slugify(t), client=client)
            if text and len(text) >= 40:
                return CifraFetchResult(
                    ok=True,
                    cifra=text,
                    title=t,
                    artist=a,
                    source="cifraclub",
                    url=url,
                    candidates=candidates or None,
                )

            fallback = _fetch_cifras_com(t, a, client=client)
            if fallback and fallback.ok:
                fallback.candidates = candidates or None
                return fallback

            return CifraFetchResult(
                ok=False,
                cifra="",
                error=(
                    "Não encontramos cifra em Cifra Club / cifras.com.br. "
                    "Cole manualmente se tiver."
                ),
                candidates=candidates or None,
            )
    except httpx.HTTPError as e:
        return CifraFetchResult(
            ok=False,
            cifra="",
            error=f"Falha de rede ao buscar cifra: {e}",
        )
    except Exception as e:
        return CifraFetchResult(
            ok=False,
            cifra="",
            error=f"Falha ao buscar cifra: {type(e).__name__}: {e}",
        )
