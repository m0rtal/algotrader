# Task 4 — worker-owned corporate adjustment: PREIMPLEMENTED_VERIFIED

## Результат и SHA

- Делегация начата с clean HEAD `3b0c966f699a8e3c09a3f1913216d50638bbd5ba`, ветка `feature/daily-reference-writer-coordination`, worktree `/home/hermes/worktrees/algotrader-daily-writer-expansion`.
- Первым прочитан exact `task-4-brief.md`. Действующая Task 4 относится только к worker adjustment; прежнее упоминание dividend Task 4 в отчёте Task 3 не использовано как разрешение менять dividend owner.
- **Обнаружено пересечение задач:** approved implementation commit Task 3 `2fc7779926ff0930b51974b79ef5a6bb674b20eb` уже содержит весь worker block из текущего Task 4 brief и требуемые runtime tests. `git show` подтверждает происхождение реализации. Поэтому повторного production изменения нет; Task 3 не отменялась, не переписывалась и не amended/rebased.
- Этот Task 4 commit сохраняет только данный отчёт. Source, tests, design, plan и brief остаются неизменными. Это validation-only работа, не новая реализация и не новый test-first цикл. Commit body явно отмечает повторную проверку существующей реализации.
- Требуется parent review этого отчёта и уже существующего implementation SHA перед Task 5. Task 5 не начиналась.

## Сверка с approved block и design

Программная AST-проверка дала exit 0: последовательность от `written = derive_splits.run_derivation(db_path)` до `finally: conn.close()` совпадает с executable block `task-4-brief.md`, без структурных расхождений. Проверены public return annotation `tuple[bool, str]` и точное совпадение chronological SELECT с `apply_all_pending`.

Сохранены границы `openspec/changes/coordinate-daily-reference-data-writers/design.md`, sections «Exact additional owners and boundaries», owner 6, и «Corporate preparation versus mutation»:

1. Derivation, read scans и split detection завершаются до worker connection и adjustment acquisition. Corporate merge имеет собственную уже committed транзакцию и release.
2. Worker connection открывается до flock. SELECT и полная `(figi, date, float)` preparation выполняются до acquisition.
3. Ровно один `corporate-actions/adjusted-bars` lock охватывает `BEGIN IMMEDIATE`, реальные borrower calls и один commit либо attempted rollback. Acquisition symbols связаны на worker module scope.
4. `apply_forward_split` и `_already_applied` не замоканы в transaction tests. Consistency check и arithmetic SQL остаются внутри транзакции; borrower не получает ownership connection.
5. BEGIN/body/commit охвачены `BaseException`; rollback пытается выполниться до release. Rollback failure не маскирует primary failure. Native close выполняется после release.
6. Existing numeric BUSY translation сохраняет primary cause внутри owner. Наружный generic worker adapter возвращает `False`, а не ложный success. Bounded DEFER formatter и downstream propagation остаются Task 5; их завершение не заявляется.
7. Forward convention, calculated OHLC/volume, selected-event transaction grouping и idempotency guard не менялись. `apply_all_pending` сохраняет public borrower API.

Отдельная byte comparison с начальным approved HEAD подтверждает неизменность 11 source/test/config paths: worker; forward-adjustment; derive-splits; common importer; writer primitive; `apps/api/pyproject.toml`; coordination, forward, derive, daily-chain и inventory tests. Остальные source paths также отсутствуют в diff.

## RED: исторический TDD и независимый replay

Исторический RED действительно существует **до implementation**: прочитан `/home/hermes/.hermes/cache/scratch/task3-red-final.log` против базы `c28130d36d7d392639735bfc0b300a4ca19323c4`. Результат: **42 failed, 1 passed, 85 deselected, 2 warnings**, exit 1. Единственный pass относится к прежнему empty-input merge contract. Этот вывод отделён от новых запусков ниже; он не выдаётся за новый RED этой делегации.

Для независимой проверки воспроизведён **worker-only RED**. Scratch bootstrap `/home/hermes/.hermes/cache/scratch/task4_offline_pytest.py` загружает неизменный `worker.py` через `git show c28130d36d7d392639735bfc0b300a4ca19323c4:apps/api/worker.py` в отдельный Python process. Current approved helpers/tests сохранены; tracked source не меняется. Это replay pre-implementation worker, а не checkout/revert и не новый production edit.

Команда из worktree root:

```bash
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  /home/hermes/algotrader/apps/api/.venv/bin/python \
  /home/hermes/.hermes/cache/scratch/task4_offline_pytest.py \
  --replay-pre-implementation-worker \
  apps/api/tests/test_daily_reference_writer_coordination.py \
  -k 'worker-adjustment or corporate_adjustment or corporate_merge_real_derivation' \
  -q -p no:cacheprovider --tb=short \
  --junitxml=/home/hermes/.hermes/cache/scratch/task4-red-replay.xml \
  > /home/hermes/.hermes/cache/scratch/task4-red-replay.log 2>&1
```

