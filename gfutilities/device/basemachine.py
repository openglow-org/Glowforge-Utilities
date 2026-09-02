"""
(C) Copyright 2020
Scott Wiederhold, s.e.wiederhold@gmail.com
https://community.openglow.org

SPDX-License-Identifier:    MIT
"""
import logging
from queue import Queue
from requests import Session
from threading import Thread
from typing import Union

from gfutilities._common import *
from gfutilities.configuration import set_cfg
from gfutilities.device.settings import send_report, get_machine_setting
from gfutilities.service.websocket import send_wss_event, firmware_check

logger = logging.getLogger(LOGGER_NAME)


class BaseMachine:
    """
    BaseMachine Base Class
    Serves as the foundation for emulator and machine control classes.
    Implements the basic functions required for operation with the Glowforge online service.
    """
    def __init__(self):
        """
        Initializes the object, child threads and message queues.
        """
        self._action_thread: _ActionThread = _ActionThread(self, {})
        self._q_msg_tx: Union[Queue, None] = None
        self.running_action_id: Union[int, None] = 0
        self.running_action_type: Union[str, None] = None
        self._running_action_cancelled: bool = False
        # Per-shot camera settings the service attaches to the current image
        # action (canonical source for the image handlers; see _image_settings).
        self._action_settings: dict = {}
        self._session: Union[Session, None] = None

        set_cfg('FACTORY_FIRMWARE.FW_VERSION', get_machine_setting('MCov'), True)
        set_cfg('FACTORY_FIRMWARE.APP_VERSION', get_machine_setting('MCdv'), True)

    def _ok_to_run_action(self, action_id: str, msg_type: str, status: str) -> bool:
        """
        Checks the action against any current running actions.
        If this a request to cancel an action:
            - If the action is currently running, it sets the _running_action_cancelled flag as True
            - If the action is not currently running, it sends a cancelled message to the service.
            - For both conditions, it returns False
        If there are not any currently running actions, it sets this as the current running action and returns True.
        If there is a current running action, and this is not a request to cancel it, sends a cancelled message
        to the service for this requested action, and returns false.
        :param action_id: Action ID
        :type action_id: str
        :param msg_type: Action Type
        :type msg_type: str
        :param status: Status of message (either 'ready' or 'cancelled')
        :type status: str
        :return: Return True if this it is ok to run this action, False if not
        :rtype: bool
        """
        action_id = int(action_id)
        if action_id == self.running_action_id and status == 'cancelled':
            logger.debug('action %s (%s) cancellation received' % (action_id, msg_type))
            self._running_action_cancelled = True
            return False
        if status == 'cancelled':
            return False
        # Only a 'ready' action starts. The service uses 'ready' to launch work;
        # the other status values in the protocol (new/started/success/failure)
        # are never a launch, so treat anything that is not 'ready' as a no-op
        # rather than starting the action.
        if status != 'ready':
            logger.debug('ignoring status "%s" for action %s (%s)' % (status, action_id, msg_type))
            return False
        if self.running_action_id:
            return False
        logger.debug('running action set to %s (%s)' % (action_id, msg_type))
        self.running_action_id = action_id
        self.running_action_type = msg_type
        self._running_action_cancelled = False
        return True

    @staticmethod
    def _image_settings(msg: dict) -> dict:
        """
        Normalize the per-shot camera settings the service attaches to an
        image action. The service sends them either as a dict (e.g.
        {"LCfl":1} lid flash, or {"HCil":..,"HCae":0,"HCex":..,"HCag":0,
        "HCga":..} head exposure/gain) or, for lidar, as a list of dicts
        (measure-laser on/off). Returns the single/first settings dict, or
        {} when the action carries none.

        Policy (which keys a machine honors is up to its hardware layer):
        illumination (HCil) and lid flash (LCfl) are lighting the capture
        path can honor directly. The factory-scale exposure/gain values
        (HCex/HCga/HCae/HCag) are deliberately NOT applied on the mainline
        camera - its controls use different units and the raw values would
        mis-expose - so a machine uses its own camera defaults there (see
        the head-image handler in gfhardware). This is a documented
        override, not an omission.
        :param msg: Incoming WSS Message
        :type msg: dict
        :return: normalized settings dict
        :rtype: dict
        """
        s = msg.get('settings')
        if isinstance(s, list):
            return s[0] if s else {}
        if isinstance(s, dict):
            return s
        return {}

    def _action_cleanup(self) -> None:
        """
        Post-action failsafe hook, called by the action thread after every
        action - including ones that raised. Child classes override this to
        force their hardware safe (e.g. lock the laser latch); the default
        does nothing.
        :return:
        """
        pass

    def _button_wait(self, msg: dict) -> None:
        """
        Child class handler for "print:waiting" event - waiting for button push
        To be implemented by the child class.
        This is method should return after the big button has been pressed.
        :param msg: Incoming WSS Message
        :type msg: dict
        :return:
        """
        raise NotImplementedError

    def head_image(self, msg: dict) -> None:
        """
        Process head image request.
        Sends related start and finish messages, and calls child class handler.
        :param msg: Incoming WSS Message
        :type msg: dict
        :return:
        """
        send_wss_event(self._q_msg_tx, msg['id'], 'head_image:starting')
        self._action_settings = self._image_settings(msg)
        self._head_image(msg, self._action_settings or None)
        send_wss_event(self._q_msg_tx, msg['id'], 'head_image:completed')

    def _head_image(self, msg: dict, settings: dict = None) -> None:
        """
        Child class handler for capturing image from head cam.
        To be implemented by the child class.
        This method should capture the image from the head camera, and upload the resulting image to the Web API.
        :param msg: Incoming WSS Message
        :type msg: dict
        :param settings: Camera settings
        :type settings: dict
        :return:
        """
        raise NotImplementedError

    def hunt(self, msg: dict) -> None:
        """
        Home the focus lens.
        Sends related start and finish messages, and calls child class handler.
        :param msg: Incoming WSS Message
        :type msg: dict
        :return:
        """
        send_wss_event(self._q_msg_tx, msg['id'], 'hunt:starting')
        self._hunt(msg)
        self._finish_action(msg['id'], 'hunt')

    def _hunt(self, msg: dict) -> None:
        """
        Child class handler for focus lens homing cycle
        To be implemented by the child class.
        This method should home the lens and set it to the appropriate zeroing offset.
        :param msg: Incoming WSS Message
        :type msg: dict
        :return:
        """
        raise NotImplementedError

    def _initialize(self) -> None:
        """
        Child class handler for initializing the machine
        To be implemented by the child class.
        :return:
        """
        raise NotImplementedError

    def lid_image(self, msg: dict) -> None:
        """
        Process lid image request.
        Sends related start and finish messages, and calls child class handler.
        :param msg: Incoming WSS Message
        :type msg: dict
        :return:
        """
        send_wss_event(self._q_msg_tx, msg['id'], 'lid_image:starting')
        self._action_settings = self._image_settings(msg)
        self._lid_image(msg, self._action_settings or None)
        send_wss_event(self._q_msg_tx, msg['id'], 'lid_image:completed')

    @staticmethod
    def lamp_level(settings: dict = None) -> int:
        """
        The lid lamp brightness for one capture, from the action's settings.

        `LCfl` is the service's per-shot flash switch and it is the only key
        that decides this: a captured 1.x session shows lid_image actions
        arriving as {"LCfl": 1}. Zero means capture the bed as it is lit,
        which is a request worth honoring rather than overriding. How bright
        a flash is comes from `LLvl`, the lamp's own brightness setting, so
        a service that changes the lamp changes the flash with it.

        An action that says nothing about the flash gets one.
        :param settings: normalized per-action settings, or None
        :type settings: dict
        :return: lamp brightness, 0-255
        :rtype: int
        """
        level = get_machine_setting('LLvl')
        if settings and not settings.get('LCfl', 1):
            return 0
        return int(level if level is not None else 132)

    def _lid_image(self, msg: dict, settings: dict = None) -> None:
        """
        Child class handler for lid image requests.
        To be implemented by the child class.
        This method should capture the image from the lid camera, and upload the resulting image to the Web API.
        The lamp brightness for the shot is lamp_level(settings).
        :param msg: Incoming WSS Message
        :type msg: dict
        :param settings: Camera settings
        :type settings: dict
        :return:
        """
        raise NotImplementedError

    def lidar_image(self, msg: dict) -> None:
        """
        Process lidar image request.
        Sends related start and finish messages, and calls child class handler to capture
        head images with and without the distance measuring laser enabled.
        :param msg: Incoming WSS Message
        :type msg: dict
        :return:
        """
        send_wss_event(self._q_msg_tx, msg['id'], 'lidar_image:starting')
        self._action_settings = self._image_settings(msg)
        self._head_image(msg, self._action_settings or None)
        send_wss_event(self._q_msg_tx, msg['id'], 'lidar_image:completed')

    def user_image(self, msg: dict) -> None:
        """
        Process user image request (a user-requested camera snapshot).
        Sends related start and finish messages, and calls child class handler.
        :param msg: Incoming WSS Message
        :type msg: dict
        :return:
        """
        send_wss_event(self._q_msg_tx, msg['id'], 'user_image:starting')
        self._action_settings = self._image_settings(msg)
        self._user_image(msg, self._action_settings or None)
        send_wss_event(self._q_msg_tx, msg['id'], 'user_image:completed')

    def _user_image(self, msg: dict, settings: dict = None) -> None:
        """
        Child class handler for a user-requested image: the bed (lid) view.

        Settled from the factory application. Its four image actions are one
        class with four vtables, and user_image differs from lid_image in
        nothing but its name: same camera (the lid one), same requirement
        that the lid be closed. head_image and lidar_image are the pair that
        differ, on the head camera with no lid gate.
        :param msg: Incoming WSS Message
        :type msg: dict
        :param settings: Camera settings
        :type settings: dict
        :return:
        """
        self._lid_image(msg, settings)

    def run_update_check(self, msg: dict) -> None:
        """
        Answer an update_check without ever installing factory firmware.

        The factory application does nothing else with this action: it writes
        'u' to the runit control fifo of a separate 'glowforge-updater'
        service and reports ':completed' (or ':failed' if that write fails).
        No version query, no download, no install lives in the application at
        all. ForgeFIRM has no such service and must never acquire one, so the
        answer is the same event without the hand-off: what is left to do is
        probe the version the service advertises, which is what the forgectrl
        compatibility banner reads, and say the check is done.
        Interface for the action dispatch.
        :param msg: Incoming WSS Message
        :type msg: dict
        :return:
        """
        try:
            firmware_check(self._session)
        except Exception:
            # The one honest failure here: the check itself did not happen.
            logger.exception('firmware version probe failed')
            send_wss_event(self._q_msg_tx, msg['id'], 'update_check:failed')
            return
        logger.info('update_check answered; ForgeFIRM never installs factory '
                    'firmware, so there is nothing to hand off to')
        send_wss_event(self._q_msg_tx, msg['id'], 'update_check:completed')

    def run_factory_reset(self, msg: dict) -> None:
        """
        Refuse a factory_reset. A cloud command must never wipe a machine.

        What the factory does with it: the action posts a command to the
        hardware task, which tells runit not to restart the application and
        then replaces the process with /usr/bin/factory_reset.sh, passing
        'reboot' when the request asks for one. There is no ForgeFIRM
        equivalent and there will not be one.

        ':failed' rather than ':cancelled' is deliberate. In this protocol a
        cancel is what the service says when it withdraws an action; failure
        is what a machine says when the thing did not happen, which is the
        factory's own report when the reset script cannot be launched.
        Interface for the action dispatch.
        :param msg: Incoming WSS Message
        :type msg: dict
        :return:
        """
        logger.warning('factory_reset requested by service; ForgeFIRM does not '
                       'reset on cloud command - refusing')
        send_wss_event(self._q_msg_tx, msg['id'], 'factory_reset:failed')

    def run_head_firmware_update(self, msg: dict) -> None:
        """
        Refuse a head_firmware_update, and say so on the wire.

        The factory takes a 'head_firmware_filename' from the request, reads
        it out of /glowforge/fw/head/ and runs /usr/bin/head-update.sh, which
        pushes firmware into the laser head's microcontroller. That is the
        same class of command as a factory reset and gets the same answer.
        Silence would be worse than a refusal: the service is waiting on a
        terminal event for the action either way.
        Interface for the action dispatch.
        :param msg: Incoming WSS Message
        :type msg: dict
        :return:
        """
        logger.warning('head_firmware_update requested by service (%s); '
                       'ForgeFIRM does not flash the head on cloud command '
                       '- refusing', msg.get('head_firmware_filename'))
        send_wss_event(self._q_msg_tx, msg['id'], 'head_firmware_update:failed')

    def motion(self, msg: dict) -> None:
        """
        Process motion request.
        Sends related start and finish messages, and calls child class handler.
        :param msg: Incoming WSS Message
        :type msg: dict
        :return:
        """
        send_wss_event(self._q_msg_tx, msg['id'], msg['action_type'] + ':starting')
        self._motion(msg)
        self._finish_action(msg['id'], msg['action_type'])

    def _finish_action(self, action_id: int, action_type: str) -> None:
        """
        Terminal event of a job: ':cancelled' when the run was cut short
        (locally or by the service), else ':completed'. Logged, so the
        machine log carries the same record as the wire.
        """
        event = 'cancelled' if self._running_action_cancelled else 'completed'
        logger.info('%s [%s]: finished with event ":%s"' % (action_type, action_id, event))
        send_wss_event(self._q_msg_tx, action_id, '%s:%s' % (action_type, event))

    def _motion(self, msg: dict) -> None:
        """
        Child class handler for motion requests.
        To be implemented by the child class.
        This method should download the specified motion file and execute it.
        :param msg: Incoming WSS Message
        :type msg: dict
        :return:
        """
        raise NotImplementedError

    def _start_action(self, msg: dict) -> None:
        """
        Start the accepted action's thread. The previous thread can still be
        in its final instants after releasing its claim; wait it out rather
        than dropping an accepted action on the floor - a dropped action has
        no thread and no terminal event, and its claim would reject every
        action after it forever.
        :param msg: Incoming WSS Message
        :type msg: dict
        :return:
        """
        if self._action_thread.is_alive():
            self._action_thread.join(timeout=5)
        if self._action_thread.is_alive():
            logger.error('previous action thread did not exit; failing %s'
                         % msg.get('action_type'))
            send_wss_event(self._q_msg_tx, msg.get('id'),
                           '%s:failed' % msg.get('action_type'))
            self.running_action_id = None
            self.running_action_type = None
            return
        self._action_thread = _ActionThread(self, msg)
        self._action_thread.start()

    def run_capture(self, msg: dict) -> None:
        """
        Process capture request.
        Fires up CaptureThread thread to handle image request.
        Interface for GFUI Service
        :param msg: Incoming WSS Message
        :type msg: dict
        :return:
        """
        if self._ok_to_run_action(msg['id'], msg['action_type'], msg['status']):
            self._start_action(msg)

    def run_puls(self, msg: dict) -> None:
        """
        Process pulse file.
        Fires up MotionThread thread to handle puls file request.
        Interface for GFUI Service
        :param msg: Incoming WSS Message
        :type msg: dict
        :return:
        """
        if self._ok_to_run_action(msg['id'], msg['action_type'], msg['status']):
            self._start_action(msg)

    def run_settings_report(self, msg: dict) -> None:
        """
        Send settings report.
        Interface for GFUI Service
        :param msg: Incoming WSS Message
        :type msg: dict
        :return:
        """
        if self._ok_to_run_action(msg['id'], msg['action_type'], msg['status']):
            try:
                send_report(self._q_msg_tx, msg)
            finally:
                # The report is synchronous, so release the claim it just
                # took, whether or not the report went out - inside the
                # accepted branch only: a rejected request (settings during
                # a print) must not wipe the RUNNING action's id, or a
                # later cancel of that action is dropped.
                self.running_action_id = None
                self.running_action_type = None

    def _shutdown(self) -> None:
        """
        Child class handler for shutting down the machine
        To be implemented by the child class.
        :return:
        """
        raise NotImplementedError

    def start(self, session: Session, msq_tx_q: Queue) -> None:
        """
        Initialize Machine and Start message handling threads.
        :param session: WSS Session
        :type session: Session
        :param msq_tx_q: WSS Msg Tx Queue
        :type msq_tx_q: Queue
        :return:
        """
        logger.debug('starting')
        self._session = session
        self._q_msg_tx = msq_tx_q
        self._initialize()
        logger.debug('started')

    def stop(self) -> None:
        """
        Stop message handling threads.
        :return:
        """
        logger.debug('stopping')
        self._shutdown()
        logger.debug('stopped')


