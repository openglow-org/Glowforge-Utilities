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
        self.q_capture = Queue()
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
        Processes messages from WSS service
        :return:
        """
        self._machine.start(self.session, self.q_msg_tx)
        try:
            while not self.stop:
                # A dead WS client thread means no more frames can ever
                # arrive: return so the outer connect loop reconnects
                # instead of blocking on an empty queue forever.
                if self._ws is not None and not self._ws.is_alive():
                    logger.error('WS client thread died; reconnecting')
                    break
                try:
                    raw = self.q_msg_rx.get(timeout=0.5)
                except Empty:
                    continue
                except KeyboardInterrupt:
                    break
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    logger.warning('unparseable service message: %r',
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
