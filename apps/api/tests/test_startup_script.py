"""Offline bootstrap contracts from isolate-worker-watchdogs."""
from pathlib import Path
import shlex
import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / 'scripts' / 'startup-algotrader.sh'
WRAPPER = ROOT / 'scripts' / 'algotrader-moex-supervisor-clean.sh'


def statements(source):
    out, pending = [], ''
    for raw in source.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.endswith('\\'):
            pending += line[:-1].strip() + ' '
        else:
            out.append(pending + line)
            pending = ''
    assert not pending, 'unterminated executable continuation'
    return out


def launches(source):
    lines = statements(source)
    def one(fragment):
        found = [line for line in lines
                 if line.startswith('nohup ') and fragment in line]
        assert len(found) == 1, fragment
        return found[0]
    return (one('/algotrader-api-supervisor.sh'),
            one('/algotrader-moex-supervisor-clean.sh'),
            one('/algotrader-supervisor.sh'))


def assert_order(source):
    lines = statements(source)
    api, first, derived = launches(source)
    guard = lines.index('if ! wait_for_api_health; then')
    close = lines.index('fi', guard)
    assert lines.index(api) < guard < close < lines.index(first)
    assert close < lines.index(derived)
    assert 'exit 1' in lines[guard + 1:close]
    assert any('Refusing to start workers' in line for line in lines[guard + 1:close])


def arguments_before_redirect(line):
    return shlex.split(line.split(' >> ', 1)[0])


def test_script_waits_for_api_before_starting_workers():
    assert_order(SCRIPT.read_text())


def test_exact_clean_first_and_derived_arguments():
    _, first, derived = launches(SCRIPT.read_text())
    assert arguments_before_redirect(first) == [
        'nohup', 'bash', '$PROJECT/scripts/algotrader-moex-supervisor-clean.sh']
    assert arguments_before_redirect(derived) == [
        'nohup', 'env', '-u', 'PYTHONPATH', '-u', 'PYTHONHOME', 'bash',
        '$PROJECT/scripts/algotrader-supervisor.sh', 'algotrader-derived',
        '$PY', 'worker.py', 'daily', 'derived', '$API_DIR']


def test_clean_wrapper_exact_first_role_and_environment():
    lines = statements(WRAPPER.read_text())
    command = next(line for line in lines if line.startswith('exec '))
    assert shlex.split(command) == [
        'exec', '/home/hermes/algotrader/scripts/algotrader-supervisor.sh',
        'algotrader-moex-backfill', '/home/hermes/algotrader/apps/api/.venv/bin/python',
        'worker.py', 'daily', 'first', '/home/hermes/algotrader/apps/api']
    assert lines.index('unset PYTHONPATH') < lines.index(command)
    assert lines.index('unset PYTHONHOME') < lines.index(command)


def test_comments_and_location_do_not_change_order(tmp_path):
    source = SCRIPT.read_text()
    executable = '\n'.join(statements(source))
    comments = ('# wait_for_api_health\n'
                '# algotrader-moex-supervisor-clean.sh before API\n'
                '# algotrader-supervisor.sh algotrader-moex-backfill\n')
    relocated = tmp_path / 'other-checkout' / 'scripts' / 'startup-algotrader.sh'
    relocated.parent.mkdir(parents=True)
    relocated.write_text(comments + executable)
    assert_order(relocated.read_text())
    assert_order(executable + '\n' + comments)


def test_executable_worker_before_health_is_rejected():
    source = SCRIPT.read_text()
    lines = statements(source)
    _, first, _ = launches(source)
    lines.remove(first)
    lines.insert(0, first)
    with pytest.raises(AssertionError):
        assert_order('\n'.join(lines))


def test_script_uses_health_endpoint_and_bounded_retry():
    lines = statements(SCRIPT.read_text())
    curl_checks = [line for line in lines if 'curl -sf http://127.0.0.1:8000/health' in line]
    assert len(curl_checks) == 2  # jq and grep branches, executable statements
    assert 'local max_attempts=30' in lines
    assert 'sleep 1' in lines
    assert 'return 1' in lines
