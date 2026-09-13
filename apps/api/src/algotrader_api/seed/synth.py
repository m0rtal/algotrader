"""Deterministic synthesizer for OHLCV bars.

Generates 16 fake MOEX tickers × 252 daily bars with seed=42 so the
backend can serve something realistic before real Tinkoff data arrives.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import duckdb

TICKERS = [
    {"ticker": "SBER", "name": "Сбер Банк", "sector": "Финансы", "base_price": 280.0, "vol": 0.02},
    {"ticker": "GAZP", "name": "Газпром", "sector": "Энергетика", "base_price": 165.0, "vol": 0.025},
    {"ticker": "LKOH", "name": "Лукойл", "sector": "Энергетика", "base_price": 7200.0, "vol": 0.022},
    {"ticker": "YNDX", "name": "Яндекс", "sector": "Технологии", "base_price": 4500.0, "vol": 0.03},
    {"ticker": "GMKN", "name": "Норникель", "sector": "Металлы", "base_price": 150.0, "vol": 0.025},
    {"ticker": "NVTK", "name": "НОВАТЭК", "sector": "Энергетика", "base_price": 1100.0, "vol": 0.02},
    {"ticker": "ROSN", "name": "Роснефть", "sector": "Энергетика", "base_price": 540.0, "vol": 0.022},
    {"ticker": "MTSS", "name": "МТС", "sector": "Телеком", "base_price": 250.0, "vol": 0.018},
    {"ticker": "MGNT", "name": "Магнит", "sector": "Потребсектор", "base_price": 6500.0, "vol": 0.022},
    {"ticker": "ALRS", "name": "АЛРОСА", "sector": "Металлы", "base_price": 70.0, "vol": 0.025},
    {"ticker": "AFKS", "name": "АФК Система", "sector": "Холдинги", "base_price": 18.0, "vol": 0.03},
    {"ticker": "VKCO", "name": "VK", "sector": "Технологии", "base_price": 700.0, "vol": 0.035},
    {"ticker": "OZON", "name": "Ozon Holdings", "sector": "Потребсектор", "base_price": 3500.0, "vol": 0.04},
    {"ticker": "TRNFP", "name": "Транснефть ап", "sector": "Энергетика", "base_price": 145000.0, "vol": 0.015},
    {"ticker": "FEES", "name": "ФСК ЕЭС", "sector": "Энергетика", "base_price": 0.022, "vol": 0.022},
    {"ticker": "IRAO", "name": "Интер РАО", "sector": "Энергетика", "base_price": 4.5, "vol": 0.02},
]


def _lcg(seed: int) -> callable:  # type: ignore[name-defined]
    """Tiny linear congruential generator — reproducible but not crypto."""
    state = [seed]

    def rand() -> float:
        state[0] = (state[0] * 1664525 + 1013904223) & 0xFFFFFFFF
        return state[0] / 0xFFFFFFFF

    return rand


def generate_bars_for_ticker(ticker_info: dict, *, start_date: date, n_days: int = 252, seed: int = 42) -> list[dict]:
    """Generate n_days of OHLCV bars for one ticker using a deterministic RNG."""
    rng = _lcg(seed + hash(ticker_info["ticker"]) % 100000)
    bars = []
    price = ticker_info["base_price"]
    base_vol = ticker_info["vol"]

    for i in range(n_days):
        ts = start_date + timedelta(days=i)
        # Skip weekends (simplification: don't worry about MOEX holidays for synth)
        if ts.weekday() >= 5:
            continue

        # Geometric Brownian-ish motion: log return ~ N(0.0005, vol)
        drift = 0.0005
        noise = (rng() - 0.5) * 2 * base_vol
        log_return = drift + noise
        close = price * math.exp(log_return)
        # Open = previous close, with small gap
        open_p = price * (1 + (rng() - 0.5) * base_vol * 0.3)
        # High / Low bracket the open/close
        high = max(open_p, close) * (1 + rng() * base_vol * 0.5)
        low = min(open_p, close) * (1 - rng() * base_vol * 0.5)
        # Volume — fake but positive
        volume = int(1_000_000 + rng() * 5_000_000)

        bars.append(
            {
                "ticker": ticker_info["ticker"],
                "ts": ts.isoformat(),
                "open": round(open_p, 4),
                "high": round(high, 4),
                "low": round(low, 4),
                "close": round(close, 4),
                "volume": volume,
                "adj_close": round(close, 4),
            }
        )
        price = close

    return bars




def seed_bars_sqlite(sqlite_path: str, *, start_date: date | None = None) -> int:
    """Seed the SQLite `bars` + `instruments` tables.

    Replaces the legacy parquet seeder after `remove-duckdb-and-parquet`.
    Generates one synthetic figi per ticker (UUID v5 from the ticker
    string so the seed is deterministic) and bulk-inserts all candles
    via `replace_bars_for_figi`.

    Returns total bars written.
    """
    import uuid

    from ..db.bars_sqlite import replace_bars_for_figi
    from ..db.sqlite import execute as _sqlite_exec

    start = start_date or (date.today() - timedelta(days=365))
    total = 0

    # NS UUID v5 from "ALGOTRADER_SYNTH:<ticker>" for stability across runs.
    NS = uuid.UUID("12345678-1234-5678-9abc-def012345678")

    # Ensure schema is there (bars + instruments). Migrations run in
    # main.py lifespan before seed_bars_sqlite is called; this is a
    # belt-and-braces no-op.
    try:
        _sqlite_exec(
            sqlite_path,
            "INSERT OR IGNORE INTO instruments (ticker, figi, class, name, currency, lot_size) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("__synth_seed__", "__synth_seed__", "share", "seed", "rub", 1),
        )
        _sqlite_exec(
            sqlite_path,
            "DELETE FROM instruments WHERE figi = '__synth_seed__'",
            (),
        )
    except Exception:
        pass

    for info in TICKERS:
        bars = generate_bars_for_ticker(info, start_date=start)
        figi = str(uuid.uuid5(NS, f"ALGOTRADER_SYNTH:{info['ticker']}"))
        ticker = info["ticker"]
        _sqlite_exec(
            sqlite_path,
            "INSERT OR IGNORE INTO instruments (ticker, figi, class, name, currency, lot_size) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ticker, figi, "share", info["name"], "rub", 1),
        )
        candles = [
            {
                "ts": b["ts"],
                "open": float(b["open"]),
                "high": float(b["high"]),
                "low": float(b["low"]),
                "close": float(b["close"]),
                "volume": int(b["volume"]),
            }
            for b in bars
        ]
        replace_bars_for_figi(sqlite_path, figi, candles)
        total += len(candles)
    return total
