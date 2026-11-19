"""
Copyright 2020 514 LLC d/b/a OpenGlow
Written by Scott Wiederhold
https://community.openglow.org

SPDX-License-Identifier:    MIT
"""
import logging
import json
import time
from queue import Queue, Empty

from gfutilities._common import *
from gfutilities.configuration import *
from gfutilities.device.basemachine import BaseMachine
from gfutilities.service.authentication import authenticate_machine
from gfutilities.service.dispatch import dispatch_action
from gfutilities.service.websocket import get_session, firmware_check, ws_connect

logger = logging.getLogger(LOGGER_NAME)

# A session is ended (the process restarts and connects again) only for a
# reconnect that is stuck: the socket seen not ready, with the machine idle
# (no action holding it, no event queued or held by the transmit pump), at
# every pass for WS_STALL_S, and the client's reconnect loop not having
# begun a step (an attempt or a wait) for as long. An outage of any length
# keeps the session while the loop cycles, beginning a step at least every
# 300 s plus one sign-in and one handshake, and so does the end of a long
# session, whose last step is as old as the session. A session ended this
# way ends the process; its respawn's first settings report carries the
# machine's values, and the service answers with its connect-time hunt, as
# at any start without the no-hunt marker.
WS_STALL_S = 900.0

_monotonic = time.monotonic


class GFUIService:
    """
    Glowforge UI Service Connector
    This connects to the servers specified in the configuration, and interfaces with the service.
    """
    def __init__(self, machine: BaseMachine):
        """
        Class Initialization
        Initialized WSS Queues, and configures logging.
        """
        self.session = None
        self.q_msg_rx = Queue()
        self.q_msg_tx = Queue()
        self._machine = machine
        self._ws = None
        self.stop = False
        logger.info('INITIALIZED')

    def request_stop(self) -> None:
        """Ask run() to exit at the next loop turn (e.g. from a SIGTERM
        handler). run() then shuts the machine down and safes the hardware."""
        self.stop = True

    def connect(self) -> bool:
        """
        Authenticates machine to service, checks firmware, and establishes WSS session.
        :return: Status of connection
        :rtype: bool
        """
        # Grab a persistent web session.  Once authenticated, this session is used for all web API's.
        self.session = get_session()
        # Authenticate machine
        if not authenticate_machine(self.session):
            return False
        # Check the factory firmware version the service advertises, and
        # record it for the forgectrl compatibility banner. ForgeFIRM never
        # downloads or installs factory firmware, so the result only informs
        # the operator (see run_update_check / record_factory_latest).
        if get_cfg('FACTORY_FIRMWARE.CHECK'):
            firmware_check(self.session)
        # Establish WebSocket Connection. Pass the session so the client can
        # re-sign-in for a fresh single-use ws_token on every reconnect.
        # Keep the client so run() can stop its thread when the session ends.
        ws = ws_connect(self.q_msg_rx, self.q_msg_tx, self.session)
        if not ws:
            return False
        self._ws = ws
        return True

    def run(self) -> None:
        """
        Processes messages from WSS service. A stop requested before the
        loop (during the sign-in) starts no machine: forgectrl has already
        stopped and latched the hardware, and the machine's start would
        reset and enable it. The start runs inside the try, so a start that
        raises still stops the machine and ends the session.
        :return:
        """
        if self.stop:
            self._disconnect()
            return
        down_since = None
        try:
            self._machine.start(self.session, self.q_msg_tx)
            while not self.stop:
                # A dead WS client thread means no more frames can ever
                # arrive: return so the outer connect loop reconnects
                # instead of blocking on an empty queue forever.
                if self._ws is not None and not self._ws.is_alive():
                    logger.error('WS client thread died; reconnecting')
                    break
                # A socket down this long, with the machine idle all the
                # while and its reconnect not moving, is a reconnect that
                # stalled (see WS_STALL_S). A running job does not need the
                # socket, and its events wait for the reconnect.
                if (self._ws is None or self._ws.ready
                        or self._machine.running_action_id is not None
                        or self._events_waiting()):
                    down_since = None
                elif down_since is None:
                    down_since = _monotonic()
                step_at = getattr(self._ws, 'step_at', None)
                if (down_since is not None and step_at is not None
                        and _monotonic() - down_since > WS_STALL_S
                        and _monotonic() - step_at > WS_STALL_S):
                    logger.error('the service socket has not been ready, and its reconnect '
                                 'has not moved, for %.0f s with the machine idle; ending '
                                 'the session' % (_monotonic() - down_since))
                    break
                try:
                    raw = self.q_msg_rx.get(timeout=0.5)
                except Empty:
                    continue
                except KeyboardInterrupt:
                    break
                try:
                    msg = json.loads(raw)
                except Exception as e:
                    # Not only ValueError: a frame nested past the decoder's
                    # depth raises RecursionError.
                    logger.warning('unparseable service message (%s): %r', type(e).__name__,
                                   raw[:200] if isinstance(raw, str) else raw)
                    self.q_msg_rx.task_done()
                    continue
                if not isinstance(msg, dict):
                    logger.warning('non-object service message: %r', msg)
                    self.q_msg_rx.task_done()
                    continue
                logger.info('service action request: %s (%s)',
                            msg.get('action_type'), msg.get('status'))
                # One malformed or unexpected frame must never take down
                # the service loop mid-print: the action threads keep the
                # running job supervised, and the next frame is processed
                # normally.
                try:
                    result = dispatch_action(self._machine, msg,
                                             allow_print=True)
                except Exception:
                    logger.exception('action dispatch failed; continuing')
                    result = False
                logger.info('%s (%s) service action %s',
                            msg.get('action_type'), msg.get('status'), result)
                self.q_msg_rx.task_done()
        finally:
            # Shut down safe even when the loop dies on an unexpected
            # error: machine.stop() stops motion, locks the latch, and
            # files the final idle report.
            self._machine.stop()
            self._disconnect()

    def _events_waiting(self) -> bool:
        """An event is queued, or held by the transmit pump for its next
        socket."""
        return (not self.q_msg_tx.empty()
                or getattr(self._ws, '_unsent', None) is not None)

    def _disconnect(self) -> None:
        """
        Flush any final queued events to the service, then stop the WS
        client thread so nothing of the session outlives run().
        :return:
        """
        if self._ws is None:
            return
        deadline = time.monotonic() + 2
        while (self._ws.ready and not self.q_msg_tx.empty()
               and time.monotonic() < deadline):
            time.sleep(0.05)
        if not self._ws.shutdown():
            logger.warning('WS client thread did not exit cleanly')
        self._ws = None
