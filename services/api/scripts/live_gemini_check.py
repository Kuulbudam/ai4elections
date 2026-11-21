"""Live check of the Google Gemini backup provider (no database needed).

Skips (exit 0, clearly reported) when GEMINI_API_KEY is not set. Otherwise checks, with the real Gemini API:
1. a free-tier Gemini model is listed for the key;
2. Ask-the-data style tool calling works over two rounds (Gemini 3 needs its "thought signature" passed back);
3. a conversation that started on another provider can continue on Gemini (placeholder signature accepted);
4. switch-over: with a Groq key that is refused, the assistant still answers, through Gemini;
5. the sheet reader reads a SYNTHETIC result sheet with every figure right;
6. switch-over for the sheet reader too.
Only synthetic figures are sent. Needs GEMINI_API_KEY.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not os.environ.get("GEMINI_API_KEY", "").strip():
    print("SKIPPED: GEMINI_API_KEY is not set, so the Gemini backup cannot be checked live.")
    if s := os.environ.get("GITHUB_STEP_SUMMARY"):
        Path(s).open("a").write("## Live Gemini check\n\nSKIPPED — GEMINI_API_KEY not set.\n")
    sys.exit(0)

from app.assistant import llm  # noqa: E402
from app.sheets import read as rd  # noqa: E402
from app.sheets.image import clean  # noqa: E402
from app.sheets.synthetic import figures_from_csv, photo_like, render  # noqa: E402

KEY = os.environ["GEMINI_API_KEY"].strip()
results: list[tuple[str, bool, str]] = []

TOOLS = [{"type": "function", "function": {
    "name": "area_totals", "description": "Vote totals for one Nigerian state.",
    "parameters": {"type": "object", "properties": {"state": {"type": "string"}}, "required": ["state"]}}}]  # fmt: skip
TOTALS = {"state": "Plateau", "valid_votes": 48213, "APC": 20117, "PDP": 15890, "LP": 11002}


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""), flush=True)
    return ok


def tool_round_trip(model: llm.ChatModel, messages: list[dict]) -> str:
    """Let the model call area_totals, give it the figures, return its final answer."""
    for _ in range(4):
        msg = model.chat(messages, TOOLS)
        calls = msg.get("tool_calls") or []
        if not calls:
            return (msg.get("content") or "").strip()
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for tc in calls:
            messages.append({"role": "tool", "tool_call_id": tc.get("id", "x"), "content": json.dumps(TOTALS)})
    return ""


def main() -> int:
    listed = llm.list_models(KEY, llm.GEMINI_BASE_URL)
    names = llm.candidate_models(KEY, llm.GEMINI_BASE_URL, llm.PREFERRED_GEMINI_MODELS)
    print("Gemini Flash models listed:", [m for m in listed if "flash" in m] or "(none)", flush=True)
    if not check("free-tier Gemini models are listed", all(n in listed for n in names), f"will try {names}"):
        return report()
    # As the app does it: each candidate in turn (the newest Flash models are often overloaded).
    gemini = llm.FallbackChat([llm.OpenAICompatible(KEY, llm.GEMINI_BASE_URL, n) for n in names])
    q = [{"role": "system", "content": "Answer only from the tool. Copy figures exactly."},
         {"role": "user", "content": "How many valid votes were recorded in Plateau? Use the tool."}]  # fmt: skip

    try:
        answer = tool_round_trip(gemini, list(q))
        check("tool calling over two rounds", "48213" in answer.replace(",", ""), f"{gemini.name}: {answer[:120]}")
    except llm.LLMError as e:
        check("tool calling over two rounds", False, str(e))

    # A history from another provider: an earlier tool call with no Gemini signature.
    foreign = q + [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_groq1",
                    "type": "function",
                    "function": {"name": "area_totals", "arguments": '{"state": "Plateau"}'},
                }
            ],
        },  # fmt: skip
        {"role": "tool", "tool_call_id": "call_groq1", "content": json.dumps(TOTALS)},
    ]
    try:
        answer = tool_round_trip(gemini, foreign)
        check("continues a conversation begun on another provider", "48213" in answer.replace(",", ""), answer[:120])
    except llm.LLMError as e:
        check("continues a conversation begun on another provider", False, str(e))

    refused = llm.OpenAICompatible("not-a-real-key", llm.DEFAULT_BASE_URL, "openai/gpt-oss-120b")
    both = llm.FallbackChat([refused] + [llm.OpenAICompatible(KEY, llm.GEMINI_BASE_URL, n) for n in names])
    try:
        answer = tool_round_trip(both, list(q))
        check("assistant switches to Gemini when Groq refuses", both.name in names and "48213" in
              answer.replace(",", ""), f"answered by {both.name}")  # fmt: skip
    except llm.LLMError as e:
        check("assistant switches to Gemini when Groq refuses", False, str(e))

    fig = figures_from_csv(1)
    jpeg = clean(photo_like(render(fig))).jpeg
    vision = rd.FallbackVision([rd.OpenAICompatibleVision(KEY, llm.GEMINI_BASE_URL, n) for n in names])
    reading = rd.read_sheet(vision, jpeg)
    vision_name = vision.name
    got = reading["figures"]
    wrong = [k for k in rd.COUNT_FIELDS if got.get(k) != fig.get(k)]
    parties = {p["party"]: p["votes"] for p in got.get("party_votes", [])}
    wrong += [p["party"] for p in fig["party_votes"] if parties.get(p["party"]) != p["votes"]]
    check("sheet reader reads the SYNTHETIC sheet exactly", reading["status"] == "read" and not wrong,
          f"{vision_name}: status {reading['status']}" + (f", wrong: {wrong}" if wrong else "")
          + (f" ({reading['notes']})" if reading["status"] != "read" else ""))  # fmt: skip

    fallback = rd.FallbackVision([rd.OpenAICompatibleVision("not-a-real-key", llm.DEFAULT_BASE_URL, "qwen/qwen3.8-27b")]
                                 + [rd.OpenAICompatibleVision(KEY, llm.GEMINI_BASE_URL, n) for n in names])  # fmt: skip
    reading = rd.read_sheet(fallback, jpeg)
    check("sheet reader switches to Gemini when Groq refuses",
          reading["status"] == "read" and fallback.name in names, f"read by {fallback.name}")  # fmt: skip
    return report()


def report() -> int:
    passed = sum(ok for _, ok, _ in results)
    print(f"\n{passed}/{len(results)} checks passed", flush=True)
    if s := os.environ.get("GITHUB_STEP_SUMMARY"):
        lines = [f"## Live Gemini check: {passed}/{len(results)}", ""]
        lines += [f"- {'✅' if ok else '❌'} {n}" + (f" — {d}" if d else "") for n, ok, d in results]
        Path(s).open("a").write("\n".join(lines) + "\n")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
