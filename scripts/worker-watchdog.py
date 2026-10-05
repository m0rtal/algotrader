#!/usr/bin/env python3
import argparse
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import math
import os
from pathlib import Path
import re
import signal
import sqlite3
import time


@dataclass(frozen=True)
class Identity:
    pid: int
    ppid: int
    ticks: int
    started: float


def identity(pid):
    text = Path(f'/proc/{pid}/stat').read_text()
    fields = text[text.rindex(')') + 2:].split()
    if fields[0] in ('Z', 'X'):
        raise ProcessLookupError()
    ppid, ticks = int(fields[1]), int(fields[19])
    boot = next(int(line.split()[1]) for line in
                Path('/proc/stat').read_text().splitlines()
                if line.startswith('btime '))
    return Identity(pid, ppid, ticks, boot + ticks / os.sysconf('SC_CLK_TCK'))


def target(pid_file):
    with pid_file.open('rb') as source:
        raw = source.read(33)
    if len(raw) > 32 or not re.fullmatch(rb'[1-9][0-9]*\n?', raw):
        raise ValueError()
    pid = int(raw)
    if pid > 2147483647:
        raise ValueError()
    return pid, raw


def heartbeat(db, child, now, max_days):
    if not math.isfinite(now) or not math.isfinite(max_days) or max_days <= 0:
        raise ValueError()
    if now < child.started:
        raise ValueError()
    with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro',
                                uri=True, timeout=5)) as con:
        con.execute('PRAGMA query_only=ON')
        tables = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('pipeline','pipeline_heartbeat')")}
        if not tables:
            raise sqlite3.DatabaseError()
        rows = []
        if 'pipeline' in tables:
            rows.extend(con.execute(
                "SELECT finished_at FROM pipeline WHERE phase=? AND detail=? "
                "ORDER BY id DESC LIMIT 1",
                ('worker.heartbeat', f'pid={child.pid}')).fetchall())
        if 'pipeline_heartbeat' in tables:
            live = con.execute(
                'SELECT updated_at FROM pipeline_heartbeat WHERE worker_pid=?',
                (child.pid,)).fetchmany(33)
            if len(live) > 32:
                raise ValueError()
            rows.extend(live)
    stamps = []
    for (value,) in rows:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        epoch = dt.timestamp()
        if not math.isfinite(epoch) or epoch > now:
            raise ValueError()
        if epoch >= math.floor(child.started):
            stamps.append(epoch)
    age = now - (max(stamps) if stamps else child.started)
    if age > max_days * 86400:
        return 'stale'
    return 'fresh' if stamps else 'grace'


def check(db, pid_file, parent, max_days=0.0208, now=None):
    fd = None
    try:
        if not callable(getattr(os, 'pidfd_open', None)) or not callable(
                getattr(signal, 'pidfd_send_signal', None)):
            return 'unknown:pidfd'
        # Never unlink/recreate this lock inode while any supervisor is active.
        with open(str(pid_file) + '.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            pid, raw = target(pid_file)
            first = identity(pid)
            if first.ppid != parent or parent <= 1:
                return 'unknown:parent'
            outcome = heartbeat(db, first, time.time() if now is None else now,
                                max_days)
            if outcome != 'stale':
                return outcome
            fd = os.pidfd_open(pid, 0)
            if identity(pid) != first:
                return 'unknown:identity'
            if target(pid_file) != (pid, raw):
                return 'unknown:target'
            # Lock covers cooperating PID publishers through this send.
            # pidfd pins the process even if numeric PID is subsequently reused.
            signal.pidfd_send_signal(fd, signal.SIGKILL, None, 0)
            return 'stale:signaled'
    except Exception as exc:
        # Class name only; never str(exc), DB values, paths, or command arguments.
        allowed = {'ValueError', 'TypeError', 'OSError', 'PermissionError',
                   'FileNotFoundError', 'ProcessLookupError', 'BlockingIOError',
                   'OperationalError', 'DatabaseError', 'NotSupportedError',
                   'OverflowError', 'StopIteration'}
        reason = type(exc).__name__
        return 'unknown:' + (reason if reason in allowed else 'error')
    finally:
        if fd is not None:
            os.close(fd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--pid-file', type=Path, required=True)
    parser.add_argument('--parent', type=int, required=True)
    parser.add_argument('--max-age-days', type=float, default=0.0208)
    args = parser.parse_args()
    print(check(args.db, args.pid_file, args.parent, args.max_age_days))


if __name__ == '__main__':
    main()
