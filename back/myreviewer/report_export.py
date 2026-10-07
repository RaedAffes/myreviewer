"""Standalone HTML and PDF export of a stored report.

Backs the dashboard's "Download as …" action. Given a report dict (the payload
returned by `/api/analyze` and `/api/reports/<id>`), renders it two ways:

* `build_html`   — one self-contained HTML file (inline CSS, no network).
* `build_pdf`    — a PDF via fpdf2, using a system DejaVu font so every model
                   character renders correctly (arrows, unicode dashes…).

All model-generated text is HTML-escaped before emitting. No external assets.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

FONT_DIRS = [
    "/usr/share/fonts/truetype/dejavu",
    "/usr/local/share/fonts/truetype/dejavu",
    str(Path.home() / ".fonts"),
]

SEVERITY_BADGE = {
    "critical": "#f8d7da;color:#a0282d",
    "important": "#fde8cc;color:#8a4b00",
    "warning": "#fde8cc;color:#8a4b00",
    "minor": "#d5e5f7;color:#1b4f8a",
    "info": "#d5e5f7;color:#1b4f8a",
    "nit": "#e5e7eb;color:#5b6472",
}

STATUS_BADGE = {
    "satisfied": "#d9f2e2;color:#075c33",
    "partial": "#fdecc8;color:#8a4b00",
    "unmet": "#f8d7da;color:#a0282d",
    "unverified": "#e5e7eb;color:#5b6472",
}

READINESS_BADGE = {
    "ready": "#d9f2e2;color:#075c33",
    "fix-before-merge": "#fde8cc;color:#8a4b00",
    "rework": "#f8d7da;color:#a0282d",
}

RECOMMENDATION_BADGE = {
    "APPROVE": "#d9f2e2;color:#075c33",
    "APPROVE WITH MINOR SUGGESTIONS": "#fdf3d0;color:#8a6b00",
    "REQUEST CHANGES": "#fde8cc;color:#8a4b00",
    "BLOCK": "#f8d7da;color:#a0282d",
}

_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { font-family: -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
       margin: 0 auto; max-width: 860px; padding: 32px 24px 80px; color: #1c2333; }
h1 { font-size: 26px; margin: 0 0 6px; letter-spacing: -0.3px; }
h2 { font-size: 17px; margin: 0 0 14px; }
h3 { font-size: 15px; }
.meta { color: #5b6472; font-size: 13px; margin: 0 0 16px; }
.mono { font-family: ui-monospace, 'Cascadia Code', Consolas, monospace; }
.badge { display:inline-block; border-radius: 999px; padding: 4px 14px; font-weight: 700;
         font-size: 12px; white-space: nowrap; margin: 2px 6px 2px 0; }
.badge.tag { border-radius: 6px; padding: 1px 8px; font-weight: 600; font-size: 11px; }
.card { border: 1px solid #dbe1ea; border-radius: 12px; padding: 18px 22px; margin: 0 0 18px; }
.item { border: 1px solid #e2e7ef; border-left: 3px solid #6c7aea; border-radius: 10px;
        padding: 12px 16px; margin: 10px 0; }
.item p { margin: 6px 0 0; }
.muted { color: #5b6472; }
.finding-title, .item-title { font-weight: 700; font-size: 14px; margin: 0; }
.verdict { border-top: 1px dashed #dbe1ea; margin-top: 12px; padding-top: 10px; font-size: 13px; }
pre { background: #f4f6fa; border: 1px solid #e2e7ef; border-radius: 8px; padding: 10px 12px;
      white-space: pre-wrap; font-size: 12px; overflow-wrap: anywhere; }
.fix { color: #2e7d4f; font-size: 13px; margin-top: 6px; }
.bug { border-left-color: #a0282d; }
.req.satisfied { border-left-color: #3d9a63; }
.req.partial { border-left-color: #e0a400; }
.req.unmet { border-left-color: #a0282d; }
ul, ol { margin: 6px 0 0; padding-left: 22px; font-size: 13px; }
li { margin: 3px 0; }
hr { border: 0; border-top: 1px solid #dbe1ea; margin: 14px 0; }
blockquote { margin: 8px 0; padding: 6px 14px; border-left: 3px solid #dbe1ea;
             color: #5b6472; }
code { font-family: ui-monospace, Consolas, monospace; font-size: 12px;
       background: #f4f6fa; border: 1px solid #e2e7ef; border-radius: 5px; padding: 1px 5px; }
.foot { color: #8a92a3; font-size: 11px; margin-top: 24px; }
"""


