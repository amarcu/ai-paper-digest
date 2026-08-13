"""Summarize papers via headless Claude Code (``claude -p``).

Alternative to the API engine in summarize.py: runs on the machine's Claude
Code login, so a Claude Pro/Max subscription covers it with no per-token
billing. Papers are chunked (many abstracts per invocation) to keep the call
count low; usage draws from the subscription's rolling limits.
"""

from __future__ import annotations

import json
import re
import subprocess

from .summarize import TOPICS

CHUNK_SIZE = 20
CALL_TIMEOUT_SECONDS = 600

CHUNK_INSTRUCTIONS = (
    "You write entries for a public daily digest of new AI research papers. For each "
    "paper below, write a plain-language summary of two to four sentences covering what "
    "problem the paper tackles, the approach it takes, and the key findings or claims. "
    "Write for a technical reader who has not seen the paper: spell out acronyms on "
    "first use where the abstract defines them, do not repeat the title, do not open "
    "with 'This paper', and do not editorialize about importance. Within each summary "
    "use light inline Markdown: wrap the single most important finding or number in "
    "**bold**, and put names of models, methods, datasets, and benchmarks in "
    "`backticks`. No links, headers, or bullet lists inside summaries. Also assign each "
    f"paper the single best-fitting topic tag from: {', '.join(TOPICS)}.\n\n"
    "Output ONLY a JSON array with one object per paper, in the form "
    '{"id": "<paper id>", "summary": "<2-4 sentences>", "topic": "<tag>"}. '
    "No markdown fences, no commentary before or after the array.\n"
)


def _chunk_prompt(papers: list[dict]) -> str:
    parts = [CHUNK_INSTRUCTIONS]
    for index, paper in enumerate(papers, start=1):
        parts.append(
            f"Paper {index} (id: {paper['id']})\n"
            f"Title: {paper['title']}\n"
            f"Abstract: {paper['abstract']}\n"
        )
    return "\n".join(parts)


def _extract_json_array(text: str) -> list[dict]:
    # Tolerate a fenced or prefixed response despite the instructions.
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON array in response")
    return json.loads(match.group(0))


def run_prompt(prompt: str, timeout: int = CALL_TIMEOUT_SECONDS) -> str:
    """One headless Claude Code completion on this machine's login."""
    completed = subprocess.run(
        ["claude", "-p", "--output-format", "text"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"claude -p exited {completed.returncode}: {completed.stderr.strip()[:300]}")
    return completed.stdout


def _run_chunk(papers: list[dict]) -> dict[str, dict]:
    entries = _extract_json_array(run_prompt(_chunk_prompt(papers)))
    valid_ids = {p["id"] for p in papers}
    return {
        entry["id"]: {"summary": entry.get("summary"), "topic": entry.get("topic")}
        for entry in entries
        if isinstance(entry, dict) and entry.get("id") in valid_ids
    }


def summarize_papers_cli(papers: list[dict], chunk_size: int = CHUNK_SIZE) -> dict[str, dict]:
    """Summarize via ``claude -p``; return {paper_id: {summary, topic} | {error}}."""
    summaries: dict[str, dict] = {}
    chunks = [papers[i : i + chunk_size] for i in range(0, len(papers), chunk_size)]
    for number, chunk in enumerate(chunks, start=1):
        result: dict[str, dict] = {}
        error = None
        for _attempt in range(2):
            try:
                result = _run_chunk(chunk)
                error = None
                break
            except (RuntimeError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
                error = str(exc)[:200]
        for paper in chunk:
            summaries[paper["id"]] = result.get(paper["id"]) or {"error": error or "missing_from_response"}
        done = sum(1 for s in summaries.values() if "summary" in s)
        print(f"chunk {number}/{len(chunks)}: {done}/{len(summaries)} summarized")
    return summaries
