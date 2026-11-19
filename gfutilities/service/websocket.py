"""
Copyright 2020 514 LLC d/b/a OpenGlow
Written by Scott Wiederhold
https://community.openglow.org

SPDX-License-Identifier:    MIT
"""
from datetime import timedelta
import json
import logging
import os
from pathlib import Path
from queue import Empty, Queue
import random
import re
import requests
from requests import Request, Response, Session
from requests.adapters import HTTPAdapter
import urllib3
from urllib3.util.retry import Retry
from urllib.parse import urlsplit
from threading import Event, Lock, Thread
import time
from typing import Any, Union
import zlib

import websocket

from gfutilities._common import *
from gfutilities.configuration import get_cfg, set_cfg
from gfutilities.puls import decode_all_steps
from gfutilities.puls.source import PulseSource, PulseSourceError

# Event timestamps count milliseconds on the monotonic clock from the start
# of this process: the board has no real-time clock, and a wall clock that
# NTP sets after the start would move every later stamp.
_START_MONOTONIC = time.monotonic()
response_id = 31
_response_id_lock = Lock()

# Events queued while the socket is down go out after the reconnect, in the
# order they were queued; past this depth new events are dropped (with a
# warning) instead of growing the queue without bound.
TX_QUEUE_MAX = 1000

# A text frame longer than this is dropped unread: no service action comes
# near it, and every frame is held and split in memory.
FRAME_MAX_CHARS = 1024 * 1024

# The reconnect waits RECONNECT_BASE_S after a lost session, doubling after
# each failed attempt up to RECONNECT_MAX_S, each wait drawn between half
# and all of its value. A session that stayed up RECONNECT_RESET_S starts
# the count again, so the routine hourly drop reconnects at once.
RECONNECT_BASE_S = 5.0
RECONNECT_MAX_S = 300.0
RECONNECT_RESET_S = 60.0

# The WebSocket library's socket timeout, which also bounds its handshake:
# without one, a peer that accepts the connection and never completes the
# upgrade holds the client thread in a read forever.
WS_SOCKET_TIMEOUT_S = 20

# The sign-in and the firmware probe answer with a small JSON object: one
# past this many decoded bytes is not one, and neither is a body of more than
# twice this many bytes as sent (a gzip answer that decodes to little); either
# is not read further.
JSON_BODY_MAX_BYTES = 64 * 1024

# The firmware probe's one attempt: seconds for the connect and for each
# read, and a deadline counted from before the request and judged on every
# receive of the answer's body (the head is bounded by the per-read timeout
# only).
FIRMWARE_PROBE_TIMEOUT_S = 5
FIRMWARE_PROBE_DEADLINE_S = 10

# The most a body read hands back at once where the response cannot hand
# over what has arrived (no read1): small, so a stop or a deadline is judged
# within the arrival of this many bytes.
_SMALL_READ = 1024


def _body_pieces(r, piece: int):
    """The body exactly as the server sent it (never decoded), in pieces
    handed over as they arrive: a piece is whatever the connection holds, up
    to ``piece`` bytes, so a caller can judge a stop or a deadline on every
    receive. A response that cannot hand over what has arrived reads in
    pieces of _SMALL_READ; one with no raw stream at all falls back to its
    decoded content."""
    raw = getattr(r, 'raw', None)
    read1 = getattr(type(raw), 'read1', None)
    if (isinstance(raw, urllib3.HTTPResponse) and read1 is not None
            and read1 is not getattr(urllib3.response.BaseHTTPResponse, 'read1', None)):
        while True:
            data = raw.read1(piece, decode_content=False)
            if not data:
                return
            yield data
    elif raw is not None and hasattr(raw, 'stream'):
        yield from raw.stream(_SMALL_READ, decode_content=False)
    else:
        yield from r.iter_content(chunk_size=piece)


def _url_for_log(url) -> str:
    """A URL as the log shows it: the scheme, the host and the path, never
    the query. A signed storage URL carries its credential in the query."""
    return str(url).split('?', 1)[0]


def _host(url) -> str:
    """The host a URL names, lowercased, or '' when it names none."""
    try:
        return (urlsplit(str(url).replace('wss://', 'https://', 1)).hostname or '').lower()
    except ValueError:
        return ''


def _is_service_url(url) -> bool:
    """True when the URL is on the Glowforge service's own host
    (SERVICE.SERVER_URL), the one host the machine's session token is for."""
    service = _host(get_cfg('SERVICE.SERVER_URL') or '')
    return bool(service) and _host(url) == service


# The two action fields that carry a signed storage URL.
_SIGNED_URL_FIELD = re.compile(r'("(?:motion_url|endpoint)"\s*:\s*"[^"?]*)\?[^"]*"')


def _frame_for_log(message: str) -> str:
    """A service frame as the debug log shows it: a signed storage URL in
    motion_url or endpoint is cut at its query."""
    return _SIGNED_URL_FIELD.sub(r'\1"', message)


# A JSON answer is asked for in gzip, the one encoding _read_json reads,
# whatever the installed decoders would have the session ask for.
JSON_HEADERS = {'Accept-Encoding': 'gzip'}


