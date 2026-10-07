"""Specialist review passes for `effort="max"`.

Three focused LLM passes (correctness, security, tests) run on the same
summary + change log + repo context + diff as the main bug review, then their
findings are merged into the main result with a file+title dedupe so the
final report never shows the same issue twice.

Every specialist prompt keeps the PR summary injected, exactly like the
main stages — this module only narrows the FOCUS of each pass.
"""

from __future__ import annotations

import re

from .bug_review import (
    BUG_TYPES,
    SEVERITY_ORDER,
    VALID_SEVERITIES,
    BugFinding,
    _split_fields,
    _yes_no_unknown,
)
from .change_log import ChangeLogEntry
from .clusterer import Concern
from .llm_client import LLMClient
from .prompts import SPECIALIST_SYSTEM, SPECIALIST_USER

# focus label -> allowed TYPE values listed in the prompt
SPECIALISTS: list[tuple[str, str]] = [
    (
        "correctness and logic: logic errors, edge cases, concurrency, error "
        "handling, state transitions, resource leaks, API-contract breaks, "
        "data consistency, performance traps",
        "logic | edge-case | concurrency | error-handling | api-contract | "
        "migration | performance",
    ),
    (
        "security only: authentication and authorization flaws, exposed "
        "secrets, injection vulnerabilities, unsafe deserialization, SSRF, "
        "path traversal, insecure file handling, permission issues, insecure "
        "API endpoints",
        "security",
    ),
    (
        "tests and regression risk: whether the changed behaviour is covered "
        "by existing tests, tests invalidated by the change, missing tests "
        "for new behaviour, and concrete test suggestions that follow the "
        "repository's existing testing conventions",
        "tests",
    ),
]

MAX_MERGED_FINDINGS = 30

BUG_HEAD_RE = re.compile(r"^\s*BUG\s*(\d+)[:.\-]?\s*(.*)$", re.IGNORECASE)
SECTION_RE = re.compile(r"^\s*BUGS\s*:", re.IGNORECASE)
SKIP_SECTIONS_RE = re.compile(
    r"^\s*(?:REQUIREMENTS|REQUIREMENTS_VERDICT|MERGE_READINESS):", re.IGNORECASE,
)


def run_specialists(
    pr_title: str,
    pr_summary: str,
    concerns: list[Concern],
    change_log: list[ChangeLogEntry],
    diff_text: str,
    llm: LLMClient,
    repo_context: str = "",
) -> list[BugFinding]:
    change_log_text = "\n".join(
        f"- {e.area}: before='{e.old_code}' after='{e.new_code}'"
        for e in change_log
    ) or "(no change log)"
    findings: list[BugFinding] = []
    for focus, allowed_types in SPECIALISTS:
        user = SPECIALIST_USER.format(
            focus=focus,
            allowed_types=allowed_types,
            title=pr_title or "(none)",
            summary=pr_summary or "(none)",
            change_log_text=change_log_text,
            repo_context=(repo_context or "(not available)").strip() or "(not available)",
            diff=_truncate(diff_text, limit=30_000),
        )
        system = SPECIALIST_SYSTEM.format(focus=focus)
        try:
            raw = llm.complete(system, user)
        except Exception:
            continue  # a failed specialist must never kill the whole review
        findings.extend(_parse_specialist_findings(raw))
    return findings


def merge_findings(primary: list[BugFinding],
                   extra: list[BugFinding]) -> list[BugFinding]:
    """Merge specialist findings into the main list, deduping on file+title."""
    seen: set[tuple[str, str]] = {_dedupe_key(f) for f in primary}
    merged = list(primary)
    for finding in sorted(extra, key=lambda f: SEVERITY_ORDER.get(f.severity, 9)):
        key = _dedupe_key(finding)
        if key in seen:
            continue
        seen.add(key)
        merged.append(finding)
        if len(merged) >= MAX_MERGED_FINDINGS:
            break
    merged.sort(key=lambda f: SEVERITY_ORDER.get(f.severity, 9))
    for index, finding in enumerate(merged, start=1):
        finding.number = index
    return merged


def _dedupe_key(finding: BugFinding) -> tuple[str, str]:
    path = finding.location.split(":")[0].strip().lower() if finding.location else ""
    if not path and finding.title:
        path = finding.title.split()[0].lower()
    title = re.sub(r"\W+", "", finding.title.lower())[:40]
    return path, title


# ── parsing ──────────────────────────────────────────────────────────────────
# Specialist output is always the BUGS-only block from SPECIALIST_USER.

def _parse_specialist_findings(text: str) -> list[BugFinding]:
    findings: list[BugFinding] = []
    current: BugFinding | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if SECTION_RE.match(line) or SKIP_SECTIONS_RE.match(line):
            continue

        head = BUG_HEAD_RE.match(raw_line)
        if head:
            current = BugFinding(number=len(findings) + 1, severity="minor", title="")
            findings.append(current)
            inline = head.group(2).strip()
            if inline:
                _apply(current, inline)
            continue
        if current is None:
            continue
        _apply(current, line)

    return [f for f in findings if f.title or f.detail]


def _apply(finding: BugFinding, line: str) -> None:
    pairs = _split_fields(line)
    if not pairs:
        if finding.fix:
            finding.fix = (finding.fix + " " + line).strip()
        elif finding.detail:
            finding.detail = (finding.detail + " " + line).strip()
        elif not finding.title:
            finding.title = line
        return
    for kind, value in pairs:
        value = value.strip()
        if kind == "severity":
            candidate = value.lower()
            for token in VALID_SEVERITIES:
                if candidate.startswith(token):
                    finding.severity = token
                    break
        elif kind == "type":
            candidate = value.lower().split()[0].rstrip(",")
            if candidate in BUG_TYPES:
                finding.bug_type = candidate
        elif kind == "title":
            if not finding.title:
                finding.title = value
        elif kind == "location":
            if not finding.location:
                finding.location = value
        elif kind == "introduced_by_pr":
            if not finding.introduced_by_pr:
                finding.introduced_by_pr = _yes_no_unknown(value)
        elif kind == "reachable":
            if not finding.reachable:
                finding.reachable = _yes_no_unknown(value)
        elif kind == "evidence":
            finding.evidence = (finding.evidence + " " + value).strip()
        elif kind == "detail":
            finding.detail = (finding.detail + " " + value).strip()
        elif kind == "fix":
            finding.fix = (finding.fix + " " + value).strip()


def _truncate(text: str, limit: int = 30_000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"
