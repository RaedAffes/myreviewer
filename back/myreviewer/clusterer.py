"""Stage 2 — map files / hunks of a PR onto distinct logical concerns.

Turns the free-form concern blocks emitted by the model (see `prompts.py`) into
structured `Concern` objects. The parser tolerates formatting drift so the
pipeline degrades gracefully if the model edits the template.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .llm_client import LLMClient
from .prompts import CLUSTER_SYSTEM, CLUSTER_USER

FILE_LIST_RE = re.compile(r"FILES:\s*(.*)", re.IGNORECASE)
TITLE_RE = re.compile(r"TITLE:\s*(.+)", re.IGNORECASE)
RATIONALE_RE = re.compile(r"RATIONALE:\s*(.+)", re.IGNORECASE)
CHANGE_RE = re.compile(r"CHANGE_TYPE:\s*({change_types})", re.IGNORECASE)
MIXED_RE = re.compile(r"MIXED:\s*(yes|no|true|false)", re.IGNORECASE)
NOTE_RE = re.compile(r"MIXED_NOTE:\s*(.*)", re.IGNORECASE)
NUMBERED_START_RE = re.compile(r"^\s*\d+[.)]\s*", re.IGNORECASE)
OTHER_SECTION_RE = re.compile(
    r"^\s*(?:SUMMARY|CHANGE_LOG|POST_REVIEW|POST_REVIEW_VERDICT):",
    re.IGNORECASE,
)


@dataclass
class Concern:
    number: int
    title: str
    rationale: str = ""
    files: list[str] = field(default_factory=list)
    change_type: str = "modify"
    is_mixed: bool = False
    mixed_note: str = ""

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "title": self.title,
            "rationale": self.rationale,
            "files": list(self.files),
            "change_type": self.change_type,
            "is_mixed": self.is_mixed,
            "mixed_note": self.mixed_note,
        }


def cluster(pr_title: str, pr_description: str, pr_summary: str, diff_text: str,
            llm: LLMClient) -> list[Concern]:
    user = CLUSTER_USER.format(
        title=pr_title or "(none)",
        description=(pr_description or "(none)").strip() or "(none)",
        summary=pr_summary or "(none)",
        diff=_truncate(diff_text),
    )
    raw = llm.complete(CLUSTER_SYSTEM, user)
    return parse_concerns(raw)


def parse_concerns(text: str) -> list[Concern]:
    concerns: list[Concern] = []
    current: Concern | None = None
    pending_titles: list[str] = []  # keeps stray titles without a block

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if OTHER_SECTION_RE.match(line):
            current = None
            continue

        title_match = TITLE_RE.match(line)
        if title_match and current is None:
            pending_titles.append(title_match.group(1).strip())
            continue

        if NUMBERED_START_RE.match(line):
            current = Concern(number=len(concerns) + 1, title="", files=[])
            concerns.append(current)
            if pending_titles:
                current.title = pending_titles.pop(0)
            inner = NUMBERED_START_RE.sub("", line)
            inline_title = TITLE_RE.match(inner)
            if inline_title and not current.title:
                current.title = inline_title.group(1).strip()
            continue

        if current is None:
            continue

        if TITLE_RE.match(line) and not current.title:
            current.title = TITLE_RE.match(line).group(1).strip()
        elif RATIONALE_RE.match(line):
            current.rationale = RATIONALE_RE.match(line).group(1).strip()
        elif FILE_LIST_RE.match(line):
            current.files = _split_files(FILE_LIST_RE.match(line).group(1))
        elif CHANGE_RE.match(line):
            current.change_type = CHANGE_RE.match(line).group(1).strip().lower()
        elif MIXED_RE.match(line):
            current.is_mixed = MIXED_RE.match(line).group(1).lower() in ("yes", "true")
        elif NOTE_RE.match(line):
            current.mixed_note = NOTE_RE.match(line).group(1).strip()

    for concern in concerns:
        if not concern.title:
            concern.title = f"concern {concern.number}"
        if not concern.change_type:
            concern.change_type = "modify"
    return concerns


def _split_files(raw: str) -> list[str]:
    parts = re.split(r"[,\n]", raw)
    names = []
    for part in parts:
        part = part.strip()
        if part and part not in names:
            names.append(part)
    return names


def _truncate(text: str, limit: int = 40_000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"