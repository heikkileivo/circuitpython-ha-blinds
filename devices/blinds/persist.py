"""The blind's record in flash NVM: its cover state and travel, which survive
every reset, so the boot can work out where the blind is (#13, #49).

One record, the 12 bytes at offset 0 of microcontroller.nvm:

    magic 0xB1, layout version 1, cover state, a pad byte,
    travel (float32, NaN = unknown), full travel (float32, NaN = not learned)

Encode and decode are pure, so the host tests run them.
"""

import struct

import cover_state

MAGIC = 0xB1
VERSION = 1
FORMAT = "<BBBxff"
SIZE = struct.calcsize(FORMAT)

# An unknown travel, and a full travel not learned yet.
NAN = float("nan")

# The code for each cover state in the record's third byte. 0 is none: the
# cover state is unknown.
_CODES = {cover_state.UP: 1,
          cover_state.DOWN: 2,
          cover_state.STOPPED: 3,
          cover_state.MOVING_UP: 4,
          cover_state.MOVING_DOWN: 5}
_STATES = {code: state for state, code in _CODES.items()}


def encode(state, travel, full_travel):
    """The record's 12 bytes, for a cover state and the travel and full
    travel in revolutions."""
    return struct.pack(FORMAT, MAGIC, VERSION, _CODES[state], travel, full_travel)


def decode(data):
    """The cover state, travel and full travel in a record's 12 bytes. A
    record with the wrong magic or another layout version reads as blank:
    the cover state unknown, the travel unknown and the full travel not
    learned. So do all zeros: a blank NVM, or a write cut by power loss. A
    full travel of 0 or less is rejected: not learned."""
    magic, version, code, travel, full_travel = struct.unpack(FORMAT, data)
    if magic != MAGIC or version != VERSION:
        return cover_state.UNKNOWN, NAN, NAN
    if not full_travel > 0:
        full_travel = NAN
    return _STATES.get(code, cover_state.UNKNOWN), travel, full_travel


class Store:
    """The record in NVM, read once at boot and kept as a RAM copy.

    Each NVM write, even of one byte, erases and rewrites all 8 KB of flash
    and blocks for 50-100 ms, so a save writes the whole record in one slice
    assignment, and only if its bytes changed. Save only while the lift servo
    is stopped.
    """

    def __init__(self, nvm):
        self._nvm = nvm
        self._data = bytes(nvm[0:SIZE])
        self.state, self.travel, self.full_travel = decode(self._data)

    def save(self, state, travel):
        """Store a cover state and travel, keeping the full travel."""
        data = encode(state, travel, self.full_travel)
        if data != self._data:
            self._nvm[0:SIZE] = data
            self._data = data
        self.state, self.travel = state, travel
