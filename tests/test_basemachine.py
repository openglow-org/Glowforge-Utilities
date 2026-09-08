"""
Copyright 2026 514 LLC d/b/a OpenGlow
Written by Scott Wiederhold
https://community.openglow.org

SPDX-License-Identifier:    MIT
"""
import json
from queue import Queue

from gfutilities.device import basemachine
from gfutilities.device.basemachine import BaseMachine


class StubMachine(BaseMachine):
    """Minimal concrete machine: records captures + settings, no hardware."""
    def __init__(self):
        BaseMachine.__init__(self)
        self.lid_captured = 0
        self.lid_lamps = []
        self.lid_settings = 'unset'
        self.head_settings = 'unset'
        self._q_msg_tx = Queue()

    def _initialize(self):
        pass

    def _shutdown(self):
        pass

    def _button_wait(self, msg):
        pass

    def _lid_image(self, msg, settings=None):
        self.lid_captured += 1
        self.lid_settings = settings
        self.lid_lamps.append(self.lamp_level(settings))

    def _head_image(self, msg, settings=None):
        self.head_settings = settings


def _events(q):
    out = []
    while not q.empty():
        out.append(json.loads(q.get())['event'])
    return out


# ---- _ok_to_run_action status handling -------------------------------------

def test_ready_starts_action():
    m = StubMachine()
    assert m._ok_to_run_action(5, 'motion', 'ready') is True
    assert m.running_action_id == 5


def test_second_ready_while_running_refused():
    m = StubMachine()
    m._ok_to_run_action(5, 'motion', 'ready')
    assert m._ok_to_run_action(6, 'motion', 'ready') is False
    assert m.running_action_id == 5


def test_non_ready_status_never_starts():
    for status in ('new', 'started', 'success', 'failure'):
        m = StubMachine()
        assert m._ok_to_run_action(5, 'motion', status) is False
        # unchanged from the idle initial (0), i.e. no action was started
        assert not m.running_action_id


def test_cancel_of_running_sets_flag():
    m = StubMachine()
    m._ok_to_run_action(5, 'motion', 'ready')
    assert m._ok_to_run_action(5, 'motion', 'cancelled') is False
    assert m._running_action_cancelled is True


def test_cancel_of_other_action_ignored():
    m = StubMachine()
    m._ok_to_run_action(5, 'motion', 'ready')
    assert m._ok_to_run_action(9, 'motion', 'cancelled') is False
    assert m._running_action_cancelled is False


# ---- user_image ------------------------------------------------------------

def test_user_image_captures_bed_and_emits_lifecycle():
    m = StubMachine()
    m.run_capture({'action_type': 'user_image', 'id': 3, 'status': 'ready'})
    m._action_thread.join(timeout=5)
    assert m.lid_captured == 1
    assert _events(m._q_msg_tx) == ['user_image:starting', 'user_image:completed']
    assert m.running_action_id is None


# ---- per-action settings normalization / plumbing (B4) ---------------------

def test_image_settings_dict_passthrough():
    assert BaseMachine._image_settings({'settings': {'LCfl': 1}}) == {'LCfl': 1}


def test_image_settings_list_takes_first():
    msg = {'settings': [{'HCil': 5}, {'HCil': 0}]}
    assert BaseMachine._image_settings(msg) == {'HCil': 5}


def test_image_settings_absent_or_empty():
    assert BaseMachine._image_settings({}) == {}
    assert BaseMachine._image_settings({'settings': None}) == {}
    assert BaseMachine._image_settings({'settings': []}) == {}


def test_lid_image_records_action_settings():
    m = StubMachine()
    m.run_capture({'action_type': 'lid_image', 'id': 4, 'status': 'ready',
                   'settings': {'LCfl': 1}})
    m._action_thread.join(timeout=5)
    assert m._action_settings == {'LCfl': 1}


def test_head_image_receives_normalized_settings():
    m = StubMachine()
    m.run_capture({'action_type': 'head_image', 'id': 5, 'status': 'ready',
                   'settings': {'HCil': 3, 'HCex': 2047}})
    m._action_thread.join(timeout=5)
    assert m.head_settings == {'HCil': 3, 'HCex': 2047}
    assert m._action_settings == {'HCil': 3, 'HCex': 2047}


def test_head_image_without_settings_passes_none():
    m = StubMachine()
    m.run_capture({'action_type': 'head_image', 'id': 6, 'status': 'ready'})
    m._action_thread.join(timeout=5)
    assert m.head_settings is None


def test_lid_image_receives_its_settings():
    m = StubMachine()
    m.run_capture({'action_type': 'lid_image', 'id': 10, 'status': 'ready',
                   'settings': {'LCfl': 1}})
    m._action_thread.join(timeout=5)
    assert m.lid_settings == {'LCfl': 1}


def test_a_user_image_carries_its_settings_to_the_lid_capture():
    m = StubMachine()
    m.run_capture({'action_type': 'user_image', 'id': 11, 'status': 'ready',
                   'settings': {'LCfl': 0}})
    m._action_thread.join(timeout=5)
    assert m.lid_settings == {'LCfl': 0}
    assert m.lid_lamps == [0]


