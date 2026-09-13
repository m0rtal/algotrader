# ru-data-sources — Proposal

## Why

Algotrader currently ingests only OHLCV bars from the Tinkoff sandbox
(`apps/api/src/algotrader_api/ingestion/`). The ML/NN research at
`/home/hermes/algotrader_research/report.md` (232 KB, 18 items, 100%
coverage validated) identifies three categories of missing data that
block a production ML/NN strategy:

1. **Macro regime indicators** (CBR Key Rate, USDRUB) — TL;DR #10
   marks macro via FRED as "solid baseline for regime detection", and
   for the MOEX universe the Russian analogue of FRED is the Central
   Bank of Russia SOAP service. Without a regime gate, the strategy
   trades identically in 2022-crisis and 2024-trend regimes, which is
   the classic Sharpe-decay failure mode (research §8 — median
   backtest→live Sharpe decay 73%).
2. **Corporate actions calendar** (dividends, splits, M&A) — Tinkoff
   SDK already exposes `get_dividends` and corporate-events RPCs, but
   algotrader does not consume them. Without dividend-aware price
   adjustment, every quarterly ex-div date produces a 5-15% phantom
   "loss" in backtests on dividend-paying MOEX large-caps (SBER,
   LKOH, GAZP, VTBR).
3. **Fundamentals coverage** (P/E, P/B, EPS, net_debt/EBITDA) — research
   TL;DR #9 flags this as "niche, quarterly cadence" but **only an
   audit of actual coverage on the 50 algotrader tickers can decide**
   whether to invest further engineering effort in DIY fundamentals
   scraping (`e-disclosure.ru` PDF parser).

The research explicitly recommends NOT building DIY Telegram/RSS scrapers
or LLM-sentiment pipelines (research TL;DR #6 — actively negative
2026 evidence for LLM sentiment on equities) until gated by coverage
gaps on the official sources below.

## What Changes

Add four new ingestion pipelines under
`apps/api/src/algotrader_api/ingestion/sources/`, following the existing
pattern of `bars.py` → `pipeline.py` → `db/duck.py`:

* **CBR macro** (`cbr.py`) — `cbr.ru/DailyInfoWebServ/DailyInfo.asmx`
  SOAP service. Metrics: `key_rate`, `usd_rub`, `eur_rub`, `repo_rate`.
  Daily cadence. Stored in `data/macro/cbr_daily.parquet`. Free,
  no registration required.
* **MOEX ISS indices** (`moex_iss.py`) — `iss.moex.com` REST API. Daily
  MOEX index closes (IMOEX, RGBI, MOEXOG). Stored in
  `data/indices/moex_daily.parquet`. Free, public.
* **Tinkoff fundamentals** (`tinkoff_fundamentals.py`) — wraps the
  existing `t_tech.invest` SDK's `get_asset_fundamentals` RPC (already
  in `apps/api/.venv/`). Quarterly cadence. One parquet file per
  ticker under `data/fundamentals/<ticker>.parquet`. Coverage audit
  report returned to operator.
* **Tinkoff corporate actions** (`tinkoff_actions.py`) — wraps
  `get_dividends` + corporate-events RPCs. Stored in
  `data/actions/corporate_actions.parquet`. Free.

Per-source `_client_protocol.py` defines the `Protocol` interface so
each source has a `FakeCbrClient` / `FakeMoexClient` for tests, mirroring
the existing `real_client.py` / `fake_client.py` split.

`pipeline.py` PhaseName literal extends to include
`fetch_macro`, `fetch_indices`, `fetch_fundamentals`,
`fetch_corporate_actions` so each new phase surfaces progress through
the existing `pipeline` SQLite table and `/api/pipeline` SSE stream.

`db/duck.py` registers three new glob views: `cbr_macro`, `moex_indices`,
`ticker_fundamentals`, `corporate_actions`.

Four new admin endpoints, four read endpoints:

