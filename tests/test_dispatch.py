"""
Copyright 2026 514 LLC d/b/a OpenGlow
Written by Scott Wiederhold
https://community.openglow.org

SPDX-License-Identifier:    MIT
"""
from gfutilities.service.dispatch import (dispatch_action, IMAGE_ACTIONS,
                                          PULS_ACTIONS, SYNC_ACTIONS)


class FakeMachine:
    """Records which run_* entry point the dispatcher called."""
    def __init__(self, accept_puls=True):
        self.calls = []
        self.running_action_id = None
        self._accept_puls = accept_puls

    def run_settings_report(self, msg):
        self.calls.append(('settings', msg['id']))

    def run_capture(self, msg):
        self.calls.append(('capture', msg['action_type']))

    def run_puls(self, msg):
        self.calls.append(('puls', msg['action_type']))
        if self._accept_puls:
            self.running_action_id = msg['id']

    def run_update_check(self, msg):
        self.calls.append(('update_check', msg['id']))

    def run_factory_reset(self, msg):
        self.calls.append(('factory_reset', msg['id']))

    def run_head_firmware_update(self, msg):
        self.calls.append(('head_firmware_update', msg['id']))


def _msg(action, status='ready', mid=1, **extra):
    m = {'action_type': action, 'status': status, 'id': mid}
    m.update(extra)
    return m


def test_settings_ready_routes_to_report():
    m = FakeMachine()
    assert dispatch_action(m, _msg('settings')) == 'dispatched'
    assert m.calls == [('settings', 1)]


def test_settings_non_ready_ignored():
    m = FakeMachine()
    assert dispatch_action(m, _msg('settings', status='success')) == 'ignored'
    assert m.calls == []


def test_all_image_actions_route_to_capture():
    for action in IMAGE_ACTIONS:
        m = FakeMachine()
        assert dispatch_action(m, _msg(action)) == 'dispatched'
        assert m.calls == [('capture', action)]


def test_user_image_is_an_image_action():
    assert 'user_image' in IMAGE_ACTIONS


def test_puls_accepted_returns_action_type():
    for action in ('hunt', 'motion', 'print'):
        m = FakeMachine(accept_puls=True)
        assert dispatch_action(m, _msg(action, mid=7)) == action


def test_puls_busy_returns_busy():
    m = FakeMachine(accept_puls=False)
    assert dispatch_action(m, _msg('motion')) == 'busy'


def test_print_refused_when_not_allowed():
    m = FakeMachine()
    assert dispatch_action(m, _msg('print'), allow_print=False) == 'refused'
    # run_puls must not have been called at all
    assert m.calls == []


def test_hunt_and_motion_allowed_when_print_disallowed():
    for action in ('hunt', 'motion'):
        m = FakeMachine()
        assert dispatch_action(m, _msg(action), allow_print=False) == action


def test_update_check_routed():
    m = FakeMachine()
    assert dispatch_action(m, _msg('update_check')) == 'dispatched'
    assert m.calls == [('update_check', 1)]


def test_factory_reset_refused():
    m = FakeMachine()
    assert dispatch_action(m, _msg('factory_reset')) == 'refused'
    assert m.calls == [('factory_reset', 1)]


def test_head_firmware_update_refused():
    m = FakeMachine()
    assert dispatch_action(m, _msg('head_firmware_update')) == 'refused'
    assert m.calls == [('head_firmware_update', 1)]


# -- only a 'ready' one of these asks for anything --------------------------

def test_sync_actions_ignore_every_status_but_ready():
    # The service sends a cancel for actions a machine never received (a
    # stray factory_reset cancel is on record from a factory machine's own
    # log), and the factory ignores those rather than answering them.
    for action in SYNC_ACTIONS:
        for status in ('new', 'started', 'success', 'failure', 'cancelled', None):
            m = FakeMachine()
            assert dispatch_action(m, _msg(action, status=status)) == 'ignored'
            assert m.calls == [], (action, status)


def test_sync_actions_are_the_ones_with_no_job():
    assert SYNC_ACTIONS == ('settings', 'update_check', 'factory_reset',
                            'head_firmware_update')
    for action in SYNC_ACTIONS:
        assert action not in PULS_ACTIONS and action not in IMAGE_ACTIONS


def test_a_cancel_still_reaches_a_running_job():
    # The status gate must never sit in front of the job actions: a cancel
    # is how a print in flight is stopped.
    for action in ('motion', 'print', 'hunt'):
        m = FakeMachine(accept_puls=False)
        dispatch_action(m, _msg(action, status='cancelled'))
        assert m.calls == [('puls', action)]
    for action in IMAGE_ACTIONS:
        m = FakeMachine()
        dispatch_action(m, _msg(action, status='cancelled'))
        assert m.calls == [('capture', action)]


def test_focus_is_ignored_as_the_factory_ignores_it():
    # A name the service knows and the factory application has no case for.
    m = FakeMachine()
    assert dispatch_action(m, _msg('focus')) == 'ignored'
    assert m.calls == []


def test_unknown_action_ignored():
    m = FakeMachine()
    assert dispatch_action(m, _msg('telemetry_blob')) == 'ignored'
    assert m.calls == []


def test_missing_action_type_ignored():
    m = FakeMachine()
    assert dispatch_action(m, {'id': 1, 'status': 'ready'}) == 'ignored'


def test_puls_actions_constant_matches_expected():
    assert PULS_ACTIONS == ('hunt', 'motion', 'print')
