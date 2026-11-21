"""Live check of voice (speech to text) with the real providers (no database needed).

A sentence is spoken by espeak-ng (a computer voice, so no real person is recorded), then:
1. Groq Whisper writes it down (LLM_API_KEY);
2. Gemini writes it down too, as the backup (GEMINI_API_KEY);
3. with a Groq key that is refused, voice still works through Gemini.
Each provider is skipped, clearly reported, when its key is not set. Needs espeak-ng (and ffmpeg for Opus).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.assistant import llm, speech  # noqa: E402

SENTENCE = "Which local government area has the most critical incidents today?"
WORDS = {"which", "local", "government", "area", "most", "critical", "incidents", "today"}
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} {name} — {detail}", flush=True)


def heard(text: str) -> float:
    got = set(re.findall(r"[a-z]+", text.lower()))
    return len(WORDS & got) / len(WORDS)


def clip() -> tuple[bytes, str]:
    if not shutil.which("espeak-ng"):
        print("espeak-ng is not installed", file=sys.stderr)
        sys.exit(1)
    with tempfile.TemporaryDirectory() as d:
        wav, ogg = Path(d) / "q.wav", Path(d) / "q.ogg"
        subprocess.run(["espeak-ng", "-v", "en-gb", "-s", "150", "-w", str(wav), SENTENCE], check=True)  # noqa: S603, S607
        if shutil.which("ffmpeg"):  # phones send Opus; send the same
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(wav), "-c:a", "libopus", "-b:a", "32k",  # noqa: S603, S607
                            str(ogg)], check=True)  # fmt: skip
            return ogg.read_bytes(), "audio/ogg"
        return wav.read_bytes(), "audio/wav"


def main() -> int:
    audio, mime = clip()
    print(f"clip: {len(audio):,} bytes {mime}")
    groq = os.environ.get("LLM_API_KEY", "").strip()
    gem = os.environ.get("GEMINI_API_KEY", "").strip()
    if groq:
        t = speech.Whisper(groq, llm.DEFAULT_BASE_URL, speech.DEFAULT_STT_MODEL)
        try:
            text = t.transcribe(audio, mime, "en")
            check("Groq Whisper writes down the spoken question", heard(text) >= 0.75, f"{heard(text):.0%}: {text!r}")
        except llm.LLMError as e:
            check("Groq Whisper writes down the spoken question", False, str(e))
    else:
        print("SKIPPED Groq: LLM_API_KEY is not set")
    if gem:
        os.environ.pop("LLM_API_KEY", None)
        g = speech.configured()
        try:
            text = g.transcribe(audio, mime, "en") if g else ""
            check("Gemini writes down the spoken question (backup)", heard(text) >= 0.75,
                  f"{getattr(g, 'name', '-')} {heard(text):.0%}: {text!r}")  # fmt: skip
        except llm.LLMError as e:
            check("Gemini writes down the spoken question (backup)", False, str(e))
        os.environ["LLM_API_KEY"] = "gsk_refused_on_purpose"
        llm._candidates.clear()
        both = speech.configured()
        try:
            text = both.transcribe(audio, mime, "en") if both else ""
            check("with Groq refusing, voice still works through Gemini", heard(text) >= 0.75 and "gemini" in both.name,
                  f"{both.name}: {text!r}")  # fmt: skip
        except llm.LLMError as e:
            check("with Groq refusing, voice still works through Gemini", False, str(e))
        os.environ.pop("LLM_API_KEY", None)
        if groq:
            os.environ["LLM_API_KEY"] = groq
    else:
        print("SKIPPED Gemini: GEMINI_API_KEY is not set")
    passed = sum(ok for _, ok, _ in results)
    print(f"{passed}/{len(results)} checks passed")
    if s := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(s).open("a") as f:
            f.write(
                "## Live voice check\n\n" + "\n".join(f"- {'✅' if ok else '❌'} {n}" for n, ok, _ in results) + "\n"
            )
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
