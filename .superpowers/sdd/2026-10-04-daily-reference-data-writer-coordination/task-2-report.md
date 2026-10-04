# Task 2 — universe и metadata: DONE_WITH_CONCERNS

## SHA и область

- База: `f809f25fc460742ba40e5b7b2c3f6bcc122556de`.
- Implementation commit: `ad5459496d0892aa5e544da23da42d9c696569d5` — `feat(ingestion): coordinate universe and backfill metadata`.
- Ветка: `feature/daily-reference-writer-coordination`.
- Worktree: `/home/hermes/worktrees/algotrader-daily-writer-expansion`.
- Выполнен только Task 2 из утверждённого brief. Task 1 report, primitive, schema, настройки и coverage configuration не изменены. Task 3–7 не выполнены этим implementer.

Изменены только:

1. `apps/api/src/algotrader_api/ingestion/universe.py`.
2. `apps/api/src/algotrader_api/ingestion/backfill.py`.
3. Новый `apps/api/tests/test_daily_reference_writer_coordination.py`.
4. Этот локальный отчёт, отдельным documentation commit после implementation SHA.

## Реальные владельцы

- `universe-sync/instruments:upsert_instruments`: отдельное принадлежащее функции соединение, `timeout=30.0`, `foreign_keys=ON`; один BEGIN/UPSERT/commit/acquire/release на строку. Срезы по 100 строк не стали атомарными batch-транзакциями. Исправлена только ошибочная формулировка docstring о batching.
- `backfill-metadata/instruments:BackfillRunner._upsert_instrument`.
- `backfill-metadata/metadata:BackfillRunner._seed_metadata_for_figi`.
- `backfill-metadata/metadata:BackfillRunner._upsert_metadata`.

Каждый metadata helper сохраняет своё существующее owned connection и SQLite timeout 5 секунд. Параметры и timestamp готовятся до acquisition. BEGIN, SQL, commit и attempted rollback находятся внутри одного flock; owned close — после release. BEGIN входит в try. `BaseException` сохраняется, включая interruption и первичную ошибку при неудачном rollback. Только numeric primary SQLite BUSY переводится в `WriterLockBusy(reason="sqlite-busy")` с точной identity, canonical paths, timeout 30.0 и исходным cause.

Проверка AST против базы подтверждает неизменные signatures и точные INSERT/UPSERT SQL всех четырёх владельцев. Все остальные функции universe/backfill, включая discovery, run, bar writers и error adapters, AST-identical. `db/sqlite.py`, `writer_lock.py`, `worker.py`, `apps/api/pyproject.toml` и Task 1 report byte-identical. Global helper lock, nested owner/borrower lock, новая API, retry и изменение journal mode не добавлены.

## TDD и исполнение

Interpreter: `/home/hermes/algotrader/apps/api/.venv/bin/python` (Python 3.11.16, pytest 9.1.1). Импорт подтверждён из candidate `apps/api/src`, не main checkout. Каждый запуск использует `env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1`, временные HOME/data/pytest paths, `ALGOTRADER_INGEST_FAKE=1` и отказ outbound IPv4/IPv6 socket connect/connect_ex. Temporary runner: `/home/hermes/.hermes/cache/scratch/task2_offline_pytest.py`.

- Baseline required regressions до source edits: **105 passed, 2 warnings**, exit 0.
- RED до source edits: **79 failed, 6 passed, 2 warnings**, exit 1. Шесть passing cases — healthy observer и существующие no-op contracts. Четыре owner-order cases падали на SQL без held acquisition; contention/identity/order cases также отвергали baseline. Лог: `/home/hermes/.hermes/cache/scratch/task2-red-final.log`.
- Дополнительный RED с окончательными fixtures: baseline owner bodies из точной базы подставлены только в память отдельного процесса, candidate files не менялись: **79 failed, 6 passed, 3 warnings**, exit 1. Третье предупреждение — `PytestAssertRewriteWarning` из replay bootstrap. Лог: `/home/hermes/.hermes/cache/scratch/task2-red-final-fixtures.log`.
- Финальный новый test module: **85 passed, 2 warnings**, exit 0.
- Новый module плюс все required Task 2 regressions: **190 passed, 2 warnings**, exit 0.
- Расширенная проверка ниже: **324 passed, 2 warnings**, exit 0; нет failed/skipped/xfail.
- `git diff --check` и staged diff check: exit 0.

Расширенный GREEN command из worktree root:

```bash
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  /home/hermes/algotrader/apps/api/.venv/bin/python \
  /home/hermes/.hermes/cache/scratch/task2_offline_pytest.py \
  apps/api/tests/test_daily_reference_writer_coordination.py \
  apps/api/tests/ingestion/test_universe.py \
  apps/api/tests/test_universe_filter.py \
  apps/api/tests/test_universe_sync_chain.py \
  apps/api/tests/test_backfill_metadata_poison.py \
  apps/api/tests/test_backfill_run_coverage.py \
  apps/api/tests/test_writer_inventory.py \
  apps/api/tests/test_backfill.py apps/api/tests/test_backfill_coverage.py \
  apps/api/tests/test_writer_lock.py apps/api/tests/test_writer_lock_diagnostics.py \
  -q -p no:cacheprovider --tb=short
```

