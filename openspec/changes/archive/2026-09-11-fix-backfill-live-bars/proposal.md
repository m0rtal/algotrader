## Why

The Bars tab in the dashboard showed only 16 tickers with 180 bars each,
even though `instruments` lists 27 400+ figis (1 917 shares, 1 578 bonds,
272 ETFs, 531 futures, 23 102 options) and the production backfill logs
report ingesting 1 086 113 bars. Three root causes stacked on top of
each other:

1. **Chunked fetch not implemented.** Live Tinkoff returns
   `INVALID_ARGUMENT 30014` for `get_candles` requests wider than ~7 days
   on the day interval, but `_backfill_one` made one call per figi for
   the full `from_..to` window, so each fetch failed.
2. **Class-restricted universe filter** introduced a few hours earlier
   (`class IN ('share','etf')`) silently excluded bonds/futures/options
   even though the official API serves daily candles for all four
   classes (verified against `Tinkoff/investAPI` docs `faq_marketdata.md`).
3. **Token/settings desync.** `PUT /api/settings/token` was writing
   the broker token to the opaque `secrets` table but not mirroring
   `last4` into the structured `settings.value.broker` row, so the UI
   kept reporting «Токен не задан» while the worker authenticated fine.

Smaller related defects fixed in the same change:

- `set_secret` produced no audit row; auditors couldn't see who overwrote
  a token.
- `tests/test_settings_coverage.py` injected fixture tokens into the
  real SQLite DB on every run because it overrode `set_sqlite_path`
  without setting `ALGOTRADER_DATA_DIR`, polluting the operator's
  running data.
- `_extract_last_bar_ts` only handled gRPC `time.{year,month,day}`
  candles, returning `None` for the new SDK's pre-converted dict
  candles (`{"ts": "YYYY-MM-DD"}`), so every event log showed
  `last_bar_ts=None` even when bars were actually written.

## What Changes

- **`_backfill_one`** now walks the requested `[from_, to]` window in
  7-day chunks, treating each chunk as an independent fetch; partial
  success is kept, full failure is the only error path.
- **`_list_instruments`** removes the asset-class filter so all figi
  classes (share, etf, bond, future) participate in the candle loop;
  the universe discovery in `_discover_universe` still iterates every
  asset class so `instruments` keeps the full metadata picture.
- **`put_settings_token`** mirrors `tokenLast4` (and `tokenRedacted`) into
  the structured settings row in the same transaction so the UI stays in
  sync with the secrets store.
- **`set_secret`** writes an audit row to `ingestion_logs` (last-4 only)
  so unexpected token overwrites are visible in the LogStrip.
- **`tests/test_settings_coverage.py`** switches to an `isolated_client`
  fixture that sets `ALGOTRADER_DATA_DIR` and clears the global
  sqlite-path holder before each test, eliminating the test pollution
  that previously wrote `t.real.WXYZ` into the operator's live DB.
- **`_extract_last_bar_ts`** falls back to `c["ts"]` ISO date before the
  `c.time.{year,month,day}` path, covering the SDK's new dict shape.

## Capabilities

### Modified Capabilities

- `data-fetch`: the backfill runner must produce full-history bar
  coverage for every asset class exposed by Tinkoff, and per-ticker
  log events must show a real `last_bar_ts` rather than `None`.

## Impact

- Backend: `apps/api/src/algotrader_api/ingestion/backfill.py`,
  `apps/api/src/algotrader_api/routes/settings.py`,
  `apps/api/src/algotrader_api/db/secrets.py`.
- Tests: `apps/api/tests/test_settings_coverage.py`,
  `apps/api/tests/test_backfill.py`,
  `apps/api/tests/test_backfill_run_coverage.py`.
- Operational: existing settings rows need to be re-saved once so the
  new `tokenLast4`/`tokenRedacted` mirrors appear (the bug only affected
  writes that happened before the fix).
- No public API shape changes; `PUT /api/settings/token` keeps the same
  response schema, just no longer lets the secrets row drift.
