# Safety, Privacy and Limitations Statement

## 1. Compliance with hackathon rules
- [x] No live/institutional system accessed or tested. EaaS never connects to INEC or any electoral system; the
  submission pack is handed over by the organisation itself.
- [x] Only synthetic, public or authorised data: our generator and the Rise starter data (synthetic); CCIJ/VEZA
  2023 public transcription, downloaded at run time and not stored in the repository.
- [x] Nonpartisan; no persuasion or microtargeting.
- [x] Human oversight for consequential decisions: every flag is reviewed by a person; AI sheet readings are
  confirmed by a person before use.

## 2. Risk register
| Risk | Who could be harmed | Likelihood | Impact | Mitigation | Residual |
|---|---|---|---|---|---|
| False positive (a normal unit flagged) | Polling officials, parties named in shared findings | Medium | Medium | Severity ranking; benign explanations on every flag; "not proof of wrongdoing" on every page; review with reason required | Low |
| False negative (real problem missed) | Voters, observers relying on the tool | Medium | Medium | Rules + statistics + sheet-vs-file + collation checks + field reports; stated limits | Medium |
| Bias against language/group | Speakers of Yoruba, Igbo; low-literacy users | Medium | Low | Five answer languages; voice in and out; plain words; Yoruba/Igbo mixing disclosed | Low |
| Privacy breach / re-identification | Observers, staff | Low | High | Row-level security per organisation; two-step sign-in required by the database; observers coded in packs; photos stripped of metadata; no voter data collected | Low |
| Misuse by bad actors (flags used as accusations) | Officials, public trust | Medium | High | No fraud wording anywhere (guarded in 5 languages); reports carry limits; packs say "not an INEC document" | Medium |
| Adversarial manipulation (forged packs, prompt injection) | Recipients of evidence | Low | High | Ed25519-signed packs with SHA-256 manifest; AI limited to fixed lookups; data in prompts treated as data | Low |
| Over-reliance by users | Analysts, newsrooms | Medium | Medium | AI output labelled; numbers checked; person confirms sheets; decisions need reasons | Low |

## 3. Privacy and data protection
Personal data: account email and display name; field observers' reports (with optional phone location); result
sheet photos (may show officials' names and signatures). Lawful basis: the organisation's legitimate interest in
election observation, with observers' consent as team members. Minimisation: no voter data; GPS only if the phone
allows; recordings turned into text and discarded; photos re-encoded without metadata. Access: organisation members
only (enforced in Postgres row-level security), field agents see only their own reports, two-step verification for
everyone. Retention: records are append-only for evidence; test data is deleted after each automated check. Packs
exclude photos and list observers by a code.

## 4. Known limitations
- Statistics flag natural extremes (home areas, very small units); about 0.4% of normal units on our synthetic set.
- Wards under 10 units are compared with the LGA; LGAs under 10 are not scored.
- The sheet reader gets about 84% of handwritten figures exactly; a person must confirm.
- Yoruba and Igbo answers may mix in English; many phones lack these voices.
- Free AI tiers limit volume; when busy, the platform keeps working without AI (people type sheet figures).
- Not tested yet with real users at election scale; email sending uses Supabase's limited free sender.

## 5. Conditions for further testing
Partner sign-off from an accredited observer group; pilot on a past election with reviewer time and agreement
measured; independent security review and penetration test; legal review under NDPA 2023 and the Electoral Act
2022; accessibility review with users; sheet-reader test on at least 500 sheets across states.

## 6. Statement
"This prototype is not approved for operational electoral deployment." Signed: Kuulbudam, team lead, 27 November 2026.
