## Context

The backfill runner needs to fetch years of historical daily candles
from the live Tinkoff API for every instrument Tinkoff exposes. The
runner made one `get_candles` call per figi for a `from_..to` window
the operator picks (5 years by default), but live Tinkoff caps the
request span at ~7 days for the day interval — a fact we discovered
empirically and isn't spelled out in the FAQ the same way the other
intervals are (`CANDLE_INTERVAL_DAY: от 1 дня до 1 года` is the FAQ
claim but the actual implementation rejects wider windows). Worse, a
prior commit had added a `class IN ('share','etf')` filter that
silently dropped bonds/futures from the loop, even though those asset
classes are well-served by `get_candles` per the same docs.

The settings/token sync bug is unrelated to backfill but exposed by the
same operator flow: the operator expects the UI to confirm a token
save, but the UI's "Settings" view reads from `settings.value.broker`
which the token endpoint never wrote to, so the page kept reporting
«Токен не задан» while the worker happily authenticated.

The `tests/test_settings_coverage.py` pollution is also unrelated but
symptomatically related: the test fixture wrote `t.real.WXYZ` into
the operator's real `state.db` on every pytest run, which overwrote
whatever the operator had just saved. This is what turned the
settings sync bug from a cosmetic display issue into a real
operational hazard.

## Goals / Non-Goals

**Goals:**

- Fetch the full configured history for every figi class Tinkoff serves.
- Keep `ingestion_logs.figi` and `last_bar_ts` populated for every
  successful fetch so the operator can see what's happening.
- Make settings/token write a single atomic operation across the two
  tables so the UI cannot drift from the secrets store.
- Stop pytest runs from polluting the operator's live DB.

**Non-Goals:**

- Replacing the live API with the REST history archive. That endpoint
  only serves share/etf and is documented as such; live `get_candles`
  covers every asset class with chunked pagination, so we keep it.
- Re-designing the audit pipeline beyond `set_secret`. A full audit
  log is a separate concern.
- Adding rate-limit-aware scheduling for the backfill worker. Today's
  flow is fire-and-forget per ticker.

## Decisions

- **Chunk size = 7 days.** Picked empirically: live Tinkoff accepts up
  to ~7-day windows on `CANDLE_INTERVAL_DAY` and rejects longer ones
  with `INVALID_ARGUMENT 30014`. Smaller chunks (1 day) would balloon
  the request count (5y × 365 calls per figi); 7 days keeps it at
  ~260 calls per figi per 5-year window, well under the 30 000 req/min
  limit of the live API.
- **Treat partial chunk failure as success.** If 1 of N chunks
  returns bars we keep going and write whatever we got; the failed
  chunk is logged at `warn` so the operator can investigate. This
  matches the documented operator UX ("honest n/a" beats "all-or-
  nothing").
- **No cache for "figi has no history".** Out of scope; would need a
  new SQLite table and TTL. Bonds/futures/options do have history, so
  the cache wouldn't trigger much anyway.
- **Mirror `tokenLast4` from the route handler, not `set_secret`.**
  `set_secret` has no HTTP context, so the audit row in the handler
  carries the actual request metadata (`request.client.host`,
  `user-agent`) which is what the operator needs to debug a stray
  write. The route handler is the only place where we have both pieces.

## Risks / Trade-offs

- Chunking multiplies request count. For 1 921 shares × 5 years, that
  is roughly 1 921 × 260 ≈ 500 000 requests. Live Tinkoff caps at
  30 000 req/min, so the worst case is ~17 minutes per universe; in
  practice the cache misses on second run, but a first-run backfill
  will saturate rate-limit. The runner doesn't currently pause on
  rate-limit errors; that's a separate follow-up.
- Tests in `tests/test_backfill.py` and `tests/test_backfill_run_coverage.py`
  assume a single `get_candles` call per `backfill_one`. We updated
  the assertions to expect the chunked count, but the test fixtures
  had to be updated alongside the production change. Any new test
  that mocks `get_candles` will need to either return an empty list
  per chunk (forcing the chunked retry path) or return the same
  fixture per chunk (as the existing tests now do).
- Removing the `class IN ('share','etf')` filter restores bonds and
  futures to the loop. The investor UI doesn't currently visualise
  them differently from shares, so the Bars tab will start showing
  them without any UI gating. That's a feature, not a regression.
