# Tasks: corporate-actions-historical-import

## 1. Common writer + dataclass

- [ ] 1.1 Write `tests/test_corporate_actions_common.py` first with 4 cases
      (insert, idempotency, conflict update, invalid action_type).
- [ ] 1.2 Run tests to verify they fail with ImportError.
- [ ] 1.3 Implement `import_corporate_actions_common.py` with
      `CorporateActionRow` dataclass and `merge_into_corporate_actions()`
      helper.
- [ ] 1.4 Run tests to verify they pass.
- [ ] 1.5 Commit: `feat(data): common corporate_actions writer + dataclass`.

## 2. Curated splits JSON + loader

- [ ] 2.1 Write `tests/test_splits_curated.py` first with 4 cases.
- [ ] 2.2 Run tests to verify they fail.
- [ ] 2.3 Write `apps/api/scripts/data/splits_curated.json` with 17
      historical splits for Russian + global paper.
- [ ] 2.4 Write `apps/api/src/algotrader_api/scripts_import/import_splits_curated.py`.
- [ ] 2.5 Write thin operator wrapper at `apps/api/scripts/import_splits_curated.py`.
- [ ] 2.6 Run tests to verify they pass.
- [ ] 2.7 Commit: `feat(data): curated historical splits (Russian + global)`.

## 3. Tinkoff dividends fetcher

- [ ] 3.1 Write `tests/test_corporate_actions_tinkoff.py` first with 3 cases
      (single figi, empty events, iterate-all-figis).
- [ ] 3.2 Run tests to verify they fail.
- [ ] 3.3 Implement `apps/api/src/algotrader_api/scripts_import/import_corporate_actions_tinkoff.py`.
- [ ] 3.4 Write thin operator wrapper at `apps/api/scripts/import_corporate_actions_tinkoff.py`.
- [ ] 3.5 Run tests to verify they pass.
- [ ] 3.6 Run full suite to confirm no regressions.
- [ ] 3.7 Commit: `feat(data): Tinkoff SDK dividends fetcher`.

## 4. MOEX ISS dividends fetcher

- [ ] 4.1 Write `tests/test_corporate_actions_moex.py` first with 3 cases
      (parses ISS payload, skips zero-value, resolves secid).
- [ ] 4.2 Run tests to verify they fail.
- [ ] 4.3 Implement `apps/api/src/algotrader_api/scripts_import/import_corporate_actions_moex.py`.
- [ ] 4.4 Write thin operator wrapper at `apps/api/scripts/import_corporate_actions_moex.py`.
- [ ] 4.5 Run tests to verify they pass.
- [ ] 4.6 Commit: `feat(data): MOEX ISS dividends fetcher (cross-check)`.

## 5. OpenSpec apply + archive

- [ ] 5.1 Append a new Requirement to canonical `openspec/specs/data-fetch/spec.md`
      (multi-source contract + idempotent merge).
- [ ] 5.2 `openspec validate data-fetch --strict` → pass.
- [ ] 5.3 `openspec archive corporate-actions-historical-import --yes --skip-specs`.

## 6. Live verification on prod + PR

- [ ] 6.1 Restart backend via 4-arg supervisor.
- [ ] 6.2 Run `python -m scripts.import_splits_curated data/state.db`.
      Verify `SELECT COUNT(*) FROM corporate_actions WHERE action_type='split';` ≥ 17.
- [ ] 6.3 Run `python -m scripts.import_corporate_actions_tinkoff data/state.db`.
- [ ] 6.4 Run `python -m scripts.import_corporate_actions_moex data/state.db`.
- [ ] 6.5 Spot-check SBER (BBG004730N88): has at least 2020-06-19 split
      and any sourced dividends.
- [ ] 6.6 Verify `bars_adjusted` returns `adj_close < close` for SBER
      pre-2020-06-19 bars.
- [ ] 6.7 `git push -u origin feature/corporate-actions-historical-import`.
- [ ] 6.8 Open PR via GitHub API.
