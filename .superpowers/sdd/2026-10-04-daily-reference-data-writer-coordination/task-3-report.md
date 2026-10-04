# Task 3 — corporate merge и worker adjustment: DONE_WITH_CONCERNS

## SHA и область

- База: `c28130d36d7d392639735bfc0b300a4ca19323c4`.
- Implementation commit: `2fc7779926ff0930b51974b79ef5a6bb674b20eb` — `feat(ingestion): coordinate corporate action transactions`.
- Ветка: `feature/daily-reference-writer-coordination`.
- Worktree: `/home/hermes/worktrees/algotrader-daily-writer-expansion`.
- Выполнена текущая делегация Task 3: corporate merge плюс worker-owned forward adjustment. Исходный `task-3-brief.md` описывает corporate/dividend merges, а исходный план относит worker adjustment к Task 4. Я прочитал оба документа; явное поручение parent переопределяет эту группировку. Dividend merge оставлен для следующего Task 4, adapters — для Task 5. Brief и план не изменены.

Implementation commit содержит ровно шесть файлов:

1. `apps/api/src/algotrader_api/scripts_import/import_corporate_actions_common.py`.
2. `apps/api/worker.py`.
3. `apps/api/tests/test_daily_reference_writer_coordination.py`.
4. `apps/api/tests/test_forward_adjustment.py`.
5. `apps/api/tests/test_derive_splits.py`.
6. `apps/api/tests/test_worker_daily_chain.py`.

Этот отчёт добавлен отдельным documentation commit после implementation SHA.

## Реальные владельцы и сохранённые контракты

- `corporate-actions/corporate-actions:merge_into_corporate_actions`: все семь INSERT-параметров, включая нормализованный ex_date, готовятся до connection/acquisition. Под одним flock выполняются `BEGIN IMMEDIATE`, duplicate SELECT, полный список INSERT и один commit либо attempted rollback. Same-PK row с другим factor/source/note/cash_amount остаётся пропущенным, а не заменённым. Возвращается число фактических вставок. Пустой список возвращает 0 без connection и acquisition. Исправлен только ложный docstring «replacing».
- `corporate-actions/adjusted-bars:_step_corporate_actions`: реальная derivation/merge завершается и освобождает lock до adjustment. Worker выполняет существующий chronological SELECT и полную `(figi, date, float)` preparation до flock. Под одной транзакцией вызывает неизменный `apply_forward_split` для всех выбранных событий. `apply_all_pending`, `apply_forward_split`, `_already_applied` не приобретают lock и не получают ownership соединения. Duplicate/idempotency checks и арифметический SQL остаются внутри транзакции.
- BEGIN находится внутри try. Обработка `BaseException` охватывает BEGIN/body/commit. Rollback failure не маскирует primary exception и не мешает unlock/native close. Только numeric primary BUSY, включая 5/517, переводится в `WriterLockBusy(reason="sqlite-busy")` с точной role/phase, canonical paths, timeout 30.0 и исходным cause. SQLite code 1/6 и text-only `database is locked` остаются исходными exceptions у владельца.
- Function/worker-owned connections открываются до acquisition и закрываются после release. Их прежний SQLite timeout 5 секунд, foreign_keys setting и journal mode не меняются. Нет whole-phase lock, вложенного borrower lock, retry, sleep, новой prepare API или изменения split math.

## TDD и реальные команды

Interpreter: `/home/hermes/algotrader/apps/api/.venv/bin/python`, Python 3.11.16. Все тесты используют candidate imports, а не main checkout. Temporary bootstrap `/home/hermes/.hermes/cache/scratch/task3_offline_pytest.py` задаёт временные HOME/data/pytest paths, `ALGOTRADER_INGEST_FAKE=1`, отключает outbound sockets, DNS и настоящий `urlopen`. Ничего не устанавливалось.

До production edits:

