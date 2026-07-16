from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent
_ENV_LOADED = False


def ensure_env_loaded() -> None:
    """Load trusicas/.env regardless of the process working directory."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    load_dotenv(_PROJECT_ROOT / ".env")
    _ENV_LOADED = True


# Default max completion tokens for lesson generation (OpenRouter may return 400 if the value is too large).
# The model card may advertise a higher "max output"; the API still enforces a lower practical cap per request.
DEFAULT_MAX_OUTPUT_TOKENS = 32_768

# Hard cap on what we send as max_tokens (OpenRouter may 400 if the value exceeds the model/route limit).
OPENROUTER_MAX_TOKENS_REQUEST_CAP = 65_536


@dataclass(frozen=True)
class Settings:
    api_key: str
    model: str
    http_referer: str | None
    x_title: str | None
    json_mode: bool
    temperature: float
    timeout_s: float
    max_output_tokens: int


def load_settings(*, temperature: float | None = None) -> Settings:
    ensure_env_loaded()
    api_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(
            "Missing OPENROUTER_API_KEY. Copy .env.example to .env and set your key."
        )
    model = (
        os.getenv("OPENROUTER_MODEL")
        or "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
    ).strip()
    http_referer = (os.getenv("OPENROUTER_HTTP_REFERER") or "").strip() or None
    x_title = (os.getenv("OPENROUTER_X_TITLE") or "").strip() or None
    json_mode = _truthy(os.getenv("OPENROUTER_JSON_MODE", "false"))
    temp = float(temperature if temperature is not None else os.getenv("TEMPERATURE", "0.25"))
    timeout_s = float(os.getenv("OPENROUTER_TIMEOUT_S", "120"))
    raw_max = (os.getenv("OPENROUTER_MAX_OUTPUT_TOKENS") or str(DEFAULT_MAX_OUTPUT_TOKENS)).strip()
    try:
        max_output_tokens = int(raw_max)
    except ValueError:
        max_output_tokens = DEFAULT_MAX_OUTPUT_TOKENS
    max_output_tokens = max(512, min(max_output_tokens, OPENROUTER_MAX_TOKENS_REQUEST_CAP))
    return Settings(
        api_key=api_key,
        model=model,
        http_referer=http_referer,
        x_title=x_title,
        json_mode=json_mode,
        temperature=temp,
        timeout_s=timeout_s,
        max_output_tokens=max_output_tokens,
    )


def _truthy(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}
