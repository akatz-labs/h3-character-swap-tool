"""Exercise the actual source process lifecycle and isolated project persistence."""
import json
import os
from pathlib import Path
import socket
import signal
import subprocess
import time
import urllib.error
import urllib.request
import pytest

ROOT = Path(__file__).parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def request(port, path, value=None, origin=None):
    headers = {'content-type': 'application/json'}
    if value is not None:
        headers['origin'] = f'http://127.0.0.1:{port}'
    if origin:
        headers['origin'] = origin
    req = urllib.request.Request(f'http://127.0.0.1:{port}' + path,
        data=json.dumps(value).encode() if value is not None else None, headers=headers)
    with urllib.request.urlopen(req, timeout=3) as response:
        return json.load(response)


def wait_ready(proc, port):
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        assert proc.poll() is None, 'Source launcher exited before readiness'
        try:
            if request(port, '/__akatz/app-host')['phase'] == 'ready':
                return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(.1)
    raise AssertionError('Source launcher never became ready')


def stop(proc):
    if proc.poll() is None:
        proc.terminate()
    proc.wait(timeout=15)


@pytest.fixture
def shared_cleanup(tmp_path):
    yield
    receipt = tmp_path / 'ecosystem/services/media.json'
    if receipt.exists():
        owner = json.loads(receipt.read_text())['process']
        if owner:
            pid = owner['pid']
            try:
                cmd = Path(f'/proc/{pid}/cmdline').read_bytes().decode()
                assert str(tmp_path) in cmd and 'media-server.mjs' in cmd
                os.kill(pid, signal.SIGTERM)
            except FileNotFoundError:
                pass


def test_source_launch_duplicate_guard_and_restart(tmp_path, monkeypatch, shared_cleanup):
    port, worker_port = free_port(), free_port()
    media_port = free_port()
    ecosystem = tmp_path / 'ecosystem'
    monkeypatch.setenv('AKATZ_DATA_ROOT', str(ecosystem))
    monkeypatch.setenv('AKATZ_MEDIA_ROOT', str(ecosystem / 'media'))
    monkeypatch.setenv('AKATZ_MEDIA_URL', f'http://127.0.0.1:{media_port}')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    command = ['node', str(ROOT / 'dist/runtime/source-app.js'), '--data', str(tmp_path / 'projects'),
        '--port', str(port), '--worker-port', str(worker_port), '--python', str(ROOT / '.venv/bin/python')]
    with (tmp_path / 'source.log').open('w') as log:
        proc = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=log, env=dict(os.environ))
        try:
            wait_ready(proc, port)
            identity = request(port, '/api/health')['instanceId']
            created = request(port, '/api/batches', {'name': 'Source persistence'})
            # Another launcher may not adopt the same project data.
            duplicate = subprocess.Popen(command[:], cwd=ROOT, stdout=log, stderr=log)
            try:
                assert duplicate.wait(timeout=15) != 0
            finally:
                stop(duplicate)
            assert request(port, '/api/health')['instanceId'] == identity
            try:
                request(port, '/api/batches', {'name': 'Must reject'}, 'https://foreign.invalid')
                raise AssertionError('Cross-origin mutation accepted')
            except urllib.error.HTTPError as error:
                assert error.code == 403
        finally:
            stop(proc)
        proc = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=log)
        try:
            wait_ready(proc, port)
            assert request(port, '/api/health')['instanceId'] == identity
            assert [p['id'] for p in request(port, '/api/state')['batches']] == [created['id']]
            assert request(port, '/api/state')['jobs'] == []
        finally:
            stop(proc)
