"""GitHub OAuth "Connect your GitHub" flow, multi-user.

Every dashboard client can connect their own GitHub account. After the OAuth
callback each connected GitHub user is stored once in ``data/github_users.json``
(keyed by GitHub login), and the browser that completed the flow gets a random
server-side session cookie (``myreviewer_session``) that maps to that login via
``data/sessions.json``. So browser A always lists/analyzes A's PRs, browser B
its own.

Security note: tokens are kept as plaintext JSON on local disk. In production
store them encrypted (or via GitHub App JWT) and keyed by an app-level account.

Endpoints using these helpers are in back/api.py.
"""

from __future__ import annotations

import json
import os
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

GITHUB_SCOPE = "repo read:user"
GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API = "https://api.github.com"

def _data_dir() -> Path:
    """Writable runtime-data directory (see myreviewer.store._data_dir)."""
    env_dir = os.environ.get("DATA_DIR")
    if env_dir:
        return Path(env_dir)
    if os.environ.get("VERCEL"):
        return Path("/tmp/myreviewer-data")
    return Path(__file__).resolve().parent / "data"


DATA_ROOT = _data_dir()
USERS_PATH = DATA_ROOT / "github_users.json"
SESSIONS_PATH = DATA_ROOT / "sessions.json"
STATE_PATH = DATA_ROOT / "oauth_state.json"

SESSION_COOKIE = "myreviewer_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

STATE_MAX_AGE_SECONDS = 10 * 60  # an authorize url is only valid for 10 minutes

DEFAULT_REDIRECT_URI = "https://myreviewer.tech/api/auth/callback"
DEFAULT_FRONTEND_URL = "https://myreviewer.tech"


class OAuthNotConfigured(Exception):
    pass


class OAuthError(Exception):
    pass


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ── OAuth dance ──────────────────────────────────────────────────────────────

def build_authorize_url(client_id: str, redirect_uri: str) -> str:
    if not client_id:
        raise OAuthNotConfigured(
            "GitHub OAuth is not configured — create an OAuth App at "
            "github.com/settings/developers/apps and add the client id + secret "
            "to back/.env."
        )
    state = secrets.token_urlsafe(16)
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    states = _read_json(STATE_PATH)
    states[state] = datetime.now(timezone.utc).isoformat()
    _write_json(STATE_PATH, states)
    return (
        f"{GITHUB_AUTHORIZE_URL}?client_id={client_id}"
        f"&redirect_uri={redirect_uri}&scope={GITHUB_SCOPE}&state={state}"
        f"&prompt=select_account"
    )


def verify_state(state: str) -> bool:
    """One-time CSRF check against any outstanding authorize request.

    Multiple accounts can start login flows at once — each gets its own state
    entry, so a later login no longer invalidates an earlier one (and an
    aborted flow no longer leaves a stale creator cookie in charge).
    """
    if not state:
        return False
    states = _read_json(STATE_PATH)
    if state not in states:
        return False
    created_at = states.pop(state)
    _write_json(STATE_PATH, states)
    try:
        created = datetime.fromisoformat(created_at)
    except (TypeError, ValueError):
        return False
    age = datetime.now(timezone.utc) - created
    return age.total_seconds() <= STATE_MAX_AGE_SECONDS


def exchange_code(client_id: str, client_secret: str, code: str,
                  redirect_uri: str) -> str:
    resp = requests.post(
        GITHUB_TOKEN_URL,
        headers={"Accept": "application/json"},
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=60,
    )
    payload = resp.json()
    if "access_token" not in payload:
        raise OAuthError(
            "OAuth exchange failed: "
            + (payload.get("error_description") or payload.get("error") or str(payload))
        )
    return payload["access_token"]


def fetch_user(token: str) -> dict:
    resp = requests.get(
        f"{GITHUB_API}/user",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json"},
        timeout=60,
    )
    if resp.status_code != 200:
        raise OAuthError(f"GitHub user lookup failed: HTTP {resp.status_code} {resp.text[:200]}")
    data = resp.json()
    return {
        "login": data.get("login", ""),
        "name": data.get("name") or data.get("login", ""),
        "avatar_url": data.get("avatar_url", ""),
    }


# ── per-user token store ─────────────────────────────────────────────────────

def save_github_user(user: dict, token: str) -> dict:
    login = user.get("login", "")
    if not login:
        raise OAuthError("GitHub returned an empty login.")
    users = _read_json(USERS_PATH)
    users[login] = {
        **user,
        "token": token,
        "connected_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(USERS_PATH, users)
    return users[login]


def get_github_user(login: str) -> dict | None:
    if not login:
        return None
    return _read_json(USERS_PATH).get(login)


def delete_github_user(login: str) -> None:
    users = _read_json(USERS_PATH)
    if login in users:
        del users[login]
        _write_json(USERS_PATH, users)


def connected_users() -> dict:
    return {login: {k: v for k, v in u.items() if k != "token"}
            for login, u in _read_json(USERS_PATH).items()}


# ── browser sessions ─────────────────────────────────────────────────────────

def create_session(login: str) -> str:
    sessions = _read_json(SESSIONS_PATH)
    session_id = uuid.uuid4().hex
    sessions[session_id] = {
        "login": login,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(SESSIONS_PATH, sessions)
    return session_id


def session_login(session_id: str) -> str | None:
    if not session_id:
        return None
    session = _read_json(SESSIONS_PATH).get(session_id)
    return (session or {}).get("login")


def destroy_session(session_id: str) -> None:
    sessions = _read_json(SESSIONS_PATH)
    if session_id in sessions:
        del sessions[session_id]
        _write_json(SESSIONS_PATH, sessions)


def connected_user_by_cookie(session_id: str) -> dict | None:
    """Resolve the GitHub user bound to a browser session cookie."""
    login = session_login(session_id)
    if not login:
        return None
    return get_github_user(login)


def resolve_token(user: dict | None) -> str:
    """Usable GitHub token for the request: the session user's token only.

    No fallback to a server-side env PAT — otherwise one user's browser
    would silently act as the app owner instead of the signed-in account.
    """
    if user and user.get("token"):
        return user["token"]
    raise OAuthError(
        "no GitHub access — connect your GitHub account in the dashboard"
    )