"""Repository evidence retrieval via the GitHub API.

Builds the REPOSITORY CONTEXT block injected into the post-review and bug
review prompts (alongside the PR summary, which stays in every prompt):

1. full post-PR content of each changed file (contents API @ PR head SHA),
2. code-search references to the changed modules (is it imported/called?),
3. related test files,
4. changed config/CI files.

Everything degrades to partial output on failure — an unreachable API or a
rate-limited search must never fail the pipeline (same contract as
`repo_context.py`).
"""

from __future__ import annotations

import re
from typing import Any

import requests

GITHUB_API = "https://api.github.com"

# Budgets — the whole block is capped so diff + context stay inside the
# model's comfortable input range.
MAX_CONTEXT_CHARS = 20_000
MAX_CHANGED_FILES = 6
MAX_FILE_CHARS = 5_000
MAX_REFERENCE_SEARCHES = 6
MAX_TEST_SEARCHES = 4
MAX_REFERENCES_LISTED = 10

CONFIG_PATH_PATTERNS = (
    re.compile(r"^\.github/workflows/[^/]+$"),
    re.compile(r"^Dockerfile$"),
    re.compile(r"^docker-compose[^/]*\.ya?ml$"),
    re.compile(r"^(package\.json|requirements[^/]*\.txt|pyproject\.toml|pom\.xml|go\.mod)$"),
    re.compile(r"^nginx[^/]*\.conf$"),
    re.compile(r"^.*\.tf$"),
)
TEST_PATH_RE = re.compile(r"(^|/)(tests?|__tests__)/|(^|/)test_[^/]+\.py$|_test\.(py|js|ts)$",
                          re.IGNORECASE)
CODE_FILE_RE = re.compile(r"\.(py|js|ts|tsx|jsx|go|java|rb|php|cs|cpp|c|h|rs|kt|swift|scala)$",
                          re.IGNORECASE)

_session = requests.Session()


def build_repo_context(token: str, owner: str, repo: str,
                       diff: Any, head_sha: str = "") -> str:
    """Return the REPOSITORY CONTEXT block, best-effort, budgeted."""
    if not token:
        return ""
    headers = _headers(token)
    parts: list[str] = []
    used = 0

    changed = [p for p in diff.file_paths if p and not p.startswith(("http://", "https://"))]

    # 1) full content of changed files at the PR head
    for path in changed[:MAX_CHANGED_FILES]:
        if used >= MAX_CONTEXT_CHARS:
            break
        content = _fetch_file(headers, owner, repo, path, head_sha)
        if not content:
            continue
        block = f"=== CHANGED FILE: {path} (post-PR content) ===\n{content[:MAX_FILE_CHARS]}"
        block = block[:MAX_CONTEXT_CHARS - used]
        parts.append(block)
        used += len(block)

    # 2) references to changed modules (callers/importers)
    stems = _module_stems(changed)
    searches = 0
    for stem in stems:
        if searches >= MAX_REFERENCE_SEARCHES or used >= MAX_CONTEXT_CHARS:
            break
        paths = _search_paths(headers, owner, repo, f"{stem}")
        searches += 1
        if not paths:
            continue
        refs = [p for p in paths if not _is_test_path(p)][:MAX_REFERENCES_LISTED]
        if refs:
            block = (f"=== REFERENCES TO '{stem}' (files mentioning it) ===\n"
                     + ", ".join(refs))
            block = block[:MAX_CONTEXT_CHARS - used]
            parts.append(block)
            used += len(block)

    # 3) related test files
    test_searches = 0
    for stem in stems:
        if test_searches >= MAX_TEST_SEARCHES or used >= MAX_CONTEXT_CHARS:
            break
        paths = _search_paths(headers, owner, repo, f"test_{stem} in:path")
        test_searches += 1
        tests = [p for p in paths if _is_test_path(p) or f"test_{stem}" in p]
        if tests:
            block = (f"=== TESTS LIKELY COVERING '{stem}' ===\n"
                     + ", ".join(tests[:MAX_REFERENCES_LISTED]))
            block = block[:MAX_CONTEXT_CHARS - used]
            parts.append(block)
            used += len(block)

    # 4) changed config / CI files
    config_paths = [p for p in changed if _is_config_path(p)][:4]
    for path in config_paths:
        if used >= MAX_CONTEXT_CHARS:
            break
        content = _fetch_file(headers, owner, repo, path, head_sha)
        if not content:
            continue
        block = f"=== CONFIG/CI: {path} ===\n{content[:MAX_FILE_CHARS]}"
        block = block[:MAX_CONTEXT_CHARS - used]
        parts.append(block)
        used += len(block)

    if not parts:
        return ""
    return "\n\n".join(parts)


# ── API helpers ──────────────────────────────────────────────────────────────

def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.raw+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _fetch_file(headers: dict, owner: str, repo: str, path: str, ref: str) -> str:
    url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}"
    if ref:
        url += f"?ref={ref}"
    try:
        resp = _session.get(url, headers=headers, timeout=30)
    except requests.RequestException:
        return ""
    if resp.status_code == 409:  # empty repo / sha not reachable (fork PRs)
        if ref:
            return _fetch_file(headers, owner, repo, path, "")
        return ""
    if resp.status_code != 200:
        return ""
    text = resp.text or ""
    return text if len(text) > 20 else ""


def _search_paths(headers: dict, owner: str, repo: str, query: str) -> list[str]:
    """GitHub code search; returns matching file paths. Empty on any failure."""
    search_headers = dict(headers)
    search_headers["Accept"] = "application/vnd.github+json"
    q = f"{query} repo:{owner}/{repo}"
    try:
        resp = _session.get(f"{GITHUB_API}/search/code",
                            headers=search_headers, params={"q": q, "per_page": 20},
                            timeout=30)
    except requests.RequestException:
        return []
    if resp.status_code != 200:  # 403/422/429 → degrade silently
        return []
    try:
        items = resp.json().get("items", [])
    except ValueError:
        return []
    return [item.get("path", "") for item in items if item.get("path")]


def _module_stems(changed_paths: list[str]) -> list[str]:
    stems: list[str] = []
    for path in changed_paths:
        if not CODE_FILE_RE.search(path) or _is_test_path(path):
            continue
        name = path.rsplit("/", 1)[-1]
        stem = re.sub(r"\.[^.]+$", "", name)
        stem = re.sub(r"^test_|_test$", "", stem)
        if stem and stem not in stems and len(stem) >= 3:
            stems.append(stem)
    return stems[:8]


def _is_test_path(path: str) -> bool:
    return bool(TEST_PATH_RE.search(path))


def _is_config_path(path: str) -> bool:
    return any(pattern.match(path) for pattern in CONFIG_PATH_PATTERNS)
