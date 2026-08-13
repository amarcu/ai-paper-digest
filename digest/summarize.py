"""Summarize papers with the Claude API via the Message Batches endpoint.

One batch request per paper (title + abstract in, structured JSON out). The
Batches API halves token cost and fits a non-interactive daily job; batches
usually complete in minutes, worst case 24 hours.
"""

from __future__ import annotations

import json
import time

from anthropic import Anthropic

MODEL = "claude-opus-5"
MAX_TOKENS = 2000

TOPICS = [
    "llms",
    "agents",
    "reasoning",
    "reinforcement-learning",
    "vision",
    "multimodal",
    "safety-and-alignment",
    "robotics",
    "theory",
    "applications",
    "other",
]

SYSTEM_PROMPT = (
    "You write entries for a public daily digest of new AI research papers. "
    "Given a paper's title and abstract, write a plain-language summary of two to four "
    "sentences covering what problem the paper tackles, the approach it takes, and the "
    "key findings or claims. Write for a technical reader who has not seen the paper: "
    "spell out acronyms on first use where the abstract defines them, do not repeat the "
    "title, do not open with 'This paper', and do not editorialize about importance. "
    "Also assign the single best-fitting topic tag."
)

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "Two to four sentence plain-language digest of the paper.",
        },
        "topic": {"type": "string", "enum": TOPICS},
    },
    "required": ["summary", "topic"],
    "additionalProperties": False,
}


def run_prompt_api(prompt: str, max_tokens: int = 4000) -> str:
    """One direct (non-batch) completion — used for the low-volume highlight calls.

    Includes the server-side fallback so a safety-classifier decline (possible on
    e.g. security papers) is re-served by another model instead of failing.
    """
    client = Anthropic()
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("request refused by safety classifiers")
    return next((block.text for block in response.content if block.type == "text"), "")


def _custom_id(paper_id: str) -> str:
    # Batch custom_ids allow only [a-zA-Z0-9_-]; arXiv ids contain a dot.
    return paper_id.replace(".", "-")


def _build_request(paper: dict) -> dict:
    return {
        "custom_id": _custom_id(paper["id"]),
        "params": {
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            "system": SYSTEM_PROMPT,
            "output_config": {
                "effort": "low",
                "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
            },
            "messages": [
                {
                    "role": "user",
                    "content": f"Title: {paper['title']}\n\nAbstract: {paper['abstract']}",
                }
            ],
        },
    }


def _parse_result(result) -> dict:
    if result.result.type != "succeeded":
        return {"error": result.result.type}
    message = result.result.message
    if message.stop_reason == "refusal":
        return {"error": "refusal"}
    text = next((block.text for block in message.content if block.type == "text"), "")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"error": f"unparseable_output:{message.stop_reason}"}
    return {"summary": parsed.get("summary"), "topic": parsed.get("topic")}


def summarize_papers(
    papers: list[dict],
    poll_seconds: int = 30,
    timeout_seconds: int = 4 * 60 * 60,
) -> dict[str, dict]:
    """Run one batch over all papers; return {paper_id: {summary, topic} | {error}}."""
    client = Anthropic()
    batch = client.messages.batches.create(requests=[_build_request(p) for p in papers])
    print(f"batch {batch.id}: {len(papers)} requests submitted")

    deadline = time.monotonic() + timeout_seconds
    while batch.processing_status != "ended":
        if time.monotonic() > deadline:
            raise TimeoutError(f"batch {batch.id} still {batch.processing_status} after {timeout_seconds}s")
        time.sleep(poll_seconds)
        batch = client.messages.batches.retrieve(batch.id)
        counts = batch.request_counts
        print(f"batch {batch.id}: {batch.processing_status} (processing={counts.processing} succeeded={counts.succeeded} errored={counts.errored})")

    id_by_custom = {_custom_id(p["id"]): p["id"] for p in papers}
    summaries: dict[str, dict] = {}
    for result in client.messages.batches.results(batch.id):
        paper_id = id_by_custom.get(result.custom_id)
        if paper_id:
            summaries[paper_id] = _parse_result(result)
    return summaries
