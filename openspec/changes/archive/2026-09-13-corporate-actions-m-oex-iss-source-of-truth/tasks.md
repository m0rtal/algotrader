# Tasks: corporate-actions-m-oex-iss-source-of-truth

## 1. Migrations + backfill script

- [ ] 1.1 Write `tests/test_corporate_actions_source_backfill.py` first with
      3 cases: tinkoff-prefix backfill, moex-prefix backfill,
      rows-without-source deletion.
- [ ] 1.2 Run tests to verify they fail.
- [ ] 1.3 Write `009_corporate_actions_source.sql` (ALTER TABLE
      ADD COLUMN source, index).
- [ ] 1.4 Write `009b_instruments_snapshot.sql` (CREATE TABLE
      instruments_snapshot with primary key (figi, observed_at)).
- [ ] 1.5 Write `apps/api/scripts/migrate_corporate_actions_source.py`
      with `backfill_source_and_cleanup(db_path)`.
- [ ] 1.6 Run tests to verify they pass.
- [ ] 1.7 Commit: `feat(data): corporate_actions source column + snapshot table + backfill`.

## 2. New split-detection importer

- [ ] 2.1 Write `tests/test_corporate_actions_splits.py` first with 4 cases:
      snapshot mode writes rows; detect mode produces split row with
      correct factor and source; detect mode does NOT write when
      face_value unchanged; detect mode handles missing prev_snapshot.
- [ ] 2.2 Run tests to verify they fail.
- [ ] 2.3 Implement
      `apps/api/src/algotrader_api/scripts_import/import_corporate_actions_splits.py`
      with `snapshot` and `detect` modes (full code in design.md §4).
- [ ] 2.4 Implement operator wrapper
      `apps/api/scripts/import_corporate_actions_splits.py`.
- [ ] 2.5 Run tests to verify they pass.
- [ ] 2.6 Commit: `feat(data): split detection via MOEX ISS face_value diff`.

## 3. Update MOEX ISS dividends fetcher

- [ ] 3.1 Update
      `apps/api/src/algotrader_api/scripts_import/import_corporate_actions_moex.py`
      to include `source='moex:iss:dividends'` on every
      `CorporateActionRow`.
- [ ] 3.2 Update
      `apps/api/tests/test_corporate_actions_moex.py` to assert
      `source == 'moex:iss:dividends'` on the returned rows.
- [ ] 3.3 Run tests; they should pass after the source attribute is added.
- [ ] 3.4 Commit: `feat(data): source field on MOEX ISS dividends`.

## 4. Update Tinkoff SDK dividends fetcher

- [ ] 4.1 Update
      `apps/api/src/algotrader_api/scripts_import/import_corporate_actions_tinkoff.py`
      to add `source='tinkoff:dividends'` on every row and to gate
      the live SDK call behind `ALGOTRADER_TINKOFF_ENABLE`.
- [ ] 4.2 Update
      `apps/api/tests/test_corporate_actions_tinkoff.py` to assert
      `source == 'tinkoff:dividends'`.
- [ ] 4.3 Run tests; they should pass after the source attribute is added.
- [ ] 4.4 Commit: `feat(data): source field on Tinkoff dividends`.

## 5. Delete curated JSON

- [ ] 5.1 Delete `apps/api/src/algotrader_api/scripts_import/import_splits_curated.py`.
- [ ] 5.2 Delete `apps/api/src/algotrader_api/scripts_import/data/splits_curated.json`.
- [ ] 5.3 Delete `apps/api/scripts/import_splits_curated.py`.
- [ ] 5.4 Delete `apps/api/tests/test_splits_curated.py`.
- [ ] 5.5 Verify no other file references `import_splits_curated` or
      `splits_curated`. If found, follow the chain.
- [ ] 5.6 Run full suite to confirm no dangling imports.
- [ ] 5.7 Commit: `chore(data): remove curated splits (rejected as unreliable)`.

## 6. OpenSpec apply + archive

- [ ] 6.1 Append the new Requirement (source audit) to canonical
      `openspec/specs/dev-workflow/spec.md`.
- [ ] 6.2 `openspec validate dev-workflow --strict` → pass.
- [ ] 6.3 `openspec archive corporate-actions-m-oex-iss-source-of-truth --yes
      --skip-specs`.
- [ ] 6.4 Commit: `docs(spec): corporate-actions source-of-truth rule`.

## 7. Live verify on prod

- [ ] 7.1 Restart backend so migration 009/009b apply on next request.
- [ ] 7.2 Run `python -m scripts.migrate_corporate_actions_source data/state.db`
      and capture stats (backfilled N, deleted M, kept K).
- [ ] 7.3 Run `python -m scripts.import_corporate_actions_splits data/state.db snapshot`.
      Verify rows in `instruments_snapshot`.
- [ ] 7.4 Run `python -m scripts.import_corporate_actions_moex data/state.db`.
      Verify rows in `corporate_actions` with `source='moex:iss:dividends'`.
- [ ] 7.5 Spot-check: every row in `corporate_actions` has a non-null
      `source`.
- [ ] 7.6 Push branch + open PR via API.
- [ ] 7.7 QA cron reviews; developer cron merges if LGTM.

## 8. Tinkoff live enable (operator action, separate)

- [ ] 8.1 Run a manual live test:
      `ALGOTRADER_TINKOFF_ENABLE=1 python -m
      scripts.import_corporate_actions_tinkoff data/state.db`.
- [ ] 8.2 Verify rows land with `source='tinkoff:dividends'` and the
      dividend amounts look reasonable (compared to MOEX ISS).
- [ ] 8.3 If successful, add to a cron job. If not, file an issue and
      leave disabled.

## Out of scope (left for future change)

- Reconciliation between Tinkoff and MOEX ISS values.
- Currency-aware dividend totals.
- Snapshot-diff-based detection of preferred→common conversions.
- US/global paper splits via Massive / FMP.
- Manual-row ADR for historical splits the snapshot-diff can't recover
  (VTBR 2024 reverse split, SBER 2020 2:1, etc.).
