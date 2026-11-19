"""
Copyright 2026 514 LLC d/b/a OpenGlow
Written by Scott Wiederhold
https://community.openglow.org

SPDX-License-Identifier:    MIT
"""
import gzip
import zlib
import logging
import struct

import pytest
from io import BytesIO
from queue import Queue

from gfutilities.configuration import set_cfg
from gfutilities.service import websocket as ws


# ---- C1: reconnect with a fresh ws_token -----------------------------------

class _FakeWSApp:
    """Records constructed URLs; run_forever returns immediately (closed)."""
    urls = []

    def __init__(self, url, **kw):
        _FakeWSApp.urls.append(url)
        self.on_open = kw.get('on_open')

    def run_forever(self, **kw):
        return None

    def close(self):
        pass

    def send(self, m):
        pass


def _ws_cfg():
    set_cfg('SERVICE.STATUS_SERVICE_URL', 'wss://status.example')
    set_cfg('SESSION.WS_TOKEN', 'TOKEN')
    set_cfg('SESSION.USER_AGENT', 'ua')


def test_build_url_uses_current_token(monkeypatch):
    _ws_cfg()
    monkeypatch.setattr('websocket.WebSocketApp', _FakeWSApp)
    _FakeWSApp.urls = []
    c = ws.WsClient(Queue(), Queue())
    c._build()
    assert _FakeWSApp.urls[-1] == 'wss://status.example/TOKEN'


def test_reconnect_reauths_for_fresh_token(monkeypatch):
    _ws_cfg()
    monkeypatch.setattr('websocket.WebSocketApp', _FakeWSApp)
    _FakeWSApp.urls = []
    reauth = []
    monkeypatch.setattr('gfutilities.service.authentication.authenticate_machine',
                        lambda s: (reauth.append(s), True)[1])
    c = ws.WsClient(Queue(), Queue(), session=object())

    calls = {'n': 0}

    def fake_sleep(_secs):
        calls['n'] += 1
        if calls['n'] >= 2:      # stop during the 2nd reconnect wait
            c.stop = True
            return True
        return False

    monkeypatch.setattr(c, '_sleep_or_stop', fake_sleep)
    c.run()
    # first connect (no re-auth) + 1 reconnect (re-auth) before stop
    assert len(_FakeWSApp.urls) == 2
    assert len(reauth) == 1


def test_no_session_never_reauths(monkeypatch):
    _ws_cfg()
    monkeypatch.setattr('websocket.WebSocketApp', _FakeWSApp)
    _FakeWSApp.urls = []
    reauth = []
    monkeypatch.setattr('gfutilities.service.authentication.authenticate_machine',
                        lambda s: reauth.append(s))
    c = ws.WsClient(Queue(), Queue(), session=None)
    calls = {'n': 0}

    def fake_sleep(_secs):
        calls['n'] += 1
        c.stop = True
        return True

    monkeypatch.setattr(c, '_sleep_or_stop', fake_sleep)
    c.run()
    assert reauth == []


# ---- C2: 401 -> re-auth -> replay ------------------------------------------

class _Resp:
    def __init__(self, code):
        self.status_code = code
        self.reason = 'r'


class _Sess:
    def __init__(self, codes):
        self._codes = list(codes)
        self.sent = 0
        self.headers = {}

    def prepare_request(self, req):
        return req

    def send(self, req, **kw):
        self.sent += 1
        self.kwargs = getattr(self, 'kwargs', []) + [kw]
        return _Resp(self._codes.pop(0))


def test_request_401_reauths_and_replays(monkeypatch):
    set_cfg('SERVICE.SERVER_URL', 'https://app.example')
    s = _Sess([401, 200])
    reauth = []
    monkeypatch.setattr('gfutilities.service.authentication.authenticate_machine',
                        lambda sess: (reauth.append(sess), True)[1])
    r = ws.request(s, 'https://app.example/x', 'GET')
    assert r.status_code == 200
    assert s.sent == 2          # original + one replay
    assert reauth == [s]


def test_request_no_replay_when_disabled(monkeypatch):
    s = _Sess([401])
    reauth = []
    monkeypatch.setattr('gfutilities.service.authentication.authenticate_machine',
                        lambda sess: reauth.append(sess))
    r = ws.request(s, 'https://app.example/sign_in', 'POST', _retry_auth=False)
    assert r is False
    assert reauth == []         # sign-in must never trigger re-auth (no recursion)


def test_request_reauth_failure_gives_up(monkeypatch):
    set_cfg('SERVICE.SERVER_URL', 'https://app.example')
    s = _Sess([401, 401])
    monkeypatch.setattr('gfutilities.service.authentication.authenticate_machine',
                        lambda sess: True)
    r = ws.request(s, 'https://app.example/x', 'GET')
    assert r is False           # replay also 401 -> False, no infinite loop
    assert s.sent == 2


# ---- C4: load_motion disk-filler + oversize handling -----------------------

def _fake_puls(body: bytes) -> bytes:
    # \x00 G F 1 | header_len (little-endian) | tag/value pairs | body
    #
    # The three tags are the ones load_motion refuses a job without: the step
    # frequency, the machine serial the job is locked to (0 = unlocked, which
    # is what the live service sends) and the pulse-data format.
    fields = (b'STfr' + (10000).to_bytes(4, 'little')
              + b'MCsn' + (0).to_bytes(4, 'little')
              + b'PDfm' + (0).to_bytes(4, 'little'))
    header = b'\x00GF1' + (8 + len(fields)).to_bytes(4, 'little') + fields
    return header + body


class _StreamResp:
    def __init__(self, data, declared=None):
        self._data = data
        # A real service declares the body length; some do not, and one may
        # declare it wrongly, which is why the guards check both.
        self.headers = {} if declared is None else {'content-length': str(declared)}
        self.closed = False
        self.chunks_read = 0

    def iter_content(self, chunk_size=1024):
        for i in range(0, len(self._data), chunk_size):
            self.chunks_read += 1
            yield self._data[i:i + chunk_size]

    def close(self):
        self.closed = True


def test_load_motion_writes_body_and_no_disk_filler(tmp_path, monkeypatch):
    set_cfg('LOGGING.DIR', str(tmp_path / 'log'))
    (tmp_path / 'log').mkdir()
    body = bytes(range(100)) * 3
    monkeypatch.setattr(ws, 'request', lambda *a, **k: _StreamResp(_fake_puls(body)))
    out = BytesIO()
    info = ws.load_motion(None, 'https://x', out)
    assert out.getvalue() == body
    assert info['size'] == len(body)
    # nothing dumped into the log dir
    assert list((tmp_path / 'log').glob('*.puls')) == []
    assert list((tmp_path / 'log').glob('*.info')) == []


# ---- D1a: GFUIService stoppable --------------------------------------------

class _FakeMachine:
    def __init__(self):
        self.started = False
        self.stopped = False

    def start(self, session, q_tx):
        self.started = True

    def stop(self):
        self.stopped = True


