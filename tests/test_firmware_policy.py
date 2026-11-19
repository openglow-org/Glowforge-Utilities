"""
Copyright 2026 514 LLC d/b/a OpenGlow
Written by Scott Wiederhold
https://community.openglow.org

SPDX-License-Identifier:    MIT
"""
import json

from gfutilities.configuration import set_cfg
from gfutilities.service import websocket


class _FakeResponse:
    def __init__(self, payload):
        self._body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        pass


def _set_baseline(tmp_path, version='2.6.0-2228'):
    set_cfg('SERVICE.SERVER_URL', 'https://app.glowforge.com')
    set_cfg('FACTORY_FIRMWARE.FW_VERSION', version)
    status_file = tmp_path / 'gf-latest.json'
    set_cfg('FACTORY_FIRMWARE.STATUS_FILE', str(status_file))
    return status_file


def test_record_factory_latest_noop_without_status_file(tmp_path):
    _set_baseline(tmp_path)
    set_cfg('FACTORY_FIRMWARE.STATUS_FILE', None)
    # Must not raise when no status file is configured.
    websocket.record_factory_latest('2.7.0-9999')


def test_firmware_check_records_and_flags_newer(tmp_path, monkeypatch):
    status_file = _set_baseline(tmp_path)
    monkeypatch.setattr(websocket, 'request',
                        lambda *a, **k: _FakeResponse({'version': '2.7.0-9999',
                                                       'download_url': 'https://x/y.fw'}))
    result = websocket.firmware_check(None)
    # Newer than our tested baseline -> returns the version dict.
    assert result['version'] == '2.7.0-9999'
    # And the latest version was recorded for the banner, with the version
    # this image was tested against and when.
    data = json.loads(status_file.read_text())
    assert data['latest_gf_version'] == '2.7.0-9999'
    assert data['tested_against_gf'] == '2.6.0-2228'
    assert set(data) == {'latest_gf_version', 'tested_against_gf'}


def test_firmware_check_matches_baseline_returns_false_but_still_records(tmp_path, monkeypatch):
    status_file = _set_baseline(tmp_path)
    monkeypatch.setattr(websocket, 'request',
                        lambda *a, **k: _FakeResponse({'version': '2.6.0-2228',
                                                       'download_url': 'https://x/y.fw'}))
    result = websocket.firmware_check(None)
    assert result is False
    # Even when it matches, the observed version is recorded.
    assert json.loads(status_file.read_text())['latest_gf_version'] == '2.6.0-2228'


def test_a_version_that_is_not_a_plain_version_string_is_not_recorded(tmp_path):
    # forgectrl copies the recorded version into /status as it is: a quote,
    # a long string or a non-string would break the status document.
    status_file = _set_baseline(tmp_path)
    websocket.record_factory_latest('2.7.0-9999')
    for version in ('2.7"0', 'x' * 32, 7, None, ''):
        websocket.record_factory_latest(version)
        assert json.loads(status_file.read_text())['latest_gf_version'] == '2.7.0-9999', version


def test_the_record_is_replaced_whole(tmp_path, monkeypatch):
    # A reader sees the old record or the new one: the file is written
    # beside it and renamed over it.
    status_file = _set_baseline(tmp_path)
    websocket.record_factory_latest('2.7.0-9999')
    renames = []
    real_replace = websocket.os.replace
    monkeypatch.setattr(websocket.os, 'replace',
                        lambda src, dst: (renames.append((src, json.loads(open(src).read()),
                                                          json.loads(open(dst).read()))),
                                          real_replace(src, dst)))
    websocket.record_factory_latest('2.8.0-1')
    assert renames and renames[0][1]['latest_gf_version'] == '2.8.0-1'
    assert renames[0][2]['latest_gf_version'] == '2.7.0-9999'
    assert json.loads(status_file.read_text())['latest_gf_version'] == '2.8.0-1'


def test_the_probe_is_one_attempt_on_the_sessions_own_transport(tmp_path, monkeypatch):
    # A slow or refusing service holds the action dispatch for one short
    # attempt (5 s on the connect and each read): the session's retrying
    # HTTP adapter is swapped for one that does not retry.
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    _set_baseline(tmp_path)
    sent = {'once': [], 'retrying': []}

    def recorder(key):
        def send(self, request, **kwargs):
            sent[key].append(kwargs.get('timeout'))
            r = requests.Response()
            r.status_code, r.reason, r._content = 503, 'busy', b''
            return r
        return send

    class Retrying(HTTPAdapter):
        send = recorder('retrying')

    class Once(HTTPAdapter):
        send = recorder('once')
    monkeypatch.setattr(websocket, '_NO_RETRY', Once())
    s = requests.Session()
    s.mount('https://', Retrying(max_retries=Retry(total=4)))
    assert websocket.firmware_check(s) is None
    assert sent == {'once': [5], 'retrying': []}


def test_the_offline_session_probe_stays_off_the_network(tmp_path, monkeypatch):
    # The offline session answers every https request from its sink.
    from gfutilities.service.offline import offline_session
    _set_baseline(tmp_path)

    class NoNetwork:
        def send(self, *a, **k):
            raise AssertionError('the probe reached the network')
    monkeypatch.setattr(websocket, '_NO_RETRY', NoNetwork())
    assert websocket.firmware_check(offline_session()) is None


def test_the_probe_answer_is_read_within_10_s(tmp_path, monkeypatch):
    # A dripping answer is cut at the attempt's deadline, judged on every
    # receive, not only at the 5 s per read.
    _set_baseline(tmp_path)
    clock = {'t': 0.0}
    monkeypatch.setattr(websocket, '_monotonic', lambda: clock['t'])

    class Drip(_FakeResponse):
        def iter_content(self, chunk_size=1):
            for b in self._body:
                clock['t'] += 1.0           # a byte a second
                yield bytes([b])
    monkeypatch.setattr(websocket, 'request',
                        lambda *a, **k: Drip({'version': '2.7.0-9999'}))
    assert websocket.firmware_check(None) is None
    assert clock['t'] < 13


def test_a_probe_that_gets_no_object_back_failed(tmp_path, monkeypatch):
    # A maintenance page, a JSON array or no answer at all: the probe did
    # not happen, which update_check reports as a failure.
    _set_baseline(tmp_path)
    for answer in (False, _FakeResponse(b'<html>busy</html>'), _FakeResponse([1, 2]),
                   _FakeResponse(b'[' * 30000 + b']' * 30000)):      # nested past the decoder, under the cap
        monkeypatch.setattr(websocket, 'request', lambda *a, **k: answer)
        try:
            got = websocket.firmware_check(None)
        except Exception as e:
            raise AssertionError('the probe raised on its answer: %r' % e)
        assert got is None
