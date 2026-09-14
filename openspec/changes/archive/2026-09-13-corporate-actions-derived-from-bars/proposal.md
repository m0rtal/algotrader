# Corporate actions derived from bars

## Why

Russian historical corporate actions (splits, consolidations) have **no
free machine-readable source**:

- MOEX ISS provides only the **current** `FACEVALUE`/`LOT` per security,
  no historical events endpoint.
- Tinkoff Invest API exposes `GetDividends` but **no splits endpoint**.
- NSD's `getCorpActions V.2` and e-disclosure.ru's gateway provide full
  historical data but **require paid subscription**.
- EODHD, Databento, Polygon, Alpaca, FMP — **none cover MOEX**.

Backtesting with real money at stake needs correct historical split
factors. Without them, any signal generated against pre-split bars is
**numerically false**.

## What changes

Add an importer that **derives** historical split factors by diffing
consecutive `bars` rows, with the current `face_value` from MOEX ISS as
a verification cross-check:

- `apps/api/src/algotrader_api/scripts_import/derive_splits.py` — detects
  price discontinuities (ratio ≤ 0.5 or ≥ 2.0) in consecutive bars and
  emits `CorporateActionRow` rows.
- `apps/api/scripts/derive_splits.py` — operator wrapper.
- `corporate_actions` table gains rows with
  `source='derived:bars+facevalue:<start_ts>:<end_ts>'`.
- Live splits (no bars yet for the post-split day) are detected at most
  24 hours late (next day's bar arrives) — **acceptable** because the
  daily guardian already runs at 20:00 UTC.

**Splits** are derived locally (zero cost).
**Dividends** continue to come from Tinkoff (`get_dividends`) and MOEX
ISS (`/dividends.json`).

## Capabilities

- **data-quality-integrity** (existing) — extended with derived-split
  ingestion path.

## Out of scope

- Real-time corporate-action WebSocket (would require MOEX subscription).
- Cross-checking derived splits against NSD/e-disclosure (cost).
- Dividend detection (already covered by existing importers).
- US/global paper (operator explicitly excluded).

## Acceptance

- After running on prod DB, `corporate_actions` contains rows for known
  historical events (YNDX 2014-06-18 split 4:1, SBER 2020-06-19 split
  2:1, VTB 2024-07-11 consolidation 5000:1) with verifiable `source`.
- No row is written when the inferred `source` is not the derivation
  algorithm — i.e. no fabricated `curated` fallback.
- Multiple splits per ticker and reverse splits are both supported.