def test_request_stop_breaks_running_loop():
    import threading
    import time
    from gfutilities.service.gfuiservice import GFUIService
    m = _FakeMachine()
    svc = GFUIService(m)
    t = threading.Thread(target=svc.run)
    t.start()
    time.sleep(0.2)                 # loop is spinning on the timeout get
    svc.request_stop()
    t.join(timeout=3)
    assert not t.is_alive()
    assert m.stopped


# ---- E1 finding 2: no thread may outlive a clean stop ----------------------

class _BlockingWSApp:
    """run_forever connects (on_open) then blocks until close()."""
    def __init__(self, url, **kw):
        import threading
        self._on_open = kw.get('on_open')
        self._closed = threading.Event()

    def run_forever(self, **kw):
        self._on_open(self)
        self._closed.wait(10)

    def close(self):
        self._closed.set()

    def send(self, m):
        pass


def test_ws_connect_returns_stoppable_client(monkeypatch):
    _ws_cfg()
    monkeypatch.setattr('websocket.WebSocketApp', _BlockingWSApp)
    c = ws.ws_connect(Queue(), Queue())
    assert isinstance(c, ws.WsClient)
    assert c.ready
    assert c.shutdown(timeout=5)
    assert not c.is_alive()


def test_gfuiservice_keeps_its_ws_client_and_shuts_it_down_with_the_machine(monkeypatch):
    from gfutilities.service import gfuiservice

    class _FakeWs:
        ready = False

        def __init__(self):
            self.down = False

        def shutdown(self, timeout=10):
            self.down = True
            return True

    fake = _FakeWs()
    monkeypatch.setattr(gfuiservice, 'get_session', lambda: object())
    monkeypatch.setattr(gfuiservice, 'authenticate_machine', lambda s: True)
    monkeypatch.setattr(gfuiservice, 'ws_connect', lambda rx, tx, s: fake)
    set_cfg('FACTORY_FIRMWARE.CHECK', None)
    m = _FakeMachine()
    svc = gfuiservice.GFUIService(m)
    assert svc.connect() is True
    assert svc._ws is fake
    svc.stop = True                 # a stop that landed during the sign-in
    svc.run()
    assert not m.started            # no machine start over forgectrl's stop
    assert fake.down
    assert svc._ws is None


def test_a_machine_start_that_raises_still_stops_the_machine_and_the_session(monkeypatch):
    from gfutilities.service import gfuiservice

    class _FakeWs:
        ready = False
        down = False

        def is_alive(self):
            return True

        def shutdown(self, timeout=10):
            self.down = True
            return True

    class _Raising(_FakeMachine):
        def start(self, session, q_tx):
            raise OSError('cnc/reset')

    fake = _FakeWs()
    m = _Raising()
    svc = gfuiservice.GFUIService(m)
    svc._ws = fake
    try:
        svc.run()
    except OSError:
        pass
    assert m.stopped and fake.down


# ---- A1: what the machine will hold in memory ------------------------------
#
# A print arrives as one compressed body and is held whole while the ring is
# fed from it, so the body is the thing that has to be bounded. These are
# memory guards, not ring guards: job length is the feeder's business.

def test_fetch_motion_refuses_a_declared_body_past_the_limit(monkeypatch):
    resp = _StreamResp(_fake_puls(bytes(4096)), declared=4096)
    monkeypatch.setattr(ws, 'request', lambda *a, **k: resp)

    info, source = ws.fetch_motion(None, 'https://x', reject_bytes=1024)

    assert info is False and source is None
    assert resp.chunks_read == 0        # refused before a byte was taken
    assert resp.closed


def test_fetch_motion_refuses_an_undeclared_body_that_runs_past_the_limit(monkeypatch):
    # No content-length, so the only guard left is the running total: a
    # service that declares nothing must not be trusted with all the memory
    # there is.
    resp = _StreamResp(_fake_puls(bytes(300000)))
    monkeypatch.setattr(ws, 'request', lambda *a, **k: resp)

    info, source = ws.fetch_motion(None, 'https://x', reject_bytes=65536)

    assert info is False and source is None
    assert resp.chunks_read > 0         # this one is caught on the way in
    assert resp.closed


def test_fetch_motion_refuses_a_body_that_outruns_its_own_declaration(monkeypatch):
    # A declaration under the limit does not license a body over it.
    resp = _StreamResp(_fake_puls(bytes(300000)), declared=1024)
    monkeypatch.setattr(ws, 'request', lambda *a, **k: resp)

    info, source = ws.fetch_motion(None, 'https://x', reject_bytes=65536)

    assert info is False and source is None
    assert resp.closed


def test_fetch_motion_runs_a_job_at_the_limit(monkeypatch):
    data = _fake_puls(bytes(1000))
    resp = _StreamResp(data, declared=len(data))
    monkeypatch.setattr(ws, 'request', lambda *a, **k: resp)

    info, source = ws.fetch_motion(None, 'https://x', reject_bytes=len(data))

    assert info and source is not None
    assert source.body_size == len(data)
    assert not resp.closed              # the caller still owns a live source


def test_fetch_motion_warns_past_the_warning_level_and_runs_the_job(monkeypatch, caplog):
    data = _fake_puls(bytes(2048))
    monkeypatch.setattr(ws, 'request', lambda *a, **k: _StreamResp(data, declared=len(data)))

    with caplog.at_level(logging.WARNING):
        info, source = ws.fetch_motion(None, 'https://x', warn_bytes=1024, reject_bytes=0)

    assert info and source is not None   # a warning is a log line, not a refusal
    assert any('past the' in r.getMessage() for r in caplog.records)


def test_fetch_motion_limits_lift_at_zero(monkeypatch):
    data = _fake_puls(bytes(200000))
    monkeypatch.setattr(ws, 'request', lambda *a, **k: _StreamResp(data, declared=len(data)))

    info, source = ws.fetch_motion(None, 'https://x', warn_bytes=0, reject_bytes=0)

    assert info and source is not None
    assert source.body_size == len(data)


def test_tx_pump_survives_a_socket_error():
    """A raw OSError out of send (the peer reset before on_close fired) must
    not end the pump: it is started once per client, and every frame after
    it would be dropped for the life of the process. The frame whose send
    failed goes out first once the socket is back."""
    import threading
    import time
    c = ws.WsClient(Queue(), Queue())
    sent = []

    class Sock:
        def __init__(self):
            self.fail_once = True

        def send(self, m):
            if self.fail_once:
                self.fail_once = False
                raise ConnectionResetError(104, 'Connection reset by peer')
            sent.append(m)

        def close(self):
            pass
    c.ws = Sock()
    c.ready = True
    t = threading.Thread(target=c._tx_pump, daemon=True)
    t.start()
    c.msg_q_tx.put('first')
    for _ in range(50):
        if not c.ready:
            break
        time.sleep(0.02)
    assert not c.ready                  # the socket is done
    assert t.is_alive()                 # the pump is not
    c.ready = True                      # the reconnect
    c.msg_q_tx.put('second')
    for _ in range(50):
        if sent:
            break
        time.sleep(0.02)
    c.stop = True
    t.join(2)
    assert sent == ['first', 'second']


