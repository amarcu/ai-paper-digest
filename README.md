# ai-paper-digest

Daily digest of new AI research papers, published at
`https://amarcu.github.io/ai-paper-digest/`. A pipeline fetches the day's
announcements from arXiv's RSS feeds, summarizes each paper with Claude,
cross-references Hugging Face Daily Papers to pick highlights, and renders a
static site (with its own RSS feed) deployed to GitHub Pages.

## How it works

1. **Fetch** — `digest/fetch.py` pulls `https://rss.arxiv.org/rss/<category>`
   for each configured category (default `cs.AI`, `cs.LG`, `cs.CL`), keeps
   items with announce type `new` or `cross`, and deduplicates papers that
   appear in several feeds (~700/day; `--no-cross` halves it). Revisions of
   older papers are skipped.
2. **Summarize** — every paper gets a 2–4 sentence summary plus a topic tag
   (structured output), through one of two engines:
   - `--engine claude-cli` — headless Claude Code (`claude -p`) on this
     machine's login, ~20 abstracts per call. **Covered by a Claude Pro/Max
     subscription**; draws from its rolling usage limits (~37 calls for a full
     day).
   - `--engine api` — the Claude API Batches endpoint (`claude-opus-5`, 50%
     batch discount). Needs `ANTHROPIC_API_KEY`; roughly $3/day at full
     volume.
3. **Highlights** — `digest/highlights.py` picks ~10 papers per day: Hugging
   Face Daily Papers community picks first (`digest/hf.py`, ranked by
   upvotes), topped up by a model pick over the remaining primary
   announcements. Each highlight gets a longer treatment written from the
   paper's full text when arXiv serves an HTML rendering.
4. **Store** — `digest/pipeline.py` writes `data/<announcement-date>.json`.
   The git history is the archive; there is no database.
5. **Render & publish** — `digest/render.py` + `templates/` produce `site/`:
   the latest digest grouped by topic with highlights on top, per-date archive
   pages, and `feed.xml` (RSS of the digests, so readers can subscribe).
   GitHub Actions deploys `site/` to Pages.

## Two ways to run it daily

**Subscription mode (recommended, no API cost).** A Mac with Claude Code
signed in runs the pipeline via launchd, pushes the day's `data/` commit, and
`deploy.yml` renders + publishes Pages on push (rendering needs no key):

```sh
cp scripts/com.amarcu.ai-paper-digest.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.amarcu.ai-paper-digest.plist
```

`scripts/run-daily.sh` reads `CLAUDE_CODE_OAUTH_TOKEN` (headless auth) and,
optionally, `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` (failure notifications)
from the `env` block of `~/.claude/settings.json`. Logs go to
`~/Library/Logs/ai-paper-digest.log`.

**API mode (cloud, machine-independent).** Add an `ANTHROPIC_API_KEY`
repository secret and `daily.yml` runs the whole thing on a GitHub Actions
cron (Mon–Fri 06:30 UTC), including the Pages deploy. Without the secret that
workflow skips cleanly, so both modes can coexist.

**One-time Pages setup:** repo Settings → Pages → Source: *GitHub Actions*.

## Running locally

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Small end-to-end test on the subscription (5 papers):
.venv/bin/python -m digest.pipeline --limit 5 --engine claude-cli

# Fetch only, no Claude calls:
.venv/bin/python -m digest.pipeline --skip-summarize

# Render and preview the site:
.venv/bin/python -m digest.render
.venv/bin/python -m http.server -d site
```

Useful flags: `--categories cs.AI,cs.LG,cs.CL`, `--no-cross`, `--limit N`,
`--skip-highlights`, `--max-highlights N`.

## Data & licensing notes

- arXiv metadata (titles, abstracts, authors) is CC0; every digest entry
  links back to its arXiv abstract page, and no PDFs are stored or served.
- Thank you to arXiv for use of its open access interoperability. Full-text
  fetches for highlights respect arXiv's ~1 request / 3 s guidance.
- Summaries are machine-generated and may contain errors; the linked paper is
  authoritative.

## Ideas

Search across days, per-topic RSS feeds, and a newsletter or Telegram channel
mirror of the highlights.
