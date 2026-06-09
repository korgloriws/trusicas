from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from generate import generate_lesson
from render_md import lesson_dict_to_markdown


def _read_lyrics(*, input_path: Path | None) -> str:
    if input_path is not None:
        return input_path.read_text(encoding="utf-8")
    return sys.stdin.read()


def main() -> int:
    p = argparse.ArgumentParser(description="Generate a structured lesson JSON+MD from English lyrics.")
    p.add_argument("--input", "-i", type=Path, default=None, help="Lyrics file (UTF-8). If omitted, read stdin.")
    p.add_argument("--out-dir", "-o", type=Path, default=Path("output"), help="Output directory")
    p.add_argument("--basename", "-b", type=str, default="lesson", help="Base filename without extension")
    p.add_argument("--title", type=str, default=None, help="Optional song title hint for the model")
    p.add_argument("--artist", type=str, default=None, help="Optional artist hint for the model")
    p.add_argument("--temperature", type=float, default=None, help="Override temperature (default: env or 0.25)")
    p.add_argument("--model", type=str, default=None, help="Override OPENROUTER_MODEL for this run only")
    args = p.parse_args()

    lyrics = _read_lyrics(input_path=args.input).strip()
    if not lyrics:
        print("No lyrics provided.", file=sys.stderr)
        return 2

    result = generate_lesson(
        lyrics,
        title_hint=args.title,
        artist_hint=args.artist,
        temperature=args.temperature,
        model=args.model,
    )

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    base = args.basename

    raw_path = out_dir / f"{base}.raw.txt"
    raw_path.write_text(result.raw, encoding="utf-8")

    if not result.ok or result.lesson is None:
        json_path = out_dir / f"{base}.lesson.json.error.txt"
        json_path.write_text(f"{result.error or 'unknown'}\n", encoding="utf-8")
        print(f"Failed to parse JSON: {result.error}", file=sys.stderr)
        print(f"Wrote raw model output to: {raw_path}", file=sys.stderr)
        return 1

    json_path = out_dir / f"{base}.lesson.json"
    json_path.write_text(json.dumps(result.lesson, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    md_path = out_dir / f"{base}.lesson.md"
    md_path.write_text(lesson_dict_to_markdown(result.lesson), encoding="utf-8")

    try:
        from store import insert_lesson

        saved = insert_lesson(
            lyrics_en=lyrics,
            title_hint=args.title,
            artist_hint=args.artist,
            model=result.model_used,
            lesson=result.lesson,
            raw_response=result.raw,
        )
        print(f"sqlite_saved_id={saved['id']}", file=sys.stderr)
    except Exception as e:
        print(f"SQLite save skipped: {e}", file=sys.stderr)

    print(str(json_path))
    print(str(md_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
