"""Stage 1 — summarize the overall intent of a PR.

Independent module so it can be reused / benchmarked alone. Returns a
structured intent summary extracted from the model's
`SUMMARY: / PROBLEM: / APPROACH: / SUBSYSTEMS: / RISKS:` block (each line kept,
empty lines dropped), so downstream stages see both the one-line intent and the
rationale without reading the diff again.
"""

from __future__ import annotations

import re

from .llm_client import LLMClient
from .prompts import SUMMARIZE_SYSTEM, SUMMARIZE_USER


def summarize(pr_title: str, pr_description: str, diff_text: str, llm: LLMClient,
              requirements: str = "", commit_messages: list[str] | None = None) -> str:
    commits = commit_messages or []
    user = SUMMARIZE_USER.format(
        title=_clean(pr_title),
        description=_clean(pr_description),
        commit_messages="\n".join(f"- {c}" for c in commits) if commits else "(none)",
        requirements=_clean(requirements or "(none declared)"),
        diff=_truncate(diff_text),
    )
    raw = llm.complete(SUMMARIZE_SYSTEM, user)
    return parse_summary(raw)


def parse_summary(text: str) -> str:
    match = re.search(r"SUMMARY:\s*", text, re.IGNORECASE)
    if not match:
        return text.strip()
    block = text[match.end():]
    idx = re.search(r"\nCONCERNS:", block, re.IGNORECASE)
    if idx:
        block = block[: idx.start()]
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    return "\n".join(lines).strip() or text.strip()


def _clean(value: str) -> str:
    return (value or "").strip() or "(none)"


def _truncate(text: str, limit: int = 20_000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"