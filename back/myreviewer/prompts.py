"""Prompt templates for the analysis stages.

The templates mirror the summary-augmented prompting idea validated in
`../../utils.py` (a structured `[SUMMARY]` of intent improves comprehension):
here the model first produces an intent summary, then clusters the diff into
concerns while using that summary as context.

NIM chat endpoints do not guarantee JSON mode, so stages read structured
text blocks (e.g. `SUMMARY:`, `CONCERNS: ... TITLE/FILES/...`) that are parsed
robustly by the stage modules.
"""

SUMMARIZE_SYSTEM = (
    "You are a senior software engineer and code reviewer. You produce "
    "precise, concise natural-language summaries of what a pull request "
    "intends to change and why."
)

SUMMARIZE_USER = """Analyze the pull request below and write a structured summary of its overall intent. Do not list every file. State the problem the change solves, the approach it takes, the subsystems it affects, and any notable risks or unknowns.

PR title: {title}
PR description: {description}
Commit messages:
{commit_messages}

DECLARED REQUIREMENTS:
{requirements}

DIFF:
{diff}

Return EXACTLY this format (nothing else):

SUMMARY: <one-line intent of the whole PR>
PROBLEM: <the problem this change solves>
APPROACH: <how the change solves it>
SUBSYSTEMS: <affected subsystems/modules, comma-separated, or 'none'>
RISKS: <notable risks, unknowns or migrations, or 'none'>

If no requirements were provided, say so briefly on the RISKS line.
"""

CLUSTER_SYSTEM = (
    "You are a code review architect. Given a pull request diff you identify "
    "the distinct logical concerns (independent, separately reviewable topics) "
    "the change addresses, assign every changed file to a concern, and flag "
    "mixed concerns where unrelated topics got bundled together."
)

CLUSTER_USER = """Group the changes of the pull request into distinct concerns. Use the PR summary as the ground-truth intent for this grouping. A concern is a coherent, independently reviewable topic (e.g. 'add user avatar upload', 'fix slow orders query'). A large or mixed PR usually contains several concerns spread across files. Assign every file in the diff to exactly one concern; if a file truly mixes unrelated ideas, assign it to its dominant concern, set MIXED=yes for that concern, and explain in MIXED_NOTE.

PR title: {title}
PR description: {description}
PR summary: {summary}

DIFF:
{diff}

Return your answer in EXACTLY this format (no extra prose around it):

SUMMARY: <one-line restatement of the overall intent>
CONCERNS:
1. TITLE: <short name>
   RATIONALE: <1-2 sentences why these files form one concern>
   FILES: <comma-separated file paths>
   CHANGE_TYPE: <add_only | modify | remove_only | mixed>
   MIXED: <yes | no>
   MIXED_NOTE: <explain bundling if MIXED=yes, otherwise 'none'>
2. ...

Guidelines:
- Cover every file from the diff.
- CHANGE_TYPE reflects the dominant edit type across the concern's files.
- Keep concerns semantically disjoint; splitting a huge PR into 2-5 concerns is normal, a single huge concern is itself a finding.
"""

PLAN_SYSTEM = (
    "You are an expert on reviewer-friendly pull request structure. You turn "
    "a concern decomposition into a concrete, actionable split plan."
)

PLAN_USER = """Given the decomposition below, produce a reviewer-friendly plan to split the change into smaller pull requests.

One suggested PR per concern, named with a clear imperative title. Recommend an order (independent changes first, riskiest/refactors last). Note any file that must move between suggested PRs to keep each one self-contained, and call out dependency churn or formatting-only noise.

PR title: {title}
PR description: {description}
PR summary: {summary}

CONCERNS:
{concerns_text}

Return the plan as markdown with these sections:
## Suggested PRs
- **PR 1 — <title>** (files: <paths>): <why> 
- ...

## Ordering
<one paragraph: recommended merge order and why>

## Notes for the author
- <specific advice: mixed files to split, lockfile churn, noise, wording>
"""

