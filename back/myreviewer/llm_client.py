"""Thin OpenAI-compatible chat client used by every analysis stage.

NVIDIA NIM serves an OpenAI-compatible surface, so the stock `openai` SDK is
used with a configurable `base_url`. The free tier is rate-limited
(~25-40 req/min), so this client retries 429 / transient errors with
exponential backoff + jitter. A per-request timeout (90 s) keeps a hung or
silent model from blocking a pipeline or benchmark run forever.

A `MockLLMClient` is included so the whole pipeline can be exercised locally
(and in tests) without any API key.
"""

from __future__ import annotations

import random
import time
from typing import Protocol

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class NitroClient:
    """Chat completion client for any OpenAI-compatible endpoint."""

    MAX_ATTEMPTS = 3
    REQUEST_TIMEOUT = 120.0

    def __init__(self, api_key: str, base_url: str, model: str,
                 temperature: float = 0.0, max_tokens: int = 4096):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = OpenAI(base_url=base_url, api_key=api_key)

    def _backoff(self, attempt: int) -> float:
        return min(60.0, 2 ** attempt + random.uniform(0, 1))

    def complete(self, system: str, user: str) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_error: Exception | None = None
        for attempt in range(self.MAX_ATTEMPTS):
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    timeout=self.REQUEST_TIMEOUT,
                )
                content = response.choices[0].message.content
                return content or ""
            except (RateLimitError, APITimeoutError) as exc:
                # Free-tier rate limits and slow/droppy responses are transient:
                # back off and retry.
                last_error = exc
            except APIConnectionError as exc:
                last_error = exc
            except APIStatusError as exc:
                if getattr(exc, "status_code", None) == 429:
                    last_error = exc
                else:
                    raise
            except Exception as exc:  # noqa: BLE001 - treat unexpected as transient
                last_error = exc
            time.sleep(self._backoff(attempt))
        raise RuntimeError(f"LLM request failed after {self.MAX_ATTEMPTS} "
                           f"attempts: {last_error}")


class MockLLMClient:
    """Deterministic fake for local verification of parsing/pipeline logic."""

    def __init__(self, model: str = "mock"):
        self.model = model

    def complete(self, system: str, user: str) -> str:
        return MOCK_RESPONSE


