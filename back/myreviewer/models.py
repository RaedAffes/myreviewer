"""Curated, verifiable model registry for the per-analysis model picker.

NVIDIA's free NIM catalog lists far more models than a free account can
actually call (we probed it 2026-09: most return HTTP 404 "not found for
account", some hang for minutes, and several widely-used ids are retired → HTTP
410). So instead of surfacing the whole catalog, we ship a small registry of
candidates and let `NIM_MODELS` (comma-separated, in .env) extend it — for
self-hosted vLLM/NIM instances or account-specific models.

The registry feeds `GET /api/models`, the dashboard model `<select>`, and
`run_pipeline(model=...)` validation. A model not in the registry is still
honored when explicitly requested, so power users are never blocked.
"""

from __future__ import annotations

VERIFIED_DEFAULT = "nvidia/nemotron-3-ultra-550b-a55b"

# The models this account can actually call (probed 2026-10). NVIDIA's free
# catalog lists many ids that return HTTP 404 "not found for account", so this
# registry stays small and only holds reachable models. Add more via
# `NIM_MODELS` (.env) for self-hosted vLLM/NIM or account-specific ids.
CURATED = [
    {
        "id": "nvidia/nemotron-3-ultra-550b-a55b",
        "label": "Nemotron 3 Ultra 550B (A55B)",
        "verified": True,
        "notes": "Default model (NIM_MODEL). Reachable on the free account (probed 2026-10).",
    },
    {
        "id": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "label": "Nemotron 3.5 Lightning 30B (A3B)",
        "verified": True,
        "notes": "Faster/smaller Nemotron; reachable on the free account (probed 2026-10).",
    },
    {
        "id": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        "label": "Nemotron 3 Nano Omni 30B (A3B) — reasoning",
        "verified": True,
        "notes": "Reasoning-focused Nemotron; reachable on the free account (probed 2026-10).",
    },
]

# Models that were problematic and should not be auto-offered. Deliberately
# small: anything else is allowed if you hardcode it in NIM_MODELS.
RETIRED = {
    "nvidia/nemotron-3-super-120b-a12b": "catalogued but HTTP 404 'not found for account' on the free tier (probed 2026-10)",
    "nvidia/llama-3.3-nemotron-super-49b-v1.5": "EOL on NIM (HTTP 410) since 2026-08",
    "nvidia/llama-3.3-nemotron-super-49b-v1": "retired on NIM (HTTP 410)",
    "openai/gpt-oss-120b": "EOL on NIM (HTTP 410) since 2026-09",
}


def available_models(config) -> list[dict]:
    """Models offered in the app picker: curated default + `NIM_MODELS` extras."""
    items: list[dict] = [dict(m) for m in CURATED]
    for extra in getattr(config, "models", None) or []:
        if not extra or not extra.strip():
            continue
        extra = extra.strip()
        if any(m["id"] == extra for m in items):
            continue
        if extra in RETIRED:
            continue
        items.append({
            "id": extra,
            "label": extra,
            "verified": False,
            "notes": "Custom model from NIM_MODELS (self-hosted or account-specific).",
        })
    default = getattr(config, "model", VERIFIED_DEFAULT) or VERIFIED_DEFAULT
    if not any(m["id"] == default for m in items):
        items.insert(0, {
            "id": default,
            "label": default,
            "verified": True,
            "notes": "Configured default model (NIM_MODEL).",
        })
    return items


def is_known_model(config, model_id: str | None) -> bool:
    if not model_id:
        return True
    return any(m["id"] == model_id for m in available_models(config))