# ---- the lid lamp ---------------------------------------------------------

def test_the_flash_is_on_when_the_action_asks_for_it():
    assert BaseMachine.lamp_level({'LCfl': 1}) == 132


def test_the_flash_is_off_when_the_action_says_so():
    # LCfl 0 is a request to photograph the bed as it is lit.
    assert BaseMachine.lamp_level({'LCfl': 0}) == 0


def test_an_action_that_says_nothing_still_gets_a_flash():
    # Every lid capture lit the lamp before the key was honored; an action
    # with no opinion must not go dark.
    assert BaseMachine.lamp_level(None) == 132
    assert BaseMachine.lamp_level({}) == 132
    assert BaseMachine.lamp_level({'HCil': 3}) == 132


def test_the_flash_follows_the_lamps_own_brightness(monkeypatch):
    # How bright a flash is comes from LLvl, so a service that moves the
    # lamp moves the flash with it.
    monkeypatch.setattr(basemachine, 'get_machine_setting', lambda k: 200)
    assert BaseMachine.lamp_level({'LCfl': 1}) == 200
    assert BaseMachine.lamp_level({'LCfl': 0}) == 0


def test_lidar_image_uses_first_settings_entry():
    m = StubMachine()
    m.run_capture({'action_type': 'lidar_image', 'id': 7, 'status': 'ready',
                   'settings': [{'HCil': 9}, {'HCil': 0}]})
    m._action_thread.join(timeout=5)
    assert m.head_settings == {'HCil': 9}


# ---- update_check ----------------------------------------------------------

def test_update_check_answers_on_the_actions_own_name(monkeypatch):
    # Events are '<action_type>:<suffix>' from the factory's own table. The
    # earlier 'firmware_update:check:*' events were neither: not an action
    # type, not a suffix the table carries.
    m = StubMachine()
    m._session = None
    probed = []
    monkeypatch.setattr(basemachine, 'firmware_check', lambda s: probed.append(True))
    m.run_update_check({'action_type': 'update_check', 'id': 8, 'status': 'ready'})
    assert probed == [True]
    assert _events(m._q_msg_tx) == ['update_check:completed']


def test_update_check_reports_probe_failure(monkeypatch):
    m = StubMachine()
    m._session = None

    def boom(_s):
        raise RuntimeError('network down')

    monkeypatch.setattr(basemachine, 'firmware_check', boom)
    m.run_update_check({'action_type': 'update_check', 'id': 8, 'status': 'ready'})
    assert _events(m._q_msg_tx) == ['update_check:failed']


def test_update_check_never_hands_off_to_an_updater():
    # The whole of the factory's action is starting a separate updater
    # service, which on this machine would mean installing a factory image
    # over ForgeFIRM. Nothing in the action surface may grow a way to spawn
    # one, so the module that answers it holds no process API at all.
    for name in ('subprocess', 'os'):
        assert not hasattr(basemachine, name), \
            'basemachine imported %s; an update hand-off would start here' % name


# ---- factory_reset ---------------------------------------------------------

def test_factory_reset_refused_as_a_failure_not_a_cancel():
    # A cancel is what the service says when it withdraws an action; a
    # failure is what a machine says when the thing did not happen, which
    # is also what the factory reports when its reset script will not run.
    m = StubMachine()
    m.run_factory_reset({'action_type': 'factory_reset', 'id': 2, 'status': 'ready'})
    assert _events(m._q_msg_tx) == ['factory_reset:failed']


# ---- head_firmware_update --------------------------------------------------

def test_head_firmware_update_refused():
    # The factory pushes firmware into the head microcontroller for this
    # one. Same class of command as a reset, same answer, and an answer
    # rather than the silence the service would otherwise wait through.
    m = StubMachine()
    m.run_head_firmware_update({'action_type': 'head_firmware_update', 'id': 3,
                                'status': 'ready',
                                'head_firmware_filename': 'head-1.2.3.bin'})
    assert _events(m._q_msg_tx) == ['head_firmware_update:failed']


# ---- hunt lifecycle honors a local cancel ---------------------------------

class HuntMachine(StubMachine):
    """A hunt whose child handler is aborted locally (lid, verdict, or a
    service cancel that landed mid-run) must end ':cancelled', not
    ':completed' - the service dead-reckons from that event."""
    def __init__(self, cancel):
        StubMachine.__init__(self)
        self.cancel = cancel

    def _hunt(self, msg):
        if self.cancel:
            self._running_action_cancelled = True


def test_hunt_completed_when_not_cancelled():
    m = HuntMachine(cancel=False)
    m.run_puls({'action_type': 'hunt', 'id': 7, 'status': 'ready'})
    m._action_thread.join(timeout=5)
    assert _events(m._q_msg_tx) == ['hunt:starting', 'hunt:completed']


def test_hunt_cancelled_when_handler_aborts():
    m = HuntMachine(cancel=True)
    m.run_puls({'action_type': 'hunt', 'id': 8, 'status': 'ready'})
    m._action_thread.join(timeout=5)
    assert _events(m._q_msg_tx) == ['hunt:starting', 'hunt:cancelled']
