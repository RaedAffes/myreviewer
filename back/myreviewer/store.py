"""Report storage for the dashboard API.

Stores analysis reports as JSON files under <backend>/data/reports so
the web dashboard can list and re-read past analyses without re-running the
LLM. Zero external dependencies (plain stdlib json + pathlib).

Layout
-----
    data/reports/<report_id>.json      full report payload (json-serializable)
    data/reports/<report_id>.meta.json  { report_id, title, owner, repo, pr,
                                         verdict, analyzed_at_iso, concerns, flags }
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

def _data_dir() -> Path:
    """Writable runtime-data directory.

    Prefers the DATA_DIR env var. On Vercel (detected via the auto-injected
    VERCEL env var) only /tmp is writable, so fall back there; otherwise use
    <backend>/data so no setup is needed anywhere.
    """
    env_dir = os.environ.get("DATA_DIR")
    if env_dir:
        return Path(env_dir)
    if os.environ.get("VERCEL"):
        return Path("/tmp/myreviewer-data")
    return Path(__file__).resolve().parent.parent / "data"


DATA_ROOT = _data_dir() / "reports"


class ReportNotFoundError(Exception):
    pass


def _slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-")
    return value or "anonymous"


def make_report_id(owner: str = "", repo: str = "", pr_number: int | None = None) -> str:
    parts = [p for p in (_slug(owner), _slug(repo)) if p]
    if pr_number is not None:
        parts.append(f"pr{pr_number}")
    return "-".join(parts) or f"pr-{int(datetime.now(timezone.utc).timestamp())}"


def _meta_owner(meta: dict) -> str:
    """GitHub login (or system) that owns a report's analysis."""
    owner = meta.get("owner") or ""
    if not owner:
        repo = meta.get("repo", "")
        owner = repo.split("/", 1)[0] if "/" in repo else ""
    return owner


def save_report(report: dict, report_id: str | None = None) -> str:
    """Persist a report payload dict; returns its report_id."""
    report_id = report_id or make_report_id(
        repo=report.get("repo", ""), pr_number=report.get("pr_number"),
    )
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    payload = dict(report)
    payload["report_id"] = report_id
    payload["analyzed_at_iso"] = report.get(
        "analyzed_at_iso", datetime.now(timezone.utc).isoformat(),
    )
    payload.setdefault("owner", _meta_owner(payload))
    (DATA_ROOT / f"{report_id}.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8",
    )
    meta = {
        "report_id": report_id,
        "title": report.get("title", ""),
        "owner": payload["owner"],
        "repo": report.get("repo", ""),
        "pr_number": report.get("pr_number"),
        "verdict": report.get("verdict", ""),
        "concerns": len(report.get("concerns", [])),
        "flags": len(report.get("flags", [])),
        "merge_readiness": report.get("merge_readiness", ""),
        "model": report.get("model", ""),
        "analyzed_at_iso": payload["analyzed_at_iso"],
    }
    (DATA_ROOT / f"{report_id}.meta.json").write_text(
        json.dumps(meta, indent=2, default=str), encoding="utf-8",
    )
    return report_id


def load_report(report_id: str) -> dict:
    path = DATA_ROOT / f"{report_id}.json"
    if not path.exists():
        raise ReportNotFoundError(report_id)
    return json.loads(path.read_text(encoding="utf-8"))


def report_owner(report_id: str) -> str:
    """Effective owner login for an existing report (for access checks)."""
    meta_path = DATA_ROOT / f"{report_id}.meta.json"
    if meta_path.exists():
        return _meta_owner(json.loads(meta_path.read_text(encoding="utf-8")))
    return ""


def list_reports(owner: str = "") -> list[dict]:
    if not DATA_ROOT.exists():
        return []
    metas = []
    for path in sorted(DATA_ROOT.glob("*.meta.json"), reverse=True):
        metas.append(json.loads(path.read_text(encoding="utf-8")))
    if owner:
        metas = [m for m in metas if _meta_owner(m) == owner]
    metas.sort(key=lambda m: m.get("analyzed_at_iso", ""), reverse=True)
    return metas


def delete_report(report_id: str) -> None:
    for suffix in (".json", ".meta.json"):
        path = DATA_ROOT / f"{report_id}{suffix}"
        if path.exists():
            path.unlink()