class _GzipMembers:
    """Every member of a gzip body, inflated no further than ``room`` bytes
    a feed, read as urllib3 reads one: the first member must be whole, and
    after it what a later member decodes before it ends or fails is kept,
    the rest of the body then dropped. An error inside the first member, or
    a first member cut short, is the body's."""

    def __init__(self):
        self._d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        self._first = True
        self._dropped = False

    @property
    def complete(self) -> bool:
        return self._d.eof or not self._first

    def feed(self, data: bytes, room: int) -> bytes:
        out = bytearray()
        while data and not self._dropped and len(out) < room:
            if self._d.eof:
                self._d = zlib.decompressobj(16 + zlib.MAX_WBITS)
                self._first = False
            try:
                out += self._d.decompress(data, room - len(out))
            except zlib.error:
                if self._first:
                    raise
                self._dropped = True
                break
            data = self._d.unused_data if self._d.eof else self._d.unconsumed_tail
        return bytes(out)


def _read_json(r, cap: int = JSON_BODY_MAX_BYTES, deadline: float = None) -> Union[dict, None]:
    """The JSON object of a small response, as sent or decoded from its gzip
    Content-Encoding (every member), read no further than ``cap`` decoded
    bytes and, when ``deadline`` (on the monotonic clock) is given, no later
    than it: judged on every receive. None when the body is longer, comes
    later, is in another encoding or does not decode (one cut short), is
    more than twice ``cap`` bytes as sent, is not JSON, or is not an
    object."""
    body = bytearray()
    sent = 0
    headers = getattr(r, 'headers', None) or {}
    encoding = (headers.get('Content-Encoding') or '').strip().lower()
    try:
        if encoding not in ('', 'identity', 'gzip', 'x-gzip'):
            logger.error('response body in the %r encoding, which is not asked for; not read'
                         % encoding[:32])
            return None
        # Only the raw stream is still encoded; the fallback is decoded.
        inflate = None
        if encoding in ('gzip', 'x-gzip') and getattr(r, 'raw', None) is not None:
            inflate = _GzipMembers()
        for piece in _body_pieces(r, 8192):
            sent += len(piece)
            if sent > 2 * cap:
                logger.error('response body past %d bytes as sent; not read further' % (2 * cap))
                return None
            if inflate is not None:
                piece = inflate.feed(piece, cap + 1 - len(body))
            body += piece
            if len(body) > cap:
                logger.error('response body past %d bytes; not read further' % cap)
                return None
            if deadline is not None and _monotonic() > deadline:
                logger.error('response body not read in time; not read further')
                return None
        if inflate is not None and not inflate.complete:
            logger.error('response body ends inside its encoding')
            return None
        doc = json.loads(bytes(body))
    except (requests.RequestException, urllib3.exceptions.HTTPError, OSError,
            ValueError, RecursionError, zlib.error) as e:
        logger.error('response body not read as JSON: %s' % type(e).__name__)
        return None
    finally:
        try:
            r.close()
        except Exception:
            pass
    if not isinstance(doc, dict):
        logger.error('response body is JSON, but not an object')
        return None
    return doc

logger = logging.getLogger(LOGGER_NAME)


