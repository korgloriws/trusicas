from __future__ import annotations

from typing import Any


def lesson_dict_to_markdown(lesson: dict[str, Any]) -> str:
    meta = lesson.get("meta") or {}
    translation = lesson.get("translation") or {}
    structures = lesson.get("structures") or {}
    vocabulary = lesson.get("vocabulary") or []
    drills = lesson.get("examples_and_drills") or {}
    curiosities = lesson.get("curiosities") or []

    lines: list[str] = []
    title = (meta.get("title_hint") or "Lyrics lesson").strip()
    lines.append(f"# {title}")
    lines.append("")
    lines.append("Gerado automaticamente (OpenRouter). Revise fatos marcados como `needs_verification`.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Tradução (linha a linha)")
    lines.append("")
    for row in translation.get("line_by_line") or []:
        if not isinstance(row, dict):
            continue
        en = str(row.get("en", "")).strip()
        pt = str(row.get("pt", "")).strip()
        if not en and not pt:
            continue
        lines.append(f"- **EN:** {en}")
        lines.append(f"  **PT:** {pt}")
        lines.append("")
    whole = str(translation.get("whole_song_pt", "")).strip()
    if whole:
        lines.append("### Sentido geral (PT)")
        lines.append("")
        lines.append(whole)
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Estruturas e gramática")
    lines.append("")
    for sec in structures.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        h = str(sec.get("heading", "")).strip()
        body = str(sec.get("body_pt", "")).strip()
        lines.append(f"### {h}" if h else "### (sem título)")
        lines.append("")
        if body:
            lines.append(body)
            lines.append("")
        exs = sec.get("examples_en") or []
        if isinstance(exs, list) and exs:
            lines.append("**Exemplos (EN):**")
            for e in exs:
                lines.append(f"- {e}")
            lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Vocabulário e expressões")
    lines.append("")
    lines.append("| Termo | Significado (PT) | Notas | Colocações (EN) |")
    lines.append("|---|---|---|---|")
    for it in vocabulary:
        if not isinstance(it, dict):
            continue
        term = str(it.get("term", "")).replace("|", "\\|")
        meaning = str(it.get("meaning_pt", "")).replace("|", "\\|")
        notes = str(it.get("notes_pt", "")).replace("|", "\\|")
        cols = it.get("common_collocations_en") or []
        col_txt = ", ".join(str(x) for x in cols) if isinstance(cols, list) else ""
        col_txt = col_txt.replace("|", "\\|")
        lines.append(f"| {term} | {meaning} | {notes} | {col_txt} |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Exemplos e fixação")
    lines.append("")
    for p in drills.get("pattern_drills") or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("pattern_name_pt", "")).strip()
        expl = str(p.get("pattern_explanation_pt", "")).strip()
        lines.append(f"### {name}" if name else "### Padrão")
        lines.append("")
        if expl:
            lines.append(expl)
            lines.append("")
        for e in p.get("examples_en") or []:
            lines.append(f"- {e}")
        lines.append("")
        for pr in p.get("fixation_prompts_pt") or []:
            lines.append(f"- *{pr}*")
        lines.append("")
    mistakes = drills.get("mistakes_pt_speakers") or []
    if isinstance(mistakes, list) and mistakes:
        lines.append("### Erros comuns (falantes de PT)")
        lines.append("")
        for m in mistakes:
            if not isinstance(m, dict):
                continue
            lines.append(
                f"- **Evite:** {m.get('wrong','')} → **Melhor:** {m.get('better','')} — {m.get('why_pt','')}"
            )
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Curiosidades")
    lines.append("")
    for c in curiosities:
        if not isinstance(c, dict):
            continue
        t = str(c.get("title", "")).strip()
        body = str(c.get("body_pt", "")).strip()
        flag = bool(c.get("needs_verification", False))
        flag_txt = " *(verificar fonte)*" if flag else ""
        lines.append(f"### {t}{flag_txt}" if t else f"### Curiosidade{flag_txt}")
        lines.append("")
        if body:
            lines.append(body)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
