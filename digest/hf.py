"""Hugging Face Daily Papers — community-curated signal for highlights.

Public, unauthenticated endpoint: one JSON list per date of the ~10-50 papers
the HF community surfaced that day, each keyed by arXiv id with an upvote
count. Papers announced on arXiv typically appear on HF the same day or the
day after, so we query the digest date and the following day and merge.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import date as date_type
from datetime import timedelta

API_URL = "https://huggingface.co/api/daily_papers?date={date}&limit=100"
USER_AGENT = "ai-paper-digest/0.1 (daily research digest; contact: alx.marcu@gmail.com)"


def _fetch_day(day: str) -> list[dict]:
    request = urllib.request.Request(API_URL.format(date=day), headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except Exception:  # noqa: BLE001 - HF is a nice-to-have signal, never fatal
        return []
    return payload if isinstance(payload, list) else []


def fetch_hf_upvotes(digest_date: str) -> dict[str, int]:
    """Return {arxiv_id: upvotes} for papers HF featured on or right after the date."""
    next_day = (date_type.fromisoformat(digest_date) + timedelta(days=1)).isoformat()
    upvotes: dict[str, int] = {}
    for day in (digest_date, next_day):
        for entry in _fetch_day(day):
            paper = entry.get("paper") or {}
            paper_id = paper.get("id")
            if paper_id:
                upvotes[paper_id] = max(upvotes.get(paper_id, 0), paper.get("upvotes") or 0)
    return upvotes