# ───────────────────────────────────────────── HTML export ─────────────────

def _badge(text: str, style: str, label: str = "") -> str:
    return (
        f'<span class="badge" style="background:{style}">'
        f"{html.escape(label)}{html.escape(text)}</span>"
    )


def _card(title: str, body: str) -> str:
    return f'<section class="card"><h2>{html.escape(title)}</h2>{body}</section>'


def _fmt_iso(value: str) -> str:
    if not value:
        return "?"
    try:
        return value.replace("T", " ").split("+")[0].split(".")[0]
    except Exception:  # noqa: BLE001
        return value


def esc_or(value: object) -> str:
    return html.escape(str(value)) if value else ""


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else []


def build_html(report: dict) -> str:
    esc = html.escape
    title = report.get("title") or "(untitled PR)"
    repo = str(report.get("repo") or "")
    pr = report.get("pr_number")
    pr_tag = f"#{esc(str(pr))}" if pr else ""

    badges = []
    if report.get("verdict"):
        badges.append(_badge(str(report["verdict"]), "#e8e6f7;color:#4a437d"))
    readiness = report.get("merge_readiness") or ""
    if readiness:
        style = READINESS_BADGE.get(readiness, "#e8e6f7;color:#4a437d")
        badges.append(_badge(readiness, style, label="merge readiness · "))
    recommendation = str(report.get("recommendation") or "")
    if recommendation:
        style = RECOMMENDATION_BADGE.get(recommendation, "#e8e6f7;color:#4a437d")
        badges.append(_badge(recommendation, style, label="recommendation · "))
        counts = report.get("severity_counts") or {}
        count_text = ", ".join(
            f"{n} {name}" for name, n in counts.items() if n
        )
        if count_text:
            badges.append(_badge(count_text, "#eef1f7;color:#4a437d", label="findings · "))

    sections = [
        f'<section class="card"><h1>{esc(title)}</h1>'
        f'<p class="meta mono">{(esc(repo) + pr_tag) or "—"} · {esc(str(report.get("stats") or ""))}'
        f" · model: {esc(str(report.get('model') or '?'))} (effort: "
        f'{esc(str(report.get("effort") or ""))}) · analyzed '
        f'{esc(_fmt_iso(report.get("analyzed_at_iso")))}</p>'
        + (" ".join(badges) if badges else "")
        + "</section>",
    ]
    if report.get("summary"):
        sections.append(_card("Overall summary", f"<pre>{esc(str(report['summary']))}</pre>"))
    sections.append(_concerns_card(_as_list(report.get("concerns"))))
    sections.append(_change_log_card(_as_list(report.get("change_log"))))
    sections.append(_post_review_card(report))
    sections.append(_bugs_card(report))
    sections.append(_requirements_card(report))
    sections.append(_flags_card(_as_list(report.get("flags"))))
    if report.get("plan_markdown"):
        sections.append(
            _card("Suggested split", md_to_html(str(report["plan_markdown"]))),
        )

    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{esc(title)} — MyReviewer</title>\n"
        f"<style>{_CSS}</style></head><body>\n"
        + "\n".join(sections)
        + '\n<p class="foot">Generated by MyReviewer (NVIDIA NIM). '
        "Downloadable report — review findings are advisory.</p>\n"
        "</body></html>"
    )


def _concerns_card(concerns: list) -> str:
    if not concerns:
        return _card("Concerns detected (0)", '<p class="muted">None identified.</p>')
    items = []
    for c in concerns:
        if not isinstance(c, dict):
            continue
        inner = [
            '<p class="item-title">'
            f'<span class="badge tag" style="background:#6c7aea;color:#0b0f19">'
            f'{html.escape(str(c.get("number", "")))}</span> '
            f'{html.escape(c.get("title") or "")}',
        ]
        if c.get("is_mixed"):
            inner.append('<span class="badge tag" style="background:#fde8cc;color:#8a4b00">mixed</span>')
        if c.get("change_type"):
            inner.append(
                f'<span class="badge tag" style="background:#e2e7ef;color:#5b6472">'
                f'{html.escape(c["change_type"])}</span>',
            )
        inner[-1] += "</p>"
        if c.get("rationale"):
            inner.append(f'<p class="muted">{esc_or(c["rationale"])}</p>')
        files = _as_list(c.get("files"))
        if files:
            inner.append('<ul class="mono">%s</ul>' % "".join(
                f"<li>{html.escape(f)}</li>" for f in files))
        if c.get("is_mixed") and c.get("mixed_note"):
            inner.append(
                f'<p style="color:#a5440a;font-size:12px">MIXED: {esc_or(c["mixed_note"])}</p>',
            )
        items.append('<div class="item">%s</div>' % "".join(inner))
    return _card(f"Concerns detected ({len(concerns)})", "".join(items))


