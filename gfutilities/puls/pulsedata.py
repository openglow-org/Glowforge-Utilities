"""
Copyright 2020 514 LLC d/b/a OpenGlow
Written by Scott Wiederhold
https://community.openglow.org

SPDX-License-Identifier:    MIT
"""
from collections import Counter

_decode_step_codes = {
    'LE': {'mask': 0b00010000, 'test': 0b00010000},  # Laser Enable (ON)
    'LP': {'mask': 0b10000000, 'test': 0b01111111},  # Laser Power Setting
    'XP': {'mask': 0b00000011, 'test': 0b00000001},  # X+ Step
    'XN': {'mask': 0b00000011, 'test': 0b00000011},  # X- Step
    'YN': {'mask': 0b00001100, 'test': 0b00000100},  # Y- Step
    'YP': {'mask': 0b00001100, 'test': 0b00001100},  # Y+ Step
    # Z sign hardware-verified: bit 6 (Z_DIR) SET moves the lens UP, away
    # from the bed = Z+.
    'ZP': {'mask': 0b01100000, 'test': 0b01100000},  # Z+ Step (Z_DIR set)
    'ZN': {'mask': 0b01100000, 'test': 0b00100000},  # Z- Step
}

_SPEED = 1000

# The counters decode_all_steps() reports, in report order.
_COUNT_KEYS = ('XP', 'XN', 'XTOT', 'XEND', 'YP', 'YN', 'YTOT', 'YEND',
               'ZP', 'ZN', 'ZTOT', 'ZEND', 'LE', 'LP')
_COUNT_INDEX = {key: i for i, key in enumerate(_COUNT_KEYS)}


def _build_step_deltas() -> tuple:
    """What one pulse byte contributes to each counter, for all 256 values.

    A step byte carries every axis and the laser at once, so its meaning is a
    pure function of its value: precompute the contribution once and a job of
    any length costs one histogram plus 256 table rows. A print runs to tens of
    millions of steps, which is more than a per-byte decode can carry.

    Each row is the tuple of (counter index, delta) pairs that are non-zero.
    """
    table = []
    for value in range(256):
        deltas = [0] * len(_COUNT_KEYS)
        if value & _decode_step_codes['LP']['mask']:
            # Power setting: the byte says nothing about motion or the laser.
            deltas[_COUNT_INDEX['LP']] = 1
        else:
            for action, code in _decode_step_codes.items():
                if (value & code['mask']) != code['test']:
                    continue
                deltas[_COUNT_INDEX[action]] += 1
                if action == 'LE':
                    continue
                axis = action[0:1]
                deltas[_COUNT_INDEX[axis + 'TOT']] += 1
                deltas[_COUNT_INDEX[axis + 'END']] += 1 if action[1:2] == 'P' else -1
        table.append(tuple((i, d) for i, d in enumerate(deltas) if d))
    return tuple(table)


_STEP_DELTAS = _build_step_deltas()


def decode_all_steps(puls: bytes, data: dict = None, mode: tuple = (8, 2)) -> dict:
    """Step and laser statistics for a run of pulse bytes.

    ``data`` accumulates a previous result, so a job can be decoded chunk by
    chunk as it arrives. ``mode`` is the (XY, Z) microstep divisor the job runs
    at, which is what turns step counts into millimeters.
    """
    totals = [0] * len(_COUNT_KEYS)
    for value, count in Counter(puls).items():
        for index, delta in _STEP_DELTAS[value]:
            totals[index] += delta * count

    cnt = dict(zip(_COUNT_KEYS, totals))

    for axis in ('X', 'Y'):
        cnt[axis + 'MM'] = (cnt[axis + 'END'] / mode[0]) * 0.15
    cnt['ZMM'] = (cnt['ZEND'] / mode[1]) * 0.70612

    for axis in ('X', 'Y', 'Z'):
        cnt[axis + 'IN'] = cnt[axis + 'MM'] / 25.4

    if data is not None:
        for key, val in cnt.items():
            cnt[key] = val + data.get(key, 0)
    return cnt


def generate_linear_puls(x: int, y: int, outfile) -> None:
    expected_count = abs(x) if abs(x) >= abs(y) else abs(y)

    max_speed = 5
    min_speed = 55
    acc = 10
    acc_dist = int((min_speed - max_speed) / acc)
    acc_dist = acc_dist if acc_dist < (expected_count / 2) else int(expected_count / 2)

    steps = 0
    d = min_speed

    # outfile may be a path or an already-open binary file object (the
    # job-held exclusive pulse-device fd; the caller owns and closes it).
    from contextlib import nullcontext
    with (nullcontext(outfile) if hasattr(outfile, 'write') else open(outfile, 'bw')) as f:
        for xs, ys in _step_gen(x, y):
            steps += 1
            s = 0
            if xs != 0:
                s |= 0b00000001 if xs > 0 else 0b00000011
            if ys != 0:
                s |= 0b00001100 if ys > 0 else 0b00000100
            f.write(bytes([s]))

            if steps <= acc_dist:
                # Accelerating
                d = d - acc if d > max_speed - acc else max_speed
            elif steps >= (expected_count - acc_dist):
                # Decelerating
                d = d + acc if d < min_speed + acc else min_speed
            f.write('\0'.encode() * d)


def _step_gen(x: int, y: int) -> tuple:
    xd = 1 if x >= 0 else -1
    yd = 1 if y >= 0 else -1
    xt = abs(x)
    yt = abs(y)

    if xt >= yt:
        maj_target = xt
        min_target = yt
        maj_dir = xd
        min_dir = yd
    else:
        maj_target = yt
        min_target = xt
        maj_dir = yd
        min_dir = xd
    if min_target > 0:
        maj_per_min = round(maj_target / min_target, 4)
    else:
        maj_per_min = maj_target

    maj_cnt = 0
    min_cnt = 0
    while maj_cnt < maj_target or min_cnt < min_target:
        if maj_cnt < maj_target:
            maj_cnt += 1
            if maj_cnt % maj_per_min < 1 and min_cnt < min_target:
                min_out = min_dir
                min_cnt += 1
            else:
                min_out = 0
        else:
            maj_dir = 0
            min_out = min_dir
            min_cnt += 1

        yield maj_dir if xt >= yt else min_out, min_out if xt >= yt else maj_dir
