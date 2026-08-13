"""Daily pipeline: fetch arXiv announcements, summarize, write data/<date>.json.

Usage:
    python -m digest.pipeline                     # full daily run
    python -m digest.pipeline --limit 5           # small end-to-end test
    python -m digest.pipeline --skip-summarize    # fetch only, no API calls
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .claude_cli import summarize_papers_cli
from .fetch import fetch_papers
from .highlights import annotate_highlights
from .summarize import MODEL, summarize_papers

DEFAULT_CATEGORIES = ["cs.AI", "cs.LG", "cs.CL"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the daily paper digest data file.")
    parser.add_argument("--categories", default=",".join(DEFAULT_CATEGORIES),
                        help="comma-separated arXiv categories (default: %(default)s)")
    parser.add_argument("--limit", type=int, default=None,
                        help="only process the first N papers (for testing)")
    parser.add_argument("--engine", choices=["api", "claude-cli"], default="api",
                        help="summarizer backend: 'api' uses the Claude API Batches endpoint "
                             "(needs ANTHROPIC_API_KEY, pay per token); 'claude-cli' uses headless "
                             "Claude Code on this machine's login (covered by a Pro/Max subscription)")
    parser.add_argument("--skip-summarize", action="store_true",
                        help="fetch and write the data file without calling Claude")
    parser.add_argument("--skip-highlights", action="store_true",
                        help="skip the highlights pass (HF cross-reference + deep summaries)")
    parser.add_argument("--max-highlights", type=int, default=10)
    parser.add_argument("--no-cross", action="store_true",
                        help="exclude cross-listed papers, keep only primary announcements")
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parent.parent / "data"),
                        help="directory for the daily JSON file (default: %(default)s)")
    args = parser.parse_args()

    categories = [c.strip() for c in args.categories.split(",") if c.strip()]
    date, papers = fetch_papers(categories, include_cross=not args.no_cross)

    if not papers:
        # arXiv makes no announcements on weekends and holidays.
        print("no new announcements in the feeds; nothing to do")
        return 0

    if args.limit:
        papers = papers[: args.limit]
    print(f"{len(papers)} papers announced {date} across {', '.join(categories)}")

    if args.skip_summarize:
        summaries = {}
    elif args.engine == "claude-cli":
        summaries = summarize_papers_cli(papers)
    else:
        summaries = summarize_papers(papers)

    for paper in papers:
        result = summaries.get(paper["id"], {})
        paper["summary"] = result.get("summary")
        paper["topic"] = result.get("topic")
        if "error" in result:
            paper["summary_error"] = result["error"]

    if not args.skip_summarize and not args.skip_highlights:
        try:
            annotate_highlights(papers, date, engine=args.engine, max_highlights=args.max_highlights)
        except Exception as error:  # noqa: BLE001 - highlights are additive, never fatal
            print(f"warning: highlights pass failed: {error}", file=sys.stderr)

    failed = [p["id"] for p in papers if p.get("summary_error")]
    if failed:
        print(f"warning: {len(failed)} papers failed to summarize: {', '.join(failed[:10])}", file=sys.stderr)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{date}.json"
    output_path.write_text(json.dumps({
        "date": date,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "categories": categories,
        "engine": "none" if args.skip_summarize else args.engine,
        "model": MODEL if args.engine == "api" else "claude-code-default",
        "paper_count": len(papers),
        "papers": papers,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {output_path} ({len(papers)} papers, {len(failed)} failures)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