# ---- the download: what it checks and where the token goes -----------------

def _header_puls(tags, magic=b'\x00GF1', body=b'\x80' * 16) -> bytes:
    fields = b''.join(t.encode() + struct.pack('<I', v) for t, v in tags)
    return magic + struct.pack('<I', 8 + len(fields)) + fields + body


_GOOD = (('STfr', 10000), ('MCsn', 0), ('PDfm', 0))


def test_fetch_motion_runs_the_header_check_on_every_job(monkeypatch, caplog):
    # A job for another machine, in another format or at another microstep
    # mode never loads, and neither does a body without the GF1 magic.
    set_cfg('MACHINE.SERIAL', '1234')
    for name, data in (
            ('another serial', _header_puls((('STfr', 10000), ('MCsn', 99), ('PDfm', 0)))),
            ('another format', _header_puls((('STfr', 10000), ('MCsn', 0), ('PDfm', 1)))),
            ('another mode', _header_puls(_GOOD + (('XSmm', 16),))),
            ('no magic', _header_puls(_GOOD, magic=b'\x00XY1'))):
        monkeypatch.setattr(ws, 'request', lambda *a, **k: _StreamResp(data))
        caplog.clear()
        with caplog.at_level(logging.ERROR):
            assert ws.fetch_motion(None, 'https://x') == (False, None), name
        assert caplog.records, name


class _RawResp(_StreamResp):
    """A response whose raw stream is the production path: the body as it
    came off the socket, never decoded."""
    def __init__(self, data, fail_after=None):
        _StreamResp.__init__(self, data)
        self.asked = []
        self.raw = self
        self._fail_after = fail_after

    def stream(self, chunk, decode_content=True):
        self.asked.append(decode_content)
        for i in range(0, len(self._data), chunk):
            if self._fail_after is not None and i >= self._fail_after:
                import urllib3
                raise urllib3.exceptions.ProtocolError('connection broken')
            yield self._data[i:i + chunk]


def test_fetch_motion_takes_the_body_undecoded_and_fails_a_broken_one(monkeypatch):
    data = gzip.compress(_header_puls(_GOOD, body=bytes(300000)))
    resp = _RawResp(data)
    monkeypatch.setattr(ws, 'request', lambda *a, **k: resp)
    info, source = ws.fetch_motion(None, 'https://x')
    assert source is not None and source.compressed
    assert resp.asked == [False]
    resp = _RawResp(_header_puls(_GOOD, body=bytes(300000)), fail_after=65536)
    monkeypatch.setattr(ws, 'request', lambda *a, **k: resp)
    try:
        got = ws.fetch_motion(None, 'https://x')
    except Exception as e:
        raise AssertionError('the broken body escaped the download: %r' % e)
    assert got == (False, None)
    assert resp.closed


class _CapturingAdapter:
    """A transport that records what each request carried."""
    def __init__(self, status=200, body=b'', location=None):
        self.sent = []
        self._status, self._body, self._location = status, body, location

    def send(self, request, **kwargs):
        import requests
        self.sent.append(request)
        r = requests.Response()
        r.status_code = self._status
        r.reason = 'x'
        r.raw = BytesIO(self._body)
        r.url = request.url
        r.request = request
        if self._location:
            r.headers['Location'] = self._location
        return r

    def close(self):
        pass


def _session(adapter):
    import requests
    s = requests.Session()
    s.headers['Authorization'] = 'Bearer SECRET'
    s.mount('https://', adapter)
    s.mount('http://', adapter)
    return s


def test_the_session_token_goes_to_the_service_host_only():
    set_cfg('SERVICE.SERVER_URL', 'https://app.example')
    data = _header_puls(_GOOD)
    for url, carries in (('https://storage.example/job?Signature=s', False),
                         ('https://app.example/job', True)):
        adapter = _CapturingAdapter(body=data)
        info, source = ws.fetch_motion(_session(adapter), url)
        assert source is not None, url
        assert ('Authorization' in adapter.sent[0].headers) is carries, url


def test_a_pulse_url_that_is_not_https_is_not_fetched():
    adapter = _CapturingAdapter(body=_header_puls(_GOOD))
    assert ws.fetch_motion(_session(adapter), 'http://storage.example/job') == (False, None)
    assert adapter.sent == []


def test_a_redirect_from_the_storage_host_is_not_followed():
    adapter = _CapturingAdapter(status=302, location='http://elsewhere.example/job')
    assert ws.fetch_motion(_session(adapter), 'https://storage.example/job') == (False, None)
    assert len(adapter.sent) == 1


