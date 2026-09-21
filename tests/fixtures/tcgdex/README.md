# Recorded TCGdex responses

These are real responses from https://api.tcgdex.net/v2, saved verbatim on
2026-09-21T10:25Z by the build that added market-average support.
They are **recorded-response** fixtures in the sense of docs/04: the parser is
tested against what the API actually returned, not against a schema written
from its documentation.

| File | Why it is here |
|---|---|
| ja_cards_SV2a-173.json | Japanese 151 Pikachu illustration rare, a normal priced card |
| ja_cards_SV2a-025.json | Its same-name sibling in the set, for the mapping-collision check |
| ja_cards_M2-115.json | Average and trend disagree sharply, so the price must be refused |
| ja_cards_M-P-023.json | A promo with no Cardmarket pricing at all |
| en_cards_sv03.5-173.json | The English counterpart, proving language separation |
| en_cards_swsh3-136.json | A card with separate reverse-holo ("-holo") fields |
| ja_sets_SV2a.json | The set listing used to find same-name siblings |

One further fixture in the tests is **derived**, not recorded: a copy of the
SV2a-025 response with its Cardmarket product id changed to collide with
SV2a-173's. No real collision turned up in a scan of 168 cards across six
Japanese sets, and the test says so where it builds the derived copy.
