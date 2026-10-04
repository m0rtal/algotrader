# SQLite evidence BUSY deferral

## Why
SQLite writers outside the advisory namespace can cause BUSY after flock acquisition. Listed-till currently fails instead of deferring; evidence commit failures can leave a borrowed transaction active.

## What Changes
Translate SQLite BUSY (including extended codes) only in listed-till and evidence record writes. Roll back every body or commit failure before releasing flock. Reuse bounded WriterLockBusy diagnostics and CLI exit 75.

## Impact
Touches the evidence CLI, evidence record wrapper, and a shared numeric classifier. Borrowed connections remain open. Existing SQLite and flock timeouts remain unchanged.

## Non-Goals
No global writer conversion, reconciliation change, retries, sleeps, network changes, production database access, or writer shutdown.
