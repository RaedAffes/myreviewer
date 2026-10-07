"""Fetch project requirements/context from a GitHub repo and from the user.

Composes the DECLARED REQUIREMENTS block consumed by the summarize and
bug/requirement-review stages. Repo docs are pulled through the GitHub
contents API (raw accept header); unreachable repos or repos without docs
degrade to an empty string instead of failing the pipeline. Manual
requirements supplied by the user are appended so their contract wins on any
conflict.
"""

from __future__ import annotations

import requests

GITHUB_API = "https://api.github.com"

# Docs that commonly state a project's requirements/acceptance criteria.
CANDIDATE_DOCS = (
    "README.md",
    "REQUIREMENTS.md",
    "docs/REQUIREMENTS.md",
    "docs/requirements.md",
    "CONTRIBUTING.md",
    "docs/CONTRIBUTING.md",
    "ADR.md",
    "docs/adr.md",
)

MAX_CHARS_PER_DOC = 6_000
MAX_REQUIREMENTS_CHARS = 16_000


def _raw(name: str, owner: str, repo: str) -> str:
    return f"{GITHUB_API}/repos/{owner}/{repo}/contents/{name}"


def fetch_repo_requirements(owner: str, repo: str, token: str) -> str:
    """Return a DECLARED REQUIREMENTS block mined from the repo, or ''."""
    if not token:
        return ""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.raw+json",
    }
    blocks: list[str] = []
    for doc in CANDIDATE_DOCS:
        try:
            resp = requests.get(_raw(doc, owner, repo), headers=headers, timeout=30)
        except requests.RequestException:
            continue
        if resp.status_code not in (200, 201, 302):
            continue
        if resp.status_code in (200, 201):
            content = resp.text or ""
        else:
            # 302 redirect for README: GitHub usually follows it already; if not, bail.
            continue
        content = content.strip()
        if not content:
            continue
        heading = f"### {doc} (excerpt)"
        snippet = content[:MAX_CHARS_PER_DOC]
        if len(content) > MAX_CHARS_PER_DOC:
            snippet += "\n…(truncated)"
        blocks.append(f"{heading}\n{snippet}")
    if not blocks:
        return ""
    body = "\n\n".join(blocks)
    if len(body) > MAX_REQUIREMENTS_CHARS:
        body = body[:MAX_REQUIREMENTS_CHARS] + "\n…(truncated)"
    return (f"[REQUIREMENTS EXPLORED FROM REPO {owner}/{repo}]\n"
            f"{body}\n\n[SOURCE: repo docs; user-supplied requirements follow if any.]")


def compose_requirements(*, repo_block: str, manual: str) -> str:
    """Merge repo-derived requirements and user-supplied requirements."""
    parts: list[str] = []
    if repo_block.strip():
        parts.append(repo_block.strip())
    manual = (manual or "").strip()
    if manual:
        parts.append(f"[USER-SUPPLIED REQUIREMENTS — authoritative on conflict]\n{manual}")
    combined = "\n\n".join(parts)
    if len(combined) > MAX_REQUIREMENTS_CHARS:
        combined = combined[:MAX_REQUIREMENTS_CHARS] + "\n…(truncated)"
    return combined