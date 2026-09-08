"""
Copyright 2026 514 LLC d/b/a OpenGlow
Written by Scott Wiederhold
https://community.openglow.org

SPDX-License-Identifier:    MIT
"""
import logging
import struct
import zlib

from gfutilities._common import LOGGER_NAME

# Bytes of the body handed to the inflater at a time. Small enough that one
# pass cannot expand into a large allocation, large enough that a job is not
# thousands of calls.
_INPUT_CHUNK = 64 * 1024

_GZIP_MAGIC = b'\x1f\x8b'

# A gzip stream ends with a four-byte uncompressed-length trailer (ISIZE)
# behind a four-byte CRC, and the shortest possible stream is 18 bytes.
_GZIP_MIN = 18

logger = logging.getLogger(LOGGER_NAME)


class PulseSourceError(Exception):
    """The body is not a pulse file this machine can play."""


class PulseSource:
    """A downloaded pulse file, held in memory, handed out as the ring asks.

    The service serves the stream compressed, and it compresses extremely
    well: a three-hour print is a couple of MB of body and a hundred MB of
    steps. Holding the body and inflating on demand is what lets a job be
    longer than the ring and still cost almost nothing to keep, and it keeps
    the job off the eMMC entirely.

    The header is parsed at construction. ``read()`` then returns payload
    bytes, inflating only as far as it is asked to, so the decoded window
    never grows to the size of the job.
    """

    def __init__(self, body: bytes):
        self._body = body
        self._in = 0
        self._decomp = zlib.decompressobj(31) if body[:2] == _GZIP_MAGIC else None
        self.compressed = self._decomp is not None
        self._out = bytearray()
        self._eof = False
        self._served = 0
        self.header = {}
        self.header_len = 0
        self.header_raw = b''
        self.program_size = None
        self._parse_header()
        self._measure_program()

    # -- header ----------------------------------------------------------
    def _parse_header(self) -> None:
        self._fill(8)
        if len(self._out) < 8 or bytes(self._out[1:4]) != b'GF1':
            raise PulseSourceError('received data not a GF puls file')
        total = struct.unpack_from('<I', self._out, 4)[0]
        if total < 8:
            raise PulseSourceError('puls header length %d is impossible' % total)
        self._fill(total)
        if len(self._out) < total:
            raise PulseSourceError('puls file ended before header was complete')
        for pos in range(8, total - 7, 8):
            self.header[bytes(self._out[pos:pos + 4]).decode()] = \
                struct.unpack_from('<I', self._out, pos + 4)[0]
        self.header_len = total - 8
        self.header_raw = bytes(self._out[:total])
        del self._out[:total]

    # -- how long the job is ---------------------------------------------
    def _measure_program(self) -> None:
        """Learn the job's whole length without inflating the whole job.

        Anything reporting progress needs a denominator, and the one number
        that must never be it is a counter that grows: under a live feed the
        kernel's byte total climbs as the ring is topped up, so a report
        divided by it would sit near complete for an entire long print. The
        job's own length does not move. A plain body carries it in its size;
        a compressed one carries it in the gzip ISIZE trailer, exact for any
        job short of 4 GiB, which is a hundred hours of cutting.

        Left as None when the number cannot be trusted, which the caller
        reports around rather than guesses at.
        """
        header_bytes = self.header_len + 8
        if self._decomp is None:
            self.program_size = max(0, len(self._body) - header_bytes)
            return
        if len(self._body) < _GZIP_MIN:
            return
        isize = struct.unpack_from('<I', self._body, len(self._body) - 4)[0]
        if isize < header_bytes:
            # A truncated body, a multi-member stream, or a job past what
            # the trailer can count: not a number to divide by.
            logger.warning('pulse body declares %d inflated bytes against a '
                           '%d byte header; job length unknown',
                           isize, header_bytes)
            return
        self.program_size = isize - header_bytes

    # -- payload ---------------------------------------------------------
    def _fill(self, want: int) -> None:
        """Decode until ``want`` payload bytes are staged, or the body ends."""
        while len(self._out) < want and not self._eof:
            if self._decomp is None:
                piece = self._body[self._in:self._in + _INPUT_CHUNK]
                self._in += len(piece)
                if not piece:
                    self._eof = True
                    break
                self._out += piece
                continue
            src = self._decomp.unconsumed_tail
            if not src:
                src = self._body[self._in:self._in + _INPUT_CHUNK]
                self._in += len(src)
            if not src:
                self._out += self._decomp.flush()
                self._eof = True
                break
            # Bounded: never inflate further ahead than the caller asked for.
            self._out += self._decomp.decompress(src, max(want - len(self._out), 1))
            if self._decomp.eof:
                self._eof = True

    def read(self, count: int) -> bytes:
        """Up to ``count`` payload bytes, or b'' once the job is spent."""
        if count <= 0:
            return b''
        self._fill(count)
        out = bytes(self._out[:count])
        del self._out[:count]
        self._served += len(out)
        return out

    @property
    def exhausted(self) -> bool:
        """True once every payload byte has been handed out."""
        return self._eof and not self._out

    @property
    def served(self) -> int:
        """Payload bytes handed out so far."""
        return self._served

    @property
    def body(self) -> bytes:
        """The body exactly as the service sent it."""
        return bytes(self._body)

    @property
    def body_size(self) -> int:
        """Size of the body as it arrived, compressed or not."""
        return len(self._body)
