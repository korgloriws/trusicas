from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace

from client import ping_openrouter
from config import load_settings


def main() -> int:
    p = argparse.ArgumentParser(
        description="Testa a ligação ao OpenRouter com um único pedido curto (sem gerar lição completa).",
    )
    p.add_argument("--model", type=str, default=None, help="Sobrescreve OPENROUTER_MODEL só neste teste")
    p.add_argument("--timeout", type=float, default=None, help="Timeout em segundos (default: até 30)")
    args = p.parse_args()
    try:
        settings = load_settings()
    except RuntimeError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
        return 1
    if args.model:
        mid = args.model.strip()
        settings = replace(settings, model=mid, models=(mid,))
    result = ping_openrouter(settings=settings, timeout_s=args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
