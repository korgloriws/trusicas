from __future__ import annotations

import json
from typing import Any

import httpx

from config import Settings


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def ping_openrouter(
    *,
    settings: Settings,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """
    One minimal chat completion (no JSON mode) to verify API key, network, and model id.
    Returns a dict: ok, model, latency_ms, reply_preview, http_status, error.
    """
    import time

    out: dict[str, Any] = {
        "ok": False,
        "model": settings.model,
        "latency_ms": None,
        "reply_preview": "",
        "http_status": None,
        "error": None,
    }
    to = float(timeout_s) if timeout_s is not None else min(45.0, max(8.0, float(settings.timeout_s)))

    headers: dict[str, str] = {
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
    }
    if settings.http_referer:
        headers["HTTP-Referer"] = settings.http_referer
    if settings.x_title:
        headers["X-Title"] = settings.x_title

    body: dict[str, Any] = {
        "model": settings.model,
        "max_tokens": min(settings.max_output_tokens, 8192),
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": "Reply with exactly one word: PONG",
            },
        ],
    }

    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=to) as client:
            r = client.post(OPENROUTER_URL, headers=headers, json=body)
        out["http_status"] = r.status_code
        out["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        if r.status_code != 200:
            out["error"] = _short_json({"status": r.status_code, "body": r.text}, limit=800)
            return out
        data = r.json()
    except httpx.TimeoutException as e:
        out["error"] = f"Timeout após {to:.0f}s: {e}"
        return out
    except httpx.RequestError as e:
        out["error"] = f"Erro de rede ao contactar OpenRouter: {e}"
        return out
    except json.JSONDecodeError as e:
        out["error"] = f"Resposta não é JSON válido: {e}"
        return out

    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        if isinstance(err, dict):
            err = err.get("message") or err.get("code") or err
        out["error"] = f"OpenRouter: {err!r}"
        return out

    try:
        choice = data["choices"][0]
    except (KeyError, IndexError, TypeError) as e:
        out["error"] = f"Resposta sem choices[0]: {e!s}; keys={list(data.keys()) if isinstance(data, dict) else type(data)}"
        return out

    message = choice.get("message")
    if not isinstance(message, dict):
        out["error"] = f"message inesperado: {type(message)!r}"
        return out

    text = _content_blocks_to_text(message.get("content")).strip()
    if not text:
        legacy = choice.get("text")
        if isinstance(legacy, str):
            text = legacy.strip()
    if not text:
        rsn = message.get("reasoning")
        if isinstance(rsn, str) and rsn.strip():
            text = rsn.strip()
        else:
            text = _content_blocks_to_text(rsn).strip()
    if not text:
        out["error"] = (
            "Conteúdo vazio (message.content e reasoning). "
            f"finish_reason={choice.get('finish_reason')!r} "
            f"message_keys={sorted(message.keys())!r}"
        )
        return out

    out["ok"] = True
    out["reply_preview"] = _preview(text, 240)
    return out


def _content_blocks_to_text(content: Any) -> str:
    """OpenAI-compatible APIs may return content as str, null, or list of parts."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        t = content.get("text")
        if isinstance(t, str):
            return t
        return ""
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                t = block.get("type")
                if t == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif isinstance(block.get("text"), str):
                    parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    # Fallback (numbers, etc.)
    return str(content)


def complete_chat(*, settings: Settings, system: str, user: str) -> tuple[str, str]:
    """
    Chat completion via OpenRouter.
    Returns (content_text, model_id_used).

    Com vários modelos no Settings.models, pede ao OpenRouter para escolher
    o endpoint mais rápido/disponível no momento (sort + partition=none).
    """
    headers: dict[str, str] = {
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
    }
    if settings.http_referer:
        headers["HTTP-Referer"] = settings.http_referer
    if settings.x_title:
        headers["X-Title"] = settings.x_title

    models = tuple(m for m in (settings.models or (settings.model,)) if m)
    if not models:
        models = (settings.model,)

    body: dict[str, Any] = {
        "max_tokens": settings.max_output_tokens,
        "temperature": settings.temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if len(models) == 1:
        body["model"] = models[0]
    else:
        # Routing cross-model: OpenRouter escolhe pelo desempenho actual
        # (não fica preso ao 1.º da lista). Ver docs «provider.sort.partition».
        body["models"] = list(models)
        body["provider"] = {
            "allow_fallbacks": True,
            "sort": {
                "by": settings.route_sort or "throughput",
                "partition": "none",
            },
        }
    if settings.json_mode:
        body["response_format"] = {"type": "json_object"}

    with httpx.Client(timeout=settings.timeout_s) as client:
        r = client.post(OPENROUTER_URL, headers=headers, json=body)
    if r.status_code >= 400:
        raise RuntimeError(
            f"OpenRouter HTTP {r.status_code}: {_preview(r.text, 2000)}"
        )
    data = r.json()

    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        if isinstance(err, dict):
            err = err.get("message") or err.get("metadata") or err
        raise RuntimeError(f"OpenRouter (corpo com erro): {err!r}")

    model_used = ""
    if isinstance(data, dict):
        raw_model = data.get("model")
        if isinstance(raw_model, str) and raw_model.strip():
            model_used = raw_model.strip()
    if not model_used:
        model_used = models[0]

    try:
        choice = data["choices"][0]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"OpenRouter: missing choices[0]. Body keys: {list(data.keys())!r}") from e

    message = choice.get("message")
    if not isinstance(message, dict):
        raise RuntimeError(
            f"OpenRouter: unexpected message type {type(message)!r}. choice={_short_json(choice)}"
        )

    text = _content_blocks_to_text(message.get("content")).strip()
    if not text:
        legacy = choice.get("text")
        if isinstance(legacy, str):
            text = legacy.strip()

    if not text:
        # Some models put text elsewhere or return only tool calls / refusals
        refusal = message.get("refusal")
        reasoning = message.get("reasoning")
        fr = choice.get("finish_reason")
        raise RuntimeError(
            "OpenRouter devolveu conteúdo vazio no message.content. "
            f"finish_reason={fr!r}, model={model_used!r}. "
            f"message keys={sorted(message.keys())!r}. "
            f"refusal={refusal!r}, reasoning_preview={_preview(reasoning)}. "
            f"choice_preview={_short_json(choice)}"
        )

    return text, model_used


def _preview(val: Any, limit: int = 400) -> str:
    if val is None:
        return ""
    s = str(val)
    return s if len(s) <= limit else s[:limit] + "…"


def _short_json(obj: Any, limit: int = 1800) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except TypeError:
        s = repr(obj)
    return s if len(s) <= limit else s[:limit] + "…"