def test_a_401_from_another_host_does_not_sign_in_again(monkeypatch):
    set_cfg('SERVICE.SERVER_URL', 'https://app.example')
    reauth = []
    monkeypatch.setattr('gfutilities.service.authentication.authenticate_machine',
                        lambda sess: reauth.append(sess) or True)
    s = _Sess([401, 200])                   # a replay would get the 200
    assert ws.request(s, 'https://storage.example/x', 'GET') is False
    assert reauth == []


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_a_download_ends_on_a_stop_and_on_a_rate_too_low(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(ws, '_monotonic', clock)
    data = _header_puls(_GOOD, body=bytes(400000))
    stops = []
    resp = _StreamResp(data)
    monkeypatch.setattr(ws, 'request', lambda *a, **k: resp)
    assert ws.fetch_motion(None, 'https://x',
                           should_stop=lambda: stops.append(1) or len(stops) > 2) == (False, None)
    assert len(stops) == 3 and resp.closed

    def slow_pieces(chunk_size=1024):
        for i in range(0, len(data), chunk_size):
            clock.t += 60.0                 # one 64 KiB piece a minute
            yield data[i:i + chunk_size]
    resp = _StreamResp(data)
    resp.iter_content = slow_pieces
    monkeypatch.setattr(ws, 'request', lambda *a, **k: resp)
    assert ws.fetch_motion(None, 'https://x') == (False, None)
    assert resp.closed


def test_a_retry_after_is_not_waited_out():
    adapter = ws.get_session().get_adapter('https://app.example/')
    assert adapter.max_retries.respect_retry_after_header is False


# ---- the image upload ------------------------------------------------------

def test_an_image_upload_that_is_not_taken_raises(monkeypatch):
    import pytest
    import requests

    class Put:
        status_code, reason = 403, 'Forbidden'
    puts = []
    monkeypatch.setattr(requests, 'put', lambda *a, **k: puts.append(a[0]) or Put())
    with pytest.raises(ws.ImageUploadError):
        ws.img_upload(None, b'jpeg', {'endpoint': 'https://storage.example/put?sig=1',
                                       'action_type': 'lid_image', 'id': 3})
    with pytest.raises(ws.ImageUploadError):
        ws.img_upload(None, b'jpeg', {'endpoint': 'http://storage.example/put',
                                       'action_type': 'lid_image', 'id': 3})
    assert puts == ['https://storage.example/put?sig=1']


# ---- sign-in -----------------------------------------------------------------

def test_a_sign_in_answer_that_is_not_the_token_document_fails_the_sign_in(monkeypatch):
    from gfutilities.service import authentication
    set_cfg('SERVICE.SERVER_URL', 'https://app.example')
    long_doc = b'{"ws_token": "w", "auth_token": "a"' + b' ' * (64 * 1024) + b'}'
    for body in (b'<html>maintenance</html>', b'[1]', b'{"ws_token": "w"}',
                 b'{"ws_token": "w", "auth_token": 5}', long_doc):
        resp = _StreamResp(body)
        monkeypatch.setattr(authentication, 'request', lambda *a, **k: resp)
        assert authentication.authenticate_machine(_Sess([])) is False, body[:20]


# ---- reconnect: back-off, socket timeout -------------------------------------

@pytest.mark.parametrize('draw', ['top', 'bottom'])
def test_reconnects_back_off_with_jitter_and_start_over_after_a_long_session(monkeypatch, draw):
    # 5 s doubling to 300 s, each wait drawn between half and all of its
    # value; a session that stayed up 60 s starts the count again.
    import random
    import websocket as wslib
    _ws_cfg()
    clock = _Clock()
    monkeypatch.setattr(ws, '_monotonic', clock)
    monkeypatch.setattr(random, 'uniform', (lambda lo, hi: hi) if draw == 'top' else (lambda lo, hi: lo))
    timeouts = []
    sessions = {'n': 0}

    class App(_FakeWSApp):
        def __init__(self, url, **kw):
            _FakeWSApp.__init__(self, url, **kw)
            timeouts.append(wslib.getdefaulttimeout())

        def run_forever(self, **kw):
            sessions['n'] += 1
            if sessions['n'] == 5:          # one long session
                self.on_open(self)
                clock.t += 60

    monkeypatch.setattr('websocket.WebSocketApp', App)
    c = ws.WsClient(Queue(), Queue())
    waits = []

    def fake_sleep(secs):
        waits.append(secs)
        if len(waits) >= 7:
            c.stop = True
        return c.stop

    monkeypatch.setattr(c, '_sleep_or_stop', fake_sleep)
    c.run()
    full = [5, 10, 20, 40, 5, 10, 20]
    assert waits == (full if draw == 'top' else [w / 2 for w in full])
    assert set(timeouts) == {20}


def test_a_failed_sign_in_backs_off_the_same_way_up_to_its_cap(monkeypatch):
    _ws_cfg()
    import random
    monkeypatch.setattr(random, 'uniform', lambda lo, hi: hi)
    monkeypatch.setattr('websocket.WebSocketApp', _FakeWSApp)
    clock = _Clock()
    monkeypatch.setattr(ws, '_monotonic', clock)
    c = ws.WsClient(Queue(), Queue(), session=object())
    waits = []
    stamps = []

    def sign_in(session):
        stamps.append(c.step_at == clock.t)     # each attempt begins a step
        clock.t += 20.0                         # a sign-in that takes 20 s and fails
        return False
    monkeypatch.setattr('gfutilities.service.authentication.authenticate_machine', sign_in)
    # The first session lasts 30 s.
    monkeypatch.setattr(_FakeWSApp, 'run_forever', lambda self, **kw: setattr(clock, 't', clock.t + 30.0))

    def fake_sleep(secs):
        stamps.append(c.step_at == clock.t)     # each wait begins a step
        waits.append(secs)
        clock.t += secs
        c.stop = len(waits) >= 9
        return c.stop
    monkeypatch.setattr(c, '_sleep_or_stop', fake_sleep)
    c.run()
    # The first wait follows the first session; every later one a sign-in
    # that failed.
    assert waits == [5, 10, 20, 40, 80, 160, 300, 300, 300]
    assert len(stamps) == 17 and all(stamps)


# ---- the service loop --------------------------------------------------------

class _LoopMachine(_FakeMachine):
    def __init__(self):
        _FakeMachine.__init__(self)
        self.running_action_id = None


def test_one_bad_frame_never_ends_the_service_loop(monkeypatch):
    from gfutilities.service import gfuiservice
    dispatched = []

    def dispatch(machine, msg, allow_print=True):
        dispatched.append(msg)
        if msg.get('boom'):
            raise RuntimeError('handler bug')
        return 'dispatched'

    monkeypatch.setattr(gfuiservice, 'dispatch_action', dispatch)
    svc = gfuiservice.GFUIService(_LoopMachine())
    for raw in ('not json', '[1, 2]', '"text"', '[' * 100000 + ']' * 100000,
                '{"boom": true}', '{"id": 5, "last": true}'):
        svc.q_msg_rx.put(raw)

    def stop_after_last(machine, msg, allow_print=True):
        r = dispatch(machine, msg)
        if msg.get('last'):
            svc.request_stop()
        return r
    monkeypatch.setattr(gfuiservice, 'dispatch_action', stop_after_last)
    try:
        svc.run()
    except Exception as e:
        raise AssertionError('a bad frame ended the service loop: %r' % e)
    assert dispatched[-1] == {'id': 5, 'last': True}
    assert {'boom': True} in dispatched


def test_a_dead_socket_thread_ends_the_service_loop():
    from gfutilities.service import gfuiservice

    class DeadWs:
        ready = True

        def is_alive(self):
            return False

        def shutdown(self, timeout=10):
            return True

    m = _LoopMachine()
    svc = gfuiservice.GFUIService(m)
    svc._ws = DeadWs()
    waits = []

    def get(timeout=None):
        waits.append(timeout)
        svc.request_stop()                  # never wait on a frame that cannot come
        raise gfuiservice.Empty
    svc.q_msg_rx.get = get
    svc.run()                               # returns: no frame can arrive
    assert waits == []
    assert m.stopped


class _OutageWs:
    def __init__(self):
        self.ready = False
        self._unsent = None
        self.step_at = 0.0                  # the reconnect loop's last step: stuck unless a case moves it

    def is_alive(self):
        return True

    def shutdown(self, timeout=10):
        return True


def _outage(monkeypatch, steps):
    """The service loop over one frame per step, each read 100 s after the
    one before; each step sets the scene for the passes after it, and one
    more frame stops the loop. Returns the socket client and the frames
    read."""
    from gfutilities.service import gfuiservice
    clock = _Clock()
    monkeypatch.setattr(gfuiservice, '_monotonic', clock)
    m = _LoopMachine()
    svc = gfuiservice.GFUIService(m)
    svc._ws = _OutageWs()
    read = []

    def dispatch(machine, msg, allow_print=True):
        read.append(msg['id'])
        clock.t += 100.0
        if msg['id'] < len(steps):
            steps[msg['id']](svc, m)
        else:
            svc.request_stop()
        return 'ignored'
    monkeypatch.setattr(gfuiservice, 'dispatch_action', dispatch)
    for i in range(len(steps) + 1):
        svc.q_msg_rx.put('{"id": %d}' % i)
    ws_client = svc._ws
    try:
        svc.run()
    except Exception as e:
        pytest.fail('the service loop raised: %r' % e)
    return ws_client, read


def _idle(svc, m):
    pass


def test_an_idle_session_with_its_socket_down_past_900_s_ends(monkeypatch):
    # The socket down and nothing to do: the session ends once 900 s have
    # passed, at the first pass after them, with the stop frame unread.
    _, read = _outage(monkeypatch, [_idle] * 10)
    assert read == list(range(10))


def test_an_idle_outage_of_an_hour_with_the_client_cycling_keeps_the_session(monkeypatch):
    # The reconnect loop begins a step every 300 s, as its back-off at its
    # cap does: the session never ends, and the respawn's connect-time hunt
    # never comes.
    from gfutilities.service import gfuiservice

    def steps(svc, m):
        svc._ws.step_at = gfuiservice._monotonic()
    plan = [steps if i % 3 == 0 else _idle for i in range(36)]
    _, read = _outage(monkeypatch, plan)
    assert read == list(range(37))


def test_a_long_session_that_has_just_dropped_is_not_ended(monkeypatch):
    # An hour up on one stamp, then the routine drop: between the close and
    # the loop's next stamp, the only stamp is the session's own start. The
    # count runs from the drop, not from that stamp.
    from gfutilities.service import gfuiservice

    def up(svc, m):
        svc._ws.ready = True

    def dropped(svc, m):
        svc._ws.ready = False

    def stamped(svc, m):
        svc._ws.step_at = gfuiservice._monotonic()
    steps = [up] + [_idle] * 35 + [dropped, stamped] + [_idle] * 3
    _, read = _outage(monkeypatch, steps)
    assert read == list(range(len(steps) + 1))


def test_a_client_whose_loop_has_begun_no_step_is_not_judged(monkeypatch):
    def unstamped(svc, m):
        svc._ws.step_at = None
    _, read = _outage(monkeypatch, [unstamped] + [_idle] * 20)
    assert read == list(range(22))


def test_a_ready_socket_keeps_the_session_whatever_the_loop_last_did(monkeypatch):
    def up(svc, m):
        svc._ws.ready = True
    _, read = _outage(monkeypatch, [up] + [_idle] * 20)
    assert read == list(range(22))


def test_a_print_that_ends_during_an_outage_keeps_its_events_for_the_reconnect(monkeypatch):
    # The print runs through the outage; its terminal event waits, queued
    # and then held by the pump, and the session waits with it: the events
    # go out once the socket is back, never lost with a restart.
    def runs(svc, m):
        m.running_action_id = 42

    def ends(svc, m):
        m.running_action_id = None
        svc.q_msg_tx.put('print:completed')

    def taken_by_the_pump(svc, m):
        svc._ws._unsent = svc.q_msg_tx.get_nowait()
    steps = [runs] + [_idle] * 11 + [ends] + [_idle] * 11 + [taken_by_the_pump] + [_idle] * 11
    client, read = _outage(monkeypatch, steps)
    assert read == list(range(len(steps) + 1))
    assert client._unsent == 'print:completed'


# ---- what reaches the log ----------------------------------------------------

def test_a_signed_url_never_reaches_the_log_with_its_query(monkeypatch, caplog):
    import requests
    secret = 'Signature=SECRETSIG'

    class Boom:
        def prepare_request(self, req):
            return req

        def send(self, req, **kw):
            raise requests.ConnectionError('Max retries exceeded with url: /job?%s' % secret)

    with caplog.at_level(logging.DEBUG):
        ws.request(Boom(), 'https://storage.example/job?%s' % secret, 'GET')
        c = ws.WsClient(Queue(), Queue())
        c._on_message(None, '{"action_type":"print","motion_url":"https://s.example/j?%s",'
                            '"endpoint":"https://s.example/i?%s","id":4}' % (secret, secret))
    assert not any(secret in r.getMessage() for r in caplog.records)
    assert c.msg_q_rx.get_nowait().count(secret) == 2      # the action itself is whole


# ---- the download against a real server ---------------------------------------
#
# A loopback HTTP server stands in for the storage host. The body drips at
# 1 KiB every 0.1 s (10 KiB/s, under the 16 KiB/s floor) with a declared
# Content-Length, so it is not chunked: a blocking read of 64 KiB would hold
# it 6.4 s.

class _Loopback:
    def __init__(self):
        import http.server
        import socketserver
        import threading
        self.done = threading.Event()
        done = self.done
        # path -> (extra headers, the body's pieces, seconds between pieces)
        self.routes = {'/drip': ({}, [b'\x80' * 1024] * 128, 0.1)}
        routes = self.routes
        self.seen = []                      # (method, path, request headers)
        seen = self.seen

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length') or 0))
                self.do_GET('POST')

            def do_GET(self, method='GET'):
                seen.append((method, self.path, dict(self.headers)))
                try:
                    route = routes.get(self.path.split('?', 1)[0])
                    if route is None:           # a head that never comes
                        done.wait(10)
                        return
                    headers, pieces, gap = route
                    self.send_response(200)
                    self.send_header('Content-Length', str(sum(len(p) for p in pieces)))
                    for k, v in headers.items():
                        self.send_header(k, v)
                    self.end_headers()
                    for i, piece in enumerate(pieces):
                        if done.is_set():
                            return
                        self.wfile.write(piece)
                        self.wfile.flush()
                        gap_s = gap(i) if callable(gap) else gap
                        if gap_s:
                            done.wait(gap_s)
                except OSError:
                    pass

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
        self.server = Server(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.05},
                         daemon=True).start()
        self.base = 'https://127.0.0.1:%d' % self.server.server_address[1]

    def session(self):
        import requests
        from requests.adapters import HTTPAdapter

        class ToLoopback(HTTPAdapter):
            # The storage URL is https; the loopback server speaks plain http.
            def send(self, request, **kw):
                request.url = request.url.replace('https://', 'http://', 1)
                return HTTPAdapter.send(self, request, **kw)
        s = requests.Session()
        s.mount('https://', ToLoopback())
        return s

    def close(self):
        self.done.set()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def loopback():
    srv = _Loopback()
    yield srv
    srv.close()


