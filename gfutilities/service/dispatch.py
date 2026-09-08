"""
Copyright 2026 514 LLC d/b/a OpenGlow
Written by Scott Wiederhold
https://community.openglow.org

SPDX-License-Identifier:    MIT
"""
import logging

from gfutilities._common import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)

# Actions that stream a pulse file and run motion.
PULS_ACTIONS = ('hunt', 'motion', 'print')
# Actions that capture a camera image and upload it.
IMAGE_ACTIONS = ('lid_image', 'head_image', 'lidar_image', 'user_image')
# Actions answered on this thread, with no job and no claim on the machine.
# Only a 'ready' one asks for anything: the service also sends 'cancelled'
# (and the other status values) for actions a machine never received, which
# the factory ignores rather than answers.
SYNC_ACTIONS = ('settings', 'update_check', 'factory_reset',
                'head_firmware_update')


def dispatch_action(machine, msg: dict, allow_print: bool = True) -> str:
    """
    Route one decoded service action to the machine.

    This is the single action-surface for both cloud clients: GFUIService
    (full cloud mode, allow_print=True) and gfhome (homing only,
    allow_print=False). Every action_type the 2.6.0 service issues is
    handled here - settings, the four image captures, the three puls
    actions, update_check, factory_reset and head_firmware_update - so a
    machine never silently drops one. The one name the service knows that
    nothing here handles is 'focus', which the factory application does not
    handle either: its own dispatch has no case for it and logs it away.

    :param machine: BaseMachine (or subclass) to dispatch to
    :param msg: decoded action message
    :param allow_print: run 'print' actions (False refuses them - homing)
    :return: the action_type when a puls action was accepted for run, else
             one of 'dispatched', 'busy', 'refused', 'ignored'
    """
    action = msg.get('action_type', '')
    status = msg.get('status')

    # The job actions come first, and deliberately without a status gate:
    # they are where a 'cancelled' means something, and the machine has to
    # see it to stop the job it is already running.
    if action in IMAGE_ACTIONS:
        machine.run_capture(msg)
        return 'dispatched'
    if action in PULS_ACTIONS:
        if action == 'print' and not allow_print:
            logger.warning('refusing print action (prints not allowed here)')
            return 'refused'
        machine.run_puls(msg)
        # A puls action that was accepted is now the running action; report
        # its type so a caller pumping the protocol can track it in flight.
        if machine.running_action_id == msg.get('id'):
            return action
        return 'busy'
    if action in SYNC_ACTIONS and status != 'ready':
        logger.info('ignoring status "%s" for action "%s"', status, action)
        return 'ignored'
    if action == 'settings':
        machine.run_settings_report(msg)
        return 'dispatched'
    if action == 'update_check':
        machine.run_update_check(msg)
        return 'dispatched'
    if action == 'factory_reset':
        machine.run_factory_reset(msg)
        return 'refused'
    if action == 'head_firmware_update':
        machine.run_head_firmware_update(msg)
        return 'refused'
    logger.info('ignoring action type "%s"', action)
    return 'ignored'


__all__ = ['dispatch_action', 'PULS_ACTIONS', 'IMAGE_ACTIONS', 'SYNC_ACTIONS']
