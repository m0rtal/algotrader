# Design — derive historical splits from bars

## Goal

Produce verified `corporate_actions` rows for historical stock splits /
consolidations on Russian equities, **without paid subscriptions**,
using only data already present in `bars` (SQLite) plus the current
`face_value` from MOEX ISS (one HTTP call per figi).

## Approach

### Detection algorithm

For each figi, iterate consecutive `bars` rows sorted by `ts`:

```
ratio = close[i] / close[i-1]
if ratio <= 0.5 or ratio >= 2.0:
    candidate = SplitCandidate(
        ts = bars[i].ts,
        factor = 1.0 / ratio,
        direction = 'split' if ratio < 1 else 'reverse_split',
    )
```

**Hardcoded threshold**: ratio ≤ 0.5 OR ratio ≥ 2.0 — operator explicit
decision 2026-09-13. Anything below this (ordinary price movement,
dividends, fees, small redenomination) is ignored.

### Disambiguate BONU vs reverse split

If `ratio >= 2.0` (price multiplied), the candidate is either:

- **Reverse split / consolidation** (e.g. 1000:1 → price ×1000)
- **BONU / stock dividend** (issuer grants extra shares → per-share price
  drops proportionally when price is adjusted back)

Differentiation:
- BONU changes `volume` proportionally (more shares traded)
- Reverse split leaves `volume` unchanged

Rule:
```
if ratio >= 2.0:
    vol_ratio = volume[i] / volume[i-1]
    if vol_ratio ≈ factor:  # volume scaled same as price
        skip (probably BONU)
    else:
        emit reverse_split
```

### Verify via face_value

`face_value` (current) is fetched from MOEX ISS `/iss/securities/{secid}.json`
**once per figi** at the start of the run.

For each candidate:
```
implied_cumulative_factor = product(candidate.factor for all candidates up to ts)
implied_old_face_value = current_face_value / implied_cumulative_factor
```

If the inferred `implied_old_face_value` matches the historical face_value
within ±20%, the candidate is **confirmed**. Otherwise still emit but tag
with `source='derived:bars:unverified-facevalue'`.

### Source attribution

```
source = 'derived:bars+facevalue:{first_bar_ts}:{last_bar_ts}'
```

Example:
```
source = 'derived:bars+facevalue:2014-01-01:2024-12-31'
```

This makes every derived row auditable: a reader can re-run the same
derivation against the bars table and reproduce the factor.

### Idempotency

Before inserting, the script checks whether a `corporate_actions` row
already exists for `(figi, 'split', ts)`. If yes, skip. No row is
overwritten — re-runs are no-ops.

### Live splits (24-hour delay)

A split declared by MOEX today does NOT appear in `corporate_actions`
until tomorrow's bar arrives. This is acceptable: the daily guardian
runs at 20:00 UTC; bars for the same day are loaded at the next
backfill cycle; the derived run can be invoked immediately after to
catch the new event.

If real-time detection is needed later, MOEX ISS `/iss/cci/corp-actions/*`
is the path (currently behind a subscription).

## Files

| File | Purpose |
|---|---|
| `apps/api/src/algotrader_api/scripts_import/derive_splits.py` | Core logic — `derive_splits_for_figi`, `run_derivation`, threshold constants |
| `apps/api/scripts/derive_splits.py` | Operator wrapper — `python -m scripts.derive_splits data/state.db` |
| `apps/api/tests/test_derive_splits.py` | Unit tests covering: single split, multiple splits, reverse split, BONU discrimination, sub-threshold ignored, idempotency |

## Dependencies on existing code

- `bars` table (`db/bars_sqlite.py` — `list_bars`)
- `corporate_actions` table (`scripts_import/import_corporate_actions_common.py`
  — `merge_into_corporate_actions`)
- MOEX ISS HTTP client (already in repo — `ingestion/universe.py` uses it)

## Open questions

None — operator approved derive-only path 2026-09-13.