class WsClient(Thread):
    """
    Web Socket Client
    Establishes a persistent WSS client that runs the WebSocket event loop in a separate thread.
    Incoming text messages are spooled to the q_msg_rx Queue; messages placed into the q_msg_tx
    Queue are sent out by a dedicated transmit-pump thread.
    """
    def __init__(self, q_rx: Queue, q_tx: Queue, session=None):
        """
        Class Initializer
        :param q_rx: WSS RX Message Queue
        :type q_rx: Queue
        :param q_tx: WSS TX Message Queue
        :type q_tx: Queue
        :param session: authenticated requests Session used to refresh the
            single-use ws_token before each reconnect. None keeps a
            single-connect client (reconnects reuse the existing token).
        """
        self.msg_q_rx = q_rx
        self.msg_q_tx = q_tx
        self.stop = False
        self.ready = False
        # Set at the first completed handshake; ws_connect waits on it.
        self.opened = Event()
        # When the current session's handshake completed, or None.
        self._opened_at = None
        # When the reconnect loop last began a step (an attempt or a wait),
        # on the monotonic clock: a loop that cycles keeps it fresh, and one
        # stuck inside a step lets it age (the service loop reads it).
        self.step_at = None
        self._session = session
        self.ws = None
        # The frame the transmit pump took off the queue and has not sent:
        # a send that fails keeps it here for the next session.
        self._unsent = None
        # daemon: the client must never keep the process alive on its own.
        # Clean teardown is still explicit - see shutdown().
        Thread.__init__(self, daemon=True)

    def _build(self) -> 'websocket.WebSocketApp':
        """Build a WebSocketApp for the ws_token currently in config."""
        return websocket.WebSocketApp(
            get_cfg('SERVICE.STATUS_SERVICE_URL') + '/' + get_cfg('SESSION.WS_TOKEN'),
            header={'User-Agent': get_cfg('SESSION.USER_AGENT')},
            subprotocols=['glowforge'],
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

    def _on_open(self, _ws) -> None:
        """WS handshake complete - ready to send/receive."""
        logger.info('RX-EVENT: ready')
        self.ready = True
        self._opened_at = _monotonic()
        self.opened.set()

    def _on_message(self, _ws, message) -> None:
        """Service sent us a message. Text frames arrive as str, binary frames as bytes."""
        if isinstance(message, (bytes, bytearray)):
            logger.error('UNEXPECTED BINARY RX-EVENT (%s bytes)' % len(message))
            return
        if len(message) > FRAME_MAX_CHARS:
            logger.error('RX-EVENT of %d characters dropped unread (past %d)'
                         % (len(message), FRAME_MAX_CHARS))
            return
        logger.debug(_frame_for_log(message))
        # The service packs several newline-delimited JSON objects into a
        # single text frame; enqueue each object on its own so the consumer
        # decodes one action at a time.
        for obj in split_frame(message):
            self.msg_q_rx.put(obj)

    def _on_error(self, _ws, error) -> None:
        logger.error('RX-EVENT: error: %s' % error)

    def _on_close(self, _ws, status_code, msg) -> None:
        self.ready = False
        logger.info('RX-EVENT: closed (%s, %s)' % (status_code, msg))

    def run(self) -> None:
        """
        Thread loop.
        Starts the transmit pump, then connects and reconnects until stop is
        requested. The ws_token is single-use and short-lived (~30s), so with
        a session each reconnect re-runs sign_in for a fresh token (and
        auth_token) and rebuilds the URL; without one, reconnects reuse the
        existing token.
        :return:
        """
        Thread(target=self._tx_pump, daemon=True).start()
        first = True
        delay = RECONNECT_BASE_S
        while not self.stop:
            # A transient failure anywhere in a session attempt (DNS blip
            # during re-auth, a raise out of run_forever) must not kill
            # this thread - the machine would silently go offline forever.
            # Log, back off, reconnect.
            self._opened_at = None
            self.step_at = _monotonic()
            try:
                if not first and self._session is not None:
                    # Lazy import: authentication imports this module.
                    from gfutilities.service.authentication import authenticate_machine
                    logger.info('re-authenticating for a fresh ws_token')
                    if not authenticate_machine(self._session):
                        logger.error('re-auth failed; will retry')
                        self.step_at = _monotonic()
                        if self._sleep_or_stop(_jitter(delay)):
                            break
                        delay = min(delay * 2, RECONNECT_MAX_S)
                        continue
                first = False
                self.ready = False
                websocket.setdefaulttimeout(WS_SOCKET_TIMEOUT_S)
                self.ws = self._build()
                # ping_interval keeps the connection alive (standard WS ping/pong).
                self.ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception:
                logger.exception('WS session attempt failed; will reconnect')
            if self.stop:
                break
            if self._opened_at is not None and _monotonic() - self._opened_at >= RECONNECT_RESET_S:
                delay = RECONNECT_BASE_S
            logger.info('RECONNECTING')
            self.step_at = _monotonic()
            if self._sleep_or_stop(_jitter(delay)):
                break
            delay = min(delay * 2, RECONNECT_MAX_S)
        logger.info('CLOSING')

    def shutdown(self, timeout: float = 10) -> bool:
        """
        Stop the client and wait for its thread to exit: request the
        receive/transmit loops to stop, close any open socket so
        run_forever() returns immediately, and join the thread.
        :param timeout: max seconds to wait for the thread
        :type timeout: float
        :return: True when the thread is down
        :rtype: bool
        """
        self.stop = True
        if self.ws:
            try:
                self.ws.close()
            except Exception as e:
                logger.debug('socket close during shutdown: %s' % e)
        if self.is_alive():
            self.join(timeout)
        return not self.is_alive()

    def _sleep_or_stop(self, secs: float) -> bool:
        """Sleep up to secs seconds, returning True as soon as stop is set."""
        for _ in range(int(secs * 10)):
            if self.stop:
                return True
            time.sleep(0.1)
        return self.stop

    def _tx_pump(self) -> None:
        """
        Sends the transmit queue's frames, in order, while the socket is
        open. A frame whose send fails is kept and sent first once the
        reconnect brings a new socket. Runs until stop is requested, then
        closes the socket so run_forever() returns.
        :return:
        """
        while not self.stop:
            if self._unsent is None:
                try:
                    self._unsent = self.msg_q_tx.get(timeout=0.5)
                except Empty:
                    continue
                self.msg_q_tx.task_done()
            if not self.ready:
                time.sleep(0.1)
                continue
            logger.info('TX-EVENT: ' + self._unsent.strip())
            try:
                self.ws.send(self._unsent)
                self._unsent = None
            except Exception as e:
                # A socket-level error (the peer reset before on_close
                # fired) escapes send as an OSError, not a
                # WebSocketException. Nothing may end this thread: it
                # is the only sender for the life of the process. The
                # socket is done; the reconnect brings a new one.
                logger.error('TX FAILED: %s' % e)
                self.ready = False
        if self.ws:
            self.ws.close()


def _jitter(secs: float) -> float:
    """A wait drawn between half of ``secs`` and all of it."""
    return random.uniform(secs / 2, secs)


_monotonic = time.monotonic


def split_frame(message: str) -> list:
    """
    Split a WebSocket text frame into its individual JSON objects.

    The service may pack several newline-delimited JSON objects into one
    frame; return each non-blank line so they can be decoded separately.
    Only a newline separates them: U+2028 and its kin may stand raw inside
    a JSON string.
    :param message: raw text frame
    :type message: str
    :return: list of JSON object strings (order preserved)
    :rtype: list
    """
    return [line.strip() for line in message.split('\n') if line.strip()]


# A factory firmware version as the banner shows it.
_FACTORY_VERSION = re.compile(r'[0-9A-Za-z._+-]{1,31}')


def record_factory_latest(version: str) -> None:
    """
    Record the latest factory firmware version the service advertises, for
    the forgectrl compatibility banner. ForgeFIRM never installs factory
    firmware; this only informs the operator whether the live Glowforge
    service has moved past the version this release was tested against.
    No-op unless FACTORY_FIRMWARE.STATUS_FILE is configured.

    Only a string of 1 to 31 characters from [0-9A-Za-z._+-] is recorded;
    anything else is logged and the file is left as it is. The file is
    written whole to a temporary file and renamed over the old one, so a
    reader sees the old record or the new one.
    :param version: version string reported by the service
    :type version: str
    """
    path = get_cfg('FACTORY_FIRMWARE.STATUS_FILE')
    if not path:
        return
    if not isinstance(version, str) or not _FACTORY_VERSION.fullmatch(version):
        logger.warning('service version not recorded: %.40r' % (version,))
        return
    tmp = '%s.tmp' % path
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, 'w') as f:
            json.dump({
                'latest_gf_version': version,
                'tested_against_gf': get_cfg('FACTORY_FIRMWARE.FW_VERSION'),
            }, f)
        os.replace(tmp, path)
    except OSError as e:
        logger.warning('could not record factory-latest version: %s' % e)


