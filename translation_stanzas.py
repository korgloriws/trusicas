from __future__ import annotations

import re
from typing import Any


_SUMMARY_MARKERS = (
    "a canção fala",
    "a musica fala",
    "a música fala",
    "this song is about",
    "the song is about",
    "trata de",
    "fala sobre",
    "narra",
    "narrador reflete",
    "letra trata",
    "significado geral",
    "sentido geral",
    "resumo",
    "interpretação",
    "metáforas que",
    "metaphor",
)


def parse_lyric_stanzas(lyrics: str) -> list[list[str]]:
    """Split lyrics into stanzas (blocks separated by blank lines)."""
    normalized = lyrics.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    blocks = re.split(r"\n(?:[ \t]*\n)+", normalized)
    stanzas: list[list[str]] = []
    for block in blocks:
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if lines:
            stanzas.append(lines)
    return stanzas


def _pt_lines_from_rows(line_by_line: list[Any]) -> list[str]:
    out: list[str] = []
    for row in line_by_line:
        if isinstance(row, dict):
            pt = str(row.get("pt") or "").strip()
            if pt:
                out.append(pt)
    return out


def count_lyric_lines(lyrics_en: str) -> int:
    stanzas = parse_lyric_stanzas(lyrics_en)
    if stanzas:
        return sum(len(s) for s in stanzas)
    return len([ln for ln in lyrics_en.split("\n") if ln.strip()])


def looks_like_summary(whole_pt: str, *, lyric_line_count: int) -> bool:
    """Detect thematic paragraph instead of line-by-line lyrics."""
    text = whole_pt.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return False
    lower = text.lower()
    if any(marker in lower for marker in _SUMMARY_MARKERS):
        return True
    pt_lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    # One or two long prose lines while the song has many lyric lines
    if lyric_line_count >= 4 and len(pt_lines) <= 2:
        avg_len = sum(len(ln) for ln in pt_lines) / max(len(pt_lines), 1)
        if avg_len > 80:
            return True
    # Far fewer lines in whole_pt than in the song
    if lyric_line_count >= 3 and len(pt_lines) < lyric_line_count // 2:
        return True
    return False


def build_whole_song_pt_from_lines(lyrics_en: str, line_by_line: list[Any]) -> str:
    stanzas = parse_lyric_stanzas(lyrics_en)
    if not stanzas:
        return "\n".join(_pt_lines_from_rows(line_by_line))

    idx = 0
    parts: list[str] = []
    for stanza in stanzas:
        pt_lines: list[str] = []
        for _ in stanza:
            if idx >= len(line_by_line):
                break
            row = line_by_line[idx]
            if isinstance(row, dict):
                pt_lines.append(str(row.get("pt") or "").strip())
            idx += 1
        pt_lines = [ln for ln in pt_lines if ln]
        if pt_lines:
            parts.append("\n".join(pt_lines))

    while idx < len(line_by_line):
        row = line_by_line[idx]
        if isinstance(row, dict):
            pt = str(row.get("pt") or "").strip()
            if pt:
                if parts:
                    parts[-1] = parts[-1] + "\n" + pt
                else:
                    parts.append(pt)
        idx += 1

    return "\n\n".join(parts)


def align_translation_stanzas(lyrics_en: str, translation: dict[str, Any] | None) -> dict[str, Any]:
    """
    whole_song_pt = Portuguese lyrics (line-by-line), not a summary.
    Always rebuilt from line_by_line when available.
    """
    if not translation or not isinstance(translation, dict):
        return translation or {}
    line_by_line = translation.get("line_by_line")
    if not isinstance(line_by_line, list) or not line_by_line:
        return translation

    pt_lines = _pt_lines_from_rows(line_by_line)
    if not pt_lines:
        return translation

    out = dict(translation)
    out["whole_song_pt"] = build_whole_song_pt_from_lines(lyrics_en, line_by_line)
    return out
