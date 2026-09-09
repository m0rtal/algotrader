"""Coverage tests for the SDK-shape-tolerant converters.

Targets the fast-path branches in _candle_to_dict (t-tech SDK flat
dict), the legacy nested-time dict shape, and the gRPC object shape.
"""

from datetime import date, datetime, timedelta

from algotrader_api.ingestion.real_client_convert import (
    _candle_to_dict,
    _to_datetime,
    _to_iso_date,
)


def _fake_date(y=2024, m=6, d=1):
    class D:
        year: int
        month: int
        day: int

    o = D()
    o.year = y
    o.month = m
    o.day = d
    return o


class _FakeQuotation:
    def __init__(self, units, nano=0):
        self.units = units
        self.nano = nano


class _FakeGrpcCandle:
    def __init__(self, year=2024, month=6, day=1, units=100, nano=0, volume=10_000):
        self.time = _fake_date(year, month, day)
        self.open = _FakeQuotation(units, nano)
        self.high = _FakeQuotation(units + 1, nano)
        self.low = _FakeQuotation(units - 1, nano)
        self.close = _FakeQuotation(units, nano)
        self.volume = volume


def test_candle_to_dict_sdk_flat_dict_yesterday():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    out = _candle_to_dict(
        {"ts": yesterday, "open": 1, "high": 2, "low": 0, "close": 1.5, "volume": 999},
    )
    assert out is not None
    assert out["ts"] == yesterday
    assert out["open"] == 1.0
    assert out["volume"] == 999


def test_candle_to_dict_sdk_flat_dict_drops_today_and_future():
    today = date.today().isoformat()
    assert _candle_to_dict({"ts": today, "open": 1, "high": 1, "low": 1, "close": 1}) is None
    future = (date.today() + timedelta(days=1)).isoformat()
    assert _candle_to_dict({"ts": future, "open": 1, "high": 1, "low": 1, "close": 1}) is None


def test_candle_to_dict_sdk_flat_dict_invalid_ts_returns_none():
    out = _candle_to_dict({"ts": "not a date", "open": 1, "high": 1, "low": 1, "close": 1})
    assert out is None


def test_candle_to_dict_sdk_flat_dict_handles_quotation_dict_for_ohlc():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    out = _candle_to_dict(
        {
            "ts": yesterday,
            "open": {"units": 5, "nano": 500_000_000},
            "high": 10.0,
            "low": None,
            "close": 7.5,
            "volume": 0,
        },
    )
    assert out is not None
    assert out["open"] == 5.5
    assert out["high"] == 10.0
    assert out["low"] == 0.0
    assert out["close"] == 7.5
    assert out["volume"] == 0


def test_candle_to_dict_legacy_nested_time_dict_shape():
    yesterday = date.today() - timedelta(days=1)
    out = _candle_to_dict(
        {
            "time": {"year": yesterday.year, "month": yesterday.month, "day": yesterday.day},
            "o": _FakeQuotation(100),
            "h": _FakeQuotation(101),
            "l": _FakeQuotation(99),
            "c": _FakeQuotation(100),
            "v": 1000,
        },
    )
    assert out is not None
    assert out["open"] == 100.0


def test_candle_to_dict_legacy_time_as_object():
    yesterday = date.today() - timedelta(days=1)
    out = _candle_to_dict(
        {
            "time": _fake_date(yesterday.year, yesterday.month, yesterday.day),
            "open": _FakeQuotation(50),
            "high": _FakeQuotation(51),
            "low": _FakeQuotation(49),
            "close": _FakeQuotation(50),
        },
    )
    assert out is not None


def test_candle_to_dict_grpc_object_quotation_none():
    """OHLC quotations as None fall back to 0.0 via the _q helper."""
    yesterday = date.today() - timedelta(days=1)
    c = _FakeGrpcCandle(
        year=yesterday.year, month=yesterday.month, day=yesterday.day,
        units=0, nano=0,
    )
    c.open = None  # type: ignore[assignment]
    c.high = None  # type: ignore[assignment]
    c.low = None  # type: ignore[assignment]
    c.close = None  # type: ignore[assignment]
    out = _candle_to_dict(c)
    assert out is not None
    assert out["open"] == 0.0


def test_candle_to_dict_grpc_object_today_or_future_drops():
    today = date.today()
    c = _FakeGrpcCandle(year=today.year, month=today.month, day=today.day)
    assert _candle_to_dict(c) is None


def test_candle_to_dict_grpc_object_quotes_none():
    yesterday = date.today() - timedelta(days=1)
    c = _FakeGrpcCandle(
        year=yesterday.year, month=yesterday.month, day=yesterday.day,
        units=0, nano=0,
    )
    c.open = None  # type: ignore[assignment]
    c.high = None  # type: ignore[assignment]
    c.low = None  # type: ignore[assignment]
    c.close = None  # type: ignore[assignment]
    out = _candle_to_dict(c)
    assert out is not None
    assert out["open"] == 0.0


def test_candle_to_dict_grpc_object_value_error_in_date_returns_none():
    class _Bogus:
        time = _fake_date(2024, 13, 32)  # invalid month/day
        open = _FakeQuotation(1)
        high = _FakeQuotation(1)
        low = _FakeQuotation(1)
        close = _FakeQuotation(1)

    assert _candle_to_dict(_Bogus()) is None


def test_to_iso_date_with_google_date_shape():
    assert _to_iso_date(_fake_date(2024, 6, 1)) == "2024-06-01"


def test_to_iso_date_falls_back_to_str():
    assert _to_iso_date(date(2024, 6, 1)) == "2024-06-01"


def test_to_datetime_from_string():
    out = _to_datetime("2024-06-01")
    assert isinstance(out, datetime)
    assert out == datetime(2024, 6, 1)


def test_to_datetime_from_date():
    out = _to_datetime(date(2024, 6, 1))
    assert isinstance(out, datetime)
    assert out.year == 2024 and out.month == 6 and out.day == 1
