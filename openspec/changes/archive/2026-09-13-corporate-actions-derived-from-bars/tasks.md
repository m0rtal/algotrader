# Tasks — corporate actions derived from bars

## 1. Pure derive algorithm + tests (TDD)

- [ ] Create `apps/api/tests/test_derive_splits.py` with these failing
      tests:
  - `test_derive_detects_single_split_with_threshold_2x`
  - `test_derive_detects_reverse_split_with_threshold_2x`
  - `test_derive_ignores_below_threshold_change` (ratio 1.5 → no candidate)
  - `test_derive_detects_multiple_splits_in_history`
  - `test_derive_discriminates_bonus_issue_from_reverse_split`
    (volume_ratio ≈ factor → BONU, skip)
  - `test_derive_handles_zero_volume_safely` (zero division guard)
- [ ] Create `apps/api/src/algotrader_api/scripts_import/derive_splits.py`
      with:
  - `SPLIT_RATIO_THRESHOLD = 2.0` (hardcoded — operator explicit
    2026-09-13)
  - `derive_splits_for_figi(bars: list[Bar], face_value: float | None) -> list[CorporateActionRow]`
  - Source string builder
- [ ] Run tests — must be GREEN

## 2. MOEX ISS face_value lookup

- [ ] Add `lookup_face_values(figis: list[str]) -> dict[str, float]`
      using MOEX ISS `/iss/securities/{secid}.json` — reuse existing
      HTTP client.
- [ ] Add test `test_lookup_face_values_handles_missing_secid`
      (404 → None)

## 3. Wire importer

- [ ] Add `run_derivation(db_path: str) -> int` that:
  1. Reads all figis with bars from DB
  2. Looks up current face_value for each (HTTP)
  3. Runs `derive_splits_for_figi` per figi
  4. Calls `merge_into_corporate_actions(db_path, rows)` (existing)
- [ ] Idempotency check: `merge_into_corporate_actions` already
      INSERT-or-replace by PK — duplicate runs are safe.

## 4. Operator wrapper

- [ ] `apps/api/scripts/derive_splits.py` — `python -m scripts.derive_splits
      data/state.db` runs the derivation.

## 5. Tests for end-to-end wiring

- [ ] `test_run_derivation_writes_split_rows` — fixture with 2:1 split
      in bars, face_value doubled → 1 row written with correct factor
      and source.
- [ ] `test_run_derivation_is_idempotent` — run twice, only 1 row
      remains.

## 6. Live verification on prod DB

- [ ] Run derivation on `apps/api/data/state.db`.
- [ ] Verify known events present (YNDX 2014-06-18 4:1, SBER 2020-06-19
      2:1, VTB 2024-07-11 5000:1 reverse split).
- [ ] Verify no fabricated rows (every row has a `derived:*` source).

## 7. OpenSpec apply + archive

- [ ] `openspec apply` and validate.
- [ ] Archive.

## 8. Push + open PR

- [ ] Push branch `feature/corporate-actions-derived-from-bars`.
- [ ] Open PR with rationale body (free derivation; no MOEX subscribe).
