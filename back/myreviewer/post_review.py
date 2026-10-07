"""Stage 5 — AI post-review of the NEW code introduced by a PR.

Unlike diagnosis (which judges PR structure/scope), this stage reviews the
resulting code itself: correctness, edge cases, regressions, security,
performance, and maintainability. Findings are ordered by severity and followed
by an overall merge verdict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .change_log import ChangeLogEntry
from .clusterer import Concern
from .llm_client import LLMClient
from .prompts import POST_REVIEW_SYSTEM, POST_REVIEW_USER

FINDING_RE = re.compile(r"^\s*FINDING\s*(\d+)[:.\-]\s*(.+)$", re.IGNORECASE)
TITLE_RE = re.compile(r"^\s*TITLE:\s*(.+)$", re.IGNORECASE)
EVIDENCE_RE = re.compile(r"^\s*EVIDENCE:\s*(.+)$", re.IGNORECASE)
FILE_RE = re.compile(r"^\s*FILES:\s*(.*)$", re.IGNORECASE)
VERDICT_RE = re.compile(r"^\s*POST_REVIEW_VERDICT:\s*(.+)$", re.IGNORECASE)
BODY_LINE_RE = re.compile(r"^\s*BODY:\s*(.*)$", re.IGNORECASE)
SEVERITY_HINT_RE = re.compile(r"(critical|important|minor|nit)", re.IGNORECASE)
SEVERITY_PREFIX_RE = re.compile(
    r"^\s*(?:critical|important|minor|nit)[\s:.\-]*", re.IGNORECASE,
)

SEVERITY_ORDER = {"critical": 0, "important": 1, "minor": 2, "nit": 3}


@dataclass
class ReviewFinding:
    number: int
    severity: str
    title: str
    body: str = ""
    files: list[str] = field(default_factory=list)
    evidence: str = ""

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "severity": self.severity,
            "title": self.title,
            "body": self.body,
            "files": list(self.files),
            "evidence": self.evidence,
        }


def build_post_review(
    pr_title: str, pr_summary: str, concerns: list[Concern],
    change_log: list[ChangeLogEntry], diff_text: str, llm: LLMClient,
    requirements: str = "", repo_context: str = "",
) -> tuple[list[ReviewFinding], str]:
    concerns_text = "\n".join(
        f"- #{c.number} {c.title} [{c.change_type}] files={c.files}"
        for c in concerns
    )
    change_log_text = "\n".join(
        f"- {e.area}: before='{e.old_code}' after='{e.new_code}'"
        for e in change_log
    ) or "(no change log)"
    user = POST_REVIEW_USER.format(
        title=pr_title or "(none)",
        summary=pr_summary or "(none)",
        concerns_text=concerns_text,
        change_log_text=change_log_text,
        requirements=requirements or "(none declared)",
        repo_context=(repo_context or "(not available)").strip() or "(not available)",
        diff=_truncate(diff_text, limit=30_000),
    )
    raw = llm.complete(POST_REVIEW_SYSTEM, user)
    findings, verdict = parse_post_review(raw)
    if findings:
        return findings, verdict
    # Graceful fallback: surface the raw model text instead of dropping it.
    return [
        ReviewFinding(
            number=1,
            severity="info",
            title="Review not parsed",
            body=(raw.strip()[:2000] or "(no model text returned)"),
        )
    ], verdict


def parse_post_review(text: str) -> tuple[list[ReviewFinding], str]:
    findings: list[ReviewFinding] = []
    verdict = ""
    current: ReviewFinding | None = None
    body_lines: list[str] = []

    def flush_body() -> None:
        nonlocal body_lines
        if current is not None and body_lines:
            current.body = " ".join(b.strip() for b in body_lines).strip()
        body_lines = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith("post_review_verdict"):
            match = VERDICT_RE.match(raw_line)
            if match:
                verdict = match.group(1).strip()
            flush_body()
            break

        finding_match = FINDING_RE.match(raw_line)
        if finding_match:
            flush_body()
            current = ReviewFinding(
                number=len(findings) + 1,
                severity=_severity(finding_match.group(2)),
                title="",
            )
            findings.append(current)
            title = SEVERITY_PREFIX_RE.sub("", finding_match.group(2)).strip(" -:")
            if title:
                current.title = title
            continue

        if current is None:
            continue

        if TITLE_RE.match(raw_line):
            current.title = TITLE_RE.match(raw_line).group(1).strip()
        elif EVIDENCE_RE.match(raw_line):
            value = EVIDENCE_RE.match(raw_line).group(1).strip()
            current.evidence = (current.evidence + " " + value).strip() if current.evidence else value
        elif BODY_LINE_RE.match(raw_line):
            body_lines.append(BODY_LINE_RE.match(raw_line).group(1).strip())
        elif FILE_RE.match(raw_line):
            current.files = _split_files(FILE_RE.match(raw_line).group(1))
        elif raw_line:
            body_lines.append(raw_line)

    flush_body()

    for finding in findings:
        if not finding.title:
            finding.title = f"finding {finding.number}"
        if not finding.severity:
            finding.severity = "minor"
    findings.sort(key=lambda f: SEVERITY_ORDER.get(f.severity, 9))
    return findings, verdict


def _severity(value: str) -> str:
    match = SEVERITY_HINT_RE.search(value)
    return match.group(1).lower() if match else "minor"


def _split_files(raw: str) -> list[str]:
    parts = re.split(r"[,\n]", raw)
    names = []
    for part in parts:
        part = part.strip()
        if part and part not in names:
            names.append(part)
    return names


def _truncate(text: str, limit: int = 30_000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"