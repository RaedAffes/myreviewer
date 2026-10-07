"""Configuration loading for MyReviewer.

Reads values from a `.env` file (or the process environment) so the tool is
provider-agnostic: by default it targets NVIDIA NIM's free OpenAI-compatible
endpoints, but every knob (base URL, key, model) can be overridden, which also
allows pointing it at a self-hosted vLLM server or the user's own account later.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b"


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


@dataclass
class Config:
    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    models: list[str] = field(default_factory=list)
    temperature: float = 0.0
    max_tokens: int = 4096
    github_token: str = ""
    github_app_enabled: bool = False
    github_app_id: str = ""
    github_webhook_secret: str = ""
    github_bot_username: str = "myreviewer[bot]"
    github_private_key_path: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    github_redirect_uri: str = ""
    github_dev_client_id: str = ""
    github_dev_client_secret: str = ""
    github_dev_redirect_uri: str = ""
    frontend_url: str = ""
    data_dir: str = ""
    # All entries seen in the environment, kept for debugging/inspection.
    raw: dict = field(default_factory=dict)

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)


def _load_dotenv(path: Path) -> dict:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            values[key] = value
    return values


def load_config(env_file: Path | None = None, overrides: dict | None = None) -> Config:
    """Load configuration, preferring explicit `overrides` > `.env` file > env.

    `env_file` defaults to back/.env. `overrides` may carry the same
    keys as the `.env` file and are typically provided by CLI arguments.
    """
    env_file = env_file or (BACKEND_ROOT / ".env")
    dotenv_values = _load_dotenv(env_file)
    env_values = dict(os.environ)
    merged: dict[str, str] = {}
    merged.update(env_values)
    for key, value in dotenv_values.items():
        if key not in merged:
            merged[key] = value
    for key, value in (overrides or {}).items():
        if value is not None:
            merged[key] = str(value)

    def _get(*names: str, default: str = "") -> str:
        for name in names:
            if merged.get(name):
                return merged[name]
        return default

    api_key = _get("NIM_API_KEY", "LLM_API_KEY", "NVIDIA_API_KEY")
    base_url = _get("NIM_BASE_URL", "LLM_BASE_URL", default=DEFAULT_BASE_URL)
    model = _get("NIM_MODEL", "LLM_MODEL", default=DEFAULT_MODEL)
    models = [m.strip() for m in
              _get("NIM_MODELS", "LLM_MODELS", default="").split(",") if m.strip()]

    try:
        temperature = float(_get("LLM_TEMPERATURE", default="0.0"))
    except ValueError:
        temperature = 0.0
    try:
        max_tokens = int(_get("LLM_MAX_TOKENS", default="4096"))
    except ValueError:
        max_tokens = 4096

    frontend_url_val = _get("APP_URL", "FRONTEND_URL")
    if not frontend_url_val and merged.get("VERCEL"):
        # Vercel auto-injects these; the app then works with zero setup.
        domain = (
            merged.get("VERCEL_PROJECT_PRODUCTION_URL")
            or merged.get("VERCEL_URL")
            or ""
        )
        frontend_url_val = f"https://{domain}" if domain else ""
    if frontend_url_val and not frontend_url_val.startswith(("http://", "https://")):
        frontend_url_val = f"https://{frontend_url_val}"

    data_dir = _get("DATA_DIR")
    if not data_dir:
        data_dir = "/tmp/myreviewer-data"

    return Config(
        api_key=api_key,
        base_url=base_url,
        model=model,
        models=models,
        temperature=temperature,
        max_tokens=max_tokens,
        github_token=_get("GITHUB_TOKEN"),
        github_app_enabled=_get("GITHUB_APP_ENABLED", default="0").strip().lower()
        in ("1", "true", "yes", "on"),
        github_app_id=_get("GITHUB_APP_ID"),
        github_webhook_secret=_get("GITHUB_WEBHOOK_SECRET"),
        github_bot_username=_get("GITHUB_BOT_USERNAME", default="myreviewer[bot]"),
        github_private_key_path=_get("GITHUB_PRIVATE_KEY_PATH"),
        github_client_id=_get("GITHUB_CLIENT_ID"),
        github_client_secret=_get("GITHUB_CLIENT_SECRET"),
        github_redirect_uri=_get(
            "GITHUB_REDIRECT_URI",
            default="https://myreviewer.tech/api/auth/callback",
        ),
        github_dev_client_id=_get("GITHUB_DEV_CLIENT_ID"),
        github_dev_client_secret=_get("GITHUB_DEV_CLIENT_SECRET"),
        github_dev_redirect_uri=_get("GITHUB_DEV_REDIRECT_URI"),
        frontend_url=frontend_url_val or "https://myreviewer.tech",
        data_dir=data_dir,
        raw=dict(merged),
    )