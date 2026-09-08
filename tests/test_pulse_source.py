"""
Copyright 2026 514 LLC d/b/a OpenGlow
Written by Scott Wiederhold
https://community.openglow.org

SPDX-License-Identifier:    MIT
"""
import gzip
import struct

import pytest

from gfutilities.puls.source import PulseSource, PulseSourceError


def _puls(payload: bytes, tags=(('STfr', 10000), ('MCsn', 0), ('PDfm', 0))) -> bytes:
    fields = b''.join(t.encode() + struct.pack('<I', v) for t, v in tags)
    return b'\x80GF1' + struct.pack('<I', 8 + len(fields)) + fields + payload


def _drain(source, count=1000) -> bytes:
    out = b''
    while True:
        chunk = source.read(count)
        if not chunk:
            return out
        out += chunk


def test_plain_body_header_and_payload():
    payload = bytes(range(256)) * 8
    source = PulseSource(_puls(payload))
    assert source.compressed is False
    assert source.header['STfr'] == 10000
    assert source.header_len == 24
    assert _drain(source) == payload
    assert source.exhausted
    assert source.served == len(payload)


def test_gzip_body_is_inflated_on_demand():
    # The service serves the stream compressed, which is what keeps a long
    # job small enough to hold in memory.
    payload = bytes(range(256)) * 400
    body = gzip.compress(_puls(payload))
    source = PulseSource(body)
    assert source.compressed is True
    assert source.body_size < len(payload) // 10
    assert source.header['STfr'] == 10000
    assert _drain(source) == payload
    assert source.exhausted


def test_reads_do_not_inflate_the_whole_job():
    # Asking for a little must decode a little: the decoded window is what
    # would otherwise grow to the size of the print.
    payload = bytes(range(256)) * 4000                  # 1 MB
    source = PulseSource(gzip.compress(_puls(payload)))
    first = source.read(1024)
    assert first == payload[:1024]
    assert len(source._out) < 128 * 1024                # staged, not the job


def test_read_returns_short_at_the_end_then_empty():
    payload = b'abcdef'
    source = PulseSource(_puls(payload))
    assert source.read(4) == b'abcd'
    assert source.exhausted is False
    assert source.read(4) == b'ef'
    assert source.read(4) == b''
    assert source.exhausted


def test_zero_length_read():
    source = PulseSource(_puls(b'xyz'))
    assert source.read(0) == b''
    assert source.read(-1) == b''


def test_empty_payload_is_a_valid_if_useless_job():
    source = PulseSource(_puls(b''))
    assert source.read(16) == b''
    assert source.exhausted


def test_header_raw_round_trips():
    payload = b'\x01\x02\x03'
    body = _puls(payload)
    source = PulseSource(body)
    assert source.header_raw + _drain(source) == body


def test_body_that_is_not_a_puls_file_is_refused():
    with pytest.raises(PulseSourceError):
        PulseSource(b'<html>404</html>')


def test_truncated_header_is_refused():
    body = _puls(b'')[:12]
    with pytest.raises(PulseSourceError):
        PulseSource(body)


def test_impossible_header_length_is_refused():
    body = b'\x80GF1' + struct.pack('<I', 2) + b'junk'
    with pytest.raises(PulseSourceError):
        PulseSource(body)


def test_short_body_is_refused():
    with pytest.raises(PulseSourceError):
        PulseSource(b'\x80GF')


@pytest.mark.parametrize('chunk', [1, 7, 64, 4096, 1 << 20])
def test_any_read_size_yields_the_same_payload(chunk):
    payload = bytes(range(256)) * 50
    for body in (_puls(payload), gzip.compress(_puls(payload))):
        assert _drain(PulseSource(body), chunk) == payload


# -- how long the job is, known before it plays --------------------------

def test_plain_body_knows_its_program_length():
    payload = bytes(range(256)) * 50
    source = PulseSource(_puls(payload))
    assert source.program_size == len(payload)
    assert len(_drain(source)) == source.program_size


def test_compressed_body_knows_its_program_length_without_inflating_it():
    # The whole point: the length comes from the gzip trailer, so a job
    # hours long can be reported against without being decoded first.
    payload = bytes(range(256)) * 4000                  # 1 MB
    source = PulseSource(gzip.compress(_puls(payload)))
    assert source.program_size == len(payload)
    assert source.served == 0                           # nothing read yet
    assert len(_drain(source)) == source.program_size


def test_an_empty_program_is_zero_not_unknown():
    assert PulseSource(_puls(b'')).program_size == 0
    assert PulseSource(gzip.compress(_puls(b''))).program_size == 0


def test_a_length_that_cannot_be_true_is_left_unknown():
    # A trailer that claims less than the header alone occupies is a
    # truncated or multi-member body: better no denominator than a wrong
    # one, which the caller reports around.
    body = bytearray(gzip.compress(_puls(b'abcdef')))
    body[-4:] = struct.pack('<I', 4)
    assert PulseSource(bytes(body)).program_size is None
