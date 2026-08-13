"""Pick the day's highlight papers and write deeper summaries for them.

Selection combines two signals: papers the Hugging Face community surfaced
(ranked by upvotes), topped up by a model pick over the remaining primary
("new") announcements. Each highlight then gets a longer treatment written
from the paper's full text when arXiv serves an HTML rendering, otherwise
from the abstract.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from html.parser import HTMLParser

from .hf import fetch_hf_upvotes

FULLTEXT_URL = "https://arxiv.org/html/{paper_id}"
FULLTEXT_MAX_CHARS = 60_000
ARXIV_FETCH_GAP_SECONDS = 3  # arXiv asks automated clients to stay around 1 request / 3s
USER_AGENT = "ai-paper-digest/0.1 (daily research digest; contact: alx.marcu@gmail.com)"

PICK_PROMPT = (
    "You are selecting highlights for a public daily digest of new AI research papers. "
    "From the numbered list below, pick the {count} papers most worth a technical reader's "
    "attention today — favor novel methods, strong or surprising results, and broad relevance "
    "over incremental or narrow work. Output ONLY a JSON array of the chosen paper ids as "
    'strings, e.g. ["2508.01234", "2508.05678"]. No commentary.\n\n{listing}'
)

DEEP_PROMPT = (
    "You write the highlights section of a public daily digest of AI research papers. "
    "Using the material below, write a four-to-six sentence treatment of the paper for a "
    "technical reader: the problem and why it matters, the core idea or method, the main "
    "results with concrete numbers where given, and any notable limitations. Plain prose, "
    "no headings or bullets, do not repeat the title, do not open with 'This paper'.\n\n"
    "Title: {title}\n\n{body}"
)


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "nav", "header", "footer"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def _fetch_fulltext(paper_id: str) -> str | None:
    request = urllib.request.Request(
        FULLTEXT_URL.format(paper_id=paper_id), headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            html = response.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - not every paper has an HTML rendering
        return None
    extractor = _TextExtractor()
    extractor.feed(html)
    text = re.sub(r"\s+", " ", " ".join(extractor.parts)).strip()
    return text[:FULLTEXT_MAX_CHARS] if len(text) > 500 else None


def _get_runner(engine: str):
    if engine == "claude-cli":
        from .claude_cli import run_prompt
        return run_prompt
    from .summarize import run_prompt_api
    return run_prompt_api


def _model_pick(papers: list[dict], count: int, run) -> list[str]:
    listing = "\n".join(
        f"{index}. [{paper['id']}] {paper['title']} ({paper.get('topic') or 'unclassified'})"
        for index, paper in enumerate(papers, start=1)
    )
    try:
        response = run(PICK_PROMPT.format(count=count, listing=listing))
        match = re.search(r"\[.*\]", response, re.DOTALL)
        picked = json.loads(match.group(0)) if match else []
    except Exception as error:  # noqa: BLE001
        print(f"highlight model-pick failed, continuing with HF picks only: {error}")
        return []
    valid = {p["id"] for p in papers}
    return [pid for pid in picked if pid in valid][:count]


def annotate_highlights(
    papers: list[dict],
    digest_date: str,
    engine: str = "claude-cli",
    max_highlights: int = 10,
    deep: bool = True,
) -> None:
    """Mark highlight papers in place; add hf_upvotes and highlight_summary."""
    run = _get_runner(engine)
    by_id = {p["id"]: p for p in papers}

    upvotes = fetch_hf_upvotes(digest_date)
    for paper_id, votes in upvotes.items():
        if paper_id in by_id:
            by_id[paper_id]["hf_upvotes"] = votes

    hf_picks = sorted(
        (p for p in papers if p.get("hf_upvotes")),
        key=lambda p: -p["hf_upvotes"],
    )[:max_highlights]
    selected = [p["id"] for p in hf_picks]

    if len(selected) < max_highlights:
        pool = [p for p in papers if p["announce_type"] == "new" and p["id"] not in selected]
        selected += _model_pick(pool, max_highlights - len(selected), run)

    print(f"highlights: {len(selected)} selected ({len(hf_picks)} via Hugging Face)")

    for paper_id in selected:
        paper = by_id[paper_id]
        paper["highlight"] = True
        if not deep:
            continue
        fulltext = _fetch_fulltext(paper_id)
        time.sleep(ARXIV_FETCH_GAP_SECONDS)
        body = (
            f"Full text (extracted, may be truncated):\n{fulltext}"
            if fulltext
            else f"Abstract:\n{paper['abstract']}"
        )
        try:
            paper["highlight_summary"] = run(DEEP_PROMPT.format(title=paper["title"], body=body)).strip()
        except Exception as error:  # noqa: BLE001 - fall back to the short summary in render
            print(f"deep summary failed for {paper_id}: {error}")
