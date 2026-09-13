# ru-data-sources — Tasks

Implementation tasks are tracked in
`docs/superpowers/plans/2026-09-12-ru-data-sources.md`
(11 TDD-driven tasks, all with ≥95% coverage gate).

## High-level checklist

- [ ] Task 1: Protocol definitions + sources package skeleton
- [ ] Task 2: Shared DuckDB + parquet helpers
- [ ] Task 3: Extend `pipeline.py` PhaseName literal
- [ ] Task 4: CBR macro fetch (real + fake)
- [ ] Task 5: MOEX ISS indices fetch
- [ ] Task 6: Tinkoff fundamentals fetch + coverage report
- [ ] Task 7: Tinkoff corporate actions fetch
- [ ] Task 8: Wire DuckDB views in `db/duck.py`
- [ ] Task 9: Admin refresh endpoints (4 POST)
- [ ] Task 10: Read endpoints (4 GET)
- [ ] Task 11: End-to-end integration smoke test

## Out of scope (deferred)

- [ ] Real SOAP/REST clients (zeep for CBR, httpx for MOEX ISS) — production
      wiring is a follow-up; current plan stops at Protocol contract + fakes.
- [ ] `e-disclosure.ru` PDF parser — gated on fundamentals coverage audit
      (`GET /api/fundamentals/coverage` shows <60%).
- [ ] LLM sentiment pipeline — research actively negative 2026 evidence;
      do not invest until gated.

## Coverage gate

Per `pyproject.toml [tool.coverage.report] fail_under = 95`:

```bash
cd apps/api && uv run pytest --cov=algotrader_api.ingestion.sources --cov-fail-under=95
```

Required to pass before any task is marked done.