def firmware_check(s: Session) -> Union[dict, bool, None]:
    """
    Query the factory firmware version the service currently advertises and
    record it for the forgectrl compatibility banner. ForgeFIRM never
    downloads or installs factory firmware - this is a read-only version
    probe, not an update step.

    One attempt through the session's own transport, with no retry and a
    FIRMWARE_PROBE_TIMEOUT_S timeout on the connect and on each read, and a
    FIRMWARE_PROBE_DEADLINE_S deadline counted from before the request and
    judged on every receive of the answer's body, so a slow service holds the
    caller (the action dispatch) for seconds, not minutes.

    Returns a {dict} when the advertised version differs from the version
    this release was tested against (FACTORY_FIRMWARE.FW_VERSION), meaning
    the live Glowforge service has moved past our tested baseline:
    {'version': '<latest factory version>', 'download_url': '<url>'}
    :param s: Requests Session object
    :type s: Session
    :return: version dict when newer than our baseline, False when it
        matches, None when the probe failed (no answer, or an answer that is
        not a JSON object)
    :rtype: Union[dict, bool, None]
    """
    logger.info('CHECKING')
    deadline = _monotonic() + FIRMWARE_PROBE_DEADLINE_S
    r = request(s, get_cfg('SERVICE.SERVER_URL') + '/update/current', 'GET',
                timeout=FIRMWARE_PROBE_TIMEOUT_S, stream=True, retry=False,
                headers=JSON_HEADERS)
    if not r:
        logger.error('FAILED')
        return None
    rj = _read_json(r, deadline=deadline)
    if rj is None:
        logger.error('FAILED')
        return None
    logger.debug(rj)
    if rj.get('version') is not None:
        record_factory_latest(rj['version'])
    if rj.get('version') is not None and not rj['version'] == get_cfg('FACTORY_FIRMWARE.FW_VERSION'):
        logger.info('Glowforge service advertises firmware %s; this release was '
                    'tested against %s (cloud mode may be affected)'
                    % (rj['version'], get_cfg('FACTORY_FIRMWARE.FW_VERSION')))
        return rj
    else:
        logger.info('SERVICE FIRMWARE MATCHES TESTED BASELINE.')
        return False


DEFAULT_USER_AGENT_PRODUCT = 'OpenGlow'


def user_agent() -> str:
    """
    The User-Agent the web service and the WebSocket see.
    SERVICE.USER_AGENT from the configuration when set; otherwise the
    library default, OpenGlow/<FACTORY_FIRMWARE.FW_VERSION>.
    :return: User-Agent header value
    :rtype: str
    """
    configured = get_cfg('SERVICE.USER_AGENT')
    if configured:
        return str(configured)
    return '%s/%s' % (DEFAULT_USER_AGENT_PRODUCT, get_cfg('FACTORY_FIRMWARE.FW_VERSION'))


def get_session() -> Session:
    """
    Creates web api session object.
    :return: Requests Session object
    :rtype: Session
    """
    s = Session()
    # One adapter for the session's life. GET retries are short: a
    # pulse download on the action thread must give up in seconds, not
    # minutes, so a cancel can reach the job it belongs to. A Retry-After
    # the service sends is not honored: one of minutes would hold the job.
    s.mount("https://", HTTPAdapter(max_retries=Retry(
        total=4, backoff_factor=1, backoff_max=8, allowed_methods=["GET"],
        respect_retry_after_header=False)))
    set_cfg('SESSION.USER_AGENT', user_agent())
    s.headers.update({'user-agent': get_cfg('SESSION.USER_AGENT')})
    logger.debug('Returning object : %s' % s)
    return s


class ImageUploadError(Exception):
    """An image the storage or the service did not take."""