def _change_log_card(change_log: list) -> str:
    if not change_log:
        return ""
    esc = html.escape
    items = []
    for e in change_log:
        if not isinstance(e, dict):
            continue
        rows = []
        if e.get("old_code"):
            rows.append(f"<p><strong>Before</strong><pre>{esc(str(e['old_code']))}</pre></p>")
        if e.get("new_code"):
            rows.append(f"<p><strong>After</strong><pre>{esc(str(e['new_code']))}</pre></p>")
        if e.get("impact"):
            rows.append(f'<p class="muted"><strong>Impact:</strong> {esc_or(e["impact"])}</p>')
        if e.get("introduced"):
            rows.append(
                f'<p class="muted"><strong>Origin:</strong> {esc_or(e["introduced"])}</p>'
            )
        files = ", ".join(_as_list(e.get("files")))
        heading = esc(e.get("area") or "") or "—"
        if files:
            heading += f' · <span class="mono">{esc(files)}</span>'
        items.append(f'<div class="item"><p class="item-title">{heading}</p>{"".join(rows)}</div>')
    return _card("AI change summary (old → new)", "".join(items))


def _finding_items(findings: list, bug: bool = False) -> str:
    items = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        sev = f.get("severity") or ""
        style = SEVERITY_BADGE.get(sev, "#e5e7eb;color:#5b6472")
        cls = " bug" if bug else ""
        location = str(f.get("location") or "") if bug else ", ".join(_as_list(f.get("files")))
        extra = ""
        if bug:
            if f.get("type"):
                extra += (
                    '<p class="muted" style="font-size:11px;font-weight:700;'
                    f'text-transform:uppercase">{html.escape(f["type"])}</p>'
                )
            meta_bits = []
            if f.get("introduced_by_pr"):
                meta_bits.append(f'introduced by PR: {f["introduced_by_pr"]}')
            if f.get("reachable"):
                meta_bits.append(f'reachable: {f["reachable"]}')
            if meta_bits:
                extra += (
                    '<p class="muted" style="font-size:11px;font-weight:700;'
                    f'text-transform:uppercase">{html.escape(" · ".join(meta_bits))}</p>'
                )
            if f.get("detail"):
                extra += f'<p class="muted">{esc_or(f["detail"])}</p>'
            if f.get("evidence"):
                extra += f'<p class="muted"><strong>Evidence:</strong> {esc_or(f["evidence"])}</p>'
            if f.get("fix"):
                extra += f'<p class="fix"><strong>Suggested fix:</strong> {esc_or(f["fix"])}</p>'
        elif f.get("body"):
            extra = f'<p class="muted">{esc_or(f["body"])}</p>'
            if f.get("evidence"):
                extra += f'<p class="muted"><strong>Evidence:</strong> {esc_or(f["evidence"])}</p>'
        loc_html = (
            f' <span class="mono" style="font-size:11px;color:#5b6472">{html.escape(location)}</span>'
            if location
            else ""
        )
        items.append(
            '<div class="item%s"><p class="finding-title">'
            f'<span class="badge tag" style="background:{style}">{html.escape(sev)}</span> '
            f'{html.escape(f.get("title") or "")}{loc_html}'
            f"</p>{extra}</div>",
        )
    return "".join(items)


def _post_review_card(report: dict) -> str:
    findings = _as_list(report.get("post_review"))
    if not findings:
        return ""
    body = _finding_items(findings)
    if report.get("post_review_verdict"):
        body += f'<p class="verdict"><strong>Verdict:</strong> {esc_or(report["post_review_verdict"])}</p>'
    return _card("Review of the new code", body)


