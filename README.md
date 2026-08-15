<div align="center">

# 📄 AI Paper Digest

**Every new AI paper on arXiv, summarized — every weekday.**

### [**Read the latest digest →**](https://amarcu.github.io/ai-paper-digest/)

[Browse the archive](https://amarcu.github.io/ai-paper-digest/archive.html)
&nbsp;·&nbsp;
[Subscribe by RSS](https://amarcu.github.io/ai-paper-digest/feed.xml)

[![Deploy site](https://github.com/amarcu/ai-paper-digest/actions/workflows/deploy.yml/badge.svg)](https://github.com/amarcu/ai-paper-digest/actions/workflows/deploy.yml)

</div>

---

Each weekday, every paper newly announced in arXiv's `cs.AI`, `cs.LG` and
`cs.CL` categories — around 700 on a typical day — gets a short summary and a
topic tag, so a whole day of AI research can be scanned in minutes:

- **Highlights first.** About ten papers per day, drawn from the Hugging Face
  Daily Papers community picks and topped up by a model pick, each with a
  longer summary written from the paper's full text.
- **Everything else, grouped by topic.** LLMs, agents, reasoning, vision,
  safety, robotics and more — every entry links to its arXiv abstract page.
  Each paper is rated for breadth of interest, and narrower papers collapse
  into a compact title list per topic so a 400-paper day stays scannable.
  The rating is editorial — what this digest considers interesting, helpful,
  or educational — with community signals weighed strongly but not decisive;
  nothing is ever removed, only folded.
- **Follow along.** The [RSS feed](https://amarcu.github.io/ai-paper-digest/feed.xml)
  delivers each day's digest; the
  [archive](https://amarcu.github.io/ai-paper-digest/archive.html) keeps every
  past day.

Summaries are machine-generated (Claude) and may contain errors — the linked
paper is always authoritative.

## How it's made

A small Python pipeline runs each weekday morning:

1. **Fetch** (`digest/fetch.py`) — pulls `https://rss.arxiv.org/rss/<category>`
   per category, keeps announce types `new` and `cross`, and deduplicates
   papers listed in several feeds. Revisions of older papers are skipped.
2. **Summarize** (`digest/summarize.py`, `digest/claude_cli.py`) — every paper
   gets a 2–4 sentence summary plus a topic tag, via either headless Claude
   Code (`--engine claude-cli`, covered by a Claude subscription) or the
   Claude API Batches endpoint (`--engine api`, needs `ANTHROPIC_API_KEY`).
3. **Highlights** (`digest/highlights.py`) — cross-references Hugging Face
   Daily Papers for community signal, picks ~10 papers, and writes a deeper
   summary from each paper's full text when arXiv serves an HTML rendering.
4. **Store** (`digest/pipeline.py`) — writes `data/<date>.json`; the git
   history is the archive, there is no database.
5. **Render & publish** (`digest/render.py` + `templates/`) — builds the
   static site and RSS feed; GitHub Actions deploys it to Pages on push.

## Run your own

Fork it, enable Pages (Settings → Pages → Source: *GitHub Actions*), then pick
a mode:

- **Subscription mode** — a machine with Claude Code signed in runs the
  pipeline on a schedule and pushes the day's data; `deploy.yml` publishes the
  site on push. A launchd setup for macOS is included:

  ```sh
  cp scripts/com.amarcu.ai-paper-digest.plist ~/Library/LaunchAgents/
  launchctl load ~/Library/LaunchAgents/com.amarcu.ai-paper-digest.plist
  ```

  `scripts/run-daily.sh` reads `CLAUDE_CODE_OAUTH_TOKEN` (headless auth) and,
  optionally, `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` (failure
  notifications) from the `env` block of `~/.claude/settings.json`. Logs go to
  `~/Library/Logs/ai-paper-digest.log`.

- **API mode** — add an `ANTHROPIC_API_KEY` repository secret and `daily.yml`
  runs everything on a GitHub Actions cron (Mon–Fri 06:30 UTC), including the
  Pages deploy. Without the secret the workflow skips cleanly. Roughly $3/day
  at full volume with the batch discount.

Local development:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Small end-to-end test (5 papers):
.venv/bin/python -m digest.pipeline --limit 5 --engine claude-cli

# Fetch only, no Claude calls:
.venv/bin/python -m digest.pipeline --skip-summarize

# Render and preview the site:
.venv/bin/python -m digest.render
.venv/bin/python -m http.server -d site
```

Useful flags: `--categories cs.AI,cs.LG,cs.CL`, `--no-cross`, `--limit N`,
`--skip-highlights`, `--max-highlights N`.

## Data & licensing

- arXiv metadata (titles, abstracts, authors) is CC0; every digest entry links
  back to its arXiv abstract page, and no PDFs are stored or served.
- Thank you to arXiv for use of its open access interoperability. Full-text
  fetches for highlights respect arXiv's ~1 request / 3 s guidance.
