---
target: specs/mlv-app/cards.md
kind: trap
source_commit: PENDING
law4: attested
---
## mlv-app/designed-fix-lost-on-partial
rule: a round that ends PARTIAL names its successor card in the same tick that reads it; a card whose design names fix rows codes them in the next round instead of re-measuring; a queued fix idle past 72 h, or one whose blocker has merged, is re-armed or closed by a ruling; a standing sweep lists every designed or partly built card with no merged PR and nothing in flight.
mechanism: a playback stall fix was designed with named fix rows, then spent four rounds re-measuring and ended PARTIAL twice on infrastructure (a hook deny, a venue outage). Nothing re-armed it, and the defect stayed in the product for two days until the owner saw it. A sweep the same day found 58 cards in that state across four leak shapes: re-measure instead of code, PARTIAL on infrastructure, queued and never re-read, and unblocked by a merge but never re-armed.
check: the sweep prints one STALLED line per designed or partly built card with no merged or open PR, no live lane and no closing ruling; each tick triages the oldest two
supersedes: none
evidence: measured
