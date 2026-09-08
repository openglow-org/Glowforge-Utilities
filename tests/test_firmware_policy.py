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
        self._payload = payload

    def json(self):
        return self._payload


def _set_baseline(tmp_path, version='2.6.0-2228'):
    set_cfg('SERVICE.SERVER_URL', 'https://app.glowforge.com')
    set_cfg('FACTORY_FIRMWARE.FW_VERSION', version)
    status_file = tmp_path / 'gf-latest.json'
    set_cfg('FACTORY_FIRMWARE.STATUS_FILE', str(status_file))
    return status_file


def test_record_factory_latest_writes_status_file(tmp_path):
    status_file = _set_baseline(tmp_path)
    websocket.record_factory_latest('2.7.0-9999')
    data = json.loads(status_file.read_text())
    assert data['latest_gf_version'] == '2.7.0-9999'
    assert data['tested_against_gf'] == '2.6.0-2228'
    assert 'checked_at' in data


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
    # And the latest version was recorded for the banner.
    assert json.loads(status_file.read_text())['latest_gf_version'] == '2.7.0-9999'


def test_firmware_check_matches_baseline_returns_false_but_still_records(tmp_path, monkeypatch):
    status_file = _set_baseline(tmp_path)
    monkeypatch.setattr(websocket, 'request',
                        lambda *a, **k: _FakeResponse({'version': '2.6.0-2228',
                                                       'download_url': 'https://x/y.fw'}))
    result = websocket.firmware_check(None)
    assert result is False
    # Even when it matches, the observed version is recorded.
    assert json.loads(status_file.read_text())['latest_gf_version'] == '2.6.0-2228'


def test_connect_does_not_download_factory_firmware():
    # The cloud-safe policy: connect() must never call firmware_download.
    import inspect
    from gfutilities.service import gfuiservice
    src = inspect.getsource(gfuiservice.GFUIService.connect)
    assert 'firmware_download' not in src
