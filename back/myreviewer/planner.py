"""Stage 4 — build the reviewer-friendly markdown report and split plan.

Composition is largely deterministic: each concern becomes a suggested smaller
PR with a generated title, scope, and ordering. An optional LLM pass can enrich
the plan with natural-language advice for the author.
"""

from __future__ import annotations

from .bug_review import RequirementCheck
from .clusterer import Concern
from .diagnoser import Flag
from .llm_client import LLMClient
from .prompts import PLAN_SYSTEM, PLAN_USER

CHANGE_TYPE_LABEL = {
    "add_only": "adds new functionality",
    "remove_only": "removes code",
    "modify": "modifies behavior",
    "mixed": "mixes several edit types",
}

READINESS_LABEL = {
    "ready": "Looks ready — no blocking findings.",
    "fix-before-merge": "Fix the cited findings before merging.",
    "rework": "The approach itself needs reworking before merge.",
}


def build_plan_markdown(*, pr_title: str, pr_description: str, pr_summary: str,
                        concerns: list[Concern], flags: list[Flag],
                        verdict: str, stats_text: str,
                        requirements_checks: list[RequirementCheck] | None = None,
                        requirements_verdict: str = "",
                        merge_readiness: str = "",
                        llm: LLMClient | None = None) -> str:
    lines: list[str] = []
    lines.append(f"# MyReviewer Review — {pr_title or '(untitled PR)'}")
    lines.append("")
    lines.append(f"**Change size:** {stats_text}")
    lines.append(f"**Verdict:** {verdict}")
    lines.append("")

    if pr_summary:
        lines.append("## Overall summary")
        lines.append("")
        lines.append(pr_summary.strip())
        lines.append("")

    lines.append(f"## Concerns detected ({len(concerns)})")
    if not concerns:
        lines.append("_None identified._")
    for concern in concerns:
        lines.append("")
        lines.append(f"### {concern.number}. {concern.title}")
        lines.append("")
        lines.append(f"- **Type:** {CHANGE_TYPE_LABEL.get(concern.change_type, concern.change_type)}")
        lines.append(f"- **Files:** {', '.join(concern.files) if concern.files else '(unspecified)'}")
        if concern.rationale:
            lines.append(f"- **Why grouped:** {concern.rationale}")
        if concern.is_mixed:
            lines.append(f"- **MIXED:** {concern.mixed_note or 'bundles unrelated topics'}")
    lines.append("")

    mixed = [c for c in concerns if c.is_mixed]
    if mixed:
        lines.append("## Mixed-concern highlights")
        lines.append("")
        for concern in mixed:
            lines.append(f"- **{concern.title}** — {concern.mixed_note}")
        lines.append("")

    lines.append("## Flags")
    if not flags:
        lines.append("_No issues flagged._")
    for flag in flags:
        files = f" ({', '.join(flag.files)})" if flag.files else ""
        lines.append(f"- [{flag.severity}] **{flag.category}** — {flag.message}{files}")
    lines.append("")

    lines.append("## Suggested split")
    lines.append("")
    if not concerns:
        lines.append("_Nothing to split._")
    else:
        for position, concern in enumerate(concerns, start=1):
            suffix = "  \u2014 **mixed concern, split internally**" if concern.is_mixed else ""
            lines.append(f"- **Suggested PR {position}: {_suggested_title(concern)}**{suffix}")
            if concern.files:
                lines.append(f"  - Files: {', '.join(concern.files)}")
        lines.append("")
        lines.append("### Recommended merge order")
        lines.append("")
        lines.append(_ordering_advice(concerns))
        lines.append("")

    if llm is not None:
        body = _llm_refinement(pr_title, pr_description, pr_summary, concerns, llm)
        if body:
            lines.append("## Notes for the author")
            lines.append("")
            lines.append(body.strip())
            lines.append("")
    else:
        lines.append("## Notes for the author")
        lines.append("")
        lines.append(_author_advice(concerns, flags))
        lines.append("")

    if merge_readiness or requirements_checks or requirements_verdict:
        lines.append("## Decision for the reviewer")
        lines.append("")
        if merge_readiness:
            label = READINESS_LABEL.get(merge_readiness)
            if label:
                lines.append(f"- **Merge readiness (advisory):** {merge_readiness} — {label}")
            else:
                lines.append(f"- **Merge readiness (advisory):** {merge_readiness}")
        lines.append("")
        if requirements_checks:
            lines.append("**Requirement compatibility** (advisory — the reviewer "
                         "makes the final accept/reject call):")
            lines.append("")
            order = {"unmet": 0, "partial": 1, "unverified": 2, "satisfied": 3}
            for check in sorted(requirements_checks,
                                key=lambda c: order.get(c.status, 9)):
                evidence = f" — {check.evidence}" if check.evidence else ""
                lines.append(f"- **{check.status}:** {check.requirement}{evidence}")
            lines.append("")
        if requirements_verdict:
            lines.append(f"**Requirements verdict:** {requirements_verdict}")
            lines.append("")

    lines.append("---")
    lines.append("_Generated by MyReviewer. Approve, edit, or discard freely._")
    return "\n".join(lines)


def _suggested_title(concern: Concern) -> str:
    title = concern.title.strip()
    if title.lower().startswith(("add ", "fix ", "refactor ", "remove ", "implement ", "update ")):
        return title[0].upper() + title[1:]
    return title.capitalize()


def _ordering_advice(concerns: list[Concern]) -> str:
    if len(concerns) <= 1:
        return "A single concern: nothing to reorder."
    head = concerns[0]
    tail = [c.title for c in concerns[1:]]
    return (
        f"Merge the self-contained change first (`{head.title}`), then the "
        f"dependent ones in order: {', '.join(tail)}. Keep refactors and "
        "dependency bumps last so a regression is easy to bisect."
    )


def _author_advice(concerns: list[Concern], flags: list[Flag]) -> str:
    bullets = []
    mixed = [c for c in concerns if c.is_mixed]
    if mixed:
        for concern in mixed:
            bullets.append(f"- Split `{concern.title}`: its files mix unrelated topics ({concern.mixed_note or 'see above'}).")
    lockfile = [f for f in flags if f.category == "dependency"]
    if lockfile:
        bullets.append("- Separate dependency/lockfile churn from feature code, or explain it in the PR description.")
    large = [f for f in flags if f.category == "size" and f.severity == "warning"]
    if large:
        bullets.append("- The diff is large; splitting it or adding a review guide in the description will raise review quality.")
    if not bullets:
        bullets.append("- The scope looks coherent; a clear PR title and a two-sentence description will help reviewers the most.")
    return "\n".join(bullets)


def _llm_refinement(pr_title: str, pr_description: str, pr_summary: str,
                    concerns: list[Concern], llm: LLMClient) -> str:
    concerns_text = "\n".join(
        f"- #{c.number} {c.title} [{c.change_type}] files={c.files} mixed={'yes' if c.is_mixed else 'no'}"
        for c in concerns
    )
    user = PLAN_USER.format(
        title=pr_title or "(none)",
        description=(pr_description or "(none)").strip() or "(none)",
        summary=pr_summary or "(none)",
        concerns_text=concerns_text,
    )
    raw = llm.complete(PLAN_SYSTEM, user)
    return raw.strip()