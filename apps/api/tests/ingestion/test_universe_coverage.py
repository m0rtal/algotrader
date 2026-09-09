"""Coverage for universe.discover_universe error paths."""

import pytest

from algotrader_api.ingestion.universe import discover_universe


class _MixedClient:
    """Some SDK methods succeed, some raise — exercises per-class error logs."""

    async def get_shares(self):
        return [{"ticker": "SBER", "figi": "BBG004730N88", "class": "share"}]

    async def get_bonds(self):
        raise RuntimeError("bonds broken")

    async def get_etfs(self):
        return [{"ticker": "FXRL", "figi": "BBG111", "class": "etf"}]

    async def get_futures(self):
        raise ConnectionError("futures timeout")

    async def get_options(self):
        return [{"ticker": "OPTX", "figi": "BBG222", "class": "option"}]


@pytest.mark.asyncio
async def test_discover_universe_continues_on_class_error():
    out = await discover_universe(_MixedClient())
    # 3 classes returned (shares, etfs, options), 2 raised and were logged.
    classes = sorted(r["class"] for r in out)
    assert classes == ["etf", "option", "share"]
    assert len(out) == 3