def img_upload(s: Session, img: bytes, msg: dict) -> bool:
    """
    Uploads head/lid image. Returns True; an image that is not uploaded
    raises (ImageUploadError, or the transport's own exception), so the
    action that asked for it ends ':failed', never ':completed'.
    :param s: Requests Session object
    :type s: Session
    :param img: Path to file to upload, or bytes object containing image
    :type img: bytes
    :param msg: WSS message
    :type msg: dict
    :return: True
    :rtype: bool
    """
    logger.info('START')
    endpoint = msg.get('endpoint')
    if endpoint:
        # v2.6.0+ protocol: the service supplies a presigned storage URL in the
        # action's "endpoint" field and the image is uploaded straight to it.
        # The presigned URL carries its own auth, so it must NOT go through the
        # authenticated Glowforge session (a Bearer header makes GCS reject it);
        # PUT the raw bytes with a plain request. Only over https, and no
        # redirect is followed (a presigned PUT does not redirect): the image
        # is the operator's bed.
        if urlsplit(str(endpoint)).scheme != 'https':
            raise ImageUploadError('the upload endpoint is not https (%s)' % _url_for_log(endpoint))
        r = requests.put(endpoint, data=img, headers={'Content-Type': 'image/jpeg'}, timeout=30,
                         allow_redirects=False)
        ok = r.status_code in (200, 201, 204)
    else:
        # Legacy fallback: POST to the app server (pre-2.6.0 behavior).
        url = get_cfg('SERVICE.SERVER_URL') + '/api/machines/%s/%s' % (msg['action_type'], msg['id'])
        r = request(s, url, 'POST', data=img, headers={'Content-Type': 'image/jpeg'})
        ok = bool(r)
    if not ok:
        logger.error('FAILED: %s %s' % (getattr(r, 'status_code', '?'), getattr(r, 'reason', '')))
        raise ImageUploadError('the image upload failed (%s)' % getattr(r, 'status_code', 'no answer'))
    logger.debug('upload response: %s' % r)
    logger.info('COMPLETE')
    return True


def check_puls_header(header: dict, serial: Any = None) -> Union[str, None]:
    """
    Decide whether a pulse file may run on this machine.

    Everything here is checked before a single byte reaches the kernel ring,
    the way the factory checks it: a header that fails is a job that never
    loads, not a job aborted halfway.

    :param header: parsed pulse header, 4-character tags to integers
    :param serial: this machine's serial number
    :return: None when the header is acceptable, else the reason to refuse
    :rtype: Union[str, None]
    """
    stfr = header.get('STfr')
    if not isinstance(stfr, int) or stfr <= 0:
        return 'no usable STfr (%r) to clock the step stream' % (stfr,)

    # A job carries the serial of the machine it was cut for. Zero means the
    # service did not lock it, which the factory accepts and this machine has
    # only ever been sent; a nonzero value that is not ours is somebody else's
    # job and their material. No serial is logged either way.
    locked = header.get('MCsn')
    if locked is None:
        return 'header carries no MCsn, so the job is not addressed to a machine'
    if locked:
        try:
            mine = int(serial)
        except (TypeError, ValueError):
            return 'header is serial-locked but this machine has no usable serial'
        if mine != locked:
            return 'header is serial-locked to a different machine'

    # PDfm names the pulse-data format. Format 0 is the one the step decoder
    # and the kernel ring assume, and the only one ever observed. What the
    # factory would do with another value is not known from the firmware, so
    # refusing beats misreading a byte stream that drives a laser.
    fmt = header.get('PDfm')
    if fmt is None:
        return 'header carries no PDfm, so the pulse data format is undeclared'
    if fmt != 0:
        return 'unsupported pulse data format PDfm=%r' % (fmt,)

    # XSmm and YSmm name the microstep mode the service planned the stream
    # for, and the mode this client sets the drivers to for the job. Every
    # header the service has sent carries 8, the factory's own analog
    # config, and 8 is the only mode a cloud job has been run at here; the
    # service plans the stream at its scale whatever the machine reports.
    # A header at any other mode is a stream this machine has never run,
    # so it is refused before a byte reaches the ring, and the log line is
    # the report that starts the work on finer modes in cloud mode. A
    # header without the tags runs at 8, the mode the client sets at start.
    for tag in ('XSmm', 'YSmm'):
        mode = header.get(tag)
        if mode is not None and mode != 8:
            return 'unsupported microstep mode %s=%r: cloud jobs run at 8 only' % (tag, mode)

    return None


# Bytes taken off the socket, and written out by load_motion, at a time.
_DOWNLOAD_CHUNK = 64 * 1024
_WRITE_CHUNK = 256 * 1024

# How much pulse body this machine will hold in memory. These bound PROCESS
# MEMORY and nothing else: the body is the job as the service compressed it,
# and the ring is fed from it as it drains, so job length is not what they
# cap. The service compresses the step stream tens to one, so a couple of MB
# is an hours-long print; the warning level is past anything that has been
# seen, and the refusal level is past anything a machine could cut in days.
# A caller passes 0 to lift either one.
PULSE_WARN_BYTES = 32 * 1024 * 1024
PULSE_REJECT_BYTES = 128 * 1024 * 1024


def _body_stream(r, chunk: int = _DOWNLOAD_CHUNK):
    """The body exactly as the service sent it, in pieces as they arrive.

    The service serves pulse data compressed, tens to one, so keeping it
    undecoded is what lets a job of any length sit in a few MB of memory.
    A response that cannot hand over its raw stream falls back to the decoded
    one, which costs only memory.
    """
    return _body_pieces(r, chunk)