def _bugs_card(report: dict) -> str:
    findings = _as_list(report.get("bug_findings"))
    if not findings:
        return ""
    return _card("Bugs &amp; risks (deep review)", _finding_items(findings, bug=True))


def _requirements_card(report: dict) -> str:
    checks = _as_list(report.get("requirements_checks"))
    if not checks:
        return ""
    items = []
    for c in checks:
        if not isinstance(c, dict):
            continue
        status = c.get("status") or "unverified"
        style = STATUS_BADGE.get(status, "#e5e7eb;color:#5b6472")
        body = (
            f'<span class="badge tag" style="background:{style}">{html.escape(status)}</span> '
            f'{html.escape(c.get("requirement") or "")}'
        )
        if c.get("evidence"):
            body += f'<p class="muted" style="margin:6px 0 0">{esc_or(c["evidence"])}</p>'
        items.append(f'<div class="item req {html.escape(status)}"><p class="item-title">{body}</p></div>')
    body = "".join(items)
    if report.get("requirements_verdict"):
        body += (
            f'<p class="verdict"><strong>Requirements verdict:</strong> '
            f'{esc_or(report["requirements_verdict"])}</p>'
        )
    body += (
        '<p class="muted" style="font-size:12px;margin-top:8px">Advisory — '
        "the reviewer makes the final accept/reject call.</p>"
    )
    return _card("Requirements compatibility", body)


def _flags_card(flags: list) -> str:
    if not flags:
        return _card("Flags", '<p class="muted">No issues flagged.</p>')
    items = []
    for f in flags:
        if not isinstance(f, dict):
            continue
        sev = f.get("severity") or ""
        style = SEVERITY_BADGE.get(sev, "#e5e7eb;color:#5b6472")
        files = ", ".join(f.get("files") or [])
        files_html = (
            f' <span class="mono" style="color:#5b6472">{html.escape(files)}</span>' if files else ""
        )
        items.append(
            f'<li><span class="badge tag" style="background:{style}">{html.escape(sev)}</span> '
            f'<strong>{html.escape(f.get("category") or "")}</strong> {esc_or(f.get("message") or "")}{files_html}'
            "</li>",
        )
    return _card("Flags", "<ul>%s</ul>" % "".join(items))


# ──────────────────────────────────────── Markdown export ────────────────

def _md_escape(text: object) -> str:
    """Keep user/model text from breaking the markdown document."""
    s = str(text)
    s = s.replace("\\", "\\\\").replace("`", "\\`")
    s = re.sub(r"^\s{0,3}(#{1,6}\s)", r"\\\1", s)
    return s


def _md_files(files: object) -> str:
    joined = ", ".join(str(f) for f in _as_list(files))
    return f" `{_md_escape(joined)}`" if joined else ""