def _timed(fn):
    import time
    t0 = time.monotonic()
    out = fn()
    return out, time.monotonic() - t0


@pytest.mark.parametrize('reads', ['as they arrive', 'in small reads'])
def test_a_stop_reaches_a_dripping_download_on_its_next_receive(loopback, monkeypatch, reads):
    import time
    import urllib3
    if reads == 'in small reads':           # a response class with no read1 of its own
        monkeypatch.setattr(urllib3.HTTPResponse, 'read1',
                            urllib3.response.BaseHTTPResponse.read1)
    t0 = time.monotonic()
    out, took = _timed(lambda: ws.fetch_motion(loopback.session(), loopback.base + '/drip',
                                               should_stop=lambda: time.monotonic() - t0 > 0.3))
    assert out == (False, None)
    assert took < 2.0


def test_a_dripping_download_under_the_floor_ends_past_the_grace(loopback, monkeypatch):
    monkeypatch.setattr(ws, 'DOWNLOAD_GRACE_S', 0.3)
    out, took = _timed(lambda: ws.fetch_motion(loopback.session(), loopback.base + '/drip'))
    assert out == (False, None)
    assert took < 2.5                       # 0.3 s grace, then the floor: about 0.8 s


def test_a_stop_ends_a_download_whose_answer_never_comes(loopback, monkeypatch):
    import time
    monkeypatch.setattr(ws, 'DOWNLOAD_TIMEOUT_S', 0.5)
    monkeypatch.setattr(ws, 'DOWNLOAD_READ_S', 0.5)     # the head's read waits this long
    t0 = time.monotonic()
    out, took = _timed(lambda: ws.fetch_motion(loopback.session(), loopback.base + '/stall',
                                               should_stop=lambda: time.monotonic() - t0 > 0.2))
    assert out == (False, None)
    assert took < 1.2                       # the first attempt's 0.5 s, then the stop


