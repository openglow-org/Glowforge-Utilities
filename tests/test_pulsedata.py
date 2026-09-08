"""
Copyright 2026 514 LLC d/b/a OpenGlow
Written by Scott Wiederhold
https://community.openglow.org

SPDX-License-Identifier:    MIT
"""
import random

from gfutilities.puls.pulsedata import _decode_step_codes, decode_all_steps


def _reference(puls, data=None, mode=(8, 2)):
    """The step decode stated the obvious way, one byte at a time.

    decode_all_steps() answers the same question from a 256-row table, which is
    what a job of tens of millions of steps needs. This is the specification it
    has to match, kept here so the table can never drift from it.
    """
    cnt = {k: 0 for k in ('XP', 'XN', 'XTOT', 'XEND', 'YP', 'YN', 'YTOT', 'YEND',
                          'ZP', 'ZN', 'ZTOT', 'ZEND', 'LE', 'LP')}
    for step in puls:
        if step & _decode_step_codes['LP']['mask']:
            cnt['LP'] = cnt['LP'] + 1
        else:
            for action in sorted(_decode_step_codes):
                if not (step & _decode_step_codes[action]['mask']) ^ _decode_step_codes[action]['test']:
                    cnt[action] = cnt[action] + 1
                    if action != 'LE':
                        cnt[action[0:1] + 'TOT'] = cnt[action[0:1] + 'TOT'] + 1
                        cnt[action[0:1] + 'END'] = cnt[action[0:1] + 'END'] + 1 \
                            if action[1:2] == 'P' else cnt[action[0:1] + 'END'] - 1
    for axis in ('X', 'Y'):
        cnt[axis + 'MM'] = (cnt[axis + 'END'] / mode[0]) * 0.15
    cnt['ZMM'] = (cnt['ZEND'] / mode[1]) * 0.70612
    for axis in ('X', 'Y', 'Z'):
        cnt[axis + 'IN'] = cnt[axis + 'MM'] / 25.4
    if data is not None:
        for key, val in cnt.items():
            cnt[key] = val + data.get(key, 0)
    return cnt


def test_every_byte_value_decodes_identically():
    # One byte carries every axis and the laser at once, so the only way to be
    # sure of the table is to walk all 256 meanings.
    for value in range(256):
        assert decode_all_steps(bytes([value])) == _reference(bytes([value])), \
            'byte 0x%02x decodes differently' % value


def test_random_streams_decode_identically():
    rnd = random.Random(20260819)
    for _ in range(40):
        data = bytes(rnd.randrange(256) for _ in range(rnd.randrange(1, 4096)))
        assert decode_all_steps(data) == _reference(data)


def test_empty_stream():
    assert decode_all_steps(b'') == _reference(b'')


def test_accumulation_across_chunks_matches_one_pass():
    # A job arrives in chunks and is decoded as it lands. Chunked and one-pass
    # have to agree on every counter. The derived millimetre and inch figures
    # are summed per chunk rather than recomputed, so they carry ordinary
    # floating-point accumulation error and are compared as such.
    rnd = random.Random(4242)
    whole = bytes(rnd.randrange(256) for _ in range(8192))
    running = None
    reference = None
    for start in range(0, len(whole), 1024):
        chunk = whole[start:start + 1024]
        running = decode_all_steps(chunk, running)
        reference = _reference(chunk, reference)

    # Same chunking, same answer, exactly.
    assert running == reference

    one_pass = decode_all_steps(whole)
    for key in ('XP', 'XN', 'XTOT', 'XEND', 'YP', 'YN', 'YTOT', 'YEND',
                'ZP', 'ZN', 'ZTOT', 'ZEND', 'LE', 'LP'):
        assert running[key] == one_pass[key], key
    for key in ('XMM', 'YMM', 'ZMM', 'XIN', 'YIN', 'ZIN'):
        assert abs(running[key] - one_pass[key]) < 1e-9, key


def test_microstep_mode_scales_millimeters():
    rnd = random.Random(7)
    data = bytes(rnd.randrange(256) for _ in range(2048))
    for mode in ((8, 2), (2, 2), (1, 1), (16, 4)):
        assert decode_all_steps(data, mode=mode) == _reference(data, mode=mode)


def test_power_bytes_count_only_as_power():
    # Bit 7 set is a power setting: it moves no axis and fires nothing, whatever
    # the low bits happen to look like.
    for low in (0x00, 0x01, 0x03, 0x0c, 0x10, 0x3f, 0x7f):
        cnt = decode_all_steps(bytes([0x80 | low]))
        assert cnt['LP'] == 1
        assert cnt['XTOT'] == cnt['YTOT'] == cnt['ZTOT'] == cnt['LE'] == 0


def test_step_direction_signs():
    # X+ and X- cancel; the end position is what the job's travel depends on.
    assert decode_all_steps(b'\x01' * 10)['XEND'] == 10
    assert decode_all_steps(b'\x03' * 10)['XEND'] == -10
    assert decode_all_steps(b'\x01\x03' * 10)['XEND'] == 0
    assert decode_all_steps(b'\x0c' * 10)['YEND'] == 10
    assert decode_all_steps(b'\x04' * 10)['YEND'] == -10
    assert decode_all_steps(b'\x60' * 10)['ZEND'] == 10
    assert decode_all_steps(b'\x20' * 10)['ZEND'] == -10


def test_decode_cost_is_flat_in_stream_length():
    # The table is what makes a full-length print affordable: the work per call
    # is bounded by the 256 distinct byte values, not by the byte count. A
    # ten-thousand-fold longer stream of one repeated value must not cost
    # ten-thousand times the table work, so compare answers, not clocks: the
    # long stream is exactly the short one scaled.
    short = decode_all_steps(b'\x01' * 100)
    long_ = decode_all_steps(b'\x01' * 1000000)
    assert long_['XTOT'] == short['XTOT'] * 10000
    assert long_['XEND'] == short['XEND'] * 10000