def build_markdown(report: dict) -> str:
    """Render the report as a readable Markdown document."""
    out: list[str] = []
    repo = str(report.get("repo") or "")
    pr = report.get("pr_number")
    pr_tag = f"#{pr}" if pr else ""

    out.append(f"# {_md_escape(report.get('title') or '(untitled PR)')}")
    out.append("")
    out.append(
        f"**{(repo + pr_tag) or '—'}** · {_md_escape(report.get('stats') or '')} · "
        f"model: {_md_escape(report.get('model') or '?')} (effort: "
        f"{_md_escape(report.get('effort') or '')}) · "
        f"analyzed {_md_escape(_fmt_iso(report.get('analyzed_at_iso')))}",
    )
    if report.get("verdict"):
        out.append("")
        out.append(f"**Verdict:** {_md_escape(report['verdict'])}")
    readiness = report.get("merge_readiness")
    if readiness:
        out.append(f"**Merge readiness (advisory):** {_md_escape(readiness)}")
    recommendation = report.get("recommendation")
    if recommendation:
        out.append(f"**Recommendation:** {_md_escape(recommendation)}")
        counts = report.get("severity_counts") or {}
        count_text = ", ".join(f"{n} {name}" for name, n in counts.items() if n)
        if count_text:
            out.append(f"**Findings:** {_md_escape(count_text)}")

    def heading(text: str) -> None:
        out.append("")
        out.append(f"## {_md_escape(text)}")
        out.append("")

    if report.get("summary"):
        heading("Overall summary")
        out.append("\n".join(f"> {line}" for line in str(report["summary"]).splitlines()))
        out.append("")

    concerns = _as_list(report.get("concerns"))
    heading(f"Concerns detected ({len(concerns)})")
    if not concerns:
        out.append("None identified.")
    for c in concerns:
        if not isinstance(c, dict):
            continue
        line = f"[{c.get('number', '')}] {_md_escape(c.get('title') or '')}"
        if c.get("is_mixed"):
            line += " *(mixed)*"
        if c.get("change_type"):
            line += f" — {_md_escape(c['change_type'])}"
        out.append(f"- {line}")
        if c.get("rationale"):
            out.append(f"  {_md_escape(c['rationale'])}")
        for file in _as_list(c.get("files")):
            out.append(f"  - `{_md_escape(file)}`")
        if c.get("is_mixed") and c.get("mixed_note"):
            out.append(f"  **MIXED:** {_md_escape(c['mixed_note'])}")

    change_log = _as_list(report.get("change_log"))
    if change_log:
        heading("AI change summary (old → new)")
        for e in change_log:
            if not isinstance(e, dict):
                continue
            out.append("")
            out.append(f"### {_md_escape(e.get('area') or '—')}{_md_files(e.get('files'))}")
            out.append("")
            if e.get("old_code"):
                out.append("**Before:**")
                out.append("```text")
                out.append(str(e["old_code"]))
                out.append("```")
            if e.get("new_code"):
                out.append("**After:**")
                out.append("```text")
                out.append(str(e["new_code"]))
                out.append("```")
            if e.get("impact"):
                out.append(f"**Impact:** {_md_escape(e['impact'])}")

    post_review = _as_list(report.get("post_review"))
    if post_review:
        heading("Review of the new code")
        for f in post_review:
            if not isinstance(f, dict):
                continue
            out.append(
                f"- **[{_md_escape(f.get('severity') or '')}]** "
                f"{_md_escape(f.get('title') or '')}{_md_files(f.get('files'))}",
            )
            if f.get("body"):
                out.append(f"  {_md_escape(f['body'])}")
        if report.get("post_review_verdict"):
            out.append("")
            out.append(f"**Verdict:** {_md_escape(report['post_review_verdict'])}")

    bugs = _as_list(report.get("bug_findings"))
    if bugs:
        heading("Bugs & risks (deep review)")
        for b in bugs:
            if not isinstance(b, dict):
                continue
            out.append("")
            out.append(f"### [{_md_escape(b.get('severity') or '')}] {_md_escape(b.get('title') or '')}")
            if b.get("type"):
                out.append(f"- Type: {_md_escape(b['type'])}")
            if b.get("location"):
                out.append(f"- Location: `{_md_escape(b['location'])}`")
            meta_bits = []
            if b.get("introduced_by_pr"):
                meta_bits.append(f"introduced by PR: {b['introduced_by_pr']}")
            if b.get("reachable"):
                meta_bits.append(f"reachable: {b['reachable']}")
            if meta_bits:
                out.append(f"- {' · '.join(_md_escape(bit) for bit in meta_bits)}")
            if b.get("detail"):
                out.append(f"- Detail: {_md_escape(b['detail'])}")
            if b.get("evidence"):
                out.append(f"- Evidence: {_md_escape(b['evidence'])}")
            if b.get("fix"):
                out.append(f"- **Suggested fix:** {_md_escape(b['fix'])}")

    checks = _as_list(report.get("requirements_checks"))
    if checks:
        heading("Requirements compatibility")
        for c in checks:
            if not isinstance(c, dict):
                continue
            out.append(
                f"- **[{_md_escape(c.get('status') or 'unverified')}]** "
                f"{_md_escape(c.get('requirement') or '')}",
            )
            if c.get("evidence"):
                out.append(f"  {_md_escape(c['evidence'])}")
        if report.get("requirements_verdict"):
            out.append("")
            out.append(f"**Requirements verdict:** {_md_escape(report['requirements_verdict'])}")
        out.append("")
        out.append("_Advisory — the reviewer makes the final accept/reject call._")

    flags = _as_list(report.get("flags"))
    heading("Flags")
    if not flags:
        out.append("No issues flagged.")
    for f in flags:
        if not isinstance(f, dict):
            continue
        out.append(
            f"- [{_md_escape(f.get('severity') or '')}] **{_md_escape(f.get('category') or '')}** "
            f"{_md_escape(f.get('message') or '')}{_md_files(f.get('files'))}",
        )

    plan = report.get("plan_markdown")
    if plan:
        heading("Suggested split")
        out.append(str(plan))

    out.append("")
    out.append("---")
    out.append("_Generated by MyReviewer (NVIDIA NIM). Review findings are advisory._")
    return "\n".join(out) + "\n"


