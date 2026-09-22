# data-fetch Specification (delta)

## ADDED Requirements

### Requirement: BackfillRunner routes history windows to MOEX ISS
The `BackfillRunner.run` method SHALL walk any window whose duration
exceeds 9 months via `BackfillRunner._fetch_year_moex` for each
calendar year it spans, and via the Tinkoff client for the trailing
9 months. This routing applies when `source="auto"` (default) or
`source="moex"`.

#### Scenario: Auto-source with full MOEX coverage
Given a figi with `listed_from=2014-01-01` and zero existing local bars
When the operator POSTs `/api/admin/backfill/start` with no body
Then the runner calls `_fetch_year_moex(ticker, year)` for each
calendar year in the window, then `_backfill_one_tinkoff` is called
for the trailing 9 months (the last 270 days) of the window
unconditionally.

#### Scenario: Sanctions-delisted figi (MOEX probe returns None)
Given a figi where `_get_meta_moex(ticker)` returns `None`
Then `_resolve_source` returns `'tinkoff'` and the runner dispatches to
`_backfill_one_tinkoff` (the chunk loop over `client.get_candles`). The
existing `_fetch_tinkoff_fallback` is a separate code path not used by
this PR.

### Requirement: `_backfill_one` does not poison metadata on empty MOEX response
`BackfillRunner._backfill_one` SHALL NOT mark `status="skipped"` or
stamp `last_bar_ts` when the empty response came from a window that was
routed via `_fetch_year_moex` (the broker is MOEX, not Tinkoff). It
SHALL record a `warn` log line with the per-year fetch count and
continue to the next year.

#### Scenario: MOEX returns zero rows for one historical year
Given `source="auto"` and a 5-year window
When MOEX returns `data.history.data == []` for year 2020
Then `_backfill_one` logs the substring `moex empty year=2020 ticker=…`
(planned implementation: `f"moex empty year={year} ticker={ticker}"`)
and continues to year 2021. No metadata row update.
