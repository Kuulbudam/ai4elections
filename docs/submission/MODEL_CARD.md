# Model / System Card: Election as a System (EaaS)

## Summary
EaaS checks polling-unit election results and collects election-day observer reports, for human review. It
combines a fixed rule engine (ruleset 1.2.0, 14 rules), a robust statistical outlier method (method 1.0.0: median
and MAD within ward or LGA), and three AI features built on hosted language models: a result-sheet reader, "Ask the
data", and a written summary of findings. Speech is turned into text with Whisper. Version: December 2026
prototype. Authors: Kuulbudam (team lead) and team.

| Part | Kind | Model / method |
|---|---|---|
| Data checks V001–V014 | Deterministic rules | Python, versioned |
| Unusual results | Statistics | Robust z = 0.6745 × (x − median) / MAD; flag at ≥ 3.5; peer group ≥ 10 units |
| Sheet reader (EC8A photo) | Vision language model | Groq vision model (Qwen 3.x or Llama 4, whichever is listed) first, Google Gemini Flash as backup |
| Ask the data, findings summary | Language model with fixed lookups | Groq `openai/gpt-oss-120b` first, Gemini Flash as backup |
| Voice | Speech to text | Groq `whisper-large-v3`, Gemini as backup; phone's own voice reads answers |

## Intended use
Observer groups, newsrooms and researchers: find rows to check, read sheets faster, ask questions about their own
data, run election-day reporting, and produce checkable evidence. **Out of scope:** declaring or certifying results,
calling anything fraud, predicting winners, persuasion or targeting of voters, profiling individuals, voter
biometrics, or any connection to INEC systems.

## Training and data
No model was trained or fine-tuned. Rules and thresholds are fixed in code. Data used for development and tests:
synthetic only (EaaS generator, 12,023 units; Rise starter data, 900 units), plus public CCIJ/VEZA 2023 Plateau
results downloaded at run time and not stored. See `DATASHEET.md`.

## Evaluation
| Slice | n | Precision | Recall | F1 | Notes |
|---|---|---|---|---|---|
| Rise starter data, rules | 900 units, 42 planted | 1.00 | 1.00 | 1.00 | 46.7 alerts / 1,000 |
| Rise starter data, rules + statistics | 900, 42 planted | 0.49 | 1.00 | 0.66 | 95.6 alerts / 1,000; extra flags ranked by severity |
| EaaS synthetic, rules | 12,023 units, 15 scenarios | 1.00 | 1.00 | 1.00 | Row-by-row ground truth |
| EaaS synthetic, statistics (planted) | 30 planted | — | 1.00 | — | All very high; 0.43% flags on normal unit-indicators, none very high |
| Sheet reader, real handwritten EC8A | 100 figures, 20 sheets | — | — | — | 84% exact; 13 of 16 misses marked unsure |
| Ask the data, live | 13 checks (English, Hausa, Pidgin) | — | — | — | 13/13 pass: right figures, grounded, right language |
Full method and failure cases: `EVALUATION_REPORT.md`.

## Error analysis
- False positives (a normal unit flagged): costs reviewer time; could embarrass officials if shared without review.
  Mitigated by severity ranking, benign explanations on every flag, and the "not proof of wrongdoing" label.
- False negatives (a real problem missed): a problem goes unchecked. Statistics cannot see a problem that looks
  normal; rules cannot see a figure that is wrong but consistent. The sheet-vs-file check and observers' reports
  cover part of this.
- Sheet reader misreads: a person confirms every figure; low-confidence figures are highlighted.

## Fairness and bias
Units are compared only with their own ward or LGA, so regional turnout patterns are not flagged as unusual by
themselves. Small wards fall back to the LGA (stated on the finding). Answers in Hausa and Pidgin pass live checks;
Yoruba and Igbo can mix in English. Many phones lack Hausa, Yoruba or Igbo voices; the app says so instead of
reading in the wrong voice.

## Uncertainty and abstention
Statistical findings carry severity (medium, high, very high by |z|) and a separate confidence (low with data
warnings; high for ward groups of 20+). Units are not scored when the peer group is under 10 or MAD is 0. The sheet
reader gives high/medium/low per figure and may say a sheet is unreadable. Ask the data marks answers whose numbers
could not be matched to the data.

## Human oversight
Nothing is final without a person: sheet figures are confirmed by a person; findings are reviewed by admins and
analysts with a written reason; escalations go to the area coordinator, then up to admins. All decisions, AI
questions and answers, and reports are append-only with a hash-chained audit trail.

## Ethical and legal considerations
Nonpartisan by design: no party or candidate is favoured; the wording guard blocks fraud/rigging language in five
languages. Data protection under NDPA 2023: minimal personal data (account email, observers' reports), row-level
security per organisation, two-step sign-in, photos re-encoded without location metadata, submission packs list
observers by code, recordings not stored. Misuse risk: a flag shared as an accusation; mitigated by labels on every
page and report.

## Maintenance
Rules and methods are versioned; old results keep the version they were made with. Live checks run on every change
(GitHub Actions). Issues: github.com/Kuulbudam/ai4elections/issues.