# ────────────────────────────────── minimal markdown (matches dashboard) ───

def md_to_html(markdown: str) -> str:
    """Port of the dashboard's renderMarkdown: headings, lists, hr, quote, code, bold."""
    out: list[str] = []
    in_list: list[str] = []
    in_quote = False

    def close_blocks() -> None:
        nonlocal in_quote
        while in_list:
            in_list.pop()
        if in_quote:
            out.append("</blockquote>")
            in_quote = False

    for raw in markdown.split("\n"):
        trimmed = raw.rstrip().strip()
        if not trimmed:
            close_blocks()
            continue
        if re.match(r"^---+\s*$", trimmed):
            close_blocks()
            out.append("<hr>")
            continue
        if trimmed == "```":
            close_blocks()
            out.append("<pre></pre>")
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", trimmed)
        if m:
            close_blocks()
            level = min(6, len(m.group(1)))
            out.append(f"<h{level}>{_md_inline(m.group(2))}</h{level}>")
            continue
        m = re.match(r"^>\s?(.*)$", trimmed)
        if m:
            if not in_quote:
                out.append("<blockquote>")
                in_quote = True
            out.append(_md_inline(m.group(1)))
            continue
        m = re.match(r"^[-*]\s+(.*)$", trimmed)
        if m:
            if not in_list:
                out.append("<ul>")
                in_list.append("ul")
            out.append(f"<li>{_md_inline(m.group(1))}</li>")
            continue
        m = re.match(r"^(\d+)[.)]\s+(.*)$", trimmed)
        if m:
            if not in_list:
                out.append("<ol>")
                in_list.append("ol")
            out.append(f"<li>{_md_inline(m.group(2))}</li>")
            continue
        close_blocks()
        out.append(f"<p>{_md_inline(trimmed)}</p>")
    close_blocks()
    return "".join(out)


def _md_inline(text: str) -> str:
    result = html.escape(text)
    result = re.sub(r"`([^`]+)`", lambda m: f"<code>{html.escape(m.group(1))}</code>", result)
    result = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", result)
    result = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", result)
    return result


# ─────────────────────────────────────────────── PDF export ────────────────

def _find_fonts() -> tuple[str | None, str | None, str | None]:
    for directory in FONT_DIRS:
        d = Path(directory)
        if not d.is_dir():
            continue
        regular = d / "DejaVuSans.ttf"
        bold = d / "DejaVuSans-Bold.ttf"
        mono = d / "DejaVuSansMono.ttf"
        if regular.exists():
            return (
                str(regular),
                str(bold) if bold.exists() else None,
                str(mono) if mono.exists() else None,
            )
    return None, None, None


def _pdf_text(text: object, only_ascii: bool) -> str:
    """Normalize whitespace and (fallback fonts) strip non-latin-1 chars."""
    value = re.sub(r"\s+", " ", str(text)).strip()
    if only_ascii:
        return value.encode("latin-1", errors="replace").decode("latin-1")
    return value


def build_pdf(report: dict) -> bytes:
    from fpdf import FPDF

    font, font_bold, font_mono = _find_fonts()
    only_ascii = font is None

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()
    if font:
        pdf.add_font("DejaVu", "", font)
        pdf.add_font("DejaVu", "B", font_bold or font)
        if font_mono:
            pdf.add_font("Mono", "", font_mono)
        pdf.set_font("DejaVu", size=10)
    else:
        pdf.set_font("helvetica", size=10)

    def space(h: float = 5.0) -> None:
        if pdf.get_y() + h > pdf.h - 16:
            pdf.add_page()
        pdf.ln(h)

    def write(text: object, size: int = 10, style: str = "", indent: float = 0.0) -> None:
        pdf.set_font("DejaVu" if font else "helvetica", style=style, size=size)
        x = pdf.get_x()
        if indent:
            pdf.set_x(x + indent)
        pdf.multi_cell(0, 5.5, _pdf_text(text, only_ascii), new_x="LMARGIN", new_y="NEXT")

    def heading(text: str, level: int = 2) -> None:
        space(5)
        write(text, size=13 if level == 1 else 12, style="B")
        if level <= 2:
            pdf.set_draw_color(219, 225, 234)
            pdf.set_line_width(0.3)
            pdf.line(pdf.l_margin, pdf.get_y() + 1, pdf.w - pdf.r_margin, pdf.get_y() + 1)
            pdf.ln(3.5)

    def para(text: object) -> None:
        write(text)
        pdf.ln(1.5)

    _render_pdf(pdf, report, write=write, heading=heading, para=para,
                space=space, only_ascii=only_ascii, font_mono=font_mono)
    return pdf.output()


