"""Render the static site from data/*.json.

Outputs to site/ (gitignored — the GitHub Actions workflow deploys it as a
Pages artifact; locally, open site/index.html or `python -m http.server -d site`).

Usage:
    python -m digest.render [--base-url URL]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE_URL = "https://amarcu.github.io/ai-paper-digest/"

TOPIC_LABELS = {
    "llms": "Large Language Models",
    "agents": "Agents",
    "reasoning": "Reasoning",
    "reinforcement-learning": "Reinforcement Learning",
    "vision": "Vision",
    "multimodal": "Multimodal",
    "safety-and-alignment": "Safety & Alignment",
    "robotics": "Robotics",
    "theory": "Theory",
    "applications": "Applications",
    "other": "Other",
    "unclassified": "Unclassified",
}


def _human_date(iso_date: str) -> str:
    return date_type.fromisoformat(iso_date).strftime("%A, %B %-d, %Y")


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"(])")

_MD_CODE = re.compile(r"`([^`]+)`")
_MD_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_MD_EM = re.compile(r"(?<!\*)\*([^*\s][^*]*)\*(?!\*)")


def _md_inline(text: str | None) -> Markup:
    """Render the light inline Markdown the summarizers emit (bold, code,
    emphasis) — everything else is escaped, so stray HTML in a summary can
    never reach the page."""
    if not text:
        return Markup("")
    html = str(escape(text))
    html = _MD_CODE.sub(r"<code>\1</code>", html)
    html = _MD_BOLD.sub(r"<strong>\1</strong>", html)
    html = _MD_EM.sub(r"<em>\1</em>", html)
    return Markup(html)


def _summary_blocks(text: str) -> list[dict]:
    """Break a highlight deep summary into renderable blocks.

    New-style summaries arrive as an overview paragraph plus '- ' bullet
    lines; older ones are a single paragraph, which gets regrouped into
    two-sentence paragraphs so it reads as prose rather than a wall.
    """
    blocks: list[dict] = []
    for line in (l.strip() for l in text.splitlines() if l.strip()):
        if line.startswith("- "):
            if not blocks or blocks[-1]["kind"] != "ul":
                blocks.append({"kind": "ul", "items": []})
            blocks[-1]["items"].append(line[2:].strip())
        else:
            blocks.append({"kind": "p", "text": line})
    # Drop a leaked conversational preamble ("Here's the digest treatment:").
    if len(blocks) > 1 and blocks[0]["kind"] == "p" \
            and len(blocks[0]["text"]) < 60 and blocks[0]["text"].endswith(":"):
        blocks = blocks[1:]
    if len(blocks) == 1 and blocks[0]["kind"] == "p":
        sentences = _SENTENCE_SPLIT.split(blocks[0]["text"])
        if len(sentences) > 2:
            blocks = [{"kind": "p", "text": " ".join(sentences[i:i + 2])}
                      for i in range(0, len(sentences), 2)]
    return blocks


def _load_days(data_dir: Path) -> list[dict]:
    days = []
    for path in sorted(data_dir.glob("*.json")):
        day = json.loads(path.read_text(encoding="utf-8"))
        day["date_human"] = _human_date(day["date"])
        for paper in day["papers"]:
            if paper.get("highlight_summary"):
                paper["highlight_blocks"] = _summary_blocks(paper["highlight_summary"])
        days.append(day)
    return days


# Papers rated below this breadth-of-interest score collapse into a compact
# title-only list per topic; unscored papers (older data) stay featured.
FOLD_BELOW = 3


def _group_topics(papers: list[dict]) -> list[tuple[str, list[dict], list[dict]]]:
    groups: dict[str, list[dict]] = {}
    for paper in papers:
        groups.setdefault(paper.get("topic") or "unclassified", []).append(paper)
    # Largest topics first; ties alphabetical for a stable page layout.
    ordered = sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
    return [
        (
            topic,
            [p for p in group if (p.get("interest") or FOLD_BELOW) >= FOLD_BELOW],
            [p for p in group if (p.get("interest") or FOLD_BELOW) < FOLD_BELOW],
        )
        for topic, group in ordered
    ]


# Volume thresholds for the calendar heat levels (papers per day).
CAL_LEVELS = [(500, 4), (300, 3), (150, 2), (1, 1)]


def _cal_level(count: int) -> int:
    for threshold, level in CAL_LEVELS:
        if count >= threshold:
            return level
    return 0


def _build_calendar(days: list[dict], today: date_type) -> dict:
    """GitHub-style activity grid: one column per week (Monday-start), cells
    colored by paper count and linking to that day's archive page."""
    counts = {d["date"]: d["paper_count"] for d in days}
    first = date_type.fromisoformat(days[0]["date"])
    this_monday = today - timedelta(days=today.weekday())
    first_monday = first - timedelta(days=first.weekday())
    weeks_count = min(53, max(12, (this_monday - first_monday).days // 7 + 1))
    start = this_monday - timedelta(weeks=weeks_count - 1)

    weeks, month_labels = [], []
    for index in range(weeks_count):
        week_start = start + timedelta(weeks=index)
        month = week_start.strftime("%b")
        previous = (week_start - timedelta(weeks=1)).strftime("%b")
        month_labels.append(month if index == 0 or previous != month else "")
        cells = []
        for offset in range(7):
            day = week_start + timedelta(days=offset)
            count = counts.get(day.isoformat(), 0)
            pretty = day.strftime("%b %-d")
            if count:
                label = f"{count} papers · {pretty}"
            elif day > today:
                label = pretty
            else:
                label = f"No digest · {pretty}"
            cells.append({
                "date": day.isoformat(),
                "count": count,
                "level": _cal_level(count),
                "future": day > today,
                "label": label,
            })
        weeks.append(cells)

    # Labels overflow their 12px column; drop an earlier label when the next
    # month starts within two columns so they can't overlap ("MayJun").
    last = None
    for index, label in enumerate(month_labels):
        if not label:
            continue
        if last is not None and index - last < 3:
            month_labels[last] = ""
        last = index
    return {"weeks": weeks, "month_labels": month_labels}


def _feed_description(day: dict) -> str:
    highlights = [p for p in day["papers"] if p.get("highlight")]
    leads = highlights or day["papers"]
    titles = "; ".join(p["title"] for p in leads[:5])
    return f"{day['paper_count']} new AI papers on arXiv. Leading entries: {titles}"


def render_site(data_dir: Path, out_dir: Path, base_url: str) -> int:
    days = _load_days(data_dir)
    if not days:
        print("no data files to render", file=sys.stderr)
        return 1

    env = Environment(
        loader=FileSystemLoader(REPO_ROOT / "templates"),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["md"] = _md_inline
    digest_template = env.get_template("digest.html")
    archive_template = env.get_template("archive.html")
    feed_template = env.get_template("feed.xml")

    (out_dir / "archive").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO_ROOT / "templates" / "style.css", out_dir / "style.css")

    calendar = _build_calendar(days, date_type.today())

    def digest_context(day: dict, index: int, root: str) -> dict:
        return {
            "day": day,
            "root": root,
            "calendar": calendar,
            "topics": _group_topics(day["papers"]),
            "highlights": [p for p in day["papers"] if p.get("highlight")],
            "topic_labels": TOPIC_LABELS,
            "prev_date": days[index - 1]["date"] if index > 0 else None,
            "next_date": days[index + 1]["date"] if index < len(days) - 1 else None,
        }

    for index, day in enumerate(days):
        page = digest_template.render(**digest_context(day, index, root="../"))
        (out_dir / "archive" / f"{day['date']}.html").write_text(page, encoding="utf-8")

    latest_index = len(days) - 1
    (out_dir / "index.html").write_text(
        digest_template.render(**digest_context(days[latest_index], latest_index, root="")),
        encoding="utf-8",
    )

    newest_first = list(reversed(days))
    (out_dir / "archive.html").write_text(
        archive_template.render(days=newest_first, root="", calendar=calendar),
        encoding="utf-8",
    )

    for day in newest_first:
        day["feed_description"] = _feed_description(day)
        announced = datetime.combine(
            date_type.fromisoformat(day["date"]), datetime.min.time(), tzinfo=timezone.utc
        ).replace(hour=7)
        day["pub_date"] = format_datetime(announced)
    (out_dir / "feed.xml").write_text(
        feed_template.render(
            days=newest_first[:30],
            base_url=base_url,
            build_date=format_datetime(datetime.now(timezone.utc)),
        ),
        encoding="utf-8",
    )

    print(f"rendered {len(days)} day(s) to {out_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the digest static site.")
    parser.add_argument("--data-dir", default=str(REPO_ROOT / "data"))
    parser.add_argument("--out", default=str(REPO_ROOT / "site"))
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help="public URL the site is served from, used in feed.xml")
    args = parser.parse_args()
    base_url = args.base_url if args.base_url.endswith("/") else args.base_url + "/"
    return render_site(Path(args.data_dir), Path(args.out), base_url)


if __name__ == "__main__":
    sys.exit(main())
