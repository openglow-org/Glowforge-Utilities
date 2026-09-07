"""
(C) Copyright 2026
Scott Wiederhold, s.e.wiederhold@gmail.com
https://community.openglow.org

SPDX-License-Identifier:    MIT
"""
from gfutilities.service.websocket import check_puls_header


# A header shaped like the ones the service actually sends: a real step
# frequency, an unlocked serial and format 0.
def _header(**over):
    h = {'STfr': 28160, 'MCsn': 0, 'PDfm': 0, 'PDct': 5}
    h.update(over)
    return h


def test_typical_service_header_is_accepted():
    assert check_puls_header(_header(), 123456) is None


def test_unlocked_serial_is_accepted():
    # Zero means the service did not lock the job to a machine. Every header
    # captured from the live service so far carries zero, so refusing it would
    # refuse every job.
    assert check_puls_header(_header(MCsn=0), 123456) is None


def test_serial_locked_to_this_machine_is_accepted():
    assert check_puls_header(_header(MCsn=123456), 123456) is None
    # The serial arrives as a string from config in the running app.
    assert check_puls_header(_header(MCsn=123456), '123456') is None


def test_serial_locked_to_another_machine_is_refused():
    reason = check_puls_header(_header(MCsn=999999), 123456)
    assert reason is not None
    assert 'different machine' in reason


def test_no_serial_leaks_into_the_reason():
    # Log lines get exported. Neither machine's serial belongs in one.
    reason = check_puls_header(_header(MCsn=999999), 123456)
    assert '999999' not in reason and '123456' not in reason


def test_serial_locked_but_machine_serial_unusable_is_refused():
    for serial in (None, '', 'not-a-number'):
        reason = check_puls_header(_header(MCsn=123456), serial)
        assert reason is not None and 'usable serial' in reason


def test_missing_serial_field_is_refused():
    h = _header()
    del h['MCsn']
    reason = check_puls_header(h, 123456)
    assert reason is not None and 'MCsn' in reason


def test_missing_format_field_is_refused():
    h = _header()
    del h['PDfm']
    reason = check_puls_header(h, 123456)
    assert reason is not None and 'PDfm' in reason


def test_unknown_format_is_refused():
    # Only format 0 matches what the step decoder and the kernel ring assume.
    reason = check_puls_header(_header(PDfm=1), 123456)
    assert reason is not None and 'PDfm' in reason


def test_unusable_step_frequency_is_refused():
    for stfr in (None, 0, -1, 'fast'):
        h = _header()
        if stfr is None:
            del h['STfr']
        else:
            h['STfr'] = stfr
        reason = check_puls_header(h, 123456)
        assert reason is not None and 'STfr' in reason


def test_the_service_microstep_mode_is_accepted():
    # Every header the service has sent carries 8 on both axes.
    assert check_puls_header(_header(XSmm=8, YSmm=8), 123456) is None


def test_a_header_without_microstep_tags_is_accepted():
    # The client sets the drivers to 8 at start; a header that says nothing
    # runs at that.
    assert check_puls_header(_header(), 123456) is None


def test_another_microstep_mode_is_refused():
    # A finer mode is a stream this machine has never run in cloud mode:
    # refused before the ring, and the log line is the report that starts
    # the work on it.
    for tag, mode in (('XSmm', 16), ('YSmm', 32), ('XSmm', 1), ('YSmm', 4)):
        reason = check_puls_header(_header(**{tag: mode}), 123456)
        assert reason is not None and tag in reason and '8' in reason, (tag, mode, reason)


def test_refusal_happens_in_a_fixed_order():
    # A header wrong in several ways reports the step frequency first: it is
    # the field the run-time math needs, and the one already checked before
    # this change.
    h = _header(STfr=0, PDfm=7)
    del h['MCsn']
    assert 'STfr' in check_puls_header(h, 123456)
