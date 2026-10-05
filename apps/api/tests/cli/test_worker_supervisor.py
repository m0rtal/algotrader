"""Role-isolated watchdog checks using only owned temporary state and children."""
import importlib.util
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
HELPER = ROOT / 'scripts' / 'worker-watchdog.py'


def load_helper():
    spec = importlib.util.spec_from_file_location('worker_watchdog', HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def stamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec='seconds')


@contextmanager
def child():
    proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                            start_new_session=True)
    try:
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()  # only fixture-owned Popen; not product fallback
        proc.wait(timeout=5)


def create_db(path):
    with closing(sqlite3.connect(path)) as con, con:
        con.executescript('''
            CREATE TABLE pipeline(id INTEGER PRIMARY KEY, phase TEXT,
                                  finished_at TEXT, detail TEXT);
            CREATE TABLE pipeline_heartbeat(worker_pid INTEGER, phase TEXT,
                updated_at TEXT, PRIMARY KEY(worker_pid, phase));
        ''')


def add(con, store, pid, epoch, detail=None):
    if store == 'pipeline':
        con.execute('INSERT INTO pipeline(phase,finished_at,detail) VALUES(?,?,?)',
                    ('worker.heartbeat', stamp(epoch),
                     detail if detail is not None else f'pid={pid}'))
    else:
        con.execute('INSERT OR REPLACE INTO pipeline_heartbeat VALUES(?,?,?)',
                    (pid, 'live_loop', stamp(epoch)))


@unittest.skipUnless(callable(getattr(os, 'pidfd_open', None)) and
                     callable(getattr(signal, 'pidfd_send_signal', None)),
                     'mandatory system-Python run covers pidfd behavior')