| Verb   | Path                                    | Trigger / Response |
|--------|-----------------------------------------|--------------------|
| POST   | `/api/admin/macro/refresh`              | Trigger CBR fetch (returns `run_id`) |
| POST   | `/api/admin/indices/refresh`            | Trigger MOEX ISS fetch |
| POST   | `/api/admin/fundamentals/refresh`       | Trigger Tinkoff fundamentals |
| POST   | `/api/admin/corporate_actions/refresh`  | Trigger Tinkoff corporate actions |
| GET    | `/api/macro/regime`                     | Current CBR regime classification |
| GET    | `/api/indices/recent?days=30`           | Recent MOEX index series |
| GET    | `/api/fundamentals/coverage`            | Per-ticker coverage matrix |
| GET    | `/api/corporate_actions/upcoming?days=14` | Upcoming dividends / events |

All new code lives in `apps/api/src/algotrader_api/ingestion/sources/`
and `apps/api/src/algotrader_api/routes/`. Existing ingestion (bars,
universe discovery) is untouched.

## Out of Scope (explicit non-goals)

* Telegram channel scraping (legal + credentials cost; research flags as
  unnecessary given official CBR + MOEX ISS coverage)
* LLM sentiment pipeline (research actively negative 2026 evidence)
* e-disclosure.ru PDF parsing (Phase 2 only if fundamentals coverage
  audit shows <60% on top-50 tickers)
* Smart-Lab / Banki.ru / Investing.com HTML scraping (fragile; gated
  on budget)
* Self-hosted FinBERT/RuBERT (no GPU benefit over hosted API if needed)

## Trigger for DIY fallback

Per the operator operator instruction on 2026-09-12 ("если будет не
хватать, тогда перейдём на самостоятельную реализацию"), the
fundamentals-coverage audit endpoint is the gating diagnostic. If
`GET /api/fundamentals/coverage` shows:

* ≥60% field coverage on top-50 tickers — Phase 2 closes; no DIY needed.
* <60% coverage — open a follow-up OpenSpec change for
  `e-disclosure.ru` PDF parser + RSS feed.

Similarly for macro: if the strategy needs intraday macro (not
quarterly), CBR internal API or MOEX intraday is the next step.

## Impact

* **Latency:** all new endpoints add zero blocking work to existing
  bars/universe fetches. CBR fetch is one SOAP round-trip (~200 ms);
  MOEX ISS one REST call (~300 ms); Tinkoff fundamentals parallel
  fetch with rate-limit 14 req/min — 50 tickers = ~3.5 minutes.
* **Storage:** `data/macro/cbr_daily.parquet` ≈ 5 KB/year;
  `data/indices/moex_daily.parquet` ≈ 50 KB/year; per-ticker
  fundamentals ≈ 5 KB/ticker/quarter × 50 = 250 KB/quarter.
* **Test coverage:** new modules require ≥95% coverage (algotrader
  hard rule). TDD: Protocol-based fakes drive unit tests; integration
  tests roundtrip fetch → parquet → DuckDB view.
* **No breaking changes** to existing API routes, parquet layout for
  bars, or DB migrations.
* **Backtest realism improvement:** dividend-aware price adjustment
  via `corporate_actions` directly addresses research §7 ("40 bps
  transaction costs kill Sharpe") by making backtests not penalize
  phantom dividend losses.

## Spec / Research Reference

* Research rationale: `/home/hermes/algotrader_research/report.md`
  (TL;DR §9 fundamentals, §10 macro, §11 news; per-item sections #6,
  #10, #11).
* Concrete data-source API docs:
  CBR — https://www.cbr.ru/development/SWS/
  MOEX ISS — https://iss.moex.com/iss/reference/
  Tinkoff — `apps/api/.venv/lib/python3.11/site-packages/t_tech/invest/services.py`
  (lazy-imported in `apps/api/src/algotrader_api/ingestion/real_client.py`).
* Existing data-fetch spec for ingestion pattern reference:
  `openspec/specs/data-fetch/spec.md`.