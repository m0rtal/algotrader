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
Then the runner calls `_fetch_year_moex(ticker, year)` for years
2014..2025 and `_fetch_year_moex(ticker, 2026, last_trading_day=today)`
for 2026, followed by `client.get_candles(figi, listed_from=2025-12-…,
date_to=today)` only when `last_trading_day.year == today.year - 1`
(transition year); otherwise pure MOEX.

#### Scenario: Auto-source with existing recent bars (Tinkoff tail)
Given a figi with `listed_from=2014-01-01` and `bars` rows from
2026-01-01 onwards
Then the MOEX walker runs for years 2014..2025, and the Tinkoff call
covers only `(max(earliest_local_bar_ts - 1 day, listed_from), today)`.

#### Scenario: Sanctions-delisted figi (MOEX probe returns None)
Given a figi where `_get_meta_moex(ticker)` returns `None`
Then the runner uses `BackfillRunner._fetch_tinkoff_fallback` for the
full window. No change from current behaviour.

### Requirement: `_backfill_one` does not poison metadata on empty MOEX response
`BackfillRunner._backfill_one` SHALL NOT mark `status="skipped"` or
stamp `last_bar_ts` when the empty response came from a window that was
routed via `_fetch_year_moex` (the broker is MOEX, not Tinkoff). It
SHALL record a `warn` log line with the per-year fetch count and
continue to the next year.

#### Scenario: MOEX returns zero rows for one historical year
Given `source="auto"` and a 5-year window
When MOEX returns `data.history.data == []` for year 2020
Then `_backfill_one` logs `warn: moex empty year=2020 ticker=…` and
continues to year 2021. No metadata row update.
