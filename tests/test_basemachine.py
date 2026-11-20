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
        # unchanged from the idle initial (None), i.e. no action was started
        assert not m.running_action_id


def test_cancel_of_running_sets_flag():
    m = StubMachine()
    m._ok_to_run_action(5, 'motion', 'ready')
    assert m._ok_to_run_action(5, 'motion', 'cancelled') is False
    assert m._running_action_cancelled is True


def test_a_repeated_ready_of_the_running_action_does_not_cancel_it():
    m = StubMachine()
    m._ok_to_run_action(5, 'motion', 'ready')
    assert m._ok_to_run_action(5, 'motion', 'ready') is False
    assert m._running_action_cancelled is False
    assert m.running_action_id == 5


def test_an_id_that_is_not_a_positive_integer_claims_nothing():
    # A falsy id would read as an idle claim: the next action would be
    # accepted over it, and its cancel undone.
    for bad in (0, False, True, '7', 7.0, -3, None):
        m = StubMachine()
        assert m._ok_to_run_action(bad, 'motion', 'ready') is False, bad
        assert m.running_action_id is None, bad
    m = StubMachine()
    m._ok_to_run_action(5, 'print', 'ready')
    assert m._ok_to_run_action(0, 'motion', 'ready') is False
    assert m.running_action_id == 5


def test_a_settings_report_during_a_print_keeps_the_print_cancelable():
    m = StubMachine()
    m._ok_to_run_action(5, 'print', 'ready')
    m.run_settings_report({'action_type': 'settings', 'id': 6, 'status': 'ready'})
    assert m.running_action_id == 5
    m._ok_to_run_action(5, 'print', 'cancelled')
    assert m._running_action_cancelled is True


def test_an_action_whose_predecessor_will_not_exit_fails_and_frees_the_claim():
    class Stuck:
        def is_alive(self):
            return True

        def join(self, timeout=None):
            pass

    m = StubMachine()
    m._action_thread = Stuck()
    m.run_puls({'action_type': 'motion', 'id': 9, 'status': 'ready'})
    assert _events(m._q_msg_tx) == ['motion:failed']
    assert m.running_action_id is None


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

def test_image_settings_without_a_usable_entry_are_empty():
    # The service sends settings as a dict, as a list of dicts (lidar), or
    # not at all; a null and an empty list both mean no settings, never an
    # error.
    for msg in ({}, {'settings': None}, {'settings': []}):
        assert BaseMachine._image_settings(msg) == {}, msg


def test_lid_image_receives_and_records_its_settings():
    m = StubMachine()
    m.run_capture({'action_type': 'lid_image', 'id': 4, 'status': 'ready',
                   'settings': {'LCfl': 1}})
    m._action_thread.join(timeout=5)
    assert m.lid_settings == {'LCfl': 1}
    assert m._action_settings == {'LCfl': 1}


def test_head_image_receives_normalized_settings():
    m = StubMachine()
    m.run_capture({'action_type': 'head_image', 'id': 5, 'status': 'ready',
                   'settings': {'HCil': 3, 'HCex': 2047}})
    m._action_thread.join(timeout=5)
    assert m.head_settings == {'HCil': 3, 'HCex': 2047}
    assert m._action_settings == {'HCil': 3, 'HCex': 2047}


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
    monkeypatch.setattr(basemachine, 'firmware_check', lambda s: probed.append(True) or False)
    m.run_update_check({'action_type': 'update_check', 'id': 8, 'status': 'ready'})
    assert probed == [True]
    assert _events(m._q_msg_tx) == ['update_check:completed']


def test_update_check_reports_a_probe_that_got_no_answer(monkeypatch):
    # The probe's own request failed (no network, a refusal): the check did
    # not happen, and the service hears so.
    from gfutilities.service import websocket
    from gfutilities.configuration import set_cfg
    set_cfg('SERVICE.SERVER_URL', 'https://app.example')
    m = StubMachine()
    m._session = None
    monkeypatch.setattr(websocket, 'request', lambda *a, **k: False)
    m.run_update_check({'action_type': 'update_check', 'id': 8, 'status': 'ready'})
    assert _events(m._q_msg_tx) == ['update_check:failed']


def test_update_check_reports_probe_failure(monkeypatch):
    m = StubMachine()
    m._session = None

    def boom(_s):
        raise RuntimeError('network down')

    monkeypatch.setattr(basemachine, 'firmware_check', boom)
    m.run_update_check({'action_type': 'update_check', 'id': 8, 'status': 'ready'})
    assert _events(m._q_msg_tx) == ['update_check:failed']


