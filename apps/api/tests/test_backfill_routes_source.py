"""POST /api/admin/backfill/start accepts `source` and threads it to runner."""
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock


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