class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.db = self.dir / 'state.db'
        self.pidfile = self.dir / 'state.db.algotrader-derived.worker.pid'
        create_db(self.db)
        self.w = load_helper()

    def test_stale_own_fresh_sibling_each_store(self):
        for store in ('pipeline', 'pipeline_heartbeat'):
            with self.subTest(store=store), child() as own, child() as sibling:
                ident = self.w.identity(own.pid)
                now = ident.started + 0.0208 * 86400 + 10
                self.pidfile.write_text(f'{own.pid}\n')
                with closing(sqlite3.connect(self.db)) as con, con:
                    con.execute('DELETE FROM pipeline')
                    con.execute('DELETE FROM pipeline_heartbeat')
                    add(con, store, own.pid, ident.started + 1)
                    add(con, store, sibling.pid, now)
                before = self.db.read_bytes()
                opened = []
                real_open = os.pidfd_open
                def track(pid, flags=0):
                    fd = real_open(pid, flags)
                    opened.append(fd)
                    return fd
                with patch.object(self.w.os, 'pidfd_open', side_effect=track):
                    self.assertEqual(self.w.check(self.db, self.pidfile,
                                     os.getpid(), now=now), 'stale:signaled')
                self.assertEqual(own.wait(timeout=5), -signal.SIGKILL)
                self.assertIsNone(sibling.poll())
                self.assertEqual(self.db.read_bytes(), before)
                self.assertEqual(len(opened), 1)
                with self.assertRaises(OSError):
                    os.fstat(opened[0])

    def test_lifetime_floor_exact_detail_and_grace_not_poll_reset(self):
        ident = self.w.Identity(12345, os.getpid(), 100, 10000.75)
        limit = 0.0208 * 86400
        with closing(sqlite3.connect(self.db)) as con, con:
            add(con, 'pipeline', ident.pid, 9999)
            add(con, 'pipeline', 999, 10002)
            add(con, 'pipeline', ident.pid, 10002, detail='pid=12345 extra')
            con.execute('INSERT INTO pipeline(phase,finished_at,detail) VALUES(?,?,?)',
                        ('worker.other', stamp(10002), f'pid={ident.pid}'))
        self.assertEqual(self.w.heartbeat(self.db, ident, 10001, 0.0208), 'grace')
        self.assertEqual(self.w.heartbeat(self.db, ident, ident.started + limit,
                                         0.0208), 'grace')
        self.assertEqual(self.w.heartbeat(self.db, ident,
                          ident.started + limit + 0.01, 0.0208), 'stale')
        # Daily writer loses subseconds. Same start second remains eligible.
        with closing(sqlite3.connect(self.db)) as con, con:
            add(con, 'pipeline', ident.pid, 10000)
        self.assertEqual(self.w.heartbeat(self.db, ident, 10001, 0.0208), 'fresh')

    def test_wrong_parent_invalid_target_and_missing_db_never_signal(self):
        with child() as proc:
            self.pidfile.write_text(f'{proc.pid}\n')
            with patch.object(self.w.signal, 'pidfd_send_signal') as send:
                self.assertTrue(self.w.check(self.db, self.pidfile,
                                             os.getpid() + 1).startswith('unknown:'))
                for text in ('0', '-1', '1 2', '123\n456', 'abc', ''):
                    self.pidfile.write_text(text)
                    self.assertTrue(self.w.check(self.db, self.pidfile,
                                                 os.getpid()).startswith('unknown:'))
                self.pidfile.write_text(f'{proc.pid}\n')
                missing = self.dir / 'does-not-exist.db'
                self.assertTrue(self.w.check(missing, self.pidfile,
                                             os.getpid()).startswith('unknown:'))
                self.assertFalse(missing.exists())
                send.assert_not_called()
            self.assertIsNone(proc.poll())

    def test_reuse_and_target_change_after_pidfd_open_close_descriptor(self):
        for race in ('identity', 'target'):
            with self.subTest(race=race), child() as proc:
                ident = self.w.identity(proc.pid)
                now = ident.started + 0.0208 * 86400 + 10
                self.pidfile.write_text(f'{proc.pid}\n')
                opened = []
                real_open = os.pidfd_open
                def replace(pid, flags=0):
                    fd = real_open(pid, flags)
                    opened.append(fd)
                    if race == 'target':
                        self.pidfile.write_text('999999\n')
                    return fd
                changed = self.w.Identity(ident.pid, ident.ppid,
                                          ident.ticks + 1, ident.started + 1)
                identities = [ident, changed if race == 'identity' else ident]
                with patch.object(self.w.os, 'pidfd_open', side_effect=replace), \
                     patch.object(self.w, 'identity', side_effect=identities), \
                     patch.object(self.w.signal, 'pidfd_send_signal') as send:
                    outcome = self.w.check(self.db, self.pidfile, os.getpid(), now=now)
                    self.assertTrue(outcome.startswith('unknown:'))
                    send.assert_not_called()
                self.assertIsNone(proc.poll())
                with self.assertRaises(OSError):
                    os.fstat(opened[0])


    def test_live_lifetime_and_utc_formats(self):
        ident = self.w.Identity(12345, os.getpid(), 100, 10000.75)
        with closing(sqlite3.connect(self.db)) as con, con:
            add(con, 'pipeline_heartbeat', ident.pid, 9999)
            add(con, 'pipeline_heartbeat', 999, 10002)
        self.assertEqual(self.w.heartbeat(self.db, ident, 10001, 0.0208), 'grace')
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute('UPDATE pipeline_heartbeat SET updated_at=? WHERE worker_pid=?',
                        ('1970-01-01 02:46:40', ident.pid))
        self.assertEqual(self.w.heartbeat(self.db, ident, 10001, 0.0208), 'fresh')
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute('UPDATE pipeline_heartbeat SET updated_at=? WHERE worker_pid=?',
                        ('1970-01-01T03:46:40.900000+01:00', ident.pid))
        self.assertEqual(self.w.heartbeat(self.db, ident, 10001, 0.0208), 'fresh')

    def test_corrupt_future_db_and_capability_errors_are_bounded(self):
        with child() as proc:
            ident = self.w.identity(proc.pid)
            self.pidfile.write_text(f'{proc.pid}\n')
            for value in ('secret-payload-not-a-date', stamp(time.time() + 3600)):
                with closing(sqlite3.connect(self.db)) as con, con:
                    con.execute('DELETE FROM pipeline')
                    con.execute('INSERT INTO pipeline VALUES(1,?,?,?)',
                                ('worker.heartbeat', value, f'pid={proc.pid}'))
                with patch.object(self.w.signal, 'pidfd_send_signal') as send:
                    out = self.w.check(self.db, self.pidfile, os.getpid())
                    self.assertTrue(out.startswith('unknown:'))
                    self.assertLess(len(out), 80)
                    self.assertNotIn('secret-payload', out)
                    send.assert_not_called()
            with closing(sqlite3.connect(self.db)) as con, con:
                con.execute('DELETE FROM pipeline')
            for attribute in ('pidfd_open',):
                with patch.object(self.w.os, attribute, None):
                    self.assertTrue(self.w.check(self.db, self.pidfile,
                                                 os.getpid()).startswith('unknown:'))
            with patch.object(self.w.signal, 'pidfd_send_signal', None):
                self.assertTrue(self.w.check(self.db, self.pidfile,
                                             os.getpid()).startswith('unknown:'))
            for error in (PermissionError('private'), sqlite3.DatabaseError('private')):
                with patch.object(self.w.sqlite3, 'connect', side_effect=error), \
                     patch.object(self.w.signal, 'pidfd_send_signal') as send:
                    out = self.w.check(self.db, self.pidfile, os.getpid())
                    self.assertTrue(out.startswith('unknown:'))
                    self.assertNotIn('private', out)
                    send.assert_not_called()
            self.assertIsNone(proc.poll())
    def test_close_on_send_or_process_disappearance(self):
        for failure in ('send', 'identity', 'open'):
            with self.subTest(failure=failure), child() as proc:
                ident = self.w.identity(proc.pid)
                now = ident.started + 0.0208 * 86400 + 10
                self.pidfile.write_text(f'{proc.pid}\n')
                opened = []
                real_open = os.pidfd_open
                def track(pid, flags=0):
                    if failure == 'open':
                        raise OSError('private-kernel-error')
                    fd = real_open(pid, flags)
                    opened.append(fd)
                    return fd
                identities = [ident, FileNotFoundError('private')] if failure == 'identity' else [ident, ident]
                with patch.object(self.w.os, 'pidfd_open', side_effect=track), \
                     patch.object(self.w, 'identity', side_effect=identities), \
                     patch.object(self.w.signal, 'pidfd_send_signal',
                                  side_effect=ProcessLookupError('private')) as send:
                    out = self.w.check(self.db, self.pidfile, os.getpid(), now=now)
                    self.assertTrue(out.startswith('unknown:'))
                    self.assertNotIn('private', out)
                    if failure != 'send':
                        send.assert_not_called()
                self.assertIsNone(proc.poll())
                for fd in opened:
                    with self.assertRaises(OSError):
                        os.fstat(fd)

    def test_own_fresh_each_store_never_opens_pidfd(self):
        for store in ('pipeline', 'pipeline_heartbeat'):
            with self.subTest(store=store), child() as proc:
                now = time.time()
                self.pidfile.write_text(f'{proc.pid}\n')
                with closing(sqlite3.connect(self.db)) as con, con:
                    add(con, store, proc.pid, now)
                before = self.db.read_bytes()
                with patch.object(self.w.os, 'pidfd_open') as opened, \
                     patch.object(self.w.signal, 'pidfd_send_signal') as send:
                    self.assertEqual(self.w.check(self.db, self.pidfile,
                                      os.getpid(), now=now), 'fresh')
                    opened.assert_not_called()
                    send.assert_not_called()
                self.assertEqual(self.db.read_bytes(), before)
                self.assertIsNone(proc.poll())

    def test_cross_store_sibling_and_missing_own_after_grace(self):
        for own_store, sibling_store in (('pipeline', 'pipeline_heartbeat'),
                                         ('pipeline_heartbeat', 'pipeline'),
                                         (None, 'pipeline')):
            with self.subTest(own_store=own_store), child() as own, child() as sibling:
                ident = self.w.identity(own.pid)
                now = ident.started + 0.0208 * 86400 + 10
                self.pidfile.write_text(f'{own.pid}\n')
                with closing(sqlite3.connect(self.db)) as con, con:
                    con.execute('DELETE FROM pipeline')
                    con.execute('DELETE FROM pipeline_heartbeat')
                    if own_store:
                        add(con, own_store, own.pid, ident.started + 1)
                    add(con, sibling_store, sibling.pid, now)
                self.assertEqual(self.w.check(self.db, self.pidfile,
                                  os.getpid(), now=now), 'stale:signaled')
                self.assertEqual(own.wait(timeout=5), -signal.SIGKILL)
                self.assertIsNone(sibling.poll())

    def test_absent_tables_and_read_only_connection(self):
        ident = self.w.Identity(12345, os.getpid(), 100, 10000.75)
        for remaining in ('pipeline', 'pipeline_heartbeat'):
            db = self.dir / (remaining + '.db')
            create_db(db)
            with closing(sqlite3.connect(db)) as con, con:
                other = 'pipeline_heartbeat' if remaining == 'pipeline' else 'pipeline'
                con.execute('DROP TABLE ' + other)
                add(con, remaining, ident.pid, 10001)
            self.assertEqual(self.w.heartbeat(db, ident, 10002, 0.0208), 'fresh')
        real_connect = sqlite3.connect
        def checked_connect(database, **kwargs):
            self.assertIn('?mode=ro', database)
            self.assertTrue(kwargs['uri'])
            con = real_connect(database, **kwargs)
            with self.assertRaises(sqlite3.OperationalError):
                con.execute('CREATE TABLE forbidden(x)')
            return con
        with patch.object(self.w.sqlite3, 'connect', side_effect=checked_connect):
            self.assertEqual(self.w.heartbeat(self.db, ident, 10001, 0.0208), 'grace')
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute('DROP TABLE pipeline')
            con.execute('DROP TABLE pipeline_heartbeat')
        with self.assertRaises(sqlite3.DatabaseError):
            self.w.heartbeat(self.db, ident, 10001, 0.0208)

    def test_live_corrupt_future_and_unrelated_corrupt_rows(self):
        with child() as proc:
            self.pidfile.write_text(f'{proc.pid}\n')
            now = time.time()
            with closing(sqlite3.connect(self.db)) as con, con:
                con.execute('INSERT INTO pipeline VALUES(1,?,?,?)',
                            ('worker.heartbeat', 'private-garbage', 'pid=999999'))
                add(con, 'pipeline', proc.pid, now)
            self.assertEqual(self.w.check(self.db, self.pidfile,
                              os.getpid(), now=now), 'fresh')
            for value in ('private-garbage', stamp(now + 3600)):
                with closing(sqlite3.connect(self.db)) as con, con:
                    con.execute('INSERT OR REPLACE INTO pipeline_heartbeat VALUES(?,?,?)',
                                (proc.pid, 'live_loop', value))
                with patch.object(self.w.signal, 'pidfd_send_signal') as send:
                    out = self.w.check(self.db, self.pidfile, os.getpid(), now=now)
                    self.assertTrue(out.startswith('unknown:'))
                    self.assertNotIn('private', out)
                    send.assert_not_called()
    def test_role_lock_fail_closed_and_serializes_publication(self):
        with child() as proc:
            self.pidfile.write_text(f'{proc.pid}\n')
            ready = self.dir / 'lock-ready'
            holder = subprocess.Popen([sys.executable, '-c',
                'import fcntl,pathlib,sys,time; '
                'f=open(sys.argv[1],"a"); fcntl.flock(f,fcntl.LOCK_EX); '
                'pathlib.Path(sys.argv[2]).write_text("ready"); time.sleep(60)',
                str(self.pidfile) + '.lock', str(ready)], start_new_session=True)
            try:
                until = time.monotonic() + 5
                while time.monotonic() < until and not ready.exists():
                    time.sleep(0.02)
                self.assertTrue(ready.exists())
                with patch.object(self.w.signal, 'pidfd_send_signal') as send:
                    self.assertEqual(self.w.check(self.db, self.pidfile, os.getpid()),
                                     'unknown:BlockingIOError')
                    send.assert_not_called()
                raw = self.pidfile.read_bytes()
                blocked = subprocess.run(['flock', '-w', '1',
                    str(self.pidfile) + '.lock', 'bash', '-c',
                    'printf "%s\\n" "$2" > "$1"', '_', str(self.pidfile), '999999'],
                    timeout=5)
                self.assertEqual(blocked.returncode, 1)
                self.assertEqual(self.pidfile.read_bytes(), raw)
                self.assertIsNone(proc.poll())
            finally:
                holder.kill()
                holder.wait(timeout=5)