def _render_pdf(pdf, report: dict, *, write, heading, para, space, only_ascii, font_mono) -> None:
    title = report.get("title") or "(untitled PR)"
    repo = str(report.get("repo") or "")
    pr = report.get("pr_number")
    pr_tag = f"#{pr}" if pr else ""

    heading(title, level=1)
    para(
        f"{repo}{pr_tag}  ·  {report.get('stats') or ''}  ·  "
        f"model: {report.get('model') or '?'} (effort: {report.get('effort') or ''})  ·  "
        f"analyzed {_fmt_iso(report.get('analyzed_at_iso'))}"
    )
    para(f"Verdict: {report.get('verdict') or '—'}"
         f"{'  ·  Merge readiness: ' + str(report.get('merge_readiness')) if report.get('merge_readiness') else ''}")
    if report.get("recommendation"):
        counts = report.get("severity_counts") or {}
        count_text = ", ".join(f"{n} {name}" for name, n in counts.items() if n)
        extra = f"  ·  Findings: {count_text}" if count_text else ""
        para(f"Recommendation: {report['recommendation']}{extra}")
    space(4)

    if report.get("summary"):
        heading("Overall summary")
        para(_pdf_text(report["summary"], only_ascii))

    concerns = _as_list(report.get("concerns"))
    heading(f"Concerns detected ({len(concerns)})")
    if not concerns:
        para("None identified.")
    for c in concerns:
        if not isinstance(c, dict):
            continue
        space(2)
        write(
            f"[{c.get('number', '')}] {c.get('title') or ''}"
            f"{' (mixed)' if c.get('is_mixed') else ''}"
            f"{' — ' + str(c.get('change_type')) if c.get('change_type') else ''}",
            style="B",
        )
        if c.get("rationale"):
            para(c["rationale"])
        files = _as_list(c.get("files"))
        if files:
            write("Files: " + ", ".join(files), size=9)
        if c.get("is_mixed") and c.get("mixed_note"):
            para("MIXED: " + str(c["mixed_note"]))

    change_log = _as_list(report.get("change_log"))
    if change_log:
        heading(f"AI change summary (old → new)")
        for e in change_log:
            if not isinstance(e, dict):
                continue
            space(2)
            write(f"{e.get('area') or '—'}  ·  {', '.join(_as_list(e.get('files')))}", style="B")
            if e.get("old_code"):
                write("BEFORE:", style="B")
                write(f"{e['old_code']}", size=9)
            if e.get("new_code"):
                write("AFTER:", style="B")
                write(f"{e['new_code']}", size=9)
            if e.get("impact"):
                para("Impact: " + str(e["impact"]))

    post_review = _as_list(report.get("post_review"))
    if post_review:
        heading("Review of the new code")
        for f in post_review:
            if not isinstance(f, dict):
                continue
            space(2)
            extra = f" — {', '.join(_as_list(f.get('files')))}" if _as_list(f.get("files")) else ""
            para(f"[{f.get('severity') or ''}] {f.get('title') or ''}{extra}")
            if f.get("body"):
                para(f["body"])
        if report.get("post_review_verdict"):
            para("Verdict: " + str(report["post_review_verdict"]))

    bugs = _as_list(report.get("bug_findings"))
    if bugs:
        heading("Bugs & risks (deep review)")
        for b in bugs:
            if not isinstance(b, dict):
                continue
            space(2)
            para(f"[{b.get('severity') or ''}] {b.get('title') or ''}")
            if b.get("type"):
                para(f"Type: {b['type']}")
            if b.get("location"):
                para(f"Location: {b['location']}")
            meta_bits = []
            if b.get("introduced_by_pr"):
                meta_bits.append(f"introduced by PR: {b['introduced_by_pr']}")
            if b.get("reachable"):
                meta_bits.append(f"reachable: {b['reachable']}")
            if meta_bits:
                para(" · ".join(meta_bits))
            if b.get("detail"):
                para(b["detail"])
            if b.get("evidence"):
                para("Evidence: " + str(b["evidence"]))
            if b.get("fix"):
                para("Suggested fix: " + str(b["fix"]))

    checks = _as_list(report.get("requirements_checks"))
    if checks:
        heading("Requirements compatibility")
        for c in checks:
            if not isinstance(c, dict):
                continue
            space(2)
            para(f"[{c.get('status') or 'unverified'}] {c.get('requirement') or ''}")
            if c.get("evidence"):
                para("Evidence: " + str(c["evidence"]))
        if report.get("requirements_verdict"):
            para("Requirements verdict: " + str(report["requirements_verdict"]))
        para("Advisory — the reviewer makes the final accept/reject call.")

    flags = _as_list(report.get("flags"))
    heading("Flags")
    if not flags:
        para("No issues flagged.")
    for f in flags:
        if not isinstance(f, dict):
            continue
        extra = " — " + ", ".join(_as_list(f.get("files"))) if _as_list(f.get("files")) else ""
        para(f"[{f.get('severity') or ''}] {f.get('category') or ''}: {f.get('message') or ''}{extra}")

    if report.get("plan_markdown"):
        heading("Suggested split")
        for block in _split_markdown_blocks(str(report["plan_markdown"])):
            kind, text = block
            if kind in ("h1", "h2", "h3", "h4"):
                write(text.strip(), style="B", size=12 if kind == "h1" else 11)
            elif kind == "ul":
                for bullet in text:
                    para("• " + bullet)
            elif kind == "ol":
                for i, item in enumerate(text, start=1):
                    para(f"{i}. {item}")
            elif kind == "quote":
                para("> " + text.strip())
            elif kind == "code":
                for line in text.splitlines():
                    write(line, size=9)
            else:
                para(text.strip())


