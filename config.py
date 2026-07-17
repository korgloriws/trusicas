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

# Pool gratuito por defeito. OpenRouter escolhe o mais disponível/rápido no momento
# (provider.sort + partition=none). Nota: openai/gpt-oss-120b:free não existe — usamos :20b:free.
DEFAULT_FREE_MODELS: tuple[str, ...] = (
    "nvidia/nemotron-3-super-120b-a12b:free",
    "qwen/qwen3-coder:free",
    "openai/gpt-oss-20b:free",
    "tencent/hy3:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
)

# Aliases conhecidos quando o slug pedido deixou de existir no OpenRouter.
_MODEL_ALIASES: dict[str, str] = {
    "openai/gpt-oss-120b:free": "openai/gpt-oss-20b:free",
}


@dataclass(frozen=True)
class Settings:
    api_key: str
    """Modelo principal (primeiro do pool / override único)."""
    model: str
    """Pool enviado ao OpenRouter em «models» para routing por disponibilidade."""
    models: tuple[str, ...]
    """Ordenação: throughput (melhor p/ lições longas) ou latency (1.º token)."""
    route_sort: str
    http_referer: str | None
    x_title: str | None
    json_mode: bool
    temperature: float
    timeout_s: float
    max_output_tokens: int


def _normalize_model_id(raw: str) -> str:
    mid = raw.strip()
    return _MODEL_ALIASES.get(mid, mid)


def _parse_models_env() -> tuple[str, ...]:
    """
    OPENROUTER_MODELS = lista separada por vírgulas.
    Se vazio, usa OPENROUTER_MODEL se definido; senão o pool DEFAULT_FREE_MODELS.
    """
    raw_list = (os.getenv("OPENROUTER_MODELS") or "").strip()
    if raw_list:
        parts = [_normalize_model_id(p) for p in raw_list.split(",")]
        models = tuple(dict.fromkeys(p for p in parts if p))
        if models:
            return models

    single = (os.getenv("OPENROUTER_MODEL") or "").strip()
    if single:
        return (_normalize_model_id(single),)

    return DEFAULT_FREE_MODELS


def _parse_route_sort() -> str:
    raw = (os.getenv("OPENROUTER_ROUTE_SORT") or "throughput").strip().lower()
    if raw in {"latency", "ttft"}:
        return "latency"
    if raw in {"price"}:
        return "price"
    return "throughput"


def load_settings(*, temperature: float | None = None) -> Settings:
    ensure_env_loaded()
    api_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(
            "Missing OPENROUTER_API_KEY. Copy .env.example to .env and set your key."
        )
    models = _parse_models_env()
    model = models[0]
    route_sort = _parse_route_sort()
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
        models=models,
        route_sort=route_sort,
        http_referer=http_referer,
        x_title=x_title,
        json_mode=json_mode,
        temperature=temp,
        timeout_s=timeout_s,
        max_output_tokens=max_output_tokens,
    )


def _truthy(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}