class _ActionThread(Thread):
    """
    ActionThread
    Responds to incoming WSS events to run an action
    """
    def __init__(self, machine: BaseMachine, msg: dict):
        """
        Initialize ActionThread Dispatcher
        :param machine: Machine object
        :type machine: BaseMachine
        :param msg: Incoming WSS Message
        :type msg: dict
        """
        self._machine = machine
        self._msg = msg
        # daemon: a blocked action (e.g. waiting on the button) must not
        # keep the process alive after the service loop exits. Safe hardware
        # state does not depend on this thread finishing - it is enforced by
        # _action_cleanup and, on process exit, the kernel dead man's switch.
        Thread.__init__(self, daemon=True)

    def run(self) -> None:
        logger.debug('action thread start')
        try:
            if self._msg['action_type'] == 'lid_image':
                self._machine.lid_image(self._msg)
            elif self._msg['action_type'] == 'head_image':
                self._machine.head_image(self._msg)
            elif self._msg['action_type'] == 'lidar_image':
                self._machine.lidar_image(self._msg)
            elif self._msg['action_type'] == 'user_image':
                self._machine.user_image(self._msg)
            elif self._msg['action_type'] == 'hunt':
                self._machine.hunt(self._msg)
            elif self._msg['action_type'] in ['motion', 'print']:
                self._machine.motion(self._msg)
        except Exception:
            # A crashed action must not leave the job armed: without this,
            # an exception mid-print would leave the action registered as
            # running and the laser latch unlocked.
            logger.exception('action %s crashed' % self._msg.get('action_type'))
            # The service is waiting on this action: a terminal event lets
            # it resolve instead of hanging on it forever.
            try:
                send_wss_event(self._machine._q_msg_tx, self._msg.get('id'),
                               '%s:failed' % self._msg.get('action_type'))
            except Exception:
                logger.exception('could not report the action failure')
        finally:
            try:
                self._machine._action_cleanup()
            except Exception:
                logger.exception('action cleanup failed')
            self._machine.running_action_id = None
            self._machine.running_action_type = None
            logger.debug('action thread stop')


__all__ = ['BaseMachine']
