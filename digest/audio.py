"""Spoken briefing of the day's top highlight, voiced with ElevenLabs.

One paper a day: the top highlight is rewritten for the ear (short plain
sentences, no Markdown, at most one number) and sent to the ElevenLabs
text-to-speech API. The MP3 lands in data/audio/<date>.mp3 and the script is
recorded in the day's JSON under "audio", so the site can show it as text.

Usage:
    python -m digest.audio                       # newest data file
    python -m digest.audio data/2026-11-11.json  # a given day
    python -m digest.audio --dry-run             # print the script, no API call

Needs ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID in the environment.
ELEVENLABS_MODEL_ID is optional (default: eleven_flash_v2_5, the cheapest).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format={output_format}"
DEFAULT_MODEL = "eleven_flash_v2_5"
# Low bitrate keeps committed files small (about 240 KB a minute); speech stays clear.
OUTPUT_FORMAT = "mp3_22050_32"
# Every character is billed. 600 characters is about 40 seconds of speech.
MAX_CHARS = 600

SCRIPT_PROMPT = (
    "You write a short spoken segment for the audio version of a public daily digest of "
    "AI research papers. It will be read aloud by a text-to-speech voice, so write for the "
    "ear, not the eye. Explain the paper below to a curious listener who works in software "
    "but is not a researcher in this field.\n\n"
    "Rules: 60 to 90 words, and never more than {max_chars} characters. Open with "
    "\"Today's pick from the AI Paper Digest.\" Then say in plain words what problem the "
    "paper looks at, what the authors did, what they found, and why it matters. Short "
    "sentences, one idea each. Use at most one number. Spell out acronyms or avoid them. "
    "No Markdown, no lists, no brackets, no model version names, no arXiv ids, no URLs. "
    "Do not invent anything that is not in the material. Output only the spoken text.\n\n"
    "Title: {title}\n\nMaterial:\n{material}"
)

_MD = re.compile(r"\*\*|`|(?<!\w)\*(?!\s)|(?<!\s)\*(?!\w)")
_SENTENCES = re.compile(r"(?<=[.!?])\s+")


class TTSError(Exception):
    """A failed text-to-speech call; ``retryable`` says whether a later retry may help."""

    def __init__(self, status: int | None, detail: str, retryable: bool) -> None:
        super().__init__(f"text-to-speech failed (status {status}): {detail}")
        self.status = status
        self.detail = detail
        self.retryable = retryable


def pick_paper(day: dict) -> dict | None:
    """The day's top highlight: most Hugging Face upvotes, then highest interest."""
    highlights = [p for p in day["papers"] if p.get("highlight")]
    pool = highlights or [p for p in day["papers"] if p.get("summary")]
    if not pool:
        return None
    return max(pool, key=lambda p: (p.get("hf_upvotes") or 0, p.get("interest") or 0))


def clean_for_speech(text: str) -> str:
    text = _MD.sub("", text)
    text = re.sub(r"^\s*-\s+", "", text, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", text).strip()


def fallback_script(paper: dict, max_chars: int = MAX_CHARS) -> str:
    """Template script from the short summary, cut at whole sentences."""
    script = f"Today's pick from the AI Paper Digest. {clean_for_speech(paper['title'])}."
    for sentence in _SENTENCES.split(clean_for_speech(paper.get("summary") or "")):
        if not sentence:
            continue
        if len(script) + 1 + len(sentence) > max_chars:
            break
        script += " " + sentence
    return script[:max_chars]


def build_script(paper: dict, run, max_chars: int = MAX_CHARS) -> tuple[str, str]:
    """Return (script, source). ``run`` is a prompt runner like claude_cli.run_prompt.

    The model's script is used when it comes back non-empty and within budget;
    otherwise the template fallback is used, so a failed call never blocks audio.
    """
    material = paper.get("highlight_summary") or paper.get("summary") or paper["abstract"]
    prompt = SCRIPT_PROMPT.format(max_chars=max_chars, title=paper["title"], material=material)
    try:
        script = clean_for_speech(run(prompt))
    except Exception as error:  # noqa: BLE001 - the template is the fallback
        print(f"script writer failed, using the template: {error}", file=sys.stderr)
        return fallback_script(paper, max_chars), "template"
    if not script or len(script) > max_chars:
        print(f"script writer returned {len(script)} characters, using the template", file=sys.stderr)
        return fallback_script(paper, max_chars), "template"
    return script, "model"


def synthesize(text: str, voice_id: str, api_key: str, model_id: str = DEFAULT_MODEL,
               timeout: float = 60) -> bytes:
    """One ElevenLabs text-to-speech call; returns the MP3 bytes."""
    request = urllib.request.Request(
        TTS_URL.format(voice_id=voice_id, output_format=OUTPUT_FORMAT),
        data=json.dumps({"text": text, "model_id": model_id}).encode("utf-8"),
        headers={"xi-api-key": api_key, "Content-Type": "application/json",
                 "Accept": "audio/mpeg"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")[:300]
        # 429 and 5xx are the service's side; 4xx means the request itself is wrong.
        raise TTSError(error.code, body, retryable=error.code == 429 or error.code >= 500) from None
    except (urllib.error.URLError, TimeoutError) as error:
        raise TTSError(None, str(error), retryable=True) from None


def _latest_data_file(data_dir: Path) -> Path | None:
    files = sorted(data_dir.glob("*.json"))
    return files[-1] if files else None


def main(argv: list[str] | None = None, run=None) -> int:
    parser = argparse.ArgumentParser(description="Voice the day's top highlight.")
    parser.add_argument("data_file", nargs="?", help="day JSON (default: newest in data/)")
    parser.add_argument("--dry-run", action="store_true",
                        help="write and print the script only; no API call, nothing saved")
    parser.add_argument("--max-chars", type=int, default=MAX_CHARS)
    args = parser.parse_args(argv)

    path = Path(args.data_file) if args.data_file else _latest_data_file(REPO_ROOT / "data")
    if path is None or not path.exists():
        print("no data file to voice", file=sys.stderr)
        return 1
    day = json.loads(path.read_text(encoding="utf-8"))
    audio_path = path.parent / "audio" / f"{day['date']}.mp3"

    if audio_path.exists() and day.get("audio") and not args.dry_run:
        print(f"{audio_path} already exists; skipping, no credits spent")
        return 0

    paper = pick_paper(day)
    if paper is None:
        print("no paper to voice for this day", file=sys.stderr)
        return 1

    if run is None:
        from .claude_cli import run_prompt as run
    script, source = build_script(paper, run, args.max_chars)
    print(f"[{source}, {len(script)} chars] {paper['id']}: {script}")
    if args.dry_run:
        return 0

    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "")
    if not api_key or not voice_id:
        print("ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID must be set", file=sys.stderr)
        return 1
    model_id = os.environ.get("ELEVENLABS_MODEL_ID") or DEFAULT_MODEL

    try:
        mp3 = synthesize(script, voice_id, api_key, model_id)
    except TTSError as error:
        print(f"{error} (retryable: {error.retryable})", file=sys.stderr)
        return 1

    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(mp3)
    day["audio"] = {
        "paper_id": paper["id"],
        "title": paper["title"],
        "script": script,
        "script_source": source,
        "file": f"audio/{day['date']}.mp3",
        "model": model_id,
    }
    path.write_text(json.dumps(day, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {audio_path} ({len(mp3) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