def copy_contained_scripts(tmp_path):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    for name in ('algotrader-supervisor.sh', 'worker-watchdog.py'):
        source = (ROOT / 'scripts' / name).read_text()
        if name == 'algotrader-supervisor.sh':
            # Contain baseline RED too: its old LOG ignores the new override.
            source = source.replace('LOG="/home/hermes/.hermes/logs/${NAME}.log"',
                                    'LOG="${ALGO_SUPERVISOR_LOG:?owned log required}"')
        (scripts / name).write_text(source)
    return scripts


def test_same_db_roles_publish_and_cleanup_independently(tmp_path):
    import json
    scripts = copy_contained_scripts(tmp_path)
    (tmp_path / 'data').mkdir()
    create_db(tmp_path / 'data' / 'state.db')
    worker = tmp_path / 'owned_worker.py'
    worker.write_text('''import json, os, pathlib, sys, time
pathlib.Path(sys.argv[1]).write_text(json.dumps({"pid": os.getpid(), "cwd": os.getcwd()}))
time.sleep(60)
''')
    processes = []
    def await_file(path):
        until = time.monotonic() + 5
        while time.monotonic() < until:
            if path.exists() and path.read_text().strip():
                return path.read_text()
            time.sleep(0.02)
        raise AssertionError(f'owned fixture did not publish {path.name}')
    try:
        for name in ('algotrader-moex-backfill', 'algotrader-derived'):
            env = dict(os.environ, ALGO_SUPERVISOR_LOG=str(tmp_path / (name + '.log')))
            env.pop('PYTHONPATH', None)
            env.pop('PYTHONHOME', None)
            processes.append(subprocess.Popen(
                ['bash', str(scripts / 'algotrader-supervisor.sh'), name,
                 sys.executable, str(worker), str(tmp_path / (name + '.json')),
                 str(tmp_path)], env=env, cwd=tmp_path, start_new_session=True))
        first = tmp_path / 'data' / 'state.db.algotrader-moex-backfill.worker.pid'
        derived = tmp_path / 'data' / 'state.db.algotrader-derived.worker.pid'
        first_pid, sibling_pid = int(await_file(first)), int(await_file(derived))
        assert first != derived
        for name in ('algotrader-moex-backfill', 'algotrader-derived'):
            captured = json.loads(await_file(tmp_path / (name + '.json')))
            assert captured['cwd'] == str(tmp_path)
        sibling_raw = derived.read_bytes()
        first.unlink()
        assert derived.read_bytes() == sibling_raw
        os.kill(sibling_pid, 0)  # fixture-only liveness probe, not product signaling
        first.write_text(f'{first_pid}\n')  # now exercise actual main-loop cleanup
        subprocess.run(['/usr/bin/python3', '-c',
            'import os,signal,sys; fd=os.pidfd_open(int(sys.argv[1])); '
            'signal.pidfd_send_signal(fd,signal.SIGKILL); os.close(fd)',
            str(first_pid)], check=True, timeout=5)
        until = time.monotonic() + 2  # less than preserved five-second restart delay
        while time.monotonic() < until and first.exists():
            time.sleep(0.02)
        assert not first.exists()
        assert derived.read_bytes() == sibling_raw
        os.kill(sibling_pid, 0)
        assert not (tmp_path / 'data' / 'state.db.worker.pid').exists()
    finally:
        for proc in processes:
            try:
                os.killpg(proc.pid, signal.SIGKILL)  # only newly owned session
            except ProcessLookupError:
                pass
            proc.wait(timeout=5)