def test_the_answer_is_tried_again_only_after_no_answer_came(monkeypatch):
    # Five attempts, 1, 2, 4 and 8 s apart, each one attempt with a 5 s
    # timeout; an answer that is not 200 is final; a stop ends the waits.
    clock = _Clock()
    monkeypatch.setattr(ws, '_monotonic', clock)
    monkeypatch.setattr(ws, '_sleep', lambda secs: setattr(clock, 't', clock.t + secs))
    calls = []

    def answer(failure):
        def req(*a, **k):
            calls.append((round(clock.t, 6), k.get('retry'), k.get('timeout')))
            k['_failure'].append(failure)
            return False
        return req
    monkeypatch.setattr(ws, 'request', answer('transport'))
    assert ws.fetch_motion(None, 'https://x') == (False, None)
    assert calls == [(t, False, (5, 15)) for t in (0, 1, 3, 7, 15)]
    assert clock.t == 15                            # no wait after the last
    del calls[:]
    monkeypatch.setattr(ws, 'request', answer(403))
    assert ws.fetch_motion(None, 'https://x') == (False, None)
    assert len(calls) == 1
    del calls[:]
    clock.t = 0.0
    monkeypatch.setattr(ws, 'request', answer('transport'))
    assert ws.fetch_motion(None, 'https://x', should_stop=lambda: clock.t > 2) == (False, None)
    assert len(calls) == 2 and clock.t < 2.5        # ended inside the second wait
    del calls[:]
    assert ws.fetch_motion(None, 'https://x', should_stop=lambda: True) == (False, None)
    assert calls == []                              # stopped before any attempt


def test_the_grace_runs_from_the_answer_not_the_request(monkeypatch):
    # An answer that takes 40 s to come, then a body read in 4 s: the grace
    # starts with the answer, so the slow answer costs the body nothing.
    clock = _Clock()
    monkeypatch.setattr(ws, '_monotonic', clock)
    data = _header_puls(_GOOD, body=bytes(400000))
    resp = _StreamResp(data)

    def pieces(chunk_size=1024):
        for i in range(0, len(data), 1024):
            clock.t += 0.01
            yield data[i:i + 1024]
    resp.iter_content = pieces

    def slow_answer(*a, **k):
        clock.t += 40.0
        return resp
    monkeypatch.setattr(ws, 'request', slow_answer)
    info, source = ws.fetch_motion(None, 'https://x')
    assert source is not None


def test_a_slow_start_inside_the_grace_is_taken(monkeypatch):
    # The first piece 25 s in, the rest fast: under the 30 s grace, taken.
    clock = _Clock()
    monkeypatch.setattr(ws, '_monotonic', clock)
    data = _header_puls(_GOOD, body=bytes(400000))
    resp = _StreamResp(data)

    def pieces(chunk_size=1024):
        for i in range(0, len(data), chunk_size):
            clock.t = 25.0 if i == 0 else clock.t + 0.1
            yield data[i:i + chunk_size]
    resp.iter_content = pieces
    monkeypatch.setattr(ws, 'request', lambda *a, **k: resp)
    info, source = ws.fetch_motion(None, 'https://x')
    assert source is not None


def test_a_corrupt_compressed_body_is_refused(monkeypatch):
    data = bytearray(gzip.compress(_header_puls(_GOOD, body=bytes(200000))))
    data[10:30] = b'\xff' * 20              # the first deflate block, past the gzip header
    monkeypatch.setattr(ws, 'request', lambda *a, **k: _StreamResp(bytes(data)))
    import zlib
    try:
        out = ws.fetch_motion(None, 'https://x')
    except zlib.error as e:
        pytest.fail('the corrupt body escaped the refusal: %s' % e)
    assert out == (False, None)


def test_the_401_replay_keeps_its_redirect_rule(monkeypatch):
    set_cfg('SERVICE.SERVER_URL', 'https://app.example')
    monkeypatch.setattr('gfutilities.service.authentication.authenticate_machine', lambda s: True)
    s = _Sess([401, 200])
    assert ws.request(s, 'https://app.example/x', 'GET', allow_redirects=False)
    assert [k['allow_redirects'] for k in s.kwargs] == [False, False]


def test_an_upload_redirect_is_not_followed(monkeypatch):
    # A 307 to http would carry the bed image in clear.
    import requests
    sent_in_clear = []

    def put(url, data=None, allow_redirects=True, **kw):
        class R:
            status_code, reason = 307, 'Temporary Redirect'
        if allow_redirects:                 # what requests does when it follows
            sent_in_clear.append(data)
            R.status_code, R.reason = 200, 'OK'
        return R()
    monkeypatch.setattr(requests, 'put', put)
    with pytest.raises(ws.ImageUploadError):
        ws.img_upload(None, b'BED-IMAGE', {'endpoint': 'https://storage.example/put?sig=1',
                                           'action_type': 'lid_image', 'id': 3})
    assert sent_in_clear == []


def test_a_pause_in_the_body_shorter_than_the_read_bound_is_taken(loopback, monkeypatch):
    # The connect has its 5 s; each read of the body has 15 s (here 0.3 s
    # and 1.5 s): a body that pauses for 0.6 s is a job, not a failure.
    monkeypatch.setattr(ws, 'DOWNLOAD_TIMEOUT_S', 0.3)
    monkeypatch.setattr(ws, 'DOWNLOAD_READ_S', 1.5)
    data = _header_puls(_GOOD, body=bytes(8192))
    loopback.routes['/pause'] = ({}, [data[:4096], data[4096:]], 0.6)
    info, source = ws.fetch_motion(loopback.session(), loopback.base + '/pause')
    assert source is not None


