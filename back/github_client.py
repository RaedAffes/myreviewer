"""Diff acquisition + GitHub access, abstracted behind one `DiffSource`.

This seam is the production anchor: Phase 1 supplies diffs locally (a file or a
`git diff base..head`), Phase 2 adds the GitHub REST/`.diff` API (App/PAT
auth), and Phase 3 (user-owned tokens via GitHub/Google OAuth) only needs a new
`DiffSource` implementation plus an auth provider — the analysis pipeline never
changes.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from typing import Protocol

import requests

MARKER = "<!-- MYREVIEWER-V1 -->"
GITHUB_API = "https://api.github.com"
HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
DIFF_GIT_RE = re.compile(r"^diff --git a/(.*) b/(.*)$")


class DiffSourceError(Exception):
    pass


class GithubApiError(DiffSourceError):
    pass


@dataclass
class Hunk:
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    header: str = ""
    additions: int = 0
    deletions: int = 0

    def to_dict(self) -> dict:
        return {
            "old_start": self.old_start, "old_lines": self.old_lines,
            "new_start": self.new_start, "new_lines": self.new_lines,
            "header": self.header, "additions": self.additions,
            "deletions": self.deletions,
        }


@dataclass
class FileDiff:
    old_path: str
    new_path: str
    status: str = "modified"
    additions: int = 0
    deletions: int = 0
    hunks: list[Hunk] = field(default_factory=list)
    raw_patch: str = ""

    @property
    def display_path(self) -> str:
        return self.new_path or self.old_path

    def to_dict(self) -> dict:
        return {
            "old_path": self.old_path, "new_path": self.new_path,
            "status": self.status, "additions": self.additions,
            "deletions": self.deletions,
            "hunks": [h.to_dict() for h in self.hunks],
        }


@dataclass
class GitDiff:
    repo: str = ""
    base: str = ""
    head: str = ""
    title: str = ""
    description: str = ""
    pr_number: int | None = None
    files: list[FileDiff] = field(default_factory=list)
    raw: str = ""
    commit_messages: list[str] = field(default_factory=list)

    @property
    def added(self) -> int:
        return sum(f.additions for f in self.files)

    @property
    def deleted(self) -> int:
        return sum(f.deletions for f in self.files)

    @property
    def file_paths(self) -> list[str]:
        return [f.display_path for f in self.files]

    @property
    def stats_text(self) -> str:
        return (f"{len(self.files)} file(s), {self.added} additions, "
                f"{self.deleted} deletions")

    def to_compact(self, max_chars: int = 40_000) -> str:
        blocks = []
        for f in self.files:
            block = [f"### {f.display_path} ({f.status}, +{f.additions}/-{f.deletions})"]
            for h in f.hunks:
                block.append(f"@@ -{h.old_start},{h.old_lines} +{h.new_start},{h.new_lines} @@")
            block.append(f.raw_patch)
            blocks.append("\n".join(block))
        body = "\n\n".join(blocks)
        if len(body) > max_chars:
            body = body[:max_chars] + "\n...[diff truncated]"
        return body


def parse_unified_diff(text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    current: FileDiff | None = None
    for raw_line in text.splitlines():
        if raw_line.startswith("diff --git "):
            if current is not None:
                files.append(current)
            match = DIFF_GIT_RE.match(raw_line)
            old_path, new_path = match.group(1), match.group(2)
            if old_path == "/dev/null":
                old_path, status = new_path, "added"
            elif new_path == "/dev/null":
                new_path, status = old_path, "deleted"
            else:
                status = "modified"
            current = FileDiff(old_path=old_path, new_path=new_path, status=status)
            continue
        if current is None:
            continue
        if raw_line.startswith("new file mode"):
            current.status = "added"
            continue
        if raw_line.startswith("deleted file mode"):
            current.status = "deleted"
            continue
        if raw_line.startswith("Binary files "):
            current.raw_patch = raw_line
            continue
        if raw_line.startswith("@@ "):
            match = HUNK_HEADER_RE.match(raw_line)
            if match:
                hunk = Hunk(
                    old_start=int(match.group(1)),
                    old_lines=int(match.group(2) or 1),
                    new_start=int(match.group(3)),
                    new_lines=int(match.group(4) or 1),
                    header=raw_line,
                )
                current.hunks.append(hunk)
                current.raw_patch = (current.raw_patch + "\n" + raw_line).strip()
            continue
        if raw_line.startswith(("+", "-", " ")):
            current.raw_patch = (current.raw_patch + "\n" + raw_line).strip()
            hunks = current.hunks
            if hunks:
                if raw_line.startswith("+"):
                    hunks[-1].additions += 1
                    current.additions += 1
                elif raw_line.startswith("-"):
                    hunks[-1].deletions += 1
                    current.deletions += 1
            continue
    if current is not None:
        files.append(current)
    return [f for f in files if f.raw_patch or f.status in ("added", "deleted")]


class DiffSource(Protocol):
    def fetch(self) -> GitDiff: ...


class LocalDiffSource:
    """Phase 1 — a saved unified diff file, or a local repo + git range."""

    def __init__(self, diff_file: str | None = None, repo_dir: str | None = None,
                 base: str = "main", head: str = "HEAD",
                 title: str = "", description: str = ""):
        if diff_file is None and repo_dir is None:
            raise DiffSourceError("need either --diff <file> or --repo <dir>")
        self.diff_file = diff_file
        self.repo_dir = repo_dir
        self.base = base
        self.head = head
        self.title = title
        self.description = description

    def fetch(self) -> GitDiff:
        if self.diff_file:
            try:
                with open(self.diff_file, encoding="utf-8") as handle:
                    raw = handle.read()
            except OSError as exc:
                raise DiffSourceError(f"cannot read diff file: {exc}") from exc
            return GitDiff(
                base=self.base, head=self.head, title=self.title,
                description=self.description, files=parse_unified_diff(raw), raw=raw,
            )
        return self._fetch_from_git()

    def _fetch_from_git(self) -> GitDiff:
        cmd = ["git", "diff", f"{self.base}...{self.head}"]
        try:
            proc = subprocess.run(
                cmd, cwd=self.repo_dir, capture_output=True, text=True, check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise DiffSourceError(
                f"git diff failed ({exc.returncode}): {exc.stderr.strip()}"
            ) from exc
        raw = proc.stdout
        title_cmd = ["git", "log", "-1", "--format=%s", self.head]
        try:
            title = subprocess.run(title_cmd, cwd=self.repo_dir, capture_output=True,
                                   text=True, check=True).stdout.strip()
        except subprocess.CalledProcessError:
            title = self.title or self.head
        return GitDiff(
            repo=self.repo_dir or "", base=self.base, head=self.head,
            title=title, description=self.description,
            files=parse_unified_diff(raw), raw=raw,
        )


class GithubApiDiffSource:
    """Phase 2 — fetch a PR diff from the GitHub REST API.

    Works with a user PAT (`GITHUB_TOKEN`) today and, behind the same
    interface, with an App installation token or a user's OAuth token later.
    """

    def __init__(self, token: str, owner: str, repo: str, pr_number: int):
        if not token:
            raise DiffSourceError(
                "GITHUB_TOKEN is required to fetch a PR from GitHub. Set it in "
                ".env, or use --diff / --repo for local analysis."
            )
        self.token = token
        self.owner = owner
        self.repo = repo
        self.pr_number = pr_number
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        })

    def fetch(self) -> GitDiff:
        pr = self._get(f"/repos/{self.owner}/{self.repo}/pulls/{self.pr_number}")
        diff_resp = self._session.get(
            f"{GITHUB_API}/repos/{self.owner}/{self.repo}/pulls/{self.pr_number}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github.v3.diff",
            },
            timeout=60,
        )
        if diff_resp.status_code != 200:
            raise GithubApiError(f"fetch diff failed: HTTP {diff_resp.status_code} {diff_resp.text[:200]}")
        raw = diff_resp.text
        commits = self._get(
            f"/repos/{self.owner}/{self.repo}/pulls/{self.pr_number}/commits",
            per_page=100,
        )
        return GitDiff(
            repo=f"{self.owner}/{self.repo}",
            base=pr.get("base", {}).get("ref", "") if isinstance(pr, dict) else "",
            head=pr.get("head", {}).get("ref", "") if isinstance(pr, dict) else "",
            title=(pr.get("title") if isinstance(pr, dict) else None) or "",
            description=(pr.get("body") if isinstance(pr, dict) else None) or "",
            pr_number=self.pr_number,
            files=parse_unified_diff(raw),
            raw=raw,
            commit_messages=[c.get("commit", {}).get("message", "").splitlines()[0]
                             for c in commits if isinstance(commits, list)],
        )

    def _get(self, path: str, per_page: int | None = None) -> list | dict:
        params = {"per_page": per_page} if per_page else None
        resp = self._session.get(f"{GITHUB_API}{path}", params=params, timeout=60)
        if resp.status_code != 200:
            raise GithubApiError(f"GitHub API {path}: HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()


def list_open_prs(token: str, max_repos: int = 20) -> list[dict]:
    """Open PRs the connected user can see, newest first.

    Fetches the user's own + collaborator repos (most recently updated) and the
    open PRs in each, so the dashboard can offer one-click analysis without the
    user pasting a URL.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    repos_resp = requests.get(
        f"{GITHUB_API}/user/repos",
        headers=headers,
        params={
            "affiliation": "owner,collaborator,organization_member",
            "per_page": max_repos,
            "sort": "updated",
        },
        timeout=60,
    )
    if repos_resp.status_code != 200:
        raise GithubApiError(
            f"list repos failed: HTTP {repos_resp.status_code} {repos_resp.text[:200]}"
        )

    prs: list[dict] = []
    for repo in repos_resp.json() or []:
        full_name = repo.get("full_name", "")
        if not full_name:
            continue
        pulls_resp = requests.get(
            f"{GITHUB_API}/repos/{full_name}/pulls",
            headers=headers,
            params={"state": "open", "per_page": 100},
            timeout=60,
        )
        if pulls_resp.status_code != 200:
            continue
        owner, _, name = full_name.partition("/")
        for pr in pulls_resp.json() or []:
            pr_number = pr.get("number")
            detail = pr
            try:
                detail_resp = requests.get(
                    f"{GITHUB_API}/repos/{full_name}/pulls/{pr_number}",
                    headers=headers,
                    timeout=60,
                )
                if detail_resp.status_code == 200:
                    detail = detail_resp.json()
            except requests.RequestException:
                pass
            prs.append({
                "owner": owner,
                "repo": name,
                "pr_number": pr_number,
                "title": pr.get("title", "") or "(untitled PR)",
                "repo_full": full_name,
                "updated_at": pr.get("updated_at", ""),
                "additions": detail.get("additions") or 0,
                "deletions": detail.get("deletions") or 0,
                "changed_files": detail.get("changed_files") or 0,
                "draft": bool(pr.get("draft")),
            })
    prs.sort(key=lambda p: p.get("updated_at") or "", reverse=True)
    return prs