def _split_markdown_blocks(markdown: str) -> list[tuple[str, object]]:
    """Very small markdown splitter for the PDF renderer."""
    blocks: list[tuple[str, object]] = []
    current: list[str] = []
    mode = "p"
    for raw in markdown.split("\n"):
        line = raw.rstrip()
        trimmed = line.strip()
        if not trimmed:
            if mode in ("p", "h1", "h2", "h3", "h4", "quote") and current:
                blocks.append((mode, "\n".join(current)))
                current = []
            elif mode == "ul" and current:
                blocks.append(("ul", list(current)))
                current = []
            elif mode == "ol" and current:
                blocks.append(("ol", list(current)))
                current = []
            continue
        if trimmed == "```":
            if mode == "code":
                blocks.append(("code", "\n".join(current)))
                current = []
                mode = "p"
            else:
                if current:
                    blocks.append((mode, "\n".join(current)))
                    current = []
                mode = "code"
            continue
        if mode == "code":
            current.append(trimmed)
            continue
        m = __import__("re").match(r"^(#{1,6})\s+(.*)$", trimmed)
        if m:
            if current:
                blocks.append((mode, "\n".join(current)))
                current = []
            mode = f"h{min(6, len(m.group(1)))}"
            current.append(m.group(2))
            continue
        m = __import__("re").match(r"^>\s?(.*)$", trimmed)
        if m:
            if current and mode != "quote":
                blocks.append((mode, "\n".join(current)))
                current = []
            mode = "quote"
            current.append(m.group(1))
            continue
        m = __import__("re").match(r"^[-*]\s+(.*)$", trimmed)
        if m:
            if current and mode != "ul":
                blocks.append((mode, "\n".join(current)))
                current = []
            mode = "ul"
            current.append(m.group(1))
            continue
        m = __import__("re").match(r"^(\d+)[.)]\s+(.*)$", trimmed)
        if m:
            if current and mode != "ol":
                blocks.append((mode, "\n".join(current)))
                current = []
            mode = "ol"
            current.append(m.group(2))
            continue
        if current and mode not in ("ul", "ol", "code"):
            mode = "p"
        current.append(trimmed)
    if current:
        blocks.append((mode, list(current) if mode in ("ul", "ol") else "\n".join(current)))
    return blocks