def test_a_compressed_body_is_held_as_the_server_sent_it(loopback):
    # A gzip Content-Encoding is the job's own compression: the body is held
    # compressed and inflated as the ring drains, never decoded in transit.
    data = gzip.compress(_header_puls(_GOOD, body=bytes(400000)))
    loopback.routes['/gzipped'] = ({'Content-Encoding': 'gzip'}, [data], 0)
    info, source = ws.fetch_motion(loopback.session(), loopback.base + '/gzipped')
    assert source is not None and source.compressed
    assert source.body_size == len(data)


def test_a_stop_is_read_on_receives_smaller_than_a_kilobyte(loopback):
    # 100 bytes every 0.1 s: each arrival is a receive the stop is read on,
    # not a wait for 1 KiB (1 s here).
    import time
    loopback.routes['/tiny'] = ({}, [bytes([0x80]) * 100] * 200, 0.1)
    t0 = time.monotonic()
    out, took = _timed(lambda: ws.fetch_motion(loopback.session(), loopback.base + '/tiny',
                                               should_stop=lambda: time.monotonic() - t0 > 0.3))
    assert out == (False, None)
    assert took < 0.8


def _asks_for_brotli(session):
    # The session as it is where a brotli decoder is installed.
    session.headers['Accept-Encoding'] = 'gzip, deflate, br'
    return session


def test_the_probe_reads_a_gzip_answer_and_asks_only_for_what_it_reads(loopback, tmp_path):
    import json
    set_cfg('SERVICE.SERVER_URL', loopback.base)
    set_cfg('FACTORY_FIRMWARE.FW_VERSION', '2.6.0-2228')
    set_cfg('FACTORY_FIRMWARE.STATUS_FILE', str(tmp_path / 'gf-latest.json'))
    answer = gzip.compress(json.dumps({'version': '2.7.0-9999'}).encode())
    loopback.routes['/update/current'] = ({'Content-Encoding': 'gzip'}, [answer], 0)
    got = ws.firmware_check(_asks_for_brotli(loopback.session()))
    assert got and got['version'] == '2.7.0-9999'
    assert [h.get('Accept-Encoding') for _, _, h in loopback.seen] == ['gzip']


def test_the_sign_in_reads_a_gzip_answer_and_asks_only_for_what_it_reads(loopback):
    import json
    from gfutilities.service import authentication
    set_cfg('SERVICE.SERVER_URL', loopback.base)
    set_cfg('MACHINE.SERIAL', '123')
    set_cfg('MACHINE.PASSWORD', 'pw')
    answer = gzip.compress(json.dumps({'ws_token': 'w', 'auth_token': 'a'}).encode())
    loopback.routes['/machines/sign_in'] = ({'Content-Encoding': 'gzip'}, [answer], 0)
    assert authentication.authenticate_machine(_asks_for_brotli(loopback.session()))
    assert [(m, h.get('Accept-Encoding')) for m, _, h in loopback.seen] == [('POST', 'gzip')]


def test_no_attempt_at_the_answer_starts_past_60_s(monkeypatch):
    # Attempts that take 20 s each: 0-20, 21-41, 43-63; the next would start
    # past the 60 s, so there are three.
    clock = _Clock()
    monkeypatch.setattr(ws, '_monotonic', clock)
    monkeypatch.setattr(ws, '_sleep', lambda secs: setattr(clock, 't', clock.t + secs))
    calls = []

    def slow(*a, **k):
        calls.append(round(clock.t, 6))
        clock.t += 20.0
        k['_failure'].append('transport')
        return False
    monkeypatch.setattr(ws, 'request', slow)
    assert ws.fetch_motion(None, 'https://x') == (False, None)
    assert calls == [0, 21, 43]


def test_an_answer_after_a_transport_failure_is_taken(monkeypatch):
    # The real request(): a connect that fails, then the job.
    import requests
    clock = _Clock()
    monkeypatch.setattr(ws, '_monotonic', clock)
    monkeypatch.setattr(ws, '_sleep', lambda secs: setattr(clock, 't', clock.t + secs))
    data = _header_puls(_GOOD)

    class Flaky(_CapturingAdapter):
        def send(self, request, **kwargs):
            if not self.sent:
                self.sent.append(request)
                raise requests.ConnectionError('connection refused')
            return _CapturingAdapter.send(self, request, **kwargs)
    adapter = Flaky(body=data)
    info, source = ws.fetch_motion(_session(adapter), 'https://storage.example/job')
    assert source is not None and len(adapter.sent) == 2


def test_the_probe_deadline_counts_from_before_the_request(tmp_path, monkeypatch):
    # An answer head that takes 9 s leaves the body 1 s of the 10.
    import json
    clock = _Clock()
    monkeypatch.setattr(ws, '_monotonic', clock)
    set_cfg('SERVICE.SERVER_URL', 'https://app.example')
    set_cfg('FACTORY_FIRMWARE.STATUS_FILE', str(tmp_path / 'gf-latest.json'))
    body = json.dumps({'version': '2.7.0-9999'}).encode()

    class Slow:
        def iter_content(self, chunk_size=1):
            for i in range(0, len(body), 7):
                clock.t += 1.0
                yield body[i:i + 7]

        def close(self):
            pass

    def head(*a, **k):
        clock.t += 9.0
        return Slow()
    monkeypatch.setattr(ws, 'request', head)
    assert ws.firmware_check(None) is None


@pytest.mark.parametrize('first_at, taken', [(25.0, True), (31.0, False)])
def test_the_grace_is_30_s(monkeypatch, first_at, taken):
    clock = _Clock()
    monkeypatch.setattr(ws, '_monotonic', clock)
    data = _header_puls(_GOOD, body=bytes(400000))
    resp = _StreamResp(data)

    def pieces(chunk_size=1024):
        for i in range(0, len(data), 1024):     # 1 KiB pieces, the first late
            clock.t = first_at if i == 0 else clock.t + 0.01
            yield data[i:i + 1024]
    resp.iter_content = pieces
    monkeypatch.setattr(ws, 'request', lambda *a, **k: resp)
    info, source = ws.fetch_motion(None, 'https://x')
    assert (source is not None) is taken


