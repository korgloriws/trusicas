from __future__ import annotations

SYSTEM_PROMPT = """You are an expert English teacher for Portuguese (Brazil) speakers.
You analyze song lyrics: translation, grammar, usage, and learning drills.

Hard rules:
- Output MUST be a single JSON object only. No markdown fences. No commentary outside JSON.
- Portuguese explanations use clear, natural Brazilian Portuguese.
- Do not invent precise biographical or release facts. For curiosities, prefer cautious wording.
  If a curiosity is not certain, set needs_verification to true.
- Preserve the lyric line order in translation.line_by_line; one object per non-empty lyric line.
- If the input repeats lines/chorus, still include each repeated line as its own entry (same as the lyrics).
"""


def build_user_prompt(lyrics: str, *, title_hint: str | None, artist_hint: str | None) -> str:
    hints: list[str] = []
    if title_hint:
        hints.append(f"Song title hint: {title_hint}")
    if artist_hint:
        hints.append(f"Artist hint: {artist_hint}")
    hint_block = ("\n".join(hints) + "\n") if hints else ""

    schema_instructions = """
Return JSON with exactly this shape (keys and nesting):
{
  "meta": {
    "title_hint": string | null,
    "artist_hint": string | null,
    "level": "A2" | "B1" | "B2" | "C1" | "mixed",
    "register_notes_pt": string
  },
  "translation": {
    "line_by_line": [ { "en": string, "pt": string } ],
    "whole_song_pt": string
  },
  "structures": {
    "sections": [
      {
        "heading": string,
        "body_pt": string,
        "examples_en": [string]
      }
    ]
  },
  "vocabulary": [
    {
      "term": string,
      "meaning_pt": string,
      "notes_pt": string,
      "common_collocations_en": [string]
    }
  ],
  "examples_and_drills": {
    "pattern_drills": [
      {
        "pattern_name_pt": string,
        "pattern_explanation_pt": string,
        "examples_en": [string],
        "fixation_prompts_pt": [string]
      }
    ],
    "mistakes_pt_speakers": [
      { "wrong": string, "better": string, "why_pt": string }
    ]
  },
  "curiosities": [
    { "title": string, "body_pt": string, "needs_verification": boolean }
  ]
}

Guidance for quality:
- structures.sections: explain real grammar/syntax you see (tenses, modals, ellipsis, contractions, questions, etc.).
- vocabulary: prioritize high-value items (phrasal verbs, idioms, informal forms like 'cause), not every word.
- examples_and_drills: short, natural English; fixation_prompts_pt tells the learner what to practice in PT.
""".strip()

    return f"""{hint_block}{schema_instructions}

Lyrics (English):
<<<
{lyrics.strip()}
>>>
"""
