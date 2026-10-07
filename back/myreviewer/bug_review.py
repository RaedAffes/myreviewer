"""Stage 6 — deep bug discovery + project-requirement compatibility check.

Runs after the post-review of the new code. Two outputs:

* `BugFinding` — typed, located, actionable bug/risk findings (each with a
  suggested fix) ordered by severity.
* `RequirementCheck` — per declared requirement a status
  (satisfied/partial/unmet/unverified) backed by evidence from the diff,
  plus an overall `REQUIREMENTS_VERDICT` and an advisory `MERGE_READINESS`.

Incompatibilities are *cited, never resolved*: the human reviewer keeps the
final accept/reject decision.

`effort="deep"` (default) adds a verification pass: the draft block is sent
back to the model for self-critique (dedupe, drop weak findings, fix
severities). `effort="standard"` skips that second call.

Parsing is deliberately tolerant: NIM models often pack several `KEY: value`
fields onto a single line (e.g. `SEVERITY: minor TYPE: logic TITLE: ...`), so
the parser scans each line for embedded field labels instead of requiring one
field per line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .change_log import ChangeLogEntry
from .clusterer import Concern
from .llm_client import LLMClient
from .prompts import (
    BUG_REVIEW_SYSTEM,
    BUG_REVIEW_USER,
    BUG_REVIEW_VERIFY_SYSTEM,
    BUG_REVIEW_VERIFY_USER,
)

SEVERITY_ORDER = {"critical": 0, "important": 1, "minor": 2, "nit": 3}
STATUS_ORDER = {"unmet": 0, "partial": 1, "unverified": 2, "satisfied": 3}
VALID_SEVERITIES = {"critical", "important", "minor", "nit"}
VALID_STATUSES = {"satisfied", "partial", "unmet", "unverified"}
VALID_READINESS = {"ready", "fix-before-merge", "rework"}

BUG_HEAD_RE = re.compile(r"^\s*BUG\s*(\d+)[:.\-]?\s*(.*)$", re.IGNORECASE)
REQ_HEAD_RE = re.compile(r"^\s*REQ\s*(\d+)[:.\-]?\s*(.*)$", re.IGNORECASE)
REQ_VERDICT_RE = re.compile(r"^\s*REQUIREMENTS_VERDICT:\s*(.*)$", re.IGNORECASE)
READINESS_RE = re.compile(
    r"^\s*MERGE_READINESS:\s*(ready|fix-before-merge|rework)", re.IGNORECASE,
)
SECTION_RE = re.compile(r"^\s*(?:BUGS|REQUIREMENTS):", re.IGNORECASE)

FIELD_LABELS = (
    ("severity", re.compile(r"\bSEVERITY\s*:", re.IGNORECASE)),
    ("type", re.compile(r"\bTYPE\s*:", re.IGNORECASE)),
    ("title", re.compile(r"\bTITLE\s*:", re.IGNORECASE)),
    ("location", re.compile(r"\bLOCATION\s*:", re.IGNORECASE)),
    ("introduced_by_pr", re.compile(r"\bINTRODUCED_BY_PR\s*:", re.IGNORECASE)),
    ("reachable", re.compile(r"\bREACHABLE\s*:", re.IGNORECASE)),
    ("detail", re.compile(r"\bDETAIL\s*:", re.IGNORECASE)),
    ("fix", re.compile(r"\bFIX\s*:", re.IGNORECASE)),
    ("requirement", re.compile(r"\bREQUIREMENT\s*:", re.IGNORECASE)),
    ("status", re.compile(r"\bSTATUS\s*:", re.IGNORECASE)),
    ("evidence", re.compile(r"\bEVIDENCE\s*:", re.IGNORECASE)),
)

BUG_TYPES = {
    "logic", "edge-case", "concurrency", "security", "api-contract",
    "migration", "error-handling", "tests", "performance", "maintainability",
}


def _yes_no_unknown(value: str) -> str:
    candidate = value.strip().strip("'\"").lower()
    for token in ("yes", "no", "unknown"):
        if candidate.startswith(token):
            return token
    return "unknown"


@dataclass
class BugFinding:
    number: int
    severity: str
    title: str
    bug_type: str = ""
    location: str = ""
    detail: str = ""
    fix: str = ""
    introduced_by_pr: str = ""
    reachable: str = ""
    evidence: str = ""

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "severity": self.severity,
            "type": self.bug_type,
            "title": self.title,
            "location": self.location,
            "detail": self.detail,
            "fix": self.fix,
            "introduced_by_pr": self.introduced_by_pr,
            "reachable": self.reachable,
            "evidence": self.evidence,
        }


@dataclass
class RequirementCheck:
    number: int
    requirement: str
    status: str
    evidence: str = ""

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "requirement": self.requirement,
            "status": self.status,
            "evidence": self.evidence,
        }


@dataclass
class BugReviewResult:
    findings: list[BugFinding] = field(default_factory=list)
    checks: list[RequirementCheck] = field(default_factory=list)
    requirements_verdict: str = ""
    merge_readiness: str = ""


def build_bug_review(
    pr_title: str, pr_summary: str, concerns: list[Concern],
    change_log: list[ChangeLogEntry], requirements_text: str,
    diff_text: str, llm: LLMClient, effort: str = "deep",
    repo_context: str = "",
) -> BugReviewResult:
    user = BUG_REVIEW_USER.format(
        title=pr_title or "(none)",
        summary=pr_summary or "(none)",
        concerns_text=_concerns_text(concerns),
        change_log_text=_change_log_text(change_log),
        requirements=requirements_text or "(none declared)",
        repo_context=(repo_context or "(not available)").strip() or "(not available)",
        diff=_truncate(diff_text, limit=30_000),
    )
    draft = llm.complete(BUG_REVIEW_SYSTEM, user)

    result = parse_bug_review(draft)
    if effort == "deep":
        verify_user = BUG_REVIEW_VERIFY_USER.format(
            title=pr_title or "(none)",
            draft=draft,
        )
        revised = llm.complete(BUG_REVIEW_VERIFY_SYSTEM, verify_user)
        revised_result = parse_bug_review(revised)
        if revised_result.findings or revised_result.checks or revised_result.requirements_verdict:
            result = revised_result

    result.findings.sort(key=lambda f: SEVERITY_ORDER.get(f.severity, 9))
    result.checks.sort(key=lambda c: STATUS_ORDER.get(c.status, 9))
    enforce_readiness_consistency(result)
    return result


def enforce_readiness_consistency(result: BugReviewResult) -> None:
    """Deterministic guard: 'ready' must never coexist with blocking findings.

    Also demotes findings the model marked unreachable AND not introduced by
    this PR (kept only because a specialist or the verify pass disagreed on
    severity) — those can never justify blocking the merge.
    """
    for finding in result.findings:
        if (finding.severity in ("critical", "important")
                and finding.introduced_by_pr == "no"
                and finding.reachable == "no"):
            finding.severity = "minor"
    if result.merge_readiness != "ready":
        return
    has_blocking = any(f.severity in ("critical", "important") for f in result.findings)
    has_unmet = any(c.status == "unmet" for c in result.checks)
    if has_blocking or has_unmet:
        result.merge_readiness = "fix-before-merge"


def parse_bug_review(text: str) -> BugReviewResult:
    result = BugReviewResult()
    finding: BugFinding | None = None
    check: RequirementCheck | None = None
    in_verdict = False

    def start_finding(inline: str) -> BugFinding:
        nonlocal finding, check
        check = None
        finding = BugFinding(number=len(result.findings) + 1,
                             severity="minor", title="")
        result.findings.append(finding)
        if inline:
            pairs = _split_fields(inline)
            if pairs:
                _apply_fields(finding, inline, is_check=False)
            else:
                finding.title = inline
        return finding

    def start_check(inline: str) -> RequirementCheck:
        nonlocal check, finding
        finding = None
        check = RequirementCheck(number=len(result.checks) + 1,
                                 requirement="", status="unverified")
        result.checks.append(check)
        if inline:
            pairs = _split_fields(inline)
            if pairs:
                _apply_fields(check, inline, is_check=True)
            else:
                check.requirement = inline
        return check

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if SECTION_RE.match(line):
            in_verdict = False
            continue

        verdict_match = REQ_VERDICT_RE.match(raw_line)
        if verdict_match:
            in_verdict = True
            result.requirements_verdict = (
                result.requirements_verdict.strip() + " "
                + verdict_match.group(1).strip()
            ).strip()
            continue
        if READINESS_RE.match(raw_line):
            in_verdict = False
            result.merge_readiness = READINESS_RE.match(raw_line).group(1).lower()
            continue

        bug_head = BUG_HEAD_RE.match(raw_line)
        if bug_head:
            in_verdict = False
            start_finding(bug_head.group(2))
            continue
        req_head = REQ_HEAD_RE.match(raw_line)
        if req_head:
            in_verdict = False
            start_check(req_head.group(2))
            continue

        if in_verdict:
            if not READINESS_RE.match(raw_line) and not BUG_HEAD_RE.match(raw_line) \
                    and not REQ_HEAD_RE.match(raw_line):
                result.requirements_verdict = (result.requirements_verdict.strip() + " "
                                               + line).strip()
            continue

        if check is not None:
            _apply_fields(check, line, is_check=True)
        elif finding is not None:
            _apply_fields(finding, line, is_check=False)

    for f in result.findings:
        if not f.title:
            f.title = f"bug {f.number}"
    return result


def _apply_fields(obj, line: str, *, is_check: bool) -> None:
    """Assign `KEY: value` fields found anywhere in `line` to the target.

    `obj` is either a `BugFinding` (is_check=False) or a `RequirementCheck`
    (is_check=True). Lines without any field label are appended to the last
    multi-line field (DETAIL/FIX for findings, EVIDENCE for checks).
    """
    pairs = _split_fields(line)
    if not pairs:
        _append_to_last(obj, line, is_check=is_check)
        return
    for kind, value in pairs:
        _assign(obj, kind, value, is_check=is_check)


def _split_fields(line: str) -> list[tuple[str, str]]:
    spans: list[tuple[int, str, int]] = []
    for kind, rx in FIELD_LABELS:
        for match in rx.finditer(line):
            spans.append((match.start(), kind, match.end()))
    if not spans:
        return []
    spans.sort(key=lambda s: s[0])
    pairs: list[tuple[str, str]] = []
    for i, (pos, kind, end) in enumerate(spans):
        stop = spans[i + 1][0] if i + 1 < len(spans) else len(line)
        value = line[end:stop].strip(" \t")
        if value:
            pairs.append((kind, value))
    return pairs


def _assign(obj, kind: str, value: str, *, is_check: bool) -> None:
    value = value.strip()
    if is_check:
        if kind == "requirement":
            if not getattr(obj, "requirement"):
                obj.requirement = value
        elif kind == "status":
            candidate = value.lower()
            for token in VALID_STATUSES:
                if candidate.startswith(token):
                    obj.status = token
                    break
        elif kind == "evidence":
            obj.evidence = value
        return

    if kind == "severity":
        candidate = value.lower()
        for token in VALID_SEVERITIES:
            if candidate.startswith(token):
                obj.severity = token
                break
    elif kind == "type":
        candidate = value.lower().split()[0].rstrip(",")
        obj.bug_type = candidate if candidate in BUG_TYPES else candidate
    elif kind == "title":
        if not getattr(obj, "title"):
            obj.title = value
    elif kind == "location":
        if not getattr(obj, "location"):
            obj.location = value
    elif kind == "detail":
        obj.detail = (obj.detail + " " + value).strip()
    elif kind == "fix":
        obj.fix = (obj.fix + " " + value).strip()
    elif kind == "introduced_by_pr":
        if not getattr(obj, "introduced_by_pr"):
            obj.introduced_by_pr = _yes_no_unknown(value)
    elif kind == "reachable":
        if not getattr(obj, "reachable"):
            obj.reachable = _yes_no_unknown(value)
    elif kind == "evidence":
        if not is_check:
            obj.evidence = (obj.evidence + " " + value).strip()


def _append_to_last(obj, line: str, *, is_check: bool) -> None:
    if is_check:
        if getattr(obj, "evidence"):
            obj.evidence = (obj.evidence + " " + line).strip()
        return
    if getattr(obj, "fix"):
        obj.fix = (obj.fix + " " + line).strip()
    elif getattr(obj, "detail"):
        obj.detail = (obj.detail + " " + line).strip()


def _concerns_text(concerns: list[Concern]) -> str:
    return "\n".join(
        f"- #{c.number} {c.title} [{c.change_type}] files={c.files}"
        for c in concerns
    ) or "(no concerns)"


def _change_log_text(change_log: list[ChangeLogEntry]) -> str:
    return "\n".join(
        f"- {e.area}: before='{e.old_code}' after='{e.new_code}'"
        for e in change_log
    ) or "(no change log)"


def _truncate(text: str, limit: int = 30_000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"