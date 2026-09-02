"""
(C) Copyright 2020
Scott Wiederhold, s.e.wiederhold@gmail.com
https://community.openglow.org

SPDX-License-Identifier:    MIT
"""
from datetime import timedelta
import json
import logging
from pathlib import Path
from queue import Queue
import requests
from requests import Request, Response, Session
from threading import Lock, Thread
import time
from typing import Any, Union

import websocket

from gfutilities._common import *
from gfutilities.configuration import get_cfg, set_cfg
from gfutilities.puls import decode_all_steps
from gfutilities.puls.source import PulseSource, PulseSourceError

start_time = time.time()
response_id = 31
_response_id_lock = Lock()

# Events queued while the socket is down are stale on reconnect; past this
# depth new events are dropped (with a warning) instead of growing the
# queue without bound.
TX_QUEUE_MAX = 1000

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
        self._session = session
        self.ws = None
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

    def _on_message(self, _ws, message) -> None:
        """Service sent us a message. Text frames arrive as str, binary frames as bytes."""
        if isinstance(message, (bytes, bytearray)):
            logger.error('UNEXPECTED BINARY RX-EVENT (%s bytes)' % len(message))
            return
        logger.debug(message)
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
        while not self.stop:
            # A transient failure anywhere in a session attempt (DNS blip
            # during re-auth, a raise out of run_forever) must not kill
            # this thread - the machine would silently go offline forever.
            # Log, back off, reconnect.
            try:
                if not first and self._session is not None:
                    # Lazy import: authentication imports this module.
                    from gfutilities.service.authentication import authenticate_machine
                    logger.info('re-authenticating for a fresh ws_token')
                    if not authenticate_machine(self._session):
                        logger.error('re-auth failed; will retry')
                        if self._sleep_or_stop(5):
                            break
                        continue
                first = False
                self.ready = False
                self.ws = self._build()
                # ping_interval keeps the connection alive (standard WS ping/pong).
                self.ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception:
                logger.exception('WS session attempt failed; will reconnect')
            if self.stop:
                break
            logger.info('RECONNECTING')
            if self._sleep_or_stop(5):
                break
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
        Drains the transmit queue and sends messages while the socket is open.
        Runs until stop is requested, then closes the socket so run_forever() returns.
        :return:
        """
        while not self.stop:
            if self.ready and not self.msg_q_tx.empty():
                send_msg = self.msg_q_tx.get()
                logger.info('TX-EVENT: ' + send_msg.strip())
                try:
                    self.ws.send(send_msg)
                except Exception as e:
                    # A socket-level error (the peer reset before on_close
                    # fired) escapes send as an OSError, not a
                    # WebSocketException. Nothing may end this thread: it
                    # is the only sender for the life of the process. The
                    # socket is done; the reconnect brings a new one.
                    logger.error('TX FAILED: %s' % e)
                    self.ready = False
                self.msg_q_tx.task_done()
            else:
                time.sleep(0.1)
        if self.ws:
            self.ws.close()


def split_frame(message: str) -> list:
    """
    Split a WebSocket text frame into its individual JSON objects.

    The service may pack several newline-delimited JSON objects into one
    frame; return each non-blank line so they can be decoded separately.
    :param message: raw text frame
    :type message: str
    :return: list of JSON object strings (order preserved)
    :rtype: list
    """
    return [line.strip() for line in message.splitlines() if line.strip()]


def record_factory_latest(version: str) -> None:
    """
    Record the latest factory firmware version the service advertises, for
    the forgectrl compatibility banner. ForgeFIRM never installs factory
    firmware; this only informs the operator whether the live Glowforge
    service has moved past the version this release was tested against.
    No-op unless FACTORY_FIRMWARE.STATUS_FILE is configured.
    :param version: version string reported by the service
    :type version: str
    """
    path = get_cfg('FACTORY_FIRMWARE.STATUS_FILE')
    if not path:
        return
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w') as f:
            json.dump({
                'latest_gf_version': version,
                'tested_against_gf': get_cfg('FACTORY_FIRMWARE.FW_VERSION'),
                'checked_at': int(time.time()),
            }, f)
    except OSError as e:
        logger.warning('could not record factory-latest version: %s' % e)


def _byte_to_int(data: bytes) -> int:
    """
    Returns integer value of big endian byte data
    :param data:
    :return: Integer value
    :rtype: int
    """
    return ord(data[0:1]) + (ord(data[1:2]) * 0x100) + (ord(data[2:3]) * 0x10000) + (ord(data[3:4]) * 0x1000000)


def firmware_check(s: Session) -> Union[dict, bool]:
    """
    Query the factory firmware version the service currently advertises and
    record it for the forgectrl compatibility banner. ForgeFIRM never
    downloads or installs factory firmware - this is a read-only version
    probe, not an update step.

    Returns a {dict} when the advertised version differs from the version
    this release was tested against (FACTORY_FIRMWARE.FW_VERSION), meaning
    the live Glowforge service has moved past our tested baseline:
    {'version': '<latest factory version>', 'download_url': '<url>'}
    :param s: Requests Session object
    :type s: Session
    :return: version dict when newer than our baseline, else False
    :rtype: Union[dict, bool]
    """
    logger.info('CHECKING')
    r = request(s, get_cfg('SERVICE.SERVER_URL') + '/update/current', 'GET')
    if not r:
        logger.error('FAILED')
        return False
    rj = r.json()
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


def get_session() -> Session:
    """
    Creates web api session object.
    :return: Requests Session object
    :rtype: Session
    """
    s = Session()
    set_cfg('SESSION.USER_AGENT', 'OpenGlow/%s' % get_cfg('FACTORY_FIRMWARE.FW_VERSION'))
    s.headers.update({'user-agent': get_cfg('SESSION.USER_AGENT')})
    logger.debug('Returning object : %s' % s)
    return s


def img_upload(s: Session, img: bytes, msg: dict) -> bool:
    """
    Uploads head/lid image
    :param s: Requests Session object
    :type s: Session
    :param img: Path to file to upload, or bytes object containing image
    :type img: bytes
    :param msg: WSS message
    :type msg: dict
    :return:
    :rtype: bool
    """
    logger.info('START')
    endpoint = msg.get('endpoint')
    if endpoint:
        # v2.6.0+ protocol: the service supplies a presigned storage URL in the
        # action's "endpoint" field and the image is uploaded straight to it.
        # The presigned URL carries its own auth, so it must NOT go through the
        # authenticated Glowforge session (a Bearer header makes GCS reject it);
        # PUT the raw bytes with a plain request.
        r = requests.put(endpoint, data=img, headers={'Content-Type': 'image/jpeg'}, timeout=30)
        ok = r.status_code in (200, 201, 204)
    else:
        # Legacy fallback: POST to the app server (pre-2.6.0 behavior).
        url = get_cfg('SERVICE.SERVER_URL') + '/api/machines/%s/%s' % (msg['action_type'], msg['id'])
        r = request(s, url, 'POST', data=img, headers={'Content-Type': 'image/jpeg'})
        ok = bool(r)
    if not ok:
        logger.error('FAILED: %s %s' % (getattr(r, 'status_code', '?'), getattr(r, 'reason', '')))
        return False
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

    return None


# Bytes taken off the socket, and handed to the ring, at a time.
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
    """The body exactly as the service sent it, in pieces.

    The service serves pulse data compressed, tens to one, so keeping it
    undecoded is what lets a job of any length sit in a few MB of memory.
    A response that cannot hand over its raw stream falls back to the decoded
    one, which costs only memory.
    """
    raw = getattr(r, 'raw', None)
    if raw is not None and hasattr(raw, 'stream'):
        return raw.stream(chunk, decode_content=False)
    return r.iter_content(chunk_size=chunk)


def _declared_length(r) -> int:
    """The body size the service declares, or 0 when it declares none."""
    try:
        return max(0, int(r.headers.get('content-length', 0)))
    except (AttributeError, TypeError, ValueError):
        return 0


def fetch_motion(s: Session, url: str, warn_bytes: int = PULSE_WARN_BYTES,
                 reject_bytes: int = PULSE_REJECT_BYTES) -> tuple:
    """Downloads a motion/print job into memory and parses its header.

    Returns ``(info, source)``, or ``(False, None)`` if the job is not one
    this machine will run. Nothing is written to the machine here: the ring is
    fed from the returned source separately, so a job may be longer than the
    ring.

    ``warn_bytes`` and ``reject_bytes`` bound the body held in memory, not the
    job (see PULSE_WARN_BYTES). The declared length is checked before a byte is
    taken, and the running total is checked as it arrives, because a service
    that declares nothing - or declares wrongly - would otherwise be trusted
    with all the memory there is. Either may be 0 to lift it.

    :param s: Requests Session object
    :param url: Target URL
    :param warn_bytes: log a body at or past this size
    :param reject_bytes: refuse a body past this size
    """
    r = request(s, url, 'GET', stream=True)
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
    for piece in _body_stream(r):
        body += piece
        if reject_bytes and len(body) > reject_bytes:
            logger.error('refusing the job: the download passed the %d bytes this '
                         'machine will hold (%d declared)' % (reject_bytes, declared))
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
    Downloads a motion/print file and writes all of it out.

    This is the whole-job path: everything is enqueued before anything plays,
    so the destination has to be able to take the entire job. A job longer
    than the ring is fed with gfhardware's feeder instead.

    :param s: Requests Session object
    :type s: Session
    :param url: Target URL
    :type url: str
    :param out_file: Where to write the results
    :type out_file: str
    :return: Header dict or False on failure
    :rtype: Union[dict, bool]
    """
    info, source = fetch_motion(s, url)
    if not info:
        return False

    # out_file may be a path or an already-open binary file object. A machine
    # streaming to the exclusive-open pulse device holds one flock'd fd for
    # the whole job (the kernel dead man's switch) and passes it in here; in
    # that case the caller owns the fd and it must not be closed.
    from contextlib import nullcontext
    if hasattr(out_file, 'write'):
        out_ctx = nullcontext(out_file)
    else:
        out_ctx = open(out_file, 'wb')

    # Optional debug capture of the raw job, off by default (it copies every
    # job into the capture directory, LOGGING.DIR). Enable with
    # LOGGING.SAVE_PULS.
    save_puls = get_cfg('LOGGING.SAVE_PULS') and get_cfg('LOGGING.DIR')
    raw = None
    base_file_name = None
    if save_puls:
        base_file_name = '%s/%s' % (get_cfg('LOGGING.DIR'),
                                    time.strftime("%Y-%m-%d_%H%M%S"))
        try:
            raw = open(base_file_name + '.puls', 'wb')
            raw.write(source.header_raw)
        except OSError as e:
            # The capture is a debug aid and never costs a job: a missing
            # directory or a full disk drops it and the print runs on.
            logger.warning('pulse capture disabled: %s' % e)
            if raw:
                raw.close()
            raw = None
            save_puls = False

    size = 0
    stat = None
    with out_ctx as f:
        try:
            while True:
                chunk = source.read(_WRITE_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                stat = decode_all_steps(chunk, stat)
                f.write(chunk)
                if raw:
                    try:
                        raw.write(chunk)
                    except OSError as e:
                        logger.warning('pulse capture stopped: %s' % e)
                        raw.close()
                        raw = None
                        save_puls = False
        except OSError as e:
            # The pulse device rejects a write once its ring is full
            # (-ENOMEM), i.e. the job is larger than the device can buffer.
            # Log a clear reason and re-raise so the caller safes the machine.
            logger.error('pulse write failed after %d bytes - the job may exceed '
                         'the device ring capacity: %s' % (size, e))
            raise
        finally:
            if raw:
                raw.close()

    info['size'] = size
    info['run_time'] = motion_run_time(info, size)
    info['stats'] = stat
    if save_puls:
        try:
            with open(base_file_name + '.info', 'w') as jf:
                jf.write(json.dumps(info, sort_keys=True, indent=4,
                                    default=str) + '\n')
        except OSError as e:
            logger.warning('pulse capture info not written: %s' % e)
    return info


def request(s: Session, url: str, method: str, timeout: int = 15, stream: bool = False,
            _retry_auth: bool = True, **kwargs) -> Union[Response, bool]:
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
    :param _retry_auth: on a 401, re-sign-in and replay the request once.
        The sign-in request itself passes False to avoid recursion.
    :type _retry_auth: bool
    :param kwargs:
    :return: Requests Response object, or False on error.
    :rtype: Union[Response, bool]
    """
    req = Request(method, str.replace(str(url), 'wss://', 'https://'), **kwargs)
    prepared = s.prepare_request(req)
    try:
        r = s.send(prepared, stream=stream, timeout=timeout)
    except requests.RequestException as e:
        # Network trouble is a routine condition (DNS blip, uplink drop,
        # timeout), not an exception to unwind a thread on: every caller
        # already handles a False return as failure.
        logger.error('request failed: %s' % e)
        return False
    if r.status_code == 401 and _retry_auth:
        # The auth_token expired mid-session: re-sign-in (fresh Bearer) and
        # replay the request once. Lazy import - authentication imports us.
        logger.info('401 - re-authenticating and retrying')
        from gfutilities.service.authentication import authenticate_machine
        if authenticate_machine(s):
            return request(s, url, method, timeout=timeout, stream=stream,
                           _retry_auth=False, **kwargs)
    if r.status_code != 200:
        logger.error('FAILED: ' + r.reason)
        return False
    return r


def send_wss_event(msg_q_tx: Queue, action_id: Union[int, None], event: str, **kwargs):
    global response_id
    global start_time
    with _response_id_lock:      # called from several threads
        response_id += 1
        rid = response_id

    if msg_q_tx.qsize() >= TX_QUEUE_MAX:
        logger.warning('event TX queue full (socket down?); dropping %s' % event)
        return

    if 'data' in kwargs:
        data = ',"%s":%s' % (kwargs['data']['key'], kwargs['data']['value'])
    else:
        data = ''
    if action_id is None:
        action_id = ''
    else:
        action_id = '"action_id":%s,' % action_id
    msg_q_tx.put(u'{"id":%s,"timestamp":%s,"type":"event","version":1,'
                 u'%s"level":"INFO","event":"%s"%s}\n' %
                 (rid, int(((time.time() - start_time) * 100) + 3800), action_id, event, data))


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
    global start_time
    if msg_q_tx.qsize() >= TX_QUEUE_MAX:
        logger.warning('event TX queue full (socket down?); dropping %s' % progress)
        return
    with _response_id_lock:      # called from several threads
        response_id += 1
        rid = response_id

    frame = '{"id":%s,"timestamp":%s,"type":"progress","version":1,' % (
        rid, int(((time.time() - start_time) * 100) + 3800))
    if action_id is not None:
        frame += '"action_id":%s,' % action_id
    frame += '"progress":"%s","current":%d,"units":"%s"' % (progress, int(current), units)
    if total is not None:
        frame += ',"total":%d' % int(total)
    if values:
        frame += ',"settings":{"values":%s}' % json.dumps(values, sort_keys=True,
                                                          separators=(',', ':'))
    msg_q_tx.put(frame + '}\n')


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
    ws_ready = False
    # Wait 15 seconds for session to establish, or error out
    for x in range(0, 16):
        if not ws.ready:
            time.sleep(1)
        else:
            ws_ready = True
            break
    if ws_ready:
        logger.info('ESTABLISHED')
        return ws
    else:
        logger.error('FAILED')
        ws.stop = True
        return False