def test_unsafe_names_rejected_before_writes(tmp_path):
    scripts = copy_contained_scripts(tmp_path)
    for index, name in enumerate(('../sibling', '/absolute', '.', '..', 'a/b', '')):
        area = tmp_path / str(index)
        area.mkdir()
        log = area / 'owned.log'
        env = dict(os.environ, ALGO_SUPERVISOR_LOG=str(log))
        env.pop('PYTHONPATH', None)
        env.pop('PYTHONHOME', None)
        process = subprocess.Popen(['bash', str(scripts / 'algotrader-supervisor.sh'),
                                    name, '/usr/bin/true', str(area)],
                                   env=env, cwd=area, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            code = process.wait(timeout=1)
            assert code != 0
            assert not log.exists()
            assert list(area.iterdir()) == []
        finally:
            # A baseline failure can start watchdog descendants: contain them too.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)


def test_guarded_cleanup_retains_replaced_target(tmp_path):
    scripts = copy_contained_scripts(tmp_path)
    (tmp_path / 'data').mkdir()
    worker = tmp_path / 'owned_worker.py'
    worker.write_text('import time; time.sleep(60)\n')
    env = dict(os.environ, ALGO_SUPERVISOR_LOG=str(tmp_path / 'owned.log'))
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    proc = subprocess.Popen(['bash', str(scripts / 'algotrader-supervisor.sh'),
        'algotrader-derived', sys.executable, str(worker), str(tmp_path)],
        cwd=tmp_path, env=env, start_new_session=True)
    try:
        pidfile = tmp_path / 'data' / 'state.db.algotrader-derived.worker.pid'
        until = time.monotonic() + 5
        while time.monotonic() < until:
            if pidfile.exists() and pidfile.read_text().strip():
                break
            time.sleep(0.02)
        original = int(pidfile.read_text())
        pidfile.write_text('999999\n')  # target replacement; never signaled
        subprocess.run(['/usr/bin/python3', '-c',
            'import os,signal,sys; fd=os.pidfd_open(int(sys.argv[1])); '
            'signal.pidfd_send_signal(fd,signal.SIGKILL); os.close(fd)',
            str(original)], check=True, timeout=5)
        log = tmp_path / 'owned.log'
        until = time.monotonic() + 2
        while time.monotonic() < until:
            if 'exited rc=' in log.read_text():
                break
            time.sleep(0.02)
        assert 'exited rc=' in log.read_text()
        assert pidfile.read_text() == '999999\n'
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)


def test_watchdog_wiring_has_no_progress_or_numeric_kill():
    shell = (ROOT / 'scripts' / 'algotrader-supervisor.sh').read_text()
    executable = '\n'.join(line for line in shell.splitlines()
                           if not line.lstrip().startswith('#'))
    assert 'sleep 30' in executable and 'sleep 5' in executable
    assert 'HEARTBEAT_MAX_AGE_DAYS=0.0208' in executable
    assert 'watchdog check:' in executable
    assert 'worker-watchdog.py' in executable
    assert 'kill -9' not in executable and 'STUCK_AT_STARTUP' not in executable
    assert 'bars_growth' not in executable and 'elapsed > 600' not in executable
    assert executable.count('PIDFILE="${STATE_DB}.${NAME}.worker.pid"') == 1


if __name__ == '__main__':
    unittest.main()