def test_update_check_never_hands_off_to_an_updater(monkeypatch):
    # The whole of the factory's action is starting a separate updater
    # service, which on this machine would mean installing a factory image
    # over ForgeFIRM. A check that finds a newer factory version starts no
    # process and is answered as done.
    import os
    import subprocess
    started = []
    for mod, names in ((subprocess, ('Popen', 'run', 'call')),
                       (os, ('system', 'fork', 'execv', 'execve', 'execvp', 'spawnv', 'posix_spawn'))):
        for name in names:
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name,
                                    lambda *a, _n=name, **k: started.append(_n))
    m = StubMachine()
    m._session = None
    monkeypatch.setattr(basemachine, 'firmware_check',
                        lambda s: {'version': '9.9.9', 'download_url': 'https://x/y.fw'})
    m.run_update_check({'action_type': 'update_check', 'id': 8, 'status': 'ready'})
    assert started == []
    assert _events(m._q_msg_tx) == ['update_check:completed']


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


def test_an_action_that_raises_fails_is_cleaned_up_and_frees_the_claim():
    # The machine's cleanup is what stops motion and locks the laser after a
    # print that raised: the action thread calls it, after the ':failed'
    # event, and the next action can be claimed.
    class CrashMachine(StubMachine):
        def __init__(self):
            StubMachine.__init__(self)
            self.cleanups = []

        def _motion(self, msg):
            raise OSError(5, 'the switch read failed')

        def _action_cleanup(self):
            self.cleanups.append(_events(self._q_msg_tx))

    m = CrashMachine()
    m.run_puls({'action_type': 'print', 'id': 11, 'status': 'ready'})
    m._action_thread.join(timeout=5)
    assert m.cleanups == [['print:starting', 'print:failed']]
    assert m.running_action_id is None
    assert m._ok_to_run_action(12, 'print', 'ready') is True


# ---- the emulator walks a job the way the machine would --------------------

def test_the_emulator_cancels_a_job_the_machine_would_refuse_and_keeps_nothing(tmp_path, monkeypatch):
    from gfutilities.configuration import set_cfg
    from gfutilities.device import emulator as emulator_mod
    set_cfg('EMULATOR.MOTION_DL_DIR', str(tmp_path))
    em = emulator_mod.Emulator()
    set_cfg('EMULATOR.ACTIVE', None)        # the emulator's own marker, for no other case
    em._q_msg_tx = Queue()
    monkeypatch.setattr(emulator_mod, 'load_motion', lambda s, url, out: False)
    em.motion({'action_type': 'print', 'id': 11, 'motion_url': 'https://x/y'})
    assert _events(em._q_msg_tx) == ['print:starting', 'print:cancelled']
    assert list(tmp_path.iterdir()) == []
    em._running_action_cancelled = False
    em.hunt({'action_type': 'hunt', 'id': 12, 'motion_url': 'https://x/y'})
    assert _events(em._q_msg_tx) == ['hunt:starting', 'hunt:cancelled']


def test_the_emulator_keeps_a_jobs_header_and_statistics_never_its_program(tmp_path, monkeypatch):
    import builtins
    import struct
    from gfutilities.configuration import set_cfg
    from gfutilities.device import emulator as emulator_mod
    from gfutilities.service import websocket
    set_cfg('EMULATOR.MOTION_DL_DIR', str(tmp_path))
    fields = b'STfr' + struct.pack('<I', 10000) + b'MCsn' + struct.pack('<I', 0) \
        + b'PDfm' + struct.pack('<I', 0)
    data = b'\x00GF1' + struct.pack('<I', 8 + len(fields)) + fields + b'\x01' * 4096

    class Resp:
        headers = {}

        def iter_content(self, chunk_size=1):
            yield data

        def close(self):
            pass
    monkeypatch.setattr(websocket, 'request', lambda *a, **k: Resp())
    written = []
    real_open = builtins.open

    def recording_open(path, mode='r', *a, **k):
        if any(c in mode for c in 'wax+'):
            written.append(str(path))
        return real_open(path, mode, *a, **k)
    monkeypatch.setattr(builtins, 'open', recording_open)
    em = emulator_mod.Emulator()
    set_cfg('EMULATOR.ACTIVE', None)
    em._q_msg_tx = Queue()
    em.motion({'action_type': 'motion', 'id': 13, 'motion_url': 'https://x/y'})
    assert _events(em._q_msg_tx) == ['motion:starting', 'motion:completed']
    assert len(written) == 1 and written[0].endswith('.info')
    assert [p.suffix for p in tmp_path.iterdir()] == ['.info']
