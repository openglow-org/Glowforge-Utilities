"""
Copyright 2026 514 LLC d/b/a OpenGlow
Written by Scott Wiederhold
https://community.openglow.org

SPDX-License-Identifier:    MIT
"""
from gfutilities import configuration
from gfutilities.configuration import get_cfg, parse, set_cfg


def test_a_configuration_file_sets_no_flag_the_program_owns(tmp_path):
    # These flags are the running machine's own state: a file that names
    # one would skip the service's homing, every hunt's motion, or a print.
    cfg = tmp_path / 'gfhome.conf'
    cfg.write_text('[EMULATOR]\nactive: True\nbypass_homing: True\n'
                   '[SETTINGS]\nset: True\n'
                   '[HOMING]\nhunt_ack_only: True\n'
                   '[CAPTURE]\nheader_path: /tmp/h.json\n')
    owned = ('EMULATOR.ACTIVE', 'SETTINGS.SET', 'HOMING.HUNT_ACK_ONLY', 'CAPTURE.HEADER_PATH')
    for key in owned:
        set_cfg(key, None)
    parse(str(cfg))
    for key in owned:
        assert get_cfg(key) is None, key
    assert get_cfg('EMULATOR.BYPASS_HOMING') is True        # an option, read as one
    set_cfg('EMULATOR.BYPASS_HOMING', None)


def test_true_and_false_read_as_booleans_in_any_case(tmp_path):
    cfg = tmp_path / 'gfhome.conf'
    cfg.write_text('[LOGGING]\nsave_sent_images: false\na: TRUE\nb: True\nc: no\n')
    parse(str(cfg))
    assert get_cfg('LOGGING.SAVE_SENT_IMAGES') is False
    assert get_cfg('LOGGING.A') is True and get_cfg('LOGGING.B') is True
    assert get_cfg('LOGGING.C') == 'no'
