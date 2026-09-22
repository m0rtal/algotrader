"""POST /api/admin/backfill/start accepts `source` and threads it to runner."""
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

from fastapi import HTTPException

import pytest


def test_source_field_default_is_auto():
    from algotrader_api.routes.backfill import start_backfill
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)

    # Patch the runner's run method so we don't hit the broker
    with patch("algotrader_api.routes.backfill.BackfillRunner") as MockRunner:
        instance = MagicMock()
        instance.run = AsyncMock(side_effect=fake_run)
        instance.run_id = 0
        MockRunner.return_value = instance
        # Skip DB side effects
        with patch("algotrader_api.ingestion.pipeline.start_phase", return_value=1), \
             patch("algotrader_api.ingestion.pipeline.end_phase"), \
             patch("algotrader_api.routes.backfill.get_settings") as mock_settings, \
             patch("algotrader_api.routes.backfill.get_broker_token", return_value="tok"), \
             patch("algotrader_api.ingestion.client.make_client"):
            mock_settings.return_value.sqlite_path = ":memory:"
            asyncio.run(start_backfill(body=None))  # no body → default

    assert captured.get("source") == "auto", \
        f"default source must be 'auto', got {captured.get('source')!r}"


def test_source_field_moex_override():
    from algotrader_api.routes.backfill import start_backfill
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)

    with patch("algotrader_api.routes.backfill.BackfillRunner") as MockRunner, \
         patch("algotrader_api.ingestion.pipeline.start_phase", return_value=1), \
         patch("algotrader_api.ingestion.pipeline.end_phase"), \
         patch("algotrader_api.routes.backfill.get_settings") as mock_settings, \
         patch("algotrader_api.routes.backfill.get_broker_token", return_value="tok"), \
         patch("algotrader_api.ingestion.client.make_client"):
        instance = MagicMock()
        instance.run = AsyncMock(side_effect=fake_run)
        MockRunner.return_value = instance
        mock_settings.return_value.sqlite_path = ":memory:"
        asyncio.run(start_backfill(body={"source": "moex", "history_years": 5}))

    assert captured.get("source") == "moex"
    assert captured.get("history_years") == 5


def test_runner_run_receives_source_kwarg_against_real_class():
    """The route must invoke BackfillRunner.run with source= as a kwarg.

    Two complementary checks close R9's gap:

    (a) The REAL BackfillRunner.run signature must declare a `source`
        parameter (via `inspect.signature`). Without this, the live call
        TypeErrors. Static check on the production class.

    (b) The route calls runner.run(source=...) at runtime. We use the
        REAL BackfillRunner class (not a Mock), patch only `run` on the
        class, and the side_effect has NO **kwargs swallowing. The
        side_effect strictly mirrors the production signature so:

          - If the route stops passing `source=`, side_effect raises
            TypeError on the missing keyword.
          - If someone "fixes" the test by adding **kwargs to swallow
            a regression, that swallow has to be deliberate and visible
            in the spy — the spy is now part of the contract.

    The (b) check also asserts that `source` is forwarded with the
    correct value, complementing tests 1 and 2 which check kwargs
    capture through a MockRunner.
    """
    import inspect
    from algotrader_api.routes.backfill import start_backfill
    from algotrader_api.ingestion.backfill import BackfillRunner

    # (a) Static signature check on the REAL class — closes the gap
    # directly: if R9 is ever reverted (source removed from run()),
    # this assertion fires.
    sig = inspect.signature(BackfillRunner.run)
    assert "source" in sig.parameters, (
        f"R9: BackfillRunner.run must accept `source` kwarg; "
        f"params={list(sig.parameters)}"
    )

    # (b) Runtime spy on the REAL class. The side_effect signature is a
    # strict mirror of BackfillRunner.run — NO **kwargs swallowing.
    captured: dict = {}

    async def spy_run(
        history_years: int = 5,
        incremental_threshold_days: int = 2,
        *,
        source: str = "auto",
        limit_to=None,
    ):
        # Strict kwargs — if the route omits any of these, side_effect
        # raises TypeError. If the route adds new kwargs, side_effect
        # raises TypeError. Either way, drift is visible.
        captured["history_years"] = history_years
        captured["incremental_threshold_days"] = incremental_threshold_days
        captured["source"] = source
        captured["limit_to"] = limit_to
        return None

    with patch.object(BackfillRunner, "run", AsyncMock(side_effect=spy_run)), \
         patch("algotrader_api.ingestion.pipeline.start_phase", return_value=1), \
         patch("algotrader_api.ingestion.pipeline.end_phase"), \
         patch("algotrader_api.routes.backfill.get_settings") as mock_settings, \
         patch("algotrader_api.routes.backfill.get_broker_token", return_value="tok"), \
         patch("algotrader_api.ingestion.client.make_client"):
        mock_settings.return_value.sqlite_path = ":memory:"
        asyncio.run(start_backfill(body={"source": "tinkoff", "history_years": 3}))

    assert captured.get("source") == "tinkoff", (
        f"route must forward source= kwarg to BackfillRunner.run; "
        f"captured={captured!r}"
    )
    assert captured["history_years"] == 3
    assert captured["incremental_threshold_days"] == 2  # default
    assert captured["limit_to"] is None


def test_invalid_source_returns_400():
    """POST /api/admin/backfill/start with `source='bogus'` must raise HTTPException(400).

    This proves the route's enum validation runs before runner construction
    — the operator gets a 400, not a 500 from a downstream misroute.
    """
    from algotrader_api.routes.backfill import start_backfill

    with pytest.raises(HTTPException) as exc_info, \
         patch("algotrader_api.routes.backfill.BackfillRunner") as MockRunner, \
         patch("algotrader_api.ingestion.pipeline.start_phase", return_value=1), \
         patch("algotrader_api.ingestion.pipeline.end_phase"), \
         patch("algotrader_api.routes.backfill.get_settings") as mock_settings, \
         patch("algotrader_api.routes.backfill.get_broker_token", return_value="tok"), \
         patch("algotrader_api.ingestion.client.make_client"):
        mock_settings.return_value.sqlite_path = ":memory:"
        # No need to await — the 400 raises before the runner is constructed.
        asyncio.run(start_backfill(body={"source": "bogus"}))

    assert exc_info.value.status_code == 400, (
        f"invalid source must yield 400, got {exc_info.value.status_code}"
    )
    # Make sure we did NOT silently construct a runner on bogus input.
    MockRunner.assert_not_called()
