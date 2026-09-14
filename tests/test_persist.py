"""The NVM record (host test 1): the blind's cover state and travel, in the
12 bytes at the start of microcontroller.nvm, as decided in #13.

NVM is a bytearray here. A blank NVM, and one whose write was cut by power
loss, both read as all zeros on the chip.
"""

import math
import unittest

import cover_state
import persist
import reset_cause


class RecordTest(unittest.TestCase):
    def test_a_record_is_laid_out_as_decided(self):
        # Magic, version 1, open (1), a pad byte, then travel 12.5 and full
        # travel 27.75 as little-endian float32.
        self.assertEqual(persist.encode(cover_state.UP, 12.5, 27.75),
                         bytes((0xB1, 1, 1, 0, 0, 0, 0x48, 0x41, 0, 0, 0xDE, 0x41)))

    def test_the_record_ends_where_the_reset_cause_starts(self):
        self.assertEqual(persist.SIZE, reset_cause.NVM_OFFSET)

    def test_each_cover_state_has_its_code(self):
        codes = {cover_state.UP: 1, cover_state.DOWN: 2, cover_state.STOPPED: 3,
                 cover_state.MOVING_UP: 4, cover_state.MOVING_DOWN: 5}
        for state, code in codes.items():
            with self.subTest(state=state):
                self.assertEqual(persist.encode(state, 1.0, 2.0)[2], code)

    def test_a_record_reads_back_as_written(self):
        for state in (cover_state.UP, cover_state.DOWN, cover_state.STOPPED,
                      cover_state.MOVING_UP, cover_state.MOVING_DOWN):
            with self.subTest(state=state):
                self.assertEqual(persist.decode(persist.encode(state, 12.5, 27.75)),
                                 (state, 12.5, 27.75))

    def test_an_unknown_travel_reads_back_as_unknown(self):
        # Travel is unknown after a stop before any re-anchor, and full
        # travel until it's learned.
        state, travel, full_travel = persist.decode(
            persist.encode(cover_state.STOPPED, persist.NAN, persist.NAN))
        self.assertEqual(state, cover_state.STOPPED)
        self.assertTrue(math.isnan(travel))
        self.assertTrue(math.isnan(full_travel))

    def test_a_full_travel_of_zero_or_less_reads_as_not_learned(self):
        for full_travel in (0.0, -27.75):
            with self.subTest(full_travel=full_travel):
                state, travel, read = persist.decode(
                    persist.encode(cover_state.UP, 12.5, full_travel))
                self.assertEqual((state, travel), (cover_state.UP, 12.5))
                self.assertTrue(math.isnan(read))


class CountingNvm(bytearray):
    """An NVM that keeps each write's key. On the chip every write, even of
    one byte, erases and rewrites all 8 KB of flash."""

    def __init__(self, data):
        super().__init__(data)
        self.writes = []

    def __setitem__(self, key, value):
        self.writes.append(key)
        super().__setitem__(key, value)


# A stored reset cause after the record: MQTT escalation (reset_cause.py).
RESET_CAUSE = bytes((0xB1, 3))


class StoreTest(unittest.TestCase):
    def test_the_record_is_read_at_boot(self):
        # An interrupted opening: its direction stays available.
        nvm = CountingNvm(persist.encode(cover_state.MOVING_UP, 12.5, 27.75) + RESET_CAUSE)

        store = persist.Store(nvm)

        self.assertEqual((store.state, store.travel, store.full_travel),
                         (cover_state.MOVING_UP, 12.5, 27.75))
        self.assertEqual(nvm.writes, [])

    def test_a_save_writes_the_whole_record_once(self):
        nvm = CountingNvm(persist.encode(cover_state.DOWN, 0.0, 27.75) + RESET_CAUSE)
        store = persist.Store(nvm)

        store.save(cover_state.MOVING_UP, persist.NAN, 27.75)

        self.assertEqual(nvm.writes, [slice(0, 12)])
        state, travel, full_travel = persist.decode(nvm[0:12])
        self.assertEqual((state, full_travel), (cover_state.MOVING_UP, 27.75))
        self.assertTrue(math.isnan(travel))
        self.assertEqual(nvm[12:14], RESET_CAUSE)
        self.assertEqual(store.state, cover_state.MOVING_UP)

    def test_only_a_save_that_changes_the_record_writes(self):
        # NaN travel included: CircuitPython doesn't skip identical writes.
        nvm = CountingNvm(persist.encode(cover_state.STOPPED, persist.NAN, persist.NAN) + RESET_CAUSE)
        store = persist.Store(nvm)

        store.save(cover_state.STOPPED, float("nan"), persist.NAN)
        store.save(cover_state.UP, persist.NAN, persist.NAN)
        store.save(cover_state.UP, persist.NAN, persist.NAN)

        self.assertEqual(nvm.writes, [slice(0, 12)])

    def test_a_learned_full_travel_changes_the_record(self):
        nvm = CountingNvm(persist.encode(cover_state.UP, 27.75, 27.75) + RESET_CAUSE)
        store = persist.Store(nvm)

        store.save(cover_state.UP, 27.9, 27.9)

        self.assertEqual(nvm.writes, [slice(0, 12)])
        state, _travel, full_travel = persist.decode(nvm[0:12])
        self.assertEqual(state, cover_state.UP)
        self.assertAlmostEqual(full_travel, 27.9, places=5)
        self.assertEqual(store.full_travel, 27.9)

    def test_a_blank_nvm_gets_a_whole_record(self):
        nvm = CountingNvm(bytes(14))
        store = persist.Store(nvm)
        self.assertEqual(store.state, cover_state.UNKNOWN)

        store.save(cover_state.DOWN, persist.NAN, persist.NAN)

        self.assertEqual(nvm.writes, [slice(0, 12)])
        self.assertEqual(persist.decode(nvm[0:12])[0], cover_state.DOWN)
        self.assertEqual(nvm[12:14], bytes(2))


class BlankRecordTest(unittest.TestCase):
    def assertBlank(self, data):
        # Blank: the cover state is unknown, the travel unknown and the full
        # travel not learned, so it falls back to the settings.toml estimate.
        state, travel, full_travel = persist.decode(data)
        self.assertEqual(state, cover_state.UNKNOWN)
        self.assertTrue(math.isnan(travel))
        self.assertTrue(math.isnan(full_travel))

    def test_all_zeros_reads_as_blank(self):
        # A never-written NVM, or a write cut by power loss.
        self.assertBlank(bytes(12))

    def test_a_wrong_magic_reads_as_blank(self):
        data = bytearray(persist.encode(cover_state.UP, 12.5, 27.75))
        data[0] = 0xB2
        self.assertBlank(data)

    def test_another_layout_version_reads_as_blank(self):
        # No migration: a new layout just discards the record.
        for version in (0, 2):
            with self.subTest(version=version):
                data = bytearray(persist.encode(cover_state.UP, 12.5, 27.75))
                data[1] = version
                self.assertBlank(data)


if __name__ == "__main__":
    unittest.main()
