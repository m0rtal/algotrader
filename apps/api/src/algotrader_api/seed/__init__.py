"""Seed module."""
import os

from .synth import TICKERS, generate_bars_for_ticker, seed_bars


def should_seed_synth(sqlite_path: str | None = None) -> bool:
    """Return True if synthetic seed should run.

    Triggers:
    1. ALGOTRADER_SYNTH_SEED=1 env var (explicit override)
    2. Broker token row in secrets table is empty (dev environment fallback)
    3. sqlite_path unset (no app database to read from yet)

    When ALGOTRADER_SQLITE_PATH is not provided, ALGOTRADER_DATA_DIR/state.db
    is used as a sensible default.
    """
    if os.environ.get("ALGOTRADER_SYNTH_SEED") == "1":
        return True
    db_path = sqlite_path or os.path.join(
        os.environ.get("ALGOTRADER_DATA_DIR", ""), "state.db"
    )
    if not db_path or not os.path.exists(db_path):
        # No app database yet — running fresh, seed synth to bootstrap UI.
        return True
    # Lazy import to avoid circular dep at module load.
    from ..db.secrets import get_broker_token
    try:
        token = get_broker_token(db_path)
    except Exception:
        return True
    return not bool(token)


__all__ = ["TICKERS", "generate_bars_for_ticker", "seed_bars", "should_seed_synth"]
