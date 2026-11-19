# Data assistant: completion report

**Date:** 19 Nov 2026 **Result:** PASS (live with the real provider, and in production).
**PRs:** #30 (assistant), #31 (live fixes). Decision record: ADR-0010.

## What it does

Admins and analysts open **Ask the data** on a project and ask in plain language. The model can only call six fixed lookups, bound to that project:
- project overview
- area totals
- search findings
- one finding with its source row
- claims overview
- validation issues

Answers come back as plain text, with links to the pages where each figure can be checked. They are labelled "AI-drafted; not a finding". Every question and answer is stored (append-only) and audited.

## Safety, enforced in code (not just asked of the model)

- Ids are checked against the project, so nothing from another project or organisation is ever returned (tested with another organisation's ids and an injection string).
- Any answer using "fraud", "rigging" or similar wording is replaced with a fixed explanation.
- Every figure with two or more digits must appear in what the lookups returned. If one doesn't, a warning is shown and the answer is marked ungrounded.
- Researchers and viewers get 403, other organisations 404, and 503 when no key is set. The platform works the same without the assistant.

## Provider

Groq free tier with `openai/gpt-oss-120b`, chosen automatically from the models the key lists. The free tier allows about **8,000 tokens per minute and 1,000 requests per day** for each chat model.

To fit within that:
- the project's details are given to the model up front;
- lookup results are compact;
- replies are capped at 1,200 tokens, with low reasoning effort;
- the assistant waits once on "try again in N seconds";
- a malformed tool call is retried once with a correction.

## Verification

- Tests: assistant unit tests use a scripted stand-in model. DB tests run the real lookups on the analysed demo data and check isolation, permissions and storage. API suite and web tests green.
- **Live, real model, real database: 9/9, twice.** It answered "Which state has the most unusual turnout flags?" with the correct state and count (Norvale, 15), grounded and cited. A leading "was it rigged?" question was not answered in those terms. An unknown finding id produced no invented details.
- **Production (run 44513896271): 24/24**, including the assistant's answer through the deployed API ("12,023 polling-unit rows", grounded).

## Limits to know

- At about 8k tokens a minute, roughly 2–4 questions per minute can be answered in total across all users. A busy demo should pace questions; the page says when the free limit is reached.
- Answers depend on the model. The checks catch invented figures and accusatory wording, but staff should still click through to the linked pages.
