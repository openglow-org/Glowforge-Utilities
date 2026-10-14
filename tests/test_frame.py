"""
Copyright 2026 514 LLC d/b/a OpenGlow
Written by Scott Wiederhold
https://community.openglow.org

SPDX-License-Identifier:    MIT
"""
import json
from queue import Queue

from gfutilities.service import websocket
from gfutilities.service.websocket import send_wss_progress, split_frame


def test_a_frame_splits_into_its_objects():
    # One object, several (one per line), blank lines dropped, an empty
    # frame, and the whitespace around an object trimmed.
    for frame, want in (('{"a":1}', ['{"a":1}']),
                        ('{"id":32,"type":"log"}\n{"id":33,"type":"event"}',
                         ['{"id":32,"type":"log"}', '{"id":33,"type":"event"}']),
                        ('\n{"a":1}\n\n  \n{"b":2}\n', ['{"a":1}', '{"b":2}']),
                        ('', []),
                        ('\n  \n', []),
                        ('   {"a":1}   ', ['{"a":1}'])):
        assert split_frame(frame) == want, frame


# -- the progress frame ---------------------------------------------------

def _sent(q: Queue) -> dict:
    raw = q.get_nowait()
    assert raw.endswith('\n')            # one frame per line, as the pump sends
    return json.loads(raw)


def test_progress_frame_carries_the_run_and_its_values():
    q = Queue()
    send_wss_progress(q, 1577564802, 'print:progress', 994, total=33291208,
                      values={'CCst': 1, 'CCbp': 1009})
    frame = _sent(q)
    assert frame['type'] == 'progress'
    assert frame['version'] == 1
    assert frame['action_id'] == 1577564802
    assert frame['progress'] == 'print:progress'
    assert frame['current'] == 994
    assert frame['units'] == 'steps'
    assert frame['total'] == 33291208
    assert frame['settings'] == {'values': {'CCbp': 1009, 'CCst': 1}}
    assert isinstance(frame['id'], int) and isinstance(frame['timestamp'], int)


def test_a_transfer_reports_bytes_and_no_total():
    # What the factory sends as a phase starts: a position, no denominator.
    q = Queue()
    send_wss_progress(q, 42, 'print:download', 0, units='bytes')
    frame = _sent(q)
    assert frame['units'] == 'bytes'
    assert 'total' not in frame
    assert 'settings' not in frame


def test_progress_without_an_action_leaves_the_field_out():
    q = Queue()
    send_wss_progress(q, None, 'print:progress', 7)
    assert 'action_id' not in _sent(q)


def test_progress_is_dropped_rather_than_queued_behind_a_dead_socket():
    q = Queue()
    for _ in range(websocket.TX_QUEUE_MAX):
        q.put('{}\n')
    send_wss_progress(q, 42, 'print:progress', 7)
    assert q.qsize() == websocket.TX_QUEUE_MAX
