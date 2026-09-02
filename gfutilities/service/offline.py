"""
The offline service: the machine driven by a local socket instead of the
Glowforge web service.

Everything downstream of the service is the same object graph the real
session drives - dispatch_action, the machine's run loop, the pulse
source, the events the machine emits - so a test that needs the machine
to run a print, pause it, or have the lid end it can hand the machine a
print action itself and read the same lifecycle back, with no account,
no network, and no job designed in the app. Nothing here reaches the
real service: the session it hands the machine serves ``file://`` and
swallows uploads, and there is no WebSocket.

Protocol, on a UNIX socket: the client sends action messages as the
service would, one JSON object per line (``id``, ``action_type``,
``status``, ``motion_url``, ``settings``...); every event the machine
would have sent the service comes back the same way, one JSON object per
line, to every connected client, and is logged.

(C) Copyright 2026
Scott Wiederhold, s.e.wiederhold@gmail.com
https://community.openglow.org

SPDX-License-Identifier:    MIT
"""
import io
import logging
import os
import socket
import threading
from queue import Queue, Empty
from typing import Union

import requests
from requests.adapters import BaseAdapter
from requests.models import Response

from gfutilities._common import LOGGER_NAME
from gfutilities.device.basemachine import BaseMachine
from gfutilities.service.gfuiservice import GFUIService

logger = logging.getLogger(LOGGER_NAME)

DEFAULT_SOCKET = '/run/gfcloud-offline.sock'
# What the offline service logs when it is up: the mark a reader of the
# log takes as "this client has no service session".
OFFLINE_MARK = 'OFFLINE service'


class _FileAdapter(BaseAdapter):
    """``file://`` for GET: the response streams the file, with the
    length declared, the way the service serves a pulse file."""

    def send(self, request, **kwargs):
        r = Response()
        r.request = request
        r.url = request.url
        path = request.url[len('file://'):]
        if request.method != 'GET':
            r.status_code, r.reason = 405, 'file adapter serves GET only'
            r.raw = io.BytesIO(b'')
            return r
        try:
            size = os.path.getsize(path)
            r.raw = open(path, 'rb')
        except OSError as e:
            r.status_code, r.reason = 404, str(e)
            r.raw = io.BytesIO(b'')
            return r
        r.status_code, r.reason = 200, 'OK'
        r.headers['content-length'] = str(size)
        return r

    def close(self):
        pass


class _SinkAdapter(BaseAdapter):
    """``http(s)://`` with no network behind it: an upload (PUT/POST) is
    accepted and kept under ``save_dir`` when one is given, anything
    else is refused. The machine's image captures complete offline; a
    fetch that would have needed the service fails the way a dead link
    does."""

    def __init__(self, save_dir=None):
        BaseAdapter.__init__(self)
        self.save_dir = save_dir
        self.uploads = 0

    def send(self, request, **kwargs):
        r = Response()
        r.request = request
        r.url = request.url
        r.raw = io.BytesIO(b'')
        if request.method in ('PUT', 'POST'):
            self.uploads += 1
            if self.save_dir:
                try:
                    os.makedirs(self.save_dir, exist_ok=True)
                    name = os.path.join(self.save_dir, 'upload-%d.bin' % self.uploads)
                    with open(name, 'wb') as f:
                        f.write(request.body or b'')
                except OSError as e:
                    logger.warning('offline upload not kept: %s' % e)
            r.status_code, r.reason = 200, 'OK (offline sink)'
            return r
        r.status_code, r.reason = 503, 'offline: no service behind this URL'
        return r

    def close(self):
        pass


def offline_session(save_dir=None) -> requests.Session:
    """A requests Session that serves ``file://`` and sinks uploads. The
    401 path of ``request()`` never triggers (nothing answers 401), so
    the session needs no credentials and performs no sign-in."""
    s = requests.Session()
    s.mount('file://', _FileAdapter())
    s.mount('http://', _SinkAdapter(save_dir))
    s.mount('https://', _SinkAdapter(save_dir))
    return s


