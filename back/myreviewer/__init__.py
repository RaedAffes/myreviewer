"""MyReviewer core

Analyzes a large pull request and recommends reviewer-friendly structure and
produces a SWE-grade review: overall intent summary, concern clustering,
mixed-concern and size flags, an AI change log (old -> new code), a
post-review of the new code, a deep bug-discovery + requirement-compatibility
review, and a concrete split plan.

The seven stages: summarize intent -> localize/cluster changes -> recognize
problem structure -> compare old/new code -> review the resulting code ->
verify requirements -> plan the split.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .bug_review import BugFinding, BugReviewResult, RequirementCheck, build_bug_review, enforce_readiness_consistency
from .change_log import ChangeLogEntry, build_change_log
from .clusterer import Concern, cluster
from .config import Config, ConfigError
from .diagnoser import Flag, diagnose
from .llm_client import LLMClient, MockLLMClient, NitroClient
from .planner import build_plan_markdown
from .post_review import ReviewFinding, build_post_review
from .specialists import merge_findings, run_specialists
from .summarizer import summarize

EFFORTS = ("standard", "deep", "max")

RECOMMENDATIONS = (
    "APPROVE",
    "APPROVE WITH MINOR SUGGESTIONS",
    "REQUEST CHANGES",
    "BLOCK",
)

__all__ = [
    "Concern", "Flag", "ChangeLogEntry", "ReviewFinding",
    "BugFinding", "RequirementCheck", "BugReviewResult",
    "AnalysisReport", "run_pipeline", "EFFORTS",
    "NitroClient", "MockLLMClient", "LLMClient", "Config", "ConfigError",
]


@dataclass
class AnalysisReport:
    pr_title: str
    pr_summary: str
    concerns: list[Concern]
    flags: list[Flag]
    verdict: str
    plan_markdown: str
    stats_text: str
    change_log: list[ChangeLogEntry] = field(default_factory=list)
    post_review: list[ReviewFinding] = field(default_factory=list)
    post_review_verdict: str = ""
    bug_findings: list[BugFinding] = field(default_factory=list)
    requirements_checks: list[RequirementCheck] = field(default_factory=list)
    requirements_verdict: str = ""
    merge_readiness: str = ""
    effort: str = "deep"
    commit_messages: list[str] = field(default_factory=list)
    model: str = ""
    severity_counts: dict = field(default_factory=dict)
    recommendation: str = ""

    def to_dict(self) -> dict:
        return {
            "title": self.pr_title,
            "summary": self.pr_summary,
            "stats": self.stats_text,
            "verdict": self.verdict,
            "concerns": [c.to_dict() for c in self.concerns],
            "flags": [f.to_dict() for f in self.flags],
            "commit_messages": self.commit_messages,
            "model": self.model,
            "effort": self.effort,
            "plan_markdown": self.plan_markdown,
            "change_log": [e.to_dict() for e in self.change_log],
            "post_review": [f.to_dict() for f in self.post_review],
            "post_review_verdict": self.post_review_verdict,
            "bug_findings": [b.to_dict() for b in self.bug_findings],
            "requirements_checks": [c.to_dict() for c in self.requirements_checks],
            "requirements_verdict": self.requirements_verdict,
            "merge_readiness": self.merge_readiness,
            "severity_counts": dict(self.severity_counts),
            "recommendation": self.recommendation,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def run_pipeline(diff, *, config: Config, mock: bool = False, model: str | None = None,
                 effort: str = "deep", requirements: str = "",
                 repo_context: str = "") -> AnalysisReport:
    """Run the seven stages over a `GitDiff` object and return a report.

    `model` overrides the configured model for this run (mock mode keeps the
    label "mock" regardless). `effort` is "standard" (no verification pass),
    "deep" (default; second LLM call that critiques the bug/requirement
    review), or "max" (deep + three specialist passes: correctness, security,
    tests — merged and deduped). `requirements` is the DECLARED REQUIREMENTS
    block (repo-derived + manual). `repo_context` is the REPOSITORY CONTEXT
    block (changed files, importers, tests, config) injected alongside the
    PR summary into the review prompts.
    """
    if effort not in EFFORTS:
        effort = "deep"
    if mock:
        llm: LLMClient = MockLLMClient()
        effective_model = "mock"
    else:
        if not config.has_api_key:
            raise ConfigError(
                "No NIM_API_KEY found — set NIM_API_KEY in back/.env for "
                "real analysis. Mock responses are an explicit dev/test option "
                "only (the dashboard's mock checkbox)."
            )
        chosen = (model or "").strip() or config.model
        llm = NitroClient(
            api_key=config.api_key,
            base_url=config.base_url,
            model=chosen,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        )
        effective_model = chosen

    pr_title = diff.title or f"{diff.base or ''} -> {diff.head or ''}".strip(" ->")
    pr_description = diff.description or ""
    compact = diff.to_compact()

    requirements_text = requirements.strip() or "(none declared)"

    pr_summary = summarize(pr_title, pr_description, compact, llm,
                           requirements=requirements_text,
                           commit_messages=diff.commit_messages)
    concerns = cluster(pr_title, pr_description, pr_summary, compact, llm)
    flags, verdict = diagnose(
        pr_title, diff.stats_text, diff.file_paths, diff.added,
        diff.deleted, concerns, None,
    )
    change_log = build_change_log(pr_title, pr_summary, concerns, compact, llm)
    post_review, post_review_verdict = build_post_review(
        pr_title, pr_summary, concerns, change_log, compact, llm,
        requirements=requirements_text, repo_context=repo_context,
    )
    bug_effort = "deep" if effort in ("deep", "max") else "standard"
    bug_review = build_bug_review(
        pr_title, pr_summary, concerns, change_log, requirements_text,
        compact, llm, effort=bug_effort, repo_context=repo_context,
    )
    if effort == "max":
        specialist_findings = run_specialists(
            pr_title, pr_summary, concerns, change_log, compact, llm,
            repo_context=repo_context,
        )
        if specialist_findings:
            bug_review.findings = merge_findings(bug_review.findings,
                                                 specialist_findings)
            enforce_readiness_consistency(bug_review)
    plan = build_plan_markdown(
        pr_title=pr_title,
        pr_description=pr_description,
        pr_summary=pr_summary,
        concerns=concerns,
        flags=flags,
        verdict=verdict,
        stats_text=diff.stats_text,
        requirements_checks=bug_review.checks,
        requirements_verdict=bug_review.requirements_verdict,
        merge_readiness=bug_review.merge_readiness,
        llm=None,
    )

    counts = _severity_counts(bug_review.findings, post_review)
    recommendation = _recommendation(counts, bug_review.checks,
                                     bug_review.merge_readiness)

    return AnalysisReport(
        pr_title=pr_title,
        pr_summary=pr_summary,
        concerns=concerns,
        flags=flags,
        verdict=verdict,
        plan_markdown=plan,
        stats_text=diff.stats_text,
        change_log=change_log,
        post_review=post_review,
        post_review_verdict=post_review_verdict,
        bug_findings=bug_review.findings,
        requirements_checks=bug_review.checks,
        requirements_verdict=bug_review.requirements_verdict,
        merge_readiness=bug_review.merge_readiness,
        effort=effort,
        commit_messages=diff.commit_messages,
        model=effective_model,
        severity_counts=counts,
        recommendation=recommendation,
    )


def _severity_counts(bug_findings, post_review) -> dict:
    """Counts over the deep findings; falls back to the post-review."""
    counts = {"critical": 0, "important": 0, "minor": 0, "nit": 0}
    source = bug_findings or post_review
    for finding in source:
        severity = getattr(finding, "severity", "")
        if severity in counts:
            counts[severity] += 1
    return counts


def _recommendation(counts: dict, checks, merge_readiness: str) -> str:
    """Deterministic final recommendation derived from validated findings."""
    if counts.get("critical") or merge_readiness == "rework":
        return "BLOCK"
    unmet = any(getattr(c, "status", "") == "unmet" for c in checks)
    if counts.get("important") or unmet or merge_readiness == "fix-before-merge":
        return "REQUEST CHANGES"
    if counts.get("minor") or counts.get("nit"):
        return "APPROVE WITH MINOR SUGGESTIONS"
    return "APPROVE"