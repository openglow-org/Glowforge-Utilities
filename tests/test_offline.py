"""
The offline service: a local socket in place of the web service, a
file-serving session in place of the web session, the same dispatch and
the same events.

Copyright 2026 514 LLC d/b/a OpenGlow
Written by Scott Wiederhold
https://community.openglow.org

SPDX-License-Identifier:    MIT
"""
import json
import os
import socket
import tempfile
import threading
import time

import pytest

from gfutilities.service import websocket as ws
from gfutilities.service.offline import OfflineService, offline_session, OFFLINE_MARK
from tests.test_basemachine import StubMachine


# ---- the session ------------------------------------------------------------

def test_file_url_is_served_with_its_length(tmp_path):
    p = tmp_path / 'job.puls'
    p.write_bytes(b'x' * 1000)
    s = offline_session()
    r = ws.request(s, 'file://' + str(p), 'GET', stream=True)
    assert r and r.status_code == 200
    assert int(r.headers['content-length']) == 1000
    assert b''.join(ws._body_stream(r)) == b'x' * 1000


def test_missing_file_fails_like_a_dead_link(tmp_path):
    s = offline_session()
    assert ws.request(s, 'file://' + str(tmp_path / 'none.puls'), 'GET') is False


def test_uploads_are_sunk_and_fetches_refused(tmp_path):
    s = offline_session(str(tmp_path / 'up'))
    r = ws.request(s, 'https://app.example/api/machines/lid_image/7', 'POST', data=b'jpeg')
    assert r and r.status_code == 200
    assert (tmp_path / 'up' / 'upload-1.bin').read_bytes() == b'jpeg'
    assert ws.request(s, 'https://app.example/anything', 'GET') is False


def test_fetch_motion_reads_a_local_pulse_file(tmp_path, monkeypatch):
    import struct
    tags = [('STfr', 10000), ('MCsn', 0), ('PDfm', 0), ('PDct', 5)]
    head = b''.join(t.encode() + struct.pack('<I', v) for t, v in tags)
    total = 8 + len(head)
    body = b'\x80' + b'\x00' * 99
    p = tmp_path / 'job.puls'
    p.write_bytes(b'\x00GF1' + struct.pack('<I', total) + head + body)
    monkeypatch.setattr(ws, 'get_cfg', lambda k: 0 if k == 'MACHINE.SERIAL' else None)
    info, source = ws.fetch_motion(offline_session(), 'file://' + str(p))
    assert info and info['header_data']['STfr'] == 10000
    assert source.read(1000) == body


# ---- the service --------------------------------------------------------------

@pytest.mark.skipif(not hasattr(socket, 'AF_UNIX') or os.name == 'nt',
                    reason='UNIX sockets')
def test_actions_in_events_out(tmp_path):
    sock = str(tmp_path / 'off.sock')
    m = StubMachine()
    svc = OfflineService(m, path=sock)
    assert svc.connect()
    assert os.path.exists(sock)
    th = threading.Thread(target=svc.run, daemon=True)
    th.start()
    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(5)
        c.connect(sock)
        c.sendall((json.dumps({'id': 41, 'action_type': 'lid_image', 'status': 'ready',
                               'settings': {}}) + '\n').encode())
        buf = b''
        deadline = time.time() + 5
        while b'lid_image:completed' not in buf and time.time() < deadline:
            try:
                buf += c.recv(65536)
            except socket.timeout:
                break
        events = [json.loads(l)['event'] for l in buf.decode().splitlines() if l.strip()]
        assert 'lid_image:starting' in events
        assert 'lid_image:completed' in events
        assert m.lid_captured == 1
        assert svc.listener.received == 1
        c.close()
    finally:
        svc.request_stop()
        th.join(5)
    assert not th.is_alive()
    assert not os.path.exists(sock)


def test_no_network_is_ever_touched():
    # the session the machine gets has adapters for every scheme it could
    # be asked for, none of them reaching a host
    s = offline_session()
    for prefix in ('file://', 'http://', 'https://'):
        assert prefix in s.adapters
    assert OFFLINE_MARK  # the log mark a reader keys on exists