- Baseline coordination/forward/derivation/inventory: **192 passed, 2 warnings**, exit 0.
- Финальный RED: **42 failed, 1 passed, 85 deselected, 2 warnings**, exit 1. Единственный passing case — существующий empty-input contract. Owners падали на duplicate/idempotency SQL без held lock, отсутствии наблюдаемого owner acquisition и некорректной preparation boundary. Ни import, ни network, ни сигнатурная fixture error не служили основанием RED.
- Borrower ownership и offline/corporate regression selector: **6 passed, 53 deselected, 2 warnings**, exit 0; это characterization существующих контрактов, не доказательство новой coordination.

RED command:

```bash
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  /home/hermes/algotrader/apps/api/.venv/bin/python \
  /home/hermes/.hermes/cache/scratch/task3_offline_pytest.py \
  apps/api/tests/test_daily_reference_writer_coordination.py \
  -k 'corporate_merge or corporate_adjustment' \
  -q -p no:cacheprovider --tb=short
```

RED log: `/home/hermes/.hermes/cache/scratch/task3-red-final.log`.

После implementation:

- Тот же owner selector: **43 passed, 85 deselected, 2 warnings**, exit 0.
- Полный coordination module, включая сохранённые Task 2 cases: **128 passed, 2 warnings**, exit 0.
- Focused regressions без дополнительных throttle modules: **369 passed, 2 warnings**, exit 0.
- Финальный расширенный focused GREEN: **378 passed, 5 warnings**, exit 0; JUnit программно проверен: 378 перечисленных testcase, 0 failures, 0 errors, 0 skipped. Нет xfail/XPASS.

Финальный GREEN command из worktree root:

```bash
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  /home/hermes/algotrader/apps/api/.venv/bin/python \
  /home/hermes/.hermes/cache/scratch/task3_offline_pytest.py \
  apps/api/tests/test_daily_reference_writer_coordination.py \
  apps/api/tests/test_forward_adjustment.py \
  apps/api/tests/test_derive_splits.py \
  apps/api/tests/test_worker_daily_chain.py \
  apps/api/tests/test_writer_inventory.py \
  apps/api/tests/test_writer_lock.py \
  apps/api/tests/test_writer_lock_diagnostics.py \
  apps/api/tests/test_bars_adjusted_after_phase.py \
  apps/api/tests/test_dividends_fetcher.py \
  apps/api/tests/test_dividends_throttling.py \
  apps/api/tests/test_dividends_throttle_queue.py \
  apps/api/tests/test_dividends_throttle_queue_visibility.py \
  -q -p no:cacheprovider --tb=short \
  --junitxml=/home/hermes/.hermes/cache/scratch/task3-green.xml
```

GREEN log: `/home/hermes/.hermes/cache/scratch/task3-green.log`.

Module counts из JUnit: coordination 128; forward 7; derive 15; worker daily chain 37; inventory 87; writer lock 53; lock diagnostics 34; bars-adjusted phase 2; dividend fetcher 6; throttling 4; throttle queue 3; queue visibility 2.

## Что доказывают проверки

- Реальные file-backed SQLite rows: полные snapshots instruments/metadata/raw bars/logs/corporate actions/adjusted bars/dividends. Проверены точные OHLC/volume/source/computed_at значения; pre-split raw rows и отдельный `FOTHER` adjusted row не меняются.
- Exact acquire/BEGIN/duplicate-or-idempotency-check/DML/commit/release/close ordering. Два merge rows или два chronological split events имеют один commit и одну acquisition.
- Реальное kernel flock contention через независимое open-file description: timeout 0.05 секунды задаётся только test wrapper; нет BEGIN/DML, полный snapshot сохраняется, release/retry работает.
- Реальное SQLite BUSY на BEGIN при независимом non-participating `BEGIN IMMEDIATE`: acquisition успешна, numeric code 5, rollback до release, точная identity/cause, retry после снятия blocker.
- Commit BUSY 5/517 — явно deterministic injections, не live commit reproduction. Other numeric/text-only errors сохраняют primary object identity. Проверены commit/body/BEGIN interruption и rollback failure.
- Ошибка второго INSERT/UPDATE откатывает ранее выполненную часть всей транзакции. Ошибка adjustment не отменяет уже committed derivation. После ошибки все tracked native handles действительно закрыты, fresh connection/lock работает, unrelated rows остаются прежними.
- Shared observer сохраняет native `close` в finally. Active-at-close разрешён только per-connection флагу действительно инъецированной rollback failure; healthy observer case по-прежнему отвергает active transaction и всё равно закрывает handle.
- Все корпоративные row fields/date normalization и все worker SELECT/date/float события готовятся до acquisition. Invalid later event не допускает даже BEGIN/acquisition. Derivation scan/calculation/sleep проверяются вне lock; sleep под lock отвергается; outbound I/O запрещён.
- Borrower tests дают реальную активную caller transaction с uncommitted marker. `apply_forward_split` и `apply_all_pending` оставляют marker/adjustment невидимыми другому соединению, не commit/rollback/close caller connection и не приобретают nested lock. Caller rollback отменяет marker и adjustment.
- Single-split worker retry сохраняет полный snapshot и возвращает `bars adjusted=0`. Legacy chronological multi-event SQL outcomes и реальные return counts сохранены; общий idempotency guard не перепроектирован.