class OfflineListener(threading.Thread):
    """The stand-in for the WebSocket client: a UNIX socket server whose
    lines go onto the receive queue and whose clients get every event the
    machine puts on the transmit queue. Same shape as WsClient where
    GFUIService looks (``ready``, ``is_alive()``, ``shutdown()``)."""

    def __init__(self, q_rx: Queue, q_tx: Queue, path: str = DEFAULT_SOCKET):
        threading.Thread.__init__(self, daemon=True, name='gfcloud-offline')
        self.q_rx = q_rx
        self.q_tx = q_tx
        self.path = path
        self.ready = False
        self.stop = False
        self.received = 0
        self.sent = 0
        self._clients = []
        self._lock = threading.Lock()
        self._sock = None

    def open(self) -> bool:
        try:
            if os.path.exists(self.path):
                os.unlink(self.path)
            d = os.path.dirname(self.path)
            if d:
                os.makedirs(d, exist_ok=True)
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._sock.bind(self.path)
            os.chmod(self.path, 0o600)
            self._sock.listen(4)
            self._sock.settimeout(0.5)
        except OSError as e:
            logger.error('%s: cannot listen on %s: %s' % (OFFLINE_MARK, self.path, e))
            return False
        self.ready = True
        logger.info('%s: no web session; listening on %s' % (OFFLINE_MARK, self.path))
        return True

    def run(self) -> None:
        pump = threading.Thread(target=self._tx_pump, daemon=True, name='gfcloud-offline-tx')
        pump.start()
        while not self.stop:
            try:
                c, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self._lock:
                self._clients.append(c)
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()
        self.ready = False

    def _serve(self, c: socket.socket) -> None:
        c.settimeout(0.5)
        buf = b''
        try:
            while not self.stop:
                try:
                    d = c.recv(65536)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not d:
                    break
                buf += d
                while b'\n' in buf:
                    line, buf = buf.split(b'\n', 1)
                    line = line.strip()
                    if not line:
                        continue
                    self.received += 1
                    self.q_rx.put(line.decode('utf-8', 'replace'))
        finally:
            with self._lock:
                if c in self._clients:
                    self._clients.remove(c)
            try:
                c.close()
            except OSError:
                pass

    def _tx_pump(self) -> None:
        while not self.stop:
            try:
                msg = self.q_tx.get(timeout=0.5)
            except Empty:
                continue
            self.sent += 1
            text = msg if isinstance(msg, str) else msg.decode('utf-8', 'replace')
            logger.info('offline event: %s' % text.strip())
            data = text if text.endswith('\n') else text + '\n'
            with self._lock:
                clients = list(self._clients)
            for c in clients:
                try:
                    c.sendall(data.encode('utf-8'))
                except OSError:
                    pass
            self.q_tx.task_done()

    def shutdown(self, timeout: float = 10) -> bool:
        self.stop = True
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass
        with self._lock:
            clients = list(self._clients)
        for c in clients:
            try:
                c.close()
            except OSError:
                pass
        try:
            os.unlink(self.path)
        except OSError:
            pass
        if self.is_alive():
            self.join(timeout)
        return not self.is_alive()


class OfflineService(GFUIService):
    """GFUIService with the service taken out: the same dispatch loop,
    the same machine, the same events, fed from a local socket."""

    def __init__(self, machine: BaseMachine, path: str = DEFAULT_SOCKET, save_dir=None):
        GFUIService.__init__(self, machine)
        self.path = path
        self.save_dir = save_dir
        self.listener: Union[OfflineListener, None] = None

    def connect(self) -> bool:
        self.session = offline_session(self.save_dir)
        listener = OfflineListener(self.q_msg_rx, self.q_msg_tx, self.path)
        if not listener.open():
            return False
        listener.start()
        self.listener = listener
        self._ws = listener
        return True


__all__ = ['OfflineService', 'OfflineListener', 'offline_session', 'DEFAULT_SOCKET',
           'OFFLINE_MARK']
