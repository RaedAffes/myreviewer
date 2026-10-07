"""FastAPI webhook handler for the MyReviewer GitHub App.

Behavior:
- If ``GITHUB_APP_ENABLED=0`` (default) it is inert: logs events and returns,
  so running the server is a safe way to inspect payloads during development.
- GitHub App mode (preferred): on each ``pull_request`` event the ``installation
  id`` from the payload is exchanged for a short-lived installation access
  token (signed JWT with the app's private key), which is used to fetch the
  diff and post/update ONE marker comment on the PR.
- Fallback token mode: if the payload has no ``installation`` (repo/org
  webhooks), a ``GITHUB_TOKEN`` PAT is used instead.

Register the webhook URL as ``https://<your-domain>/webhook`` on the GitHub App
and subscribe to the ``pull_request`` event.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from pathlib import Path

import jwt
import requests
from fastapi import FastAPI, Request, Response, status
from prometheus_fastapi_instrumentator import Instrumentator

from myreviewer import run_pipeline
from myreviewer.config import BACKEND_ROOT, load_config
from github_client import (
    GITHUB_API,
    GithubApiDiffSource,
    GithubApiError,
    find_bot_comment,
    post_pr_comment,
    prepare_comment_body,
    update_pr_comment,
)

log = logging.getLogger("myreviewer")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="MyReviewer Webhook")


def _verify_signature(payload: bytes, signature_header: str, secret: str) -> bool:
    if not signature_header or not secret:
        return False
    digest = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature_header)


def _install_token(config, installation_id: int) -> str:
    """Exchange a signed JWT for a GitHub App installation access token."""
    key_path = Path(config.github_private_key_path)
    if not config.github_app_id or not key_path.exists():
        raise GithubApiError(
            "GITHUB_APP_ID / GITHUB_PRIVATE_KEY_PATH not configured for installation tokens"
        )
    now = int(time.time())
    jwt_token = jwt.encode(
        {"iat": now, "exp": now + 600, "iss": config.github_app_id},
        key_path.read_text(),
        algorithm="RS256",
    )
    resp = requests.post(
        f"{GITHUB_API}/app/installations/{installation_id}/access_tokens",
        headers={"Authorization": f"Bearer {jwt_token}",
                 "Accept": "application/vnd.github+json"},
        timeout=60,
    )
    if resp.status_code != 201:
        raise GithubApiError(
            f"installation token failed: HTTP {resp.status_code} {resp.text[:200]}"
        )
    return resp.json()["token"]


def _handle_pull_request(payload: dict, config, token: str) -> dict:
    action = payload.get("action")
    if action not in ("opened", "reopened", "synchronize"):
        return {"handled": False, "reason": f"action '{action}' ignored"}

    pr = payload.get("pull_request") or {}
    repo = payload.get("repository") or {}
    number = pr.get("number")
    owner = (repo.get("owner") or {}).get("login") or repo.get("full_name", "").split("/")[0]
    name = repo.get("name") or repo.get("full_name", "").split("/")[1]
    if not number or not owner or not name:
        return {"handled": False, "reason": "missing pull_request/repository metadata"}

    diff_source = GithubApiDiffSource(token, owner, name, number)
    diff = diff_source.fetch()
    report = run_pipeline(diff, config=config)
    body = prepare_comment_body(report)

    existing = find_bot_comment(token, owner, name, number, config.github_bot_username)
    if existing:
        update_pr_comment(token, owner, name, existing, body)
        route = "updated"
    else:
        post_pr_comment(token, owner, name, number, body)
        route = "posted"
    return {"handled": True, "route": route, "pr": number}


@app.post("/webhook")
async def webhook(request: Request) -> Response:
    config = load_config(BACKEND_ROOT / ".env")
    payload = await request.body()
    event = request.headers.get("X-GitHub-Event", "")
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not config.github_app_enabled:
        log.info("github app disabled (GITHUB_APP_ENABLED=0); event=%s ignored", event)
        return Response(status_code=200, content="{}")

    if not _verify_signature(payload, signature, config.github_webhook_secret):
        return Response(status_code=status.HTTP_401_UNAUTHORIZED,
                        content="invalid signature")

    data = json.loads(payload or b"{}")
    if event == "ping":
        return Response(status_code=200, content='{"ping": "pong"}')

    if event != "pull_request":
        return Response(status_code=200, content='{"handled": false}')

    try:
        installation = data.get("installation") or {}
        installation_id = installation.get("id")
        if installation_id:
            token = _install_token(config, int(installation_id))
        elif config.github_token:
            token = config.github_token
        else:
            return Response(status_code=200, content='{"handled": false, "reason": "no token source"}')
        result = _handle_pull_request(data, config, token)
    except Exception as exc:
        log.exception("failed to process pull_request event")
        return Response(status_code=500,
                        content=json.dumps({"error": str(exc)}, default=str))
    return Response(status_code=200, content=json.dumps(result, default=str))


@app.get("/health")
async def health() -> dict:
    config = load_config(BACKEND_ROOT / ".env")
    return {"status": "ok", "github_app_enabled": config.github_app_enabled,
            "model": config.model,
            "has_api_key": bool(config.has_api_key)}


Instrumentator(excluded_handlers=["/metrics"]).instrument(app).expose(app)