def test_a_download_at_14_kib_s_is_under_the_floor(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(ws, '_monotonic', clock)
    data = _header_puls(_GOOD, body=bytes(5 * 1024 * 1024))
    resp = _StreamResp(data)

    def pieces(chunk_size=1024):
        for i in range(0, len(data), 14 * 1024):
            clock.t += 1.0
            yield data[i:i + 14 * 1024]
    resp.iter_content = pieces
    monkeypatch.setattr(ws, 'request', lambda *a, **k: resp)
    assert ws.fetch_motion(None, 'https://x') == (False, None)


def test_the_401_replay_of_one_attempt_is_one_attempt(monkeypatch):
    set_cfg('SERVICE.SERVER_URL', 'https://app.example')
    monkeypatch.setattr('gfutilities.service.authentication.authenticate_machine', lambda s: True)
    retrying = []

    class Once:
        def __init__(self):
            self.codes = [401, 200]

        def send(self, request, **kwargs):
            return _Resp(self.codes.pop(0))

    class Sess(_Sess):
        verify, proxies, cert = True, {}, None

        def __init__(self):
            _Sess.__init__(self, [])
            self.once = Once()

        def get_adapter(self, url):
            return self.once

        def send(self, req, **kw):
            retrying.append(req)
            return _Resp(200)
    assert ws.request(Sess(), 'https://app.example/x', 'GET', retry=False)
    assert retrying == []


def _doc(size: int) -> bytes:
    """A JSON object of exactly ``size`` bytes."""
    head, tail = b'{"version": "', b'"}'
    return head + b'x' * (size - len(head) - len(tail)) + tail


def _answer(loopback, headers: dict, body):
    # ``body``: the bytes in one piece, or a list of pieces sent 50 ms apart,
    # each a receive of its own.
    pieces = body if isinstance(body, list) else [body]
    loopback.routes['/answer'] = (headers, pieces, 0.05)
    r = loopback.session().get(loopback.base + '/answer', stream=True, timeout=5)
    try:
        return ws._read_json(r)
    except Exception as e:
        raise AssertionError('the answer escaped the reader: %r' % e)


_V = b'{"version": "2.7.0-9999"}'


@pytest.mark.parametrize('headers, body', [
    ({}, _V),
    ({'Content-Encoding': 'identity'}, _V),
    ({'Content-Encoding': 'gzip'}, gzip.compress(_V[:12]) + gzip.compress(_V[12:])),
    ({'Content-Encoding': 'gzip'}, gzip.compress(_V) + b'\0\0trailing'),
    ({'Content-Encoding': 'gzip'}, gzip.compress(_V) + b'\n'),
    ({'Content-Encoding': 'gzip'}, gzip.compress(_V) + b'\x1f\x8b'),
    ({'Content-Encoding': 'gzip'}, gzip.compress(b'') + gzip.compress(_V)[:-8]),
], ids=['as-sent', 'identity', 'two-members', 'trailing-bytes', 'one-stray-byte', 'a-member-begun',
        'empty-then-cut-short'])
def test_a_json_answer_is_read_as_sent_or_from_every_gzip_member(loopback, headers, body):
    assert _answer(loopback, headers, body) == {'version': '2.7.0-9999'}


@pytest.mark.parametrize('encoding', [None, 'gzip'])
def test_the_json_cap_is_64_kib_once_decoded(loopback, encoding):
    # In gzip the member's end (its 8-byte trailer) comes in a receive of
    # its own, after all 65536 bytes are held.
    headers = {'Content-Encoding': encoding} if encoding else {}

    def send(doc):
        if not encoding:
            return doc
        body = gzip.compress(doc)
        return [body[:-8], body[-8:]]
    got = _answer(loopback, headers, send(_doc(64 * 1024)))
    assert got is not None and got['version'].startswith('xxx')
    assert _answer(loopback, headers, send(_doc(64 * 1024 + 1))) is None


def _gzip_padded(doc: bytes, size: int) -> bytes:
    """One gzip member of ``doc``, its header padded with a comment (FCOMMENT)
    to ``size`` bytes in all."""
    co = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    deflated = co.compress(doc) + co.flush()
    trailer = struct.pack('<II', zlib.crc32(doc), len(doc))
    head = b'\x1f\x8b\x08\x10' + bytes(4) + b'\x00\xff'
    comment = b'c' * (size - len(head) - 1 - len(deflated) - len(trailer)) + b'\x00'
    return head + comment + deflated + trailer


@pytest.mark.parametrize('size, taken', [(128 * 1024, True), (128 * 1024 + 1, False)])
def test_a_gzip_answer_is_read_up_to_twice_the_cap_as_sent(loopback, size, taken):
    body = _gzip_padded(_V, size)
    assert len(body) == size
    got = _answer(loopback, {'Content-Encoding': 'gzip'}, body)
    assert (got == {'version': '2.7.0-9999'}) is taken


def test_a_stored_gzip_answer_at_the_cap_is_read(loopback):
    # Level 0: the 65536 decoded bytes are more than 65536 as sent.
    body = gzip.compress(_doc(64 * 1024), compresslevel=0)
    assert ws.JSON_BODY_MAX_BYTES < len(body) <= 2 * ws.JSON_BODY_MAX_BYTES
    got = _answer(loopback, {'Content-Encoding': 'gzip'}, body)
    assert got is not None and got['version'].startswith('xxx')


@pytest.mark.parametrize('encoding, answer', [
    ('deflate', zlib.compress(_V)),                 # not asked for
    ('br', _V),                                     # nor this
    ('gzip', _V),                                   # said gzip, is not
    ('gzip', gzip.compress(_V)[:-8]),               # cut short of its trailer
    ('gzip', gzip.compress(_V) + gzip.compress(b'xx')[:12]),    # what a later member decodes is kept
], ids=['deflate', 'br', 'not-gzip', 'cut-short', 'a-later-member-begun-with-data'])
def test_an_answer_in_another_encoding_or_that_does_not_decode_is_refused(loopback, encoding, answer):
    assert _answer(loopback, {'Content-Encoding': encoding}, answer) is None


@pytest.mark.parametrize('members', [1, 200])
def test_no_inflate_takes_a_json_answer_past_the_cap(monkeypatch, members):
    # 8 KiB at a time: 40 KiB that does not compress, then 1 MiB of zeros;
    # or 200 members of 64 KiB of zeros. Each inflate is held to what is
    # left under the cap, across members too, so no more than the cap and
    # one byte is ever inflated.
    import random
    real = zlib.decompressobj
    inflated = []

    class Counting:
        def __init__(self, *a):
            self._d = real(*a)

        def __getattr__(self, name):
            return getattr(self._d, name)

        def decompress(self, data, max_length=0):
            out = self._d.decompress(data, max_length)
            inflated.append(len(out))
            return out
    monkeypatch.setattr(zlib, 'decompressobj', Counting)
    if members == 1:
        body = gzip.compress(random.Random(7).randbytes(40 * 1024) + bytes(1 << 20))
    else:
        body = gzip.compress(bytes(64 * 1024)) * members

    class Raw:
        def stream(self, amt, decode_content=None):
            for i in range(0, len(body), 8192):
                yield body[i:i + 8192]

    class Resp:
        headers = {'Content-Encoding': 'gzip'}
        raw = Raw()

        def close(self):
            pass
    assert ws._read_json(Resp()) is None
    assert len(inflated) >= 2 and sum(inflated) <= ws.JSON_BODY_MAX_BYTES + 1
