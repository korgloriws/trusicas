#!/usr/bin/env python3
"""Gera a linha YOUTUBE_COOKIES_B64=... a partir de um cookies.txt Netscape.

Uso:
  python tools/cookies_to_b64.py cookies.txt
  python tools/cookies_to_b64.py cookies.txt >> .env   # cuidado: não committe .env
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("Uso: python tools/cookies_to_b64.py <cookies.txt>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"Ficheiro não encontrado: {path}", file=sys.stderr)
        return 1
    raw = path.read_bytes()
    if b"youtube.com" not in raw.lower() and b"netscape" not in raw.lower():
        print(
            "Aviso: o ficheiro não parece cookies Netscape do YouTube.",
            file=sys.stderr,
        )
    b64 = base64.b64encode(raw).decode("ascii")
    print(f"YOUTUBE_COOKIES_B64={b64}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
