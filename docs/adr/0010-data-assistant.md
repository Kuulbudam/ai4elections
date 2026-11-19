# ADR-0010: Data assistant (LLM) for admins and analysts

- Status: Accepted (19 Nov 2026). Founder requirement: staff ask questions about the data and get answers.
- Founder decisions: no provider or key yet; **admins and analysts only**.

## Decision

- **Provider:** any OpenAI-compatible chat-completions API with tool calling. Default **Groq** (free tier, no card;
  its terms say API inputs and outputs are not used for training). Rejected: Gemini free tier (Google may use
  prompts and outputs to improve its products, with human review); GitHub Models (retired). Configure with
  `LLM_API_KEY` (required), `LLM_BASE_URL`, `LLM_MODEL` (default `llama-3.3-70b-versatile`) on the API only.
- **No free-form SQL.** The model can only call six fixed lookups (`app/assistant/tools.py`): project overview,
  area totals, search findings, one finding, claims overview, validation issues. They reuse the tested API code
  (`area_list`, `find_rows`). Every lookup is bound to one project the caller is authorised for; any id the model
  passes is checked against that project, so nothing from another project or organisation can be returned.
- **Checks on every answer, independent of the model:**
  - wording: "fraud", "rigging" and similar → the answer is replaced with a fixed explanation;
  - grounding: every number with two or more digits must appear in the lookups' results (allowing rounding). If not,
    a visible warning is added and the answer is stored as ungrounded;
  - answers that used no lookup are labelled as such.
- **Bounded:** at most 6 lookup rounds; lookup output is truncated to 24k characters; history is limited to 8 turns.
- **Audit:** every question and answer, the lookups used, citations, grounded flag and model name are stored in
  `assistant_queries` (append-only; readable by the org's admins and analysts) and an `assistant.ask` audit event.
- **Optional by design:** with no key, `/assistant/status` says it is off, `/assistant/ask` returns 503, and the
  rest of the platform is unaffected (master prompt: no mandatory LLM dependency; reports and analysis stay AI-free).
- **UI:** "Ask the data" page per project; answers are rendered as plain text (no HTML), with links to the pages to
  check, and labelled "AI-drafted; not a finding".

## Testing

Unit tests drive the loop with a scripted stand-in model (grounding, wording, unknown tools, bad arguments,
bounded loop, history). DB tests run the real lookups on the analysed demo data, cross-project/org isolation,
permissions, storage and append-only. A live check uses the real provider when `LLM_API_KEY` is set.