Результат: **24 failed, 104 deselected, 3 warnings**, exit 1. JUnit перечисляет 24 testcase, 24 failures, **0 errors, 0 skipped**. Причины: нет observed owner acquisition/transaction, consistency/DML вызываются без held lock, отсутствует preparation boundary и явный rollback path. Ни import error, ни network failure, ни неверная fixture signature не служат основанием RED. Третий warning относится только к replay bootstrap: `PytestAssertRewriteWarning` для заранее импортированного `anyio`.

Тот же selector без `--replay-pre-implementation-worker` на approved worker дал **24 passed, 104 deselected, 2 warnings**, exit 0. JUnit: 24 testcase, **0 failures, 0 errors, 0 skipped**. Logs: `task4-owner-green.log`, `task4-owner-green.xml` в scratch directory. Source между этим RED replay и GREEN не менялся: различается только worker revision, загружаемая scratch bootstrap.

## GREEN: полный Task 4 focused selector

Interpreter: `/home/hermes/algotrader/apps/api/.venv/bin/python`, Python 3.11.16. Worktree-local `.venv` отсутствует; используется существующая environment без установки dependencies. Bootstrap вставляет candidate `apps/api/src` и `apps/api` в `sys.path`, назначает temporary HOME/data/pytest paths под `$TMPDIR`, устанавливает `ALGOTRADER_INGEST_FAKE=1` и запрещает outbound sockets, DNS и реальный `urlopen`. Production DB/logs не открываются.

```bash
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  /home/hermes/algotrader/apps/api/.venv/bin/python \
  /home/hermes/.hermes/cache/scratch/task4_offline_pytest.py \
  apps/api/tests/test_daily_reference_writer_coordination.py \
  apps/api/tests/test_forward_adjustment.py \
  apps/api/tests/test_derive_splits.py \
  apps/api/tests/test_worker_daily_chain.py \
  apps/api/tests/test_writer_inventory.py \
  -q -p no:cacheprovider --tb=short \
  --junitxml=/home/hermes/.hermes/cache/scratch/task4-green.xml \
  > /home/hermes/.hermes/cache/scratch/task4-green.log 2>&1
```

Результат: **274 passed, 2 warnings**, exit 0. JUnit totals программно сверены с перечисленными testcase: **274 tests, 0 failures, 0 errors, 0 skipped**. По modules: coordination 128; forward-adjustment 7; offline derivation 15; daily-chain 37; inventory 87. Baseline до scratch RED replay также дал 274 passed.

Два GREEN warnings — существующие FastAPI/Starlette deprecations. Migration 016 setup logs в RED replay присутствуют также в историческом Task 3 RED; они не являются failures данного owner contract и не исправлялись.

## Что подтверждают реальные тесты

- Transaction-order tests mock только derivation return 0, чтобы adjustment writes не смешивались с merge. Реальны SQLite, `apply_forward_split`, `_already_applied` и readback `bars_adjusted`.
- Preparation SELECT/date/float предшествует единственной adjustment acquisition. Chronological two-event transaction сохраняет literal OHLC/volume outcomes; invalid later event не допускает даже BEGIN.
- Success, BEGIN/UPDATE interruption, SQL/commit errors, rollback failure и реальный non-participating SQLite writer проверяют cleanup. Rollback precedes release; real raw bars и unrelated adjusted rows остаются прежними.
- Real derivation boundary доказывает merge release до adjustment acquisition; прерывание adjustment не отменяет уже committed corporate action. Derivation scans, calculation и `sleep(0)` наблюдаются вне lock; sleep под owner lock отвергается.
- `test_step_corporate_actions_ok` использует ровно два discontinuous bars с равным volume: `2024-05-14` close 100 и `2024-05-15` close 50, volume 100. Реальные derivation и worker дают `splits derived=1 bars adjusted=1`, затем `splits derived=0 bars adjusted=0`. Readback: `2024-05-15`, adjusted close 25.0, volume 100; raw bars неизменны.
- Caller-owned active transaction tests для обоих borrowers сохраняют uncommitted marker, скрывают marker/adjustment от другого connection и не разрешают helper commit/rollback/close или nested acquisition. Open worker connection подтверждается actual consistency reads и DML.
- Generic operational failure не превращается в `(True, ...)`. Exact owner BUSY identity/cause наблюдается до существующего наружного catch; это не доказательство Task 5 formatted propagation.

## Открытые ограничения

- Source implementation уже была включена в approved Task 3. Новая Task 4 implementation/новый test-first цикл не заявляются; повторять или искусственно ломать reviewed source не требовалось. Parent должен принять эту evidence-only фиксацию пересечения задач.
- Full operational suite и backend 95% coverage gate не выполнялись. Live environment подтверждает отсутствие `pytest_cov` и `coverage`. Coverage omit/exclude/threshold не менялись; focused GREEN не выдаётся за full gate.
- Interprocess first/derived stress, deployment и standing-goal acceptance остаются parent gates. Task 5 adapters, dividend merge и другие владельцы в этой делегации не изменялись.
- Никаких installs, network, production/secrets/cron access, push, merge, delegation или global memory writes. Local commit использует command-scoped `core.hooksPath=/dev/null`, чтобы не вызвать штатный global codebase-memory hook; persistent hook configuration не меняется.