DIAGNOSER_SYSTEM = (
    "You are a meticulous code reviewer. You judge whether a pull request is "
    "well-scoped and reviewer-friendly, using both quantitative signals and "
    "the concern decomposition."
)

DIAGNOSER_USER = """Assess the reviewability of the pull request described below.

PR title: {title}
PR stats:
{stats}
CONCERNS:
{concerns_text}
MIXED CONCERNS:
{mixed_text}

List reviewer-friendliness problems as a numbered markdown list. For each, give a severity label (critical / warning / info), the problem, and if relevant the files involved. Examples of problems: too many files, diff too large to review, mixed concerns, dependency churn, formatting noise, tests missing for a concern. End with a line 'VERDICT: single PR | multiple PRs' recommending one or the other. If nothing serious, the verdict may be 'single PR'.
"""

CLUSTER_PROMPT_BASE = """Concern summary for the planner:
"""

CHANGE_LOG_SYSTEM = (
    "You are a meticulous change reviewer. Given a pull request diff and its "
    "concern decomposition, you produce a clear, precise change log that "
    "compares what the code did BEFORE the PR with what it does AFTER it, "
    "concern by concern, together with the impact."
)

CHANGE_LOG_USER = """Compare the OLD (pre-PR) code with the NEW (post-PR) code for every concern below and write a precise, human-readable change log. Use the PR summary as the ground-truth intent of the change. Reference concrete behaviour and specific edited code (not just file names); reviewers should be able to understand the change's before/after without loading the whole diff.

PR title: {title}
PR summary: {summary}

CONCERNS:
{concerns_text}

DIFF:
{diff}

Return EXACTLY this format (one block per concern, nothing else):

CHANGE_LOG:
AREA 1: <concern title>
OLD: <what the code did before the change; deleted/edited behaviour>
NEW: <what the code does now; added/edited behaviour>
INTRODUCED: <introduced | pre-existing | worsened | exposed | unknown — did THIS pull request introduce the behavioural change, or did it already exist before?>
IMPACT: <behavioural impact, risks, migrations, notes for reviewers>
FILES: <comma-separated file paths>
AREA 2: ...

If a concern is untouched or noise, say so explicitly in OLD/NEW instead of skipping it.
"""

POST_REVIEW_SYSTEM = (
    "You are a senior code reviewer. After reading a change you review the "
    "resulting NEW code (correctness, edge cases, regressions, security, "
    "performance, maintainability) and report findings ordered by severity, "
    "then give an overall verdict on whether the new code is ready to merge."
)

POST_REVIEW_USER = """Review the NEW code introduced by this pull request — not its PR structure, and do not restate the change log or the split plan. Use the PR summary as the ground-truth intent of the PR: anchor every finding to what the change is trying to do. Look for genuine bugs, edge cases, regressions, security/performance issues, and maintainability problems the change introduces. List only findings grounded in the diff; every finding must carry concrete EVIDENCE (code path, caller/import chain, or repository context). A concern you cannot tie to this repository with evidence is a style suggestion, not a bug — rate it minor or nit. If the code is clean, say so in the verdict; never write "no risks" while listing findings.

PR title: {title}
PR summary: {summary}

CONCERNS:
{concerns_text}

CHANGE LOG (old -> new):
{change_log_text}

DECLARED REQUIREMENTS:
{requirements}

REPOSITORY CONTEXT (full changed files, importers, tests, config — verify reachability and evidence against it; if absent, mark evidence 'not verifiable from context'):
{repo_context}

DIFF:
{diff}

Return EXACTLY this format (nothing else):

POST_REVIEW:
FINDING 1: <critical | important | minor | nit>
TITLE: <short title>
EVIDENCE: <concrete proof the issue is real and reachable in THIS repo: file:line, import/call path, or 'not verifiable from context'>
BODY: <2-4 sentences: what is wrong, where, and why it matters>
FILES: <comma-separated file paths>
FINDING 2: ...
POST_REVIEW_VERDICT: <one paragraph: overall quality of the new code and any must-fix before merge; consistent with the findings listed above>
"""