def _declared_length(r) -> int:
    """The body size the service declares, or 0 when it declares none."""
    try:
        return max(0, int(r.headers.get('content-length', 0)))
    except (AttributeError, TypeError, ValueError):
        return 0


# A download must keep moving: past DOWNLOAD_GRACE_S it must have averaged
# at least DOWNLOAD_MIN_RATE bytes per second, or it is given up. A rate,
# not a fixed cap: a long job on a slow link is still taken.
DOWNLOAD_GRACE_S = 30.0
DOWNLOAD_MIN_RATE = 16 * 1024

# The response head: up to DOWNLOAD_ATTEMPTS attempts after a transport
# failure, each with DOWNLOAD_TIMEOUT_S on the connect and DOWNLOAD_READ_S
# on every read (the body's reads too: a pause in a body is taken up to that
# long), a wait of 1, 2, 4 and 8 s between them. No attempt starts or waits
# past DOWNLOAD_HEAD_S; an attempt under way is bounded by its own timeouts.
# A stop is read before every attempt and through every wait.
DOWNLOAD_ATTEMPTS = 5
DOWNLOAD_TIMEOUT_S = 5.0
DOWNLOAD_READ_S = 15.0
DOWNLOAD_HEAD_S = 60.0

_sleep = time.sleep


def _wait_or_stop(secs: float, should_stop) -> bool:
    """Wait ``secs`` on the monotonic clock, asking ``should_stop`` every
    0.1 s. True as soon as it answers true."""
    end = _monotonic() + secs
    while True:
        if should_stop is not None and should_stop():
            return True
        left = end - _monotonic()
        if left <= 0:
            return False
        _sleep(min(0.1, left))


def fetch_motion(s: Session, url: str, warn_bytes: int = PULSE_WARN_BYTES,
                 reject_bytes: int = PULSE_REJECT_BYTES, should_stop=None) -> tuple:
    """Downloads a motion/print job into memory and parses its header.

    Returns ``(info, source)``, or ``(False, None)`` if the job is not one
    this machine will run. Nothing is written to the machine here: the ring is
    fed from the returned source separately, so a job may be longer than the
    ring.

    The URL must be https (or file, which only a session that serves files,
    the offline one, can fetch). The session's Authorization goes only to the
    service's own host, never to the storage host a signed URL names, and a
    redirect is not followed: a signed storage URL does not redirect.

    ``warn_bytes`` and ``reject_bytes`` bound the body held in memory, not the
    job (see PULSE_WARN_BYTES). The declared length is checked before a byte is
    taken, and the running total is checked as it arrives, because a service
    that declares nothing - or declares wrongly - would otherwise be trusted
    with all the memory there is. Either may be 0 to lift it.

    The response head gets DOWNLOAD_ATTEMPTS attempts, none started past
    DOWNLOAD_HEAD_S (see there); an answer that is not 200 is not tried
    again. The body is read as it arrives, each read bounded by
    DOWNLOAD_READ_S.

    ``should_stop``, when given, is asked before every attempt, through the
    waits between them and on every receive of the body: once it answers
    true the download ends with ``(False, None)``. So does a download slower
    than DOWNLOAD_MIN_RATE past DOWNLOAD_GRACE_S, judged on every receive,
    and a transport error in the body.

    :param s: Requests Session object
    :param url: Target URL
    :param warn_bytes: log a body at or past this size
    :param reject_bytes: refuse a body past this size
    :param should_stop: callable read per piece; true ends the download
    """
    scheme = urlsplit(str(url)).scheme
    if scheme not in ('https', 'file'):
        logger.error('refusing the job: the pulse data URL is %s, not https (%s)'
                     % (scheme or 'unnamed', _host(url)))
        return False, None
    headers = {} if _is_service_url(url) else {'Authorization': None}
    head_deadline = _monotonic() + DOWNLOAD_HEAD_S
    r = False
    for attempt in range(DOWNLOAD_ATTEMPTS):
        if should_stop is not None and should_stop():
            logger.info('pulse data download stopped before an answer')
            return False, None
        failure = []
        r = request(s, url, 'GET', timeout=(DOWNLOAD_TIMEOUT_S, DOWNLOAD_READ_S), stream=True,
                    allow_redirects=False, headers=headers, retry=False, _failure=failure)
        if r or failure != ['transport'] or attempt + 1 == DOWNLOAD_ATTEMPTS:
            break
        wait = min(2.0 ** attempt, head_deadline - _monotonic())
        if wait <= 0:
            break
        if _wait_or_stop(wait, should_stop):
            logger.info('pulse data download stopped before an answer')
            return False, None
    if not r:
        logger.error('pulse data download failed')
        return False, None
    declared = _declared_length(r)
    if reject_bytes and declared > reject_bytes:
        logger.error('refusing the job: the service declares %d bytes of pulse data, '
                     'past the %d this machine will hold' % (declared, reject_bytes))
        r.close()
        return False, None
    body = bytearray()
    started = _monotonic()
    try:
        for piece in _body_stream(r):
            body += piece
            if reject_bytes and len(body) > reject_bytes:
                logger.error('refusing the job: the download passed the %d bytes this '
                             'machine will hold (%d declared)' % (reject_bytes, declared))
                r.close()
                return False, None
            if should_stop is not None and should_stop():
                logger.info('pulse data download stopped after %d bytes' % len(body))
                r.close()
                return False, None
            took = _monotonic() - started
            if took > DOWNLOAD_GRACE_S + len(body) / DOWNLOAD_MIN_RATE:
                logger.error('refusing the job: %d bytes in %.0f s is under the %d bytes/s '
                             'a download must keep' % (len(body), took, DOWNLOAD_MIN_RATE))
                r.close()
                return False, None
    except (requests.RequestException, urllib3.exceptions.HTTPError, OSError) as e:
        logger.error('pulse data download failed after %d bytes: %s'
                     % (len(body), type(e).__name__))
        r.close()
        return False, None
    if warn_bytes and len(body) >= warn_bytes:
        logger.warning('this job is %d bytes of pulse data, past the %d that is '
                       'the usual ceiling; the machine will run it' % (len(body), warn_bytes))
    try:
        source = PulseSource(body)
    except PulseSourceError as e:
        logger.error('%s' % e)
        return False, None
    # Validate everything the run-time math depends on BEFORE the first byte
    # reaches the ring: a refusal past that point leaves a job loaded with
    # nobody to run it.
    reason = check_puls_header(source.header, get_cfg('MACHINE.SERIAL'))
    if reason is not None:
        logger.error('refusing the job: %s' % reason)
        return False, None
    logger.info('pulse data is %s, %d byte body' %
                ('gzip-compressed' if source.compressed else 'uncompressed',
                 source.body_size))
    info = {
        'header_data': source.header,
        'header_len': source.header_len,
        'size': 0,
        'run_time': None,
        'stats': None,
    }
    return info, source


