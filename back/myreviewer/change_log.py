"""Stage 4 — AI change summary comparing old (pre-PR) and new (post-PR) code.

For each concern the model reads the diff and describes what the code did
BEFORE the change and what it does NOW, plus the impact and any migration
notes. This produces a clear, logged "old -> new" record reviewers can read
instead of loading the whole diff.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .clusterer import Concern
from .llm_client import LLMClient
from .prompts import CHANGE_LOG_SYSTEM, CHANGE_LOG_USER

AREA_RE = re.compile(r"^\s*AREA\s+(\d+)[.:\-]?\s*(.*)$", re.IGNORECASE)
OLD_RE = re.compile(r"^\s*OLD:\s*(.*)$", re.IGNORECASE)
NEW_RE = re.compile(r"^\s*NEW:\s*(.*)$", re.IGNORECASE)
INTRODUCED_RE = re.compile(r"^\s*INTRODUCED:\s*(.*)$", re.IGNORECASE)
IMPACT_RE = re.compile(r"^\s*IMPACT:\s*(.*)$", re.IGNORECASE)
FILE_RE = re.compile(r"^\s*FILES:\s*(.*)$", re.IGNORECASE)
# Any other top-level section marker terminates the current block.
NEXT_SECTION_RE = re.compile(r"^\s*(?:CONCERNS|CHANGE_LOG|POST_REVIEW|POST_REVIEW_VERDICT|SUMMARY):")

VALID_ORIGINS = {"introduced", "pre-existing", "worsened", "exposed", "unknown"}


@dataclass
class ChangeLogEntry:
    number: int
    area: str
    old_code: str
    new_code: str
    impact: str = ""
    files: list[str] = field(default_factory=list)
    introduced: str = ""

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "area": self.area,
            "old_code": self.old_code,
            "new_code": self.new_code,
            "impact": self.impact,
            "files": list(self.files),
            "introduced": self.introduced,
        }


def build_change_log(pr_title: str, pr_summary: str, concerns: list[Concern],
                     diff_text: str, llm: LLMClient) -> list[ChangeLogEntry]:
    concerns_text = "\n".join(
        f"- #{c.number} {c.title} [{c.change_type}] files={c.files}"
        for c in concerns
    )
    user = CHANGE_LOG_USER.format(
        title=pr_title or "(none)",
        summary=pr_summary or "(none)",
        concerns_text=concerns_text,
        diff=_truncate(diff_text, limit=30_000),
    )
    raw = llm.complete(CHANGE_LOG_SYSTEM, user)
    entries = parse_change_log(raw)
    if entries:
        return entries
    # Graceful fallback: keep a per-concern record even if the model drifts.
    return [
        ChangeLogEntry(
            number=concern.number,
            area=concern.title,
            old_code="(change summary not parsed from model output)",
            new_code=raw.strip()[:2000] or "(no model text returned)",
            impact="Review the diff directly for this concern.",
            files=list(concern.files),
        )
        for concern in concerns
    ]


def parse_change_log(text: str) -> list[ChangeLogEntry]:
    entries: list[ChangeLogEntry] = []
    current: ChangeLogEntry | None = None
    body_lines: list[str] = []
    field_open: str | None = None

    for raw_line in text.splitlines():
        if NEXT_SECTION_RE.match(raw_line) and not AREA_RE.match(raw_line):
            if current:
                if field_open == "impact":
                    _flush_field(current, "impact", body_lines)
                body_lines = []
            field_open = None
            current = None
            continue

        area_match = AREA_RE.match(raw_line)
        if area_match:
            if current:
                if field_open == "impact":
                    _flush_field(current, "impact", body_lines)
                body_lines = []
            current = ChangeLogEntry(
                number=len(entries) + 1,
                area=area_match.group(2).strip() or f"area {area_match.group(1)}",
                old_code="",
                new_code="",
            )
            entries.append(current)
            field_open = None
            continue

        if current is None:
            continue

        intro_match = INTRODUCED_RE.match(raw_line)
        if intro_match:
            _flush_field(current, field_open, body_lines)
            field_open = None
            body_lines = []
            current.introduced = _origin(intro_match.group(1))
            continue

        for key, regex, attr in (
            ("old", OLD_RE, "old_code"),
            ("new", NEW_RE, "new_code"),
            ("impact", IMPACT_RE, "impact"),
        ):
            match = regex.match(raw_line)
            if match:
                _flush_field(current, field_open, body_lines)
                field_open = key
                body_lines = [match.group(1)]
                break
        else:
            file_match = FILE_RE.match(raw_line)
            if file_match and field_open in (None, "impact"):
                if field_open == "impact":
                    _flush_field(current, "impact", body_lines)
                field_open = None
                body_lines = []
                current.files = _split_files(file_match.group(1))
            elif field_open and raw_line.strip():
                body_lines.append(raw_line.strip())

    if current and field_open:
        _flush_field(current, field_open, body_lines)

    for entry in entries:
        if not entry.area:
            entry.area = f"area {entry.number}"
    return entries


def _flush_field(entry: ChangeLogEntry | None, field: str | None,
                 lines: list[str]) -> None:
    if entry is None or not field or not lines:
        return
    text = " ".join(line.strip() for line in lines).strip()
    if text:
        if field == "old":
            entry.old_code = text
        elif field == "new":
            entry.new_code = text
        elif field == "impact":
            entry.impact = text
    lines.clear()


def _origin(value: str) -> str:
    candidate = value.strip().strip("'\"").lower()
    for token in VALID_ORIGINS:
        if candidate.startswith(token):
            return token
    return "unknown"


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