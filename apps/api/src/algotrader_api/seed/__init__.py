"""Seed module."""
import os

from .synth import TICKERS, generate_bars_for_ticker, seed_bars

# ponytail: only seed synthetic bars when no real data is available AND
# ALGOTRADER_SYNTH_SEED is set or token file is missing. Default = off when
# worker has populated data; on when token file is missing.
def should_seed_synth(token_path: str = "~/.hermes/secrets/tinkoff_token") -> bool:
    """Return True if synthetic seed should run.

    Triggers:
    1. ALGOTRADER_SYNTH_SEED=1 env var (explicit override)
    2. Token file does not exist OR is not readable (dev environment fallback)
    """
    if os.environ.get("ALGOTRADER_SYNTH_SEED") == "1":
        return True
    expanded = os.path.expanduser(token_path)
    if not os.path.exists(expanded):
        return True
    return not os.access(expanded, os.R_OK)


__all__ = ["TICKERS", "generate_bars_for_ticker", "seed_bars", "should_seed_synth"]