def motion_run_time(info: dict, size: int) -> timedelta:
    """Playing time of ``size`` payload bytes at the job's step frequency."""
    return timedelta(seconds=size / info['header_data']['STfr'])


def load_motion(s: Session, url: str, out_file) -> Union[dict, bool]:
    """
    Downloads a motion/print file and writes its payload out.

    The payload (the pulse data after the header, inflated when the service
    compressed it) is written to out_file in full before this returns.

    :param s: Requests Session object
    :type s: Session
    :param url: Target URL
    :type url: str
    :param out_file: Where to write the payload: a path, or a writable
        binary object that the caller owns. Its write must take every
        byte or raise, as a file opened 'wb' and a BytesIO do
    :type out_file: str or a writable binary object
    :return: Header dict or False on failure
    :rtype: Union[dict, bool]
    """
    info, source = fetch_motion(s, url)
    if not info:
        return False

    # out_file is a path, opened and closed here, or an already-open writable
    # object, which the caller owns and which is not closed here.
    from contextlib import nullcontext
    if hasattr(out_file, 'write'):
        out_ctx = nullcontext(out_file)
    else:
        out_ctx = open(out_file, 'wb')

    size = 0
    stat = None
    with out_ctx as f:
        try:
            while True:
                chunk = source.read(_WRITE_CHUNK)
                if not chunk:
                    break
                stat = decode_all_steps(chunk, stat)
                f.write(chunk)
                size += len(chunk)
        except OSError as e:
            # A write error ends the download: log how far it got, then
            # raise it to the caller, whose destination it is.
            logger.error('pulse write failed after %d bytes: %s' % (size, e))
            raise

    info['size'] = size
    info['run_time'] = motion_run_time(info, size)
    info['stats'] = stat
    return info


# One attempt, no retry: what a session's retrying HTTP adapter is swapped
# for when a caller asks for one attempt.
_NO_RETRY = HTTPAdapter(max_retries=0)


def request(s: Session, url: str, method: str, timeout: int = 15, stream: bool = False,
            _retry_auth: bool = True, allow_redirects: bool = True, retry: bool = True,
            _failure: list = None, **kwargs) -> Union[Response, bool]:
    """
    Submits requests to provided url using the established Session object
    :param s: Requests Session object
    :type s: Session
    :param url: Target URL
    :type url: str
    :param method: Method for the request
    :type method: str
    :param timeout: Timeout
    :type timeout: int
    :param stream: Stream
    :type stream: bool
    :param _retry_auth: on a 401 from the service's own host, re-sign-in and
        replay the request once. The sign-in request itself passes False to
        avoid recursion. A 401 from another host is that host's answer.
    :type _retry_auth: bool
    :param allow_redirects: follow a redirect (False: a 3xx is a failure)
    :type allow_redirects: bool
    :param retry: False sends one attempt through the adapter the session
        mounts for the URL, a retrying HTTP adapter swapped for one that does
        not retry; no redirect is followed then
    :type retry: bool
    :param _failure: a list that gets 'transport' when no answer came, or the
        status code of an answer that is not 200
    :type _failure: list
    :param kwargs:
    :return: Requests Response object, or False on error.
    :rtype: Union[Response, bool]
    """
    req = Request(method, str.replace(str(url), 'wss://', 'https://'), **kwargs)
    prepared = s.prepare_request(req)
    try:
        if retry:
            r = s.send(prepared, stream=stream, timeout=timeout,
                       allow_redirects=allow_redirects)
        else:
            adapter = s.get_adapter(prepared.url)
            if isinstance(adapter, HTTPAdapter) and adapter.max_retries.total:
                adapter = _NO_RETRY
            r = adapter.send(prepared, stream=stream, timeout=timeout,
                             verify=s.verify, proxies=s.proxies, cert=s.cert)
    except requests.RequestException as e:
        # Network trouble is a routine condition (DNS blip, uplink drop,
        # timeout), not an exception to unwind a thread on: every caller
        # already handles a False return as failure. The exception's text
        # carries the URL's query, so only its type and the host are logged.
        logger.error('request failed: %s (%s)' % (type(e).__name__, _host(url)))
        if _failure is not None:
            _failure.append('transport')
        return False
    if r.status_code == 401 and _retry_auth and _is_service_url(url):
        # The auth_token expired mid-session: re-sign-in (fresh Bearer) and
        # replay the request once. Lazy import - authentication imports us.
        logger.info('401 - re-authenticating and retrying')
        from gfutilities.service.authentication import authenticate_machine
        if authenticate_machine(s):
            return request(s, url, method, timeout=timeout, stream=stream,
                           _retry_auth=False, allow_redirects=allow_redirects,
                           retry=retry, _failure=_failure, **kwargs)
    if r.status_code != 200:
        logger.error('FAILED: %s %s (%s)' % (r.status_code, r.reason, _host(url)))
        if _failure is not None:
            _failure.append(r.status_code)
        return False
    return r


