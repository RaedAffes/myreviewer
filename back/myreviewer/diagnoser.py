"""Stage 3 — assess reviewability: mixed concerns, oversized diffs, noise.

Two signal sources are merged:
  * deterministic heuristics (file count, added/deleted lines, lockfile churn,
    concern spread) computed without any model call;
  * optional model-generated flags (the `diagnose_llm` helper) that catch
    qualitative problems the heuristics cannot see.

Everything is returned as ordered `Flag` objects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .clusterer import Concern
from .llm_client import LLMClient
from .prompts import DIAGNOSER_SYSTEM, DIAGNOSER_USER

LOCKFILES = {
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb",
    "poetry.lock", "go.sum", "Cargo.lock", "Gemfile.lock", "composer.lock",
    "Pipfile.lock", "uv.lock",
}

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}
SEVERITY_TOKEN = re.compile(r"\b(critical|warning|info)\b", re.IGNORECASE)


@dataclass
class Flag:
    severity: str
    category: str
    message: str
    files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "category": self.category,
            "message": self.message,
            "files": list(self.files),
        }


def heuristics(file_paths: list[str], added: int, deleted: int,
               concerns: list[Concern]) -> list[Flag]:
    flags: list[Flag] = []
    n_files = len(file_paths)
    total = added + deleted

    if n_files > 30:
        flags.append(Flag("critical", "size", f"PR touches {n_files} files; very hard to review coherently", file_paths))
    elif n_files > 15:
        flags.append(Flag("warning", "size", f"PR touches {n_files} files; consider splitting", file_paths))

    if total > 2000:
        flags.append(Flag("critical", "size", f"{total} changed lines ({added} added / {deleted} deleted)"))
    elif total > 800:
        flags.append(Flag("warning", "size", f"{total} changed lines ({added} added / {deleted} deleted); reviewers' working memory limit (~400 LOC) is exceeded"))
    elif total > 400:
        flags.append(Flag("info", "size", f"{total} changed lines — on the large side"))

    lockfiles = [p for p in file_paths if p.split("/")[-1] in LOCKFILES]
    if lockfiles:
        flags.append(Flag("info", "dependency", "dependency lockfile churn — confirm versions are intentional and separated from feature work", lockfiles))

    for concern in concerns:
        if len(concern.files) > 10:
            flags.append(Flag("warning", "scope", f"concern '{concern.title}' contains {len(concern.files)} files; likely bundles sub-topics", concern.files))
        if concern.is_mixed:
            flags.append(Flag("warning", "mixed-concern",
                              f"concern '{concern.title}' bundles unrelated topics: {concern.mixed_note or 'mixed edits'}",
                              concern.files))

    if len(concerns) > 1:
        flags.append(Flag("info", "structure", f"change covers {len(concerns)} distinct concerns; a split into multiple PRs looks beneficial"))
    elif total > 400:
        flags.append(Flag("info", "structure", "single concern but still large; consider scope reduction"))

    return flags


def llm_flags(pr_title: str, stats_text: str, concerns: list[Concern],
              llm: LLMClient | None) -> tuple[list[Flag], str]:
    """Optional qualitative pass. Without an LLM, returns an empty list and a
    heuristic verdict instead."""
    if llm is None:
        verdict = "multiple PRs" if len(concerns) > 1 else "single PR"
        return [], verdict

    mixed = [c for c in concerns if c.is_mixed]
    mixed_text = "\n".join(f"- {c.title}: {c.mixed_note}" for c in mixed) or "(none)"
    concerns_text = "\n".join(f"- #{c.number} {c.title} [{c.change_type}] files={c.files}"
                              for c in concerns)
    user = DIAGNOSER_USER.format(
        title=pr_title or "(none)",
        stats=stats_text,
        concerns_text=concerns_text,
        mixed_text=mixed_text,
    )
    raw = llm.complete(DIAGNOSER_SYSTEM, user)
    return parse_llm_flags(raw), parse_verdict(raw)


def parse_llm_flags(text: str) -> list[Flag]:
    flags: list[Flag] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith("verdict"):
            continue
        match = SEVERITY_TOKEN.search(line)
        severity = match.group(1).lower() if match else "info"
        message = SEVERITY_TOKEN.sub("", line).lstrip("*-–\t").strip(" :")
        if message:
            flags.append(Flag(severity, "review", message))
    return flags


def parse_verdict(text: str) -> str:
    match = re.search(r"VERDICT:\s*(single PR|multiple PRs)", text, re.IGNORECASE)
    return match.group(1).lower() if match else ""


def verdict_from_flags(flags: list[Flag], n_concerns: int) -> str:
    critical = any(f.severity == "critical" for f in flags)
    warning_mixed = any(f.category == "mixed-concern" for f in flags)
    if critical or warning_mixed or n_concerns > 1:
        return "multiple PRs"
    return "single PR"


def diagnose(pr_title: str, stats_text: str, file_paths: list[str], added: int,
             deleted: int, concerns: list[Concern],
             llm: LLMClient | None) -> tuple[list[Flag], str]:
    """Merge heuristic + optional LLM flags and decide the split verdict."""
    flags = heuristics(file_paths, added, deleted, concerns)
    llm_only, llm_verdict = llm_flags(pr_title, stats_text, concerns, llm)
    flags.extend(llm_only)
    seen = set()
    unique = []
    for flag in flags:
        key = (flag.severity, flag.category, flag.message[:80])
        if key in seen:
            continue
        seen.add(key)
        unique.append(flag)
    verdict = llm_verdict or verdict_from_flags(unique, len(concerns))
    unique.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.category))
    return unique, verdict