def pr_states(token: str, repo_prs: list[dict]) -> dict[str, dict]:
    """Current GitHub state for each `{repo}#{pr_number}` in the report list.

    Returns {key: {"state": ..., "merged": bool, "merged_at": ...}} where key is
    `{full_repo}#{pr_number}`. Unresolvable PRs are skipped (caller decides).
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    states: dict[str, dict] = {}
    for item in repo_prs:
        repo = item.get("repo") or ""
        pr_number = item.get("pr_number")
        if not repo or not isinstance(pr_number, int):
            continue
        key = f"{repo}#{pr_number}"
        try:
            resp = requests.get(
                f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}",
                headers=headers,
                timeout=60,
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
            merged_at = data.get("merged_at")
            states[key] = {
                "state": data.get("state", ""),
                "merged": bool(merged_at),
                "merged_at": merged_at or "",
            }
        except requests.RequestException:
            continue
    return states


class _DictObj:
    """Attribute-access adapter so dict payloads work with `prepare_comment_body`."""

    def __init__(self, data: dict):
        object.__setattr__(self, "_data", data)

    def __getattr__(self, name: str):
        value = self._data.get(name)
        if isinstance(value, dict):
            return _DictObj(value)
        if isinstance(value, list):
            return [_DictObj(item) if isinstance(item, dict) else item
                    for item in value]
        return value

    def __bool__(self):
        return bool(object.__getattribute__(self, "_data"))


def comment_body_from_payload(payload: dict) -> str:
    """Render a stored report payload (dict) as a GitHub PR comment."""
    return prepare_comment_body(_DictObj(payload))


def post_comment_from_payload(token: str, owner: str, repo: str,
                              pr_number: int, payload: dict) -> int:
    return post_pr_comment(token, owner, repo, pr_number,
                           comment_body_from_payload(payload))


def post_pr_comment(token: str, owner: str, repo: str, pr_number: int,
                    body: str) -> int:
    resp = requests.post(
        f"{GITHUB_API}/repos/{owner}/{repo}/issues/{pr_number}/comments",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json={"body": body},
        timeout=60,
    )
    if resp.status_code not in (200, 201):
        raise GithubApiError(f"post comment failed: HTTP {resp.status_code} {resp.text[:200]}")
    return int(resp.json()["id"])


def find_bot_comment(token: str, owner: str, repo: str, pr_number: int,
                     bot_username: str) -> int | None:
    resp = requests.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/issues/{pr_number}/comments?per_page=100",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        timeout=60,
    )
    if resp.status_code != 200:
        raise GithubApiError(f"list comments failed: HTTP {resp.status_code}")
    for comment in resp.json():
        user = comment.get("user") or {}
        if user.get("login") == bot_username and MARKER in comment.get("body", ""):
            return int(comment["id"])
    return None


def update_pr_comment(token: str, owner: str, repo: str, comment_id: int,
                      body: str) -> None:
    resp = requests.patch(
        f"{GITHUB_API}/repos/{owner}/{repo}/issues/comments/{comment_id}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json={"body": body},
        timeout=60,
    )
    if resp.status_code != 200:
        raise GithubApiError(f"update comment failed: HTTP {resp.status_code} {resp.text[:200]}")


def prepare_comment_body(report) -> str:
    """Render the PR comment: change summary (old -> new), post-review of the
    new code, deep bug/requirement review, and the split plan."""
    parts = [MARKER, ""]

    recommendation = getattr(report, "recommendation", "")
    if recommendation:
        parts.append(f"**Recommendation: {recommendation}**")
        counts = getattr(report, "severity_counts", None) or {}
        count_text = ", ".join(f"{n} {name}" for name, n in counts.items() if n)
        if count_text:
            parts.append(f"_Findings: {count_text}._")
        parts.append("")

    if report.change_log:
        parts.append("## AI change summary (old → new)")
        parts.append("")
        for entry in report.change_log:
            parts.append(f"### {entry.area}")
            if entry.old_code:
                parts.append(f"- **Before:** {entry.old_code}")
            if entry.new_code:
                parts.append(f"- **After:** {entry.new_code}")
            if entry.impact:
                parts.append(f"- **Impact:** {entry.impact}")
            parts.append("")

    if report.post_review:
        parts.append("## Review of the new code")
        parts.append("")
        order = {"critical": 0, "important": 1, "minor": 2, "nit": 3}
        for finding in sorted(
            report.post_review,
            key=lambda f: order.get(f.severity, 9),
        ):
            files = f" ({', '.join(finding.files)})" if finding.files else ""
            parts.append(f"**[{finding.severity}] {finding.title}**{files}")
            if finding.body:
                parts.append(finding.body)
            parts.append("")
        if report.post_review_verdict:
            parts.append(f"**Verdict:** {report.post_review_verdict}")
            parts.append("")

    if getattr(report, "bug_findings", None):
        parts.append("## Bugs & risks (deep review)")
        parts.append("")
        bug_order = {"critical": 0, "important": 1, "minor": 2, "nit": 3}
        for found in sorted(report.bug_findings,
                            key=lambda b: bug_order.get(b.severity, 9)):
            parts.append(f"**[{found.severity}] {found.title}**")
            if found.location:
                parts.append(f"_Location:_ `{found.location}`")
            if found.bug_type:
                parts.append(f"_Type:_ {found.bug_type}")
            if found.detail:
                parts.append(found.detail)
            if found.fix:
                parts.append(f"_Suggested fix:_ {found.fix}")
            parts.append("")

    if getattr(report, "requirements_checks", None):
        parts.append("## Requirements compatibility (advisory)")
        parts.append("")
        status_order = {"unmet": 0, "partial": 1, "unverified": 2, "satisfied": 3}
        for check in sorted(report.requirements_checks,
                            key=lambda c: status_order.get(c.status, 9)):
            evidence = f" — {check.evidence}" if check.evidence else ""
            parts.append(f"- **{check.status}:** {check.requirement}{evidence}")
        parts.append("")
        if getattr(report, "requirements_verdict", ""):
            parts.append(f"**Requirements verdict:** {report.requirements_verdict}")
            parts.append("")
        if getattr(report, "merge_readiness", ""):
            parts.append(f"**Merge readiness (advisory):** {report.merge_readiness}")
            parts.append("")

    parts.append(report.plan_markdown)
    return "\n".join(parts)