def _timestamp() -> int:
    """An event's timestamp: milliseconds since this process started, on
    the monotonic clock."""
    return int((time.monotonic() - _START_MONOTONIC) * 1000)


def _frame(fields: dict) -> str:
    """The JSON text of an outbound frame's fields, in their order."""
    return json.dumps(fields, separators=(',', ':'))


def send_wss_event(msg_q_tx: Queue, action_id: Union[int, None], event: str, **kwargs):
    """Queue one event frame. ``data``, when given, is ``{'key': name,
    'value': <JSON text>}``: the value is placed in the frame as the JSON
    text it is."""
    global response_id
    with _response_id_lock:      # called from several threads
        response_id += 1
        rid = response_id

    if msg_q_tx.qsize() >= TX_QUEUE_MAX:
        logger.warning('event TX queue full (socket down?); dropping %s' % event)
        return

    fields = {'id': rid, 'timestamp': _timestamp(), 'type': 'event', 'version': 1}
    if action_id is not None:
        fields['action_id'] = action_id
    fields['level'] = 'INFO'
    fields['event'] = event
    frame = _frame(fields)
    if 'data' in kwargs:
        frame = '%s,%s:%s}' % (frame[:-1], json.dumps(str(kwargs['data']['key'])),
                               kwargs['data']['value'])
    msg_q_tx.put(frame + '\n')


def send_wss_progress(msg_q_tx: Queue, action_id: Union[int, None], progress: str,
                      current: int, units: str = 'steps', total: int = None,
                      values: dict = None) -> None:
    """Report how far along a job is, on the carrier the app reads.

    The progress bar rides an outbound ``type:"progress"`` frame, and that
    frame is also the periodic settings report: whatever machine values ride
    along go in its ``settings.values`` block. The factory sends one at every
    phase transition and one every 30 s in between, ``current`` in bytes for
    a transfer and in playback ticks for a run.

    A frame that cannot be sent is dropped rather than queued: progress is
    perishable, and the next one is thirty seconds behind it.
    """
    global response_id
    if msg_q_tx.qsize() >= TX_QUEUE_MAX:
        logger.warning('event TX queue full (socket down?); dropping %s' % progress)
        return
    with _response_id_lock:      # called from several threads
        response_id += 1
        rid = response_id

    fields = {'id': rid, 'timestamp': _timestamp(), 'type': 'progress', 'version': 1}
    if action_id is not None:
        fields['action_id'] = action_id
    fields['progress'] = progress
    fields['current'] = int(current)
    fields['units'] = units
    if total is not None:
        fields['total'] = int(total)
    if values:
        fields['settings'] = {'values': dict(sorted(values.items()))}
    msg_q_tx.put(_frame(fields) + '\n')


def update_header(s: Session, header: str, value: str) -> bool:
    """
    Adds persistent header to Session
    :param s: Requests Session object
    :type s: Session
    :param header: Header Name
    :type header: str
    :param value: Header Value
    :type value: str
    :return: True
    :rtype: bool
    """
    s.headers.update({header: value})
    return True


def ws_connect(msg_q_rx: Queue, msg_q_tx: Queue, session: Session = None) -> Union['WsClient', bool]:
    """
    Establishes Web Socket Session.
    Returns the running client so the caller can stop it cleanly
    (WsClient.shutdown) when the session ends.
    :param msg_q_rx: WSS RX essage Queue
    :type msg_q_rx: Queue
    :param msg_q_tx: WSS TX Message Queue
    :type msg_q_tx: Queue
    :param session: authenticated Session used to refresh the single-use
        ws_token on reconnect (see WsClient); None keeps a single-connect client
    :type session: Session
    :return: the connected WsClient, or False on failure
    :rtype: Union[WsClient, bool]
    """
    logger.info('CONNECTING')
    ws = WsClient(msg_q_rx, msg_q_tx, session)
    ws.start()
    # Wait up to 16 seconds for the handshake, or error out.
    if ws.opened.wait(16):
        logger.info('ESTABLISHED')
        return ws
    else:
        logger.error('FAILED')
        ws.stop = True
        return False