MOCK_RESPONSE = (
    "SUMMARY: The change bundles an avatar-upload feature, a query performance "
    "fix, and a utility refactor with a dependency bump, all in one pull "
    "request.\n\n"
    "CONCERNS:\n"
    "1. TITLE: add user avatar upload\n"
    "   RATIONALE: These files implement the storage and display of avatars.\n"
    "   FILES: users/uploads.py, users/views.py, users/templates/avatar.html\n"
    "   CHANGE_TYPE: add_only\n"
    "   MIXED: no\n"
    "   MIXED_NOTE: none\n"
    "2. TITLE: fix slow orders query\n"
    "   RATIONALE: The query and its migration were changed to add an index "
    "and reduce N+1 fetches.\n"
    "   FILES: orders/queries.py, orders/migrations/0042_order_lineitem_index.py\n"
    "   CHANGE_TYPE: modify\n"
    "   MIXED: no\n"
    "   MIXED_NOTE: none\n"
    "3. TITLE: refactor utils and bump storage dep\n"
    "   RATIONALE: Helper code moved out of views and the storage dependency "
    "version changed, touching unrelated flows.\n"
    "   FILES: core/utils.py, users/views.py, requirements.txt, pyproject.toml\n"
    "   CHANGE_TYPE: modify\n"
    "   MIXED: yes\n"
    "   MIXED_NOTE: users/views.py mixes avatar upload with the utils refactor, "
    "and the dependency bumps ride along.\n"
    "\n"
    "CHANGE_LOG:\n"
    "AREA 1: add user avatar upload\n"
    "OLD: User profiles rendered a plain text link; uploads were handled on the "
    "client with no storage abstraction, so files were not persisted server-side.\n"
    "NEW: A dedicated users/uploads module persists the file, the view returns "
    "the stored storage key, and the avatar template renders the uploaded image.\n"
    "IMPACT: Users get persistent avatars; the storage dependency now affects "
    "runtime behaviour and must be configured in the deployment.\n"
    "FILES: users/uploads.py, users/views.py, users/templates/avatar.html\n"
    "AREA 2: fix slow orders query\n"
    "OLD: orders/queries.py ran N+1 queries on the line items and relied on the "
    "default table without a covering index.\n"
    "NEW: The listing query is rewritten to prefetch line items, and migration "
    "0042 adds an index on the line-item table.\n"
    "IMPACT: Order listing is faster on large accounts; requires the new "
    "migration to be applied before rollout.\n"
    "FILES: orders/queries.py, orders/migrations/0042_order_lineitem_index.py\n"
    "AREA 3: refactor utils and bump storage dep\n"
    "OLD: Utility helpers lived in users/views.py and the storage package was "
    "pinned to an old version.\n"
    "NEW: Helpers moved to core/utils.py and the storage dependency is upgraded "
    "in requirements.txt and pyproject.toml.\n"
    "IMPACT: No behaviour change expected, but the dependency bump touches "
    "unrelated flows and should be verified independently.\n"
    "FILES: core/utils.py, users/views.py, requirements.txt, pyproject.toml\n"
    "\n"
    "POST_REVIEW:\n"
    "FINDING 1: important\n"
    "TITLE: awaiting storage key breaks error path\n"
    "BODY: The new upload view awaits the storage write in the success path but "
    "shares the legacy error template, so users get a stale message if storage "
    "fails. Handle the failure branch explicitly.\n"
    "FILES: users/views.py, users/uploads.py\n"
    "FINDING 2: minor\n"
    "TITLE: mixed-concern file not split\n"
    "BODY: users/views.py still mixes the avatar feature with the utils "
    "refactor; the dependency bump in the same PR makes bisecting a regression "
    "harder than needed.\n"
    "FILES: users/views.py\n"
    "POST_REVIEW_VERDICT: The new code is functional and the orders query fix "
    "is solid, but the mixed users/views.py and the storage error path should "
    "be addressed before merge; splitting the PR would make the review much "
    "easier.\n"
    "\n"
    "BUGS:\n"
    "BUG 1:\n"
    "SEVERITY: critical\n"
    "TYPE: error-handling\n"
    "TITLE: storage failure path loses the uploaded file silently\n"
    "LOCATION: users/uploads.py:27\n"
    "DETAIL: The upload view returns the storage key on success, but a storage "
    "failure falls through to the legacy error template and no retry is "
    "offered, so users see a stale message.\n"
    "FIX: Catch the storage exception, surface the real error, and offer a "
    "retry path.\n"
    "BUG 2:\n"
    "SEVERITY: minor\n"
    "TYPE: performance\n"
    "TITLE: avatar rendering re-reads the same blob per request\n"
    "LOCATION: users/views.py:44\n"
    "DETAIL: Every render fetches the avatar blob even when the browser is "
    "already holding it; add cache headers.\n"
    "FIX: Set Cache-Control on the avatar response.\n"
    "\n"
    "REQUIREMENTS:\n"
    "REQ 1:\n"
    "REQUIREMENT: users can upload and persist a profile avatar\n"
    "STATUS: satisfied\n"
    "EVIDENCE: users/uploads.py persists the file and the view returns the "
    "stored storage key.\n"
    "REQ 2:\n"
    "REQUIREMENT: orders listing must stay fast for large accounts\n"
    "STATUS: satisfied\n"
    "EVIDENCE: migration 0042 adds the covering index and the query now "
    "prefetches line items.\n"
    "REQ 3:\n"
    "REQUIREMENT: dependency upgrades must not change runtime behaviour\n"
    "STATUS: unverified\n"
    "EVIDENCE: the storage dependency bump has no associated behaviour test "
    "in the diff.\n"
    "REQUIREMENTS_VERDICT: The avatar upload and orders performance "
    "requirements are met and grounded in code; the dependency-upgrade "
    "requirement cannot be verified from the diff. No hard incompatibility "
    "found, but the error path should be fixed before merge.\n"
    "MERGE_READINESS: fix-before-merge\n"
)