BUG_REVIEW_SYSTEM = (
    "You are a meticulous senior software engineer performing a deep review of "
    "the NEW code introduced by a pull request. You hunt for real bugs and "
    "risks that would bite users or maintainers, and you verify the change "
    "against the project's stated requirements. You only report issues you can "
    "point at in the diff, and incompatibilities are flagged and cited — never "
    "resolved automatically. The human reviewer keeps the final decision on "
    "whether to accept or reject the merge."
)

BUG_REVIEW_USER = """Deep-review the NEW code of this pull request (not its PR structure — that is judged elsewhere). Use the PR summary as the ground-truth intent of the change. Three jobs:

1) DISCOVER REAL BUGS AND PROBLEMS in the new code: logic errors, edge cases, concurrency issues, security holes, API-contract breaks, migration issues, error-handling gaps, performance traps, missing tests. Every finding must be grounded in specific code from the diff and must include concrete EVIDENCE showing the problem is reachable in THIS repository (who imports/calls the flagged code, which path executes it). A generic best-practice violation with no evidence of impact in this repo is a style suggestion — rate it minor or nit, never higher. Every finding must include a concrete suggested fix.

2) CLASSIFY ORIGIN: for each bug state whether THIS pull request introduced it (INTRODUCED_BY_PR: yes) or whether it already existed before (no) or cannot be determined (unknown). Pre-existing problems the PR does not worsen must be reported at severity minor or nit, or omitted.

3) CHECK THE CHANGE AGAINST THE PROJECT REQUIREMENTS: for each declared requirement decide whether the change satisfies it, only partially satisfies it, misses it, or cannot be verified from the diff. Cite the code that shows each status. State any real incompatibility plainly in REQUIREMENTS_VERDICT so a human reviewer can decide whether to accept or reject the PR.

PR title: {title}
PR summary: {summary}

CONCERNS:
{concerns_text}

CHANGE LOG (old -> new):
{change_log_text}

DECLARED REQUIREMENTS:
{requirements}

REPOSITORY CONTEXT (full changed files, importers, tests, config — use it to verify reachability, origin and evidence; if absent, use REACHABLE/EVIDENCE 'unknown — no repo context'):
{repo_context}

DIFF:
{diff}

Return EXACTLY this format (nothing else, no prose before or after):

BUGS:
BUG 1:
SEVERITY: <critical | important | minor | nit>
TYPE: <logic | edge-case | concurrency | security | api-contract | migration | error-handling | tests | performance>
TITLE: <short title>
LOCATION: <path:line or hunk reference>
INTRODUCED_BY_PR: <yes | no | unknown>
REACHABLE: <yes | no | unknown — does any code in this repository actually reach the flagged behaviour?>
EVIDENCE: <concrete proof: import/call path, file:line condition, or 'unknown — no repo context'>
DETAIL: <2-4 sentences: what is wrong, where, and why it matters>
FIX: <concrete suggested fix>
BUG 2:
...
REQUIREMENTS:
REQ 1:
REQUIREMENT: <requirement text>
STATUS: <satisfied | partial | unmet | unverified>
EVIDENCE: <cite the code/hunk that shows this status>
REQ 2:
...
REQUIREMENTS_VERDICT: <one paragraph: does the change meet the project requirements; list any incompatibilities explicitly>
MERGE_READINESS: <ready | fix-before-merge | rework>

Rules:
- Be terse: TITLE <= 12 words, DETAIL 1-3 short sentences, FIX <= 2 sentences, LOCATION one path:line, EVIDENCE <= 1 sentence.
- Severity must follow actual impact and repository evidence: critical only for confirmed reachable severe harm; unreachable or pre-existing-only concerns max minor/nit.
- MERGE_READINESS must be consistent with the findings: any critical or important finding, or any unmet requirement, means at least fix-before-merge. Never say 'ready' alongside critical/important findings.
- The REQUIREMENTS_VERDICT and MERGE_READINESS lines are the last two lines; always emit them, at most 4 sentences for REQUIREMENTS_VERDICT.
- If a requirement cannot be checked from the diff, mark it 'unverified' — do not invent an outcome.
- Empty BUGS block is fine when the code is clean; never invent findings.
- MERGE_READINESS is advisory only: 'ready', 'fix-before-merge' (specific findings must be fixed), or 'rework' (the approach itself is wrong).
"""