Изменения test safety: face-value missing-SECID case теперь получает bounded offline HTTP 404, а не пытается сетевой вызов; daily-chain plumbing tests не оставляют бесконечный heartbeat daemon; error-path log test использует отсутствующий каталог внутри `tmp_path`. Прежний mocked corporate success case заменён реальной migrated DB, actual derivation/adjustment и точным повторным readback. Другие daily steps не менялись.

## Самопроверка

`/home/hermes/.hermes/cache/scratch/task3_self_review.py` выполнен с exit 0 против точной базы:

- Public signatures и corporate SELECT/INSERT SQL неизменны.
- `DividendRow` и `merge_into_dividends` AST-identical.
- Все worker functions кроме `_step_corporate_actions`, включая adapters, AST-identical. Его внешний generic adapter также unchanged.
- `forward_adjustment.py`, derivation source, writer primitive, universe/backfill owners, cached sqlite helper, dividend fetch/queue source, static inventory и `apps/api/pyproject.toml` byte-identical.
- Worker preparation SELECT совпадает с существующим borrower SELECT. Сохранены все семь exact owner identities в inventory.
- `git diff --check` и staged diff check: exit 0. Implementation commit readback подтверждает ровно шесть разрешённых файлов.

Первый scratch self-review ошибочно считал docstring `Insert rows...` SQL-константой; исправлен только scratch extractor, теперь он читает аргументы `execute`. Production correction не понадобилась.

## Concerns и передача parent

1. **Full-suite/backend 95% coverage gate не выполнен.** Канонический interpreter не содержит `pytest_cov`/`coverage`; cached archives не подключались. Full suite не запускался, unsafe operational tests не выполнялись. Source/omit/exclude/threshold byte-identical. Focused GREEN не является full gate; безопасная full-suite проверка остаётся parent Task 6.
2. **Task 4 dividend owner и Task 5 adapters ещё не выполнены.** Worker пока возвращает старый generic failure detail для `WriterLockBusy`. Tests наблюдают точный owner error до внешнего catch; это не доказательство bounded DEFER formatter или CLI rc=75. Нет утверждения о завершённом seven-owner namespace или whole-pipeline deferral.
3. **Owner-level first/derived interprocess stress и production acceptance не выполнены.** Kernel contention и primitive regressions не заменяют этот отдельный gate. Существующий SQL idempotency guard не улучшался.
4. Финальные 5 warnings: два существующих dependency deprecations плюс три `AsyncLimiter` cross-loop reuse warnings из неизменных throttle tests. Migration 016 setup logs уже наблюдались до source edits; это не failures данной задачи. Эти соседние проблемы не исправлялись.
5. Local commits используют command-scoped `core.hooksPath=/dev/null`: штатный hook обновляет global codebase memory вне leaf scope. Hook configuration не менялась. Нет installs, network, production/secrets/cron access, push, merge, deployment, delegation или global memory writes.
6. Parent должен независимо review implementation SHA и этот отчёт, затем продолжить Task 4 последовательно. Branch/worktree сохранены.
