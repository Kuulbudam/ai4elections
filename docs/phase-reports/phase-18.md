# Phase 18 — Voice: completion report

**Date:** 21 Nov 2026 **Result:** PASS. **Branch:** `phase-18-voice`.

| Area | What was done | Evidence |
|---|---|---|
| Speak a question | Press **Speak** in Ask the data, talk in English, Pidgin, Hausa, Yoruba or Igbo, press **Stop**. Groq Whisper (`whisper-large-v3`) writes it down, Gemini as the backup; the question is asked | 4 unit tests; 2 DB tests; 3 web tests |
| Hear the answer | An answer to a spoken question is read back with the phone's voice. Every answer has **Read aloud**. If the phone has no voice for the language it says so | Web tests |
| Field app | "Say it instead" for the incident description | — |
| Privacy | Recordings are turned into text and not stored | Code review |
| Backup | The Gemini backup now tries five listed Flash models, so it still answers when the newest are used up for the day | Unit test; live Gemini check 6/6 |

Live voice check (a computer voice made by espeak-ng, so no real person is recorded): 3/3. Groq Whisper and the
Gemini backup each wrote down the test question word for word, and voice kept working with Groq refused. All other
live checks passed in the same run (35/35, 26/26, 12/12, 13/13, 12/12, 6/6).
