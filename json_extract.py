from __future__ import annotations

import json
import re
from typing import Any


def extract_json_object(text: str) -> dict[str, Any]:

    if text is None:
        raise ValueError("Model output is empty (null).")
    cleaned = str(text).strip()
    if cleaned in {"", "null", "None"}:
        raise ValueError("Model output is empty.")
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    cleaned = cleaned.strip()

    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj
        raise ValueError("JSON root must be an object")
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Could not locate a JSON object in model output")
    snippet = cleaned[start : end + 1]
    obj = json.loads(snippet)
    if not isinstance(obj, dict):
        raise ValueError("JSON root must be an object")
    return obj