BUG_REVIEW_VERIFY_SYSTEM = (
    "You are the final verification pass of a code review. You critique a "
    "draft list of bug findings and requirement checks: drop weak, incorrect "
    "or speculative findings, correct severities and types, merge duplicates, "
    "tighten wording, and re-rank. Keep only what a confident, strict senior "
    "reviewer would actually send."
)

BUG_REVIEW_VERIFY_USER = """Critique and revise the draft review below. Keep the same BUGS / REQUIREMENTS / REQUIREMENTS_VERDICT / MERGE_READINESS format, including the INTRODUCED_BY_PR / REACHABLE / EVIDENCE fields on every bug.

- Remove findings not directly grounded in the diff.
- Drop or downgrade findings whose REACHABLE is 'no' and that this PR did not introduce; keep pre-existing issues at minor/nit at most.
- Keep EVIDENCE concrete; replace vague evidence with 'unknown — no repo context' rather than guessing.
- Merge duplicates (keep the strongest wording).
- Fix wrong severities or bug types; severity must match impact and evidence.
- Keep requirement checks accurate to the evidence; do not soften real incompatibilities in REQUIREMENTS_VERDICT.
- Keep MERGE_READINESS consistent with the remaining findings (critical/important or unmet => at least fix-before-merge).

PR title: {title}

DRAFT REVIEW:
{draft}

Return ONLY the revised block, in the same format.
"""

# ── Specialist review passes (effort="max") ──────────────────────────────────
# One focused pass per area; findings come back in the same BUG format so the
# merger in specialists.py can dedupe them against the main bug review.

SPECIALIST_SYSTEM = (
    "You are a senior specialist code reviewer. Your entire pass covers one "
    "area only: {focus}. You report only issues in that area, each grounded in "
    "the diff and supported by concrete repository evidence; you never invent "
    "findings, and you apply the same reachability and origin rules as a "
    "general reviewer."
)

SPECIALIST_USER = """Review ONLY {focus} aspects of this pull request — one focused pass, not a general review. Use the PR summary as the ground-truth intent of the change. Report findings in the BUG format; skip the REQUIREMENTS block entirely.

PR title: {title}
PR summary: {summary}

CHANGE LOG (old -> new):
{change_log_text}

REPOSITORY CONTEXT (verify reachability and evidence against it; if absent, use 'unknown — no repo context'):
{repo_context}

DIFF:
{diff}

Return EXACTLY this format (nothing else):

BUGS:
BUG 1:
SEVERITY: <critical | important | minor | nit>
TYPE: <{allowed_types}>
TITLE: <short title>
LOCATION: <path:line or hunk reference>
INTRODUCED_BY_PR: <yes | no | unknown>
REACHABLE: <yes | no | unknown>
EVIDENCE: <concrete proof or 'unknown — no repo context'>
DETAIL: <2-4 sentences: what is wrong, where, and why it matters>
FIX: <concrete suggested fix>
BUG 2:
...

Rules:
- If you find nothing in your area, return exactly "BUGS:" and nothing else.
- Severity follows actual impact and repository evidence in THIS repo; unreachable concerns max minor/nit.
- Be terse: TITLE <= 12 words, DETAIL 1-3 short sentences, FIX <= 2 sentences, EVIDENCE <= 1 sentence.
"""