## 1. Chunked `get_candles` in `_backfill_one`

- [x] 1.1 Replace single `get_candles` call with a 7-day chunk loop
- [x] 1.2 Track `chunks_attempted` / `chunks_failed`; treat total failure as a `ticker_progress` error event carrying the last chunk's error message
- [x] 1.3 Return `raw_candles = all_candles` after the loop; downstream `is_closed_candle` / parquet-write logic untouched
- [x] 1.4 Add a `warn`-level `ingestion_logs` row for every failed chunk with the slice range and the error text

## 2. Restore all asset classes to the backfill universe

- [x] 2.1 Drop the `class IN ('share','etf')` filter from `_list_instruments`
- [x] 2.2 Confirm `_discover_universe` still iterates every asset class (`get_shares`, `get_etfs`, `get_bonds`, `get_futures`, `get_options`) so the `instruments` table stays complete
- [x] 2.3 Verify with a pytest run that `tests/test_backfill.py` and `tests/test_backfill_run_coverage.py` keep passing now that every class is in the loop

## 3. `last_bar_ts` extraction covers both candle shapes

- [x] 3.1 Add a fast-path `if isinstance(c, dict) and c.get("ts")` in `_extract_last_bar_ts`
- [x] 3.2 Keep the existing `c.time.{year,month,day}` fallback for the older SDK shape
- [x] 3.3 Smoke-test that `ingestion_logs.bars_written=N last_bar_ts=YYYY-MM-DD` is now populated

## 4. `PUT /api/settings/token` mirrors `last4`

- [x] 4.1 Read current `settings.main` row inside `put_settings_token` after the `set_secret` call
- [x] 4.2 If a row exists, UPDATE it with `broker.tokenLast4 = token[-4:]`, `broker.tokenRedacted = true`, fresh `version`, and `updated_at = CURRENT_TIMESTAMP`
- [x] 4.3 If no row exists yet, INSERT one with `DEFAULT_SETTINGS` populated and the new `last4`
- [x] 4.4 Return `TokenResponse(tokenLast4=last4, tokenRedacted=True)` from the handler — schema unchanged

## 5. `set_secret` audit row

- [x] 5.1 After the SQL upsert in `set_secret`, insert an `ingestion_logs` row with `level='info'` and `message='secrets.write key=<key> last4=<XXXX>'`
- [x] 5.2 Swallow insertion failures (the table may not exist on a fresh test DB)

## 6. Test pollution from `tests/test_settings_coverage.py`

- [x] 6.1 Replace the unsafe `_client(tmp_path)` helper with an `isolated_client` pytest fixture
- [x] 6.2 Inside the fixture, `monkeypatch.setenv("ALGOTRADER_DATA_DIR", str(tmp_path))` and clear `settings_routes._sqlite_path_holder`
- [x] 6.3 Verify that running `pytest apps/api/tests/test_settings_coverage.py` produces no rows in `apps/api/data/state.db`

## 7. Coverage gate

- [x] 7.1 Run `uv run pytest --cov=algotrader_api -q` and confirm ≥95.00% on lines/branches/functions/statements
- [x] 7.2 If the gate fails, add `# pragma: no cover` comments only for unreachable defensive branches (per `pyproject.toml [tool.coverage.report] exclude_lines`)
