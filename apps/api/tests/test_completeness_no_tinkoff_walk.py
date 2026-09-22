"""run_completeness_pass must not call Tinkoff with >7-day windows
(a known Tinkoff SDK limit that returned empty on 2026-09-16).

Tripwire (R10): find_gap_intervals must never return a window larger
than the Tinkoff 9-month (270-day) budget, so the completeness pass
never hands a >270-day window to the broker (which would hit the
Tinkoff empty cut-off and trigger metadata poison).
"""
from datetime import date, timedelta

from algotrader_api.data_quality.completeness import find_gap_intervals


def _seed_db(tmp_path):
    """Two figis:

    * Figi A — one early bar on 2026-07-15, then a ~38-day void, then
      bars 2026-08-22..08-30. The pair (2026-07-15, 2026-08-22) spans
      a single internal gap detectable by ``find_gap_intervals``.
    * Figi B — 19 consecutive daily bars (Jan 1..19). No internal gap.
    """
    import sqlite3
    db = tmp_path / "state.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE bars (figi TEXT, ts DATE, open REAL, high REAL,
                           low REAL, close REAL, volume INTEGER,
                           source TEXT, PRIMARY KEY (figi, ts));
        CREATE TABLE moex_holidays (date TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE instrument_metadata (figi TEXT PRIMARY KEY,
                                          last_run_status TEXT,
                                          total_bars INTEGER,
                                          first_bar_ts DATE,
                                          last_bar_ts DATE);
    """)
    # Figi A: early bar, then a 38-day void, then Aug 22..30
    con.execute(
        "INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("A", date(2026, 7, 15).isoformat(), 1, 1, 1, 1, 1, "tinkoff"),
    )
    for d in range(9):
        ts = date(2026, 8, 22) + timedelta(days=d)
        con.execute("INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    ("A", ts.isoformat(), 1, 1, 1, 1, 1, "tinkoff"))
    # Figi B: 19 consecutive daily bars
    for d in range(19):
        ts = date(2026, 1, 1) + timedelta(days=d)
        con.execute("INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    ("B", ts.isoformat(), 1, 1, 1, 1, 1, "tinkoff"))
    con.commit()
    con.close()
    return str(db)


def test_gap_detection_emits_window_within_9_months(tmp_path):
    """Tripwire: the gap find_gap_intervals emits for Figi A must fit
    inside the Tinkoff 9-month (270-day) window so the completeness
    pass does not regress to handing the broker a >270-day window.
    """
    db = _seed_db(tmp_path)
    gaps = find_gap_intervals(db, "A", min_gap_days=5)
    assert len(gaps) == 1, f"expected 1 gap, got {gaps}"
    start, end = gaps[0]
    # Caller fetches [start + 1, end]; the fetch-window span must be
    # bounded by the Tinkoff 9-month budget.
    span_days = (end - (start + timedelta(days=1))).days
    assert span_days <= 270, (
        f"completeness window must be ≤9 months, got {span_days} days"
    )