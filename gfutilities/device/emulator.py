"""
Copyright 2020 514 LLC d/b/a OpenGlow
Written by Scott Wiederhold
https://community.openglow.org

SPDX-License-Identifier:    MIT
"""
import json
import logging
import os
import time

from gfutilities._common import *
from gfutilities.configuration import get_cfg, set_cfg
from gfutilities.device.basemachine import BaseMachine
from gfutilities.service.websocket import load_motion, img_upload, send_wss_event

logger = logging.getLogger(LOGGER_NAME)


class _Discard:
    """A write target that keeps nothing: the emulator plays no program."""

    @staticmethod
    def write(data) -> int:
        return len(data)


def _uploaded(session, data: bytes, msg: dict) -> bool:
    """img_upload as a yes or no: the emulator reports the upload's outcome
    as its own event instead of failing the action."""
    try:
        return img_upload(session, data, msg)
    except Exception as e:
        logger.error('upload failed: %s' % e)
        return False


class Emulator(BaseMachine):
    """
    Glowforge Device Emulator
    This connects to the servers specified in the configuration, and responds the same as the hardware does.
    """
    def __init__(self):
        """
        Initialize object
        """
        BaseMachine.__init__(self)
        # Code-set marker: emulator-only behaviors (e.g. BYPASS_HOMING)
        # key on this, never on a raw config line a real machine's config
        # could carry.
        set_cfg('EMULATOR.ACTIVE', True)
        # Track which step in the homing process we are at.
        # Example script has 5 saved head images, which are uploaded to the service in sequence to
        # emulate the homing process.  Files are located in _RESOURCES/IMG
        # HOME_1.jpg: Image of the head located at expected center position
        #             This image was taken with the head already in the calibrated position to speed up the process.
        # HOME_2.jpg: Image after first retreat move
        # HOME_3.jpg: Image after second retreat move
        # HOME_4.jpg: Image after third retreat move; also the bed image
        #             (Proofgrade(tm) material in place) sent for every
        #             non-homing lid_image request unless the image directory
        #             holds a LID_IMAGE.jpg
        self._homing_stage = 0 if get_cfg('EMULATOR.BYPASS_HOMING') else 1
        # Used to track the action just before a lid_image request. This is used to detect homing operations after
        # the initial start up (after a print job, for instance)
        self._last_action = None

    def _button_wait(self, msg: dict) -> None:
        pass

    def _head_image(self, msg: dict, settings: dict = None) -> None:
        # HCil (head illumination) may be absent from a settings dict, so
        # guard the comparison - a missing key must not raise.
        hcil = (settings or {}).get('HCil')
        if hcil and hcil > 0:
            img = 'HEAD_LASER_%s.jpg' % get_cfg('EMULATOR.MATERIAL_THICKNESS')
        else:
            img = 'HEAD_NO_LASER_%s.jpg' % get_cfg('EMULATOR.MATERIAL_THICKNESS')
        self._capture_and_upload(msg, img)
        self._last_action = 'head_image'

    def _capture_and_upload(self, msg: dict, img: str) -> None:
        """
        Read the canned image (the "capture") and upload it, emitting the
        capture/upload progress events the v2.6.0 service expects during the
        homing handshake. The service waits for '<action>:upload:completed'
        (the image is now in storage) before fetching and analyzing it.
        :param msg: Incoming WSS message
        :type msg: dict
        :param img: Image filename within EMULATOR.IMAGE_SRC_DIR
        :type img: str
        """
        action = msg['action_type']
        send_wss_event(self._q_msg_tx, msg['id'], '%s:capture:starting' % action)
        with open('%s/%s' % (get_cfg('EMULATOR.IMAGE_SRC_DIR'), img), 'rb') as f:
            data = f.read()
        send_wss_event(self._q_msg_tx, msg['id'], '%s:capture:completed' % action)
        send_wss_event(self._q_msg_tx, msg['id'], '%s:upload:starting' % action)
        ok = _uploaded(self._session, data, msg)
        send_wss_event(self._q_msg_tx, msg['id'],
                       '%s:upload:%s' % (action, 'completed' if ok else 'failed'))

    def _hunt(self, msg: dict) -> None:
        self._last_action = 'hunt'
        if not self._motion_download(msg):
            self._running_action_cancelled = True

    def _initialize(self) -> None:
        pass

    def _lid_image(self, msg: dict, settings: dict = None) -> None:
        if self._homing_stage == 0 and self._last_action == 'motion':
            # Looks like were are homing again
            self._homing_stage = 1
        if self._homing_stage != 0:
            logger.info('HOME Step %s' % self._homing_stage)
            img = 'HOME_%s.jpg' % self._homing_stage
            self._homing_stage = self._homing_stage + 1 if self._homing_stage < 4 else 0
        else:
            img = 'LID_IMAGE.jpg'
            if not os.path.isfile('%s/%s' % (get_cfg('EMULATOR.IMAGE_SRC_DIR'), img)):
                img = 'HOME_4.jpg'
        self._capture_and_upload(msg, img)
        self._last_action = 'lid_image'

    def _motion(self, msg: dict) -> None:
        # Download puls file from service. A job this machine would refuse
        # is refused here too: canceled, as the hardware machine ends it.
        if not self._motion_download(msg):
            self._running_action_cancelled = True
            self._last_action = msg['action_type']
            return
        if msg['action_type'] == 'print':
            send_wss_event(self._q_msg_tx, msg['id'], 'print:download:completed')
            self._button_wait(msg)
            if not self._running_action_cancelled:
                send_wss_event(self._q_msg_tx, msg['id'], 'print:warmup:starting')
        # Run motion job
        if not self._running_action_cancelled:
            if msg['action_type'] == 'print':
                send_wss_event(self._q_msg_tx, msg['id'], 'print:running')
                send_wss_event(self._q_msg_tx, msg['id'], 'print:return_to_home:succeeded')

        self._last_action = msg['action_type']

    def _motion_download(self, msg: dict) -> bool:
        """Download the job and walk it as the machine would, keeping none
        of its program: the job's header and its statistics go to an .info
        file in EMULATOR.MOTION_DL_DIR. A job load_motion refuses writes
        nothing. Returns whether the job was taken."""
        logger.info('DOWNLOADING')
        info = load_motion(self._session, msg['motion_url'], _Discard())
        if not info:
            logger.info('REFUSED')
            return False
        base_file_name = '%s/%s_%s' % (get_cfg('EMULATOR.MOTION_DL_DIR'),
                                       time.strftime("%Y-%m-%d_%H%M%S"), msg['action_type'])
        with open(base_file_name + '.info', 'w') as f:
            f.write(json.dumps(info, sort_keys=True, indent=4, default=str) + '\n')
        logger.debug('Header: %s' % info)
        logger.info('COMPLETE')
        return True

    def _shutdown(self) -> None:
        pass
