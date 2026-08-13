"""Fetch daily paper announcements from arXiv RSS feeds.

arXiv publishes one RSS feed per category (https://rss.arxiv.org/rss/<cat>),
refreshed once daily at midnight ET with that day's announcements. Each item
carries an ``arxiv:announce_type`` distinguishing genuinely new papers from
cross-listings and revisions of older papers.
"""

from __future__ import annotations

import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

FEED_URL = "https://rss.arxiv.org/rss/{category}"
USER_AGENT = "ai-paper-digest/0.1 (daily research digest; contact: alx.marcu@gmail.com)"

NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "arxiv": "http://arxiv.org/schemas/atom",
}

# Announce types worth digesting. "replace" / "replace-cross" are revisions of
# previously announced papers and are skipped.
NEW_TYPES = {"new", "cross"}


def _fetch_feed(category: str, retries: int = 3) -> ET.Element:
    url = FEED_URL.format(category=category)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return ET.fromstring(response.read())
        except Exception as error:  # noqa: BLE001 - retry any transient fetch failure
            last_error = error
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url} after {retries} attempts") from last_error


def _parse_item(item: ET.Element, category: str) -> dict | None:
    guid = item.findtext("guid", default="")
    match = re.search(r"(\d{4}\.\d{4,5})(v\d+)?$", guid)
    if not match:
        return None
    base_id = match.group(1)

    announce_type = item.findtext("arxiv:announce_type", default="", namespaces=NS)
    if announce_type not in NEW_TYPES:
        return None

    # description looks like: "arXiv:2608.07473v1 Announce Type: new \nAbstract: ..."
    description = item.findtext("description", default="")
    abstract = description.split("Abstract:", 1)[-1].strip() if "Abstract:" in description else description.strip()

    creators = item.findtext("dc:creator", default="", namespaces=NS)
    authors = [name.strip() for name in creators.split(",") if name.strip()]

    pub_date = None
    raw_date = item.findtext("pubDate")
    if raw_date:
        try:
            pub_date = parsedate_to_datetime(raw_date).date().isoformat()
        except (TypeError, ValueError):
            pub_date = None

    return {
        "id": base_id,
        "title": re.sub(r"\s+", " ", item.findtext("title", default="")).strip(),
        "link": item.findtext("link", default=f"https://arxiv.org/abs/{base_id}"),
        "abstract": abstract,
        "authors": authors,
        "categories": [c.text for c in item.findall("category") if c.text],
        "announce_type": announce_type,
        "feed_category": category,
        "announced": pub_date,
    }


def fetch_papers(categories: list[str], include_cross: bool = True) -> tuple[str | None, list[dict]]:
    """Fetch and deduplicate the day's papers across the given category feeds.

    Returns (announcement_date, papers). The date is taken from the feed items
    themselves so the output file is named for arXiv's announcement day, not
    the day the job happened to run.
    """
    papers: dict[str, dict] = {}
    for category in categories:
        channel = _fetch_feed(category).find("channel")
        if channel is None:
            continue
        for item in channel.findall("item"):
            paper = _parse_item(item, category)
            if paper is None:
                continue
            if not include_cross and paper["announce_type"] != "new":
                continue
            existing = papers.get(paper["id"])
            if existing is None:
                papers[paper["id"]] = paper
            elif existing["announce_type"] == "cross" and paper["announce_type"] == "new":
                # The same paper can appear as "new" in its primary category
                # and "cross" elsewhere; prefer the primary announcement.
                papers[paper["id"]] = paper

    ordered = sorted(papers.values(), key=lambda p: p["id"])
    date = next((p["announced"] for p in ordered if p["announced"]), None)
    return date, ordered