## Что проверяют тесты

File-backed `fresh_db`, реальные owner bodies и kernel flock, не placeholder routes. Основной fixture содержит instrument `FCOORD`, локальные coverage/listing поля и raw bar `2024-05-15` с close 50. Corporate/split fixtures и owners будущих задач не добавлены.

- Точный порядок acquire, BEGIN, mutation, commit, release, owned close у четырёх владельцев.
- Реальный flock timeout 0.05 секунды через отдельный open-file description: нет BEGIN/DML, полные row snapshots не меняются, release/retry сохраняет данные.
- Реальный SQLite BUSY на BEGIN при независимом non-participating `BEGIN IMMEDIATE`: rollback до release, numeric code 5, exact identity/cause, retry после снятия blocker.
- Commit failure, `Interrupted(BaseException)`, BEGIN/body interruption и rollback failure: primary object identity сохранена; native close всегда выполнен; failed handles действительно closed; независимые rows неизменны и fresh connection/lock работают.
- Отдельный healthy observer отвергает active-at-close, но всё равно закрывает native handle. Исключение допускается только для per-connection флага фактически инъецированной rollback failure.
- Numeric commit injections 5/517 переводятся в BUSY; 1/6 и text-only `database is locked` остаются исходными exceptions. Это deterministic injections, не live commit reproductions.
- Активная cached transaction сохраняет uncommitted marker и open handle; отдельное соединение marker не видит. Universe её не commits/rolls back/closes. После test-owned rollback обычный retry проходит.
- Ошибка commit второй universe строки не отменяет первую; 101 строка получает 101 отдельную транзакцию; повторный вызов не создаёт дубликаты и возвращает прежний processed-row count.
- Разные conflict policies universe и runner для отсутствующих ISIN/sector сохранены. Metadata seed не стирает существующий progress/breaker state; status/timestamp semantics и defaults сохранены.
- Instrument parameters и metadata timestamp вычисляются без held owner lock. Sleep под owner lock отвергается observer; network запрещён. Реальные discovery/sync с bounded broker сохраняют точные FIGI/class/name/lot mappings трёх разных FIGI с общим ticker, без запросов non-tradeable классов.
- Существующий same-path process guard применяется к новым владельцам; primitive thread/non-reentrancy regressions входят в расширенный GREEN.
- WAL и DELETE остаются исходными journal modes; universe busy timeout 30000 ms, metadata 5000 ms и foreign-key settings проверяются на BEGIN.

В ходе тестирования исправлены только test-fixture ошибки: имя breaker column взято из migration 019; WAL явно подготовлен на fixture до tracing; timestamp observer не пытается получить чужой contended flock, а проверяет отсутствие собственного held lock. Source corrections для этих fixture findings не потребовались. Существующие migration 016 setup log messages и два dependency deprecation warnings не исправлялись в Task 2. Требуемые regression tests не нуждались в conversion настоящих owner calls с `:memory:`; их существующие mocked metadata cases не изменены.

## Открытые concerns и передача parent

1. **Backend coverage gate не подтверждён.** В предписанном interpreter отсутствуют `coverage` и `pytest_cov`. Попытка запуска с `--cov=algotrader_api --cov-branch --cov-report=term-missing --cov-fail-under=95` завершилась exit 4: `unrecognized arguments`. Ничего не устанавливалось. Threshold 95, source, omit/exclude rules остаются byte-identical. Targeted GREEN не объявлен full-suite/coverage pass; безопасная изоляция full suite и gate остаются parent Task 6.
2. **Task 5 adapters ещё не реализованы.** Helpers корректно выбрасывают BUSY, но существующие верхние runner/worker/gap catch sites пока могут скрыть metadata failure. Они намеренно не изменены. Нет утверждения о корректном whole-pipeline deferral/status.
3. **Семь-owner wiring ещё не завершён.** Inventory symbol tests проходят, но corporate/dividend merges и worker adjustment относятся к Task 3/4. Actual first/derived interprocess owner stress и operational acceptance не выполнены; primitive concurrency regressions не заменяют их.
4. Local commits выполнены с command-scoped `core.hooksPath=/dev/null`: штатный hook обновляет global codebase memory и запускает инструменты вне разрешённой leaf scope. Hooks, network, production, cron, install, push, merge и delegation не выполнялись. Report forced-added только потому, что `.superpowers/` ignored.
5. Parent должен независимо review exact implementation SHA, затем продолжить Task 3 последовательно. Branch/worktree сохранены. PR/merge/deploy и полное scheduled/seven-day acceptance не входят в этот локальный результат.
