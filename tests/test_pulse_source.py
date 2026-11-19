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


def test_reads_do_not_inflate_the_whole_job():
    # Asking for a little must decode a little: the decoded window is what
    # would otherwise grow to the size of the print.
    payload = bytes(range(256)) * 4000                  # 1 MB
    source = PulseSource(gzip.compress(_puls(payload)))
    first = source.read(1024)
    assert first == payload[:1024]
    assert len(source._out) < 128 * 1024                # staged, not the job


def _count_inflates(monkeypatch, inflated):
    """Each inflate call's output size, appended to ``inflated``."""
    from gfutilities.puls import source as source_mod
    real = source_mod.zlib.decompressobj

    class Counting:
        def __init__(self, *a):
            self._d = real(*a)

        def __getattr__(self, name):
            return getattr(self._d, name)

        def decompress(self, data, max_length=0):
            out = self._d.decompress(data, max_length)
            inflated.append(len(out))
            return out
    monkeypatch.setattr(source_mod.zlib, 'decompressobj', Counting)


def test_one_inflate_call_produces_at_most_1_mib(monkeypatch):
    inflated = []
    _count_inflates(monkeypatch, inflated)
    want = 16 * 1024 * 1024
    source = PulseSource(gzip.compress(_puls(bytes(want))))
    assert len(source.read(want)) == want
    assert max(inflated) <= 1024 * 1024


def test_read_returns_short_at_the_end_then_empty():
    payload = b'abcdef'
    source = PulseSource(_puls(payload))
    assert source.read(4) == b'abcd'
    assert source.read(4) == b'ef'
    assert source.read(4) == b''


def test_zero_length_read():
    source = PulseSource(_puls(b'xyz'))
    assert source.read(0) == b''
    assert source.read(-1) == b''


def test_empty_payload_is_a_valid_if_useless_job():
    source = PulseSource(_puls(b''))
    assert source.read(16) == b''


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


def test_a_header_of_64_kib_is_taken():
    # 8 bytes of magic and length, then 8191 tags of 8 bytes: 65536 in all.
    tags = (('STfr', 10000),) + (('ZZzz', 0),) * 8190
    for body in (_puls(b'xyz', tags), gzip.compress(_puls(b'xyz', tags))):
        try:
            source = PulseSource(body)
        except PulseSourceError as e:
            raise AssertionError('a 64 KiB header was refused: %s' % e)
        assert source.header_len + 8 == 65536
        assert _drain(source) == b'xyz'


def test_a_header_past_the_bound_is_refused_before_it_is_inflated(monkeypatch):
    # A header length is the service's word: one past the bound is refused
    # with only its first 8 bytes inflated, never the length it declares.
    inflated = []
    _count_inflates(monkeypatch, inflated)
    # 65537: one byte past the 64 KiB bound.
    body = gzip.compress(b'\x80GF1' + struct.pack('<I', 65537) + bytes(1 << 20))
    with pytest.raises(PulseSourceError):
        PulseSource(body)
    assert sum(inflated) <= 8                  # the magic and the length field only


def test_a_header_tag_that_is_not_text_is_refused():
    fields = b'STfr' + struct.pack('<I', 10000) + b'\xff\xfeAB' + struct.pack('<I', 1)
    with pytest.raises(Exception) as refused:
        PulseSource(b'\x80GF1' + struct.pack('<I', 8 + len(fields)) + fields)
    assert isinstance(refused.value, PulseSourceError)


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
    assert len(source._out) < 128 * 1024                # nothing inflated past the header
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
