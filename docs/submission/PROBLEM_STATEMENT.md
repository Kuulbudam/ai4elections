# Problem statement

**Brief:** Track 2, Electoral Data Analysis, Operational Insight and Decision Support (2A data-quality checker,
2B document intelligence, 2D anomaly explainer). Also serves Track 5 (observation and citizen accountability) and
Track 3 (answers in Hausa, Yoruba, Igbo and Nigerian Pidgin).

**User.** A citizen observer group, newsroom or research team that checks polling-unit results after an election and
watches polling units on election day. Typical team: one or two analysts, a few coordinators, and dozens to hundreds
of field observers on phones with poor signal.

**Pain.**
1. Results arrive as thousands of rows (for example 4,989 polling units in Plateau 2023). Teams cannot read every
   row, so arithmetic errors, impossible figures and copied results go unchecked, and the few real problems are lost
   among normal local variation.
2. The evidence is on paper: each polling unit's result sheet (Form EC8A) has to be typed by hand before it can be
   compared with anything.
3. On election day, reports from polling units come by phone calls and chat groups. Nobody can see coverage at a
   glance, serious incidents wait in someone's inbox, and nothing ends as evidence that can be handed over and
   checked.
4. Tools that call results "fraud" damage trust and put staff at risk; tools that hide how they decided are useless
   as evidence.

**What EaaS does.** It checks every row with 14 fixed rules, compares each polling unit with its own ward or LGA
using a robust statistical method, and explains every flag in plain words with ordinary reasons it could happen. It
reads sheet photos with AI, but a person confirms every figure before anything is checked. Field observers report
from their phones, even offline; problems go up a chain (ward → LGA → state → admin) until someone resolves them. At
the end, the team makes a report or a signed submission pack that anyone can check. A person decides at every step;
the platform never declares a result and never says fraud.

**Not in scope.** Counting, transmitting or certifying results; voter biometrics; any connection to INEC systems.
