#!/bin/bash
# Daily digest run for the subscription (launchd) mode: summarize via headless
# Claude Code on this machine's login, push the day's data, and let the
# deploy.yml workflow render + publish GitHub Pages on push.
#
# Credentials follow the launchd-runtime convention: read from the `env` block
# of ~/.claude/settings.json (CLAUDE_CODE_OAUTH_TOKEN for headless Claude Code,
# TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID for failure notifications).
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETTINGS_FILE="${SETTINGS_FILE:-$HOME/.claude/settings.json}"
LOG_PREFIX="[ai-paper-digest $(date -u +%FT%TZ)]"

export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

if [[ -f "$SETTINGS_FILE" ]]; then
    CLAUDE_CODE_OAUTH_TOKEN=$(/usr/bin/jq -r '.env.CLAUDE_CODE_OAUTH_TOKEN // ""' "$SETTINGS_FILE" 2>/dev/null || echo "")
    TELEGRAM_BOT_TOKEN=$(/usr/bin/jq -r '.env.TELEGRAM_BOT_TOKEN // ""' "$SETTINGS_FILE" 2>/dev/null || echo "")
    TELEGRAM_CHAT_ID=$(/usr/bin/jq -r '.env.TELEGRAM_CHAT_ID // ""' "$SETTINGS_FILE" 2>/dev/null || echo "")
    export CLAUDE_CODE_OAUTH_TOKEN TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID
fi

notify_failure() {
    local message="ai-paper-digest daily run FAILED: $1"
    echo "$LOG_PREFIX $message" >&2
    if [[ -n "${TELEGRAM_BOT_TOKEN:-}" && -n "${TELEGRAM_CHAT_ID:-}" ]]; then
        /usr/bin/curl -s -m 30 -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="${TELEGRAM_CHAT_ID}" --data-urlencode text="$message" > /dev/null || true
    fi
}

cd "$REPO_DIR" || { notify_failure "repo dir missing"; exit 1; }

echo "$LOG_PREFIX starting pipeline"
if ! .venv/bin/python -m digest.pipeline --engine claude-cli; then
    notify_failure "pipeline exited nonzero (see ~/Library/Logs/ai-paper-digest.log)"
    exit 1
fi

if [[ -z "$(git status --porcelain data/)" ]]; then
    echo "$LOG_PREFIX no data changes (weekend/holiday?) — nothing to push"
    exit 0
fi

git add data/
git commit -m "digest: $(date -u +%F)" || { notify_failure "git commit failed"; exit 1; }
if ! git push; then
    notify_failure "git push failed"
    exit 1
fi
echo "$LOG_PREFIX pushed; Pages deploy will run via deploy.yml"
