"""The cover state a move of the blind leaves, from how the move ended (#46),
and the cover state at boot, from the end sensors and NVM (host test 2, #49).
An open or close ends open or closed only when it reached the end sensor.
"""

import unittest

import cover_state
from cover_state import after_move, at_boot


class AfterMoveTest(unittest.TestCase):
    def test_a_move_that_reached_the_end_sensor_is_at_that_end(self):
        self.assertEqual(after_move(cover_state.REACHED, cover_state.UP), cover_state.UP)
        self.assertEqual(after_move(cover_state.REACHED, cover_state.DOWN), cover_state.DOWN)

    def test_a_move_that_stopped_short_of_the_end_sensor_is_stopped(self):
        results = (cover_state.STALLED, cover_state.TRAVEL_LIMIT, cover_state.TIMED_OUT,
                   cover_state.START_FAILED, cover_state.ERROR)
        for result in results:
            for end in (cover_state.UP, cover_state.DOWN):
                with self.subTest(result=result, end=end):
                    self.assertEqual(after_move(result, end), cover_state.STOPPED)

    def test_a_move_whose_stop_wasnt_confirmed_is_unknown(self):
        for end in (cover_state.UP, cover_state.DOWN):
            with self.subTest(end=end):
                self.assertEqual(after_move(cover_state.STOP_FAILED, end), cover_state.UNKNOWN)


# Every cover state the NVM record can hold. UNKNOWN is a blank or invalid
# record.
STORED = (cover_state.UNKNOWN, cover_state.STOPPED, cover_state.DOWN, cover_state.UP,
          cover_state.MOVING_UP, cover_state.MOVING_DOWN)


class AtBootTest(unittest.TestCase):
    """The cover state at boot (host test 2), from the two end sensors and
    the cover state stored in NVM (#49)."""

    def test_an_active_end_sensor_wins_over_the_stored_state(self):
        for stored in STORED:
            with self.subTest(stored=stored):
                self.assertEqual(at_boot(up_active=False, down_active=True, stored=stored),
                                 cover_state.DOWN)
                self.assertEqual(at_boot(up_active=True, down_active=False, stored=stored),
                                 cover_state.UP)

    def test_a_settled_state_holds_with_neither_end_sensor_active(self):
        # For example, the blind settled off the up end sensor once the lift
        # went limp.
        for stored in (cover_state.UP, cover_state.DOWN, cover_state.STOPPED):
            with self.subTest(stored=stored):
                self.assertEqual(at_boot(up_active=False, down_active=False, stored=stored),
                                 stored)

    def test_an_interrupted_move_is_unknown(self):
        # Stored opening or closing: the blind lost power or reset mid-move.
        for stored in (cover_state.MOVING_UP, cover_state.MOVING_DOWN):
            with self.subTest(stored=stored):
                self.assertEqual(at_boot(up_active=False, down_active=False, stored=stored),
                                 cover_state.UNKNOWN)

    def test_a_blank_record_is_unknown_never_closed(self):
        self.assertEqual(at_boot(up_active=False, down_active=False, stored=cover_state.UNKNOWN),
                         cover_state.UNKNOWN)

    def test_both_end_sensors_active_is_unknown(self):
        # They can't both be: one is faulty, so neither can be trusted.
        for stored in STORED:
            with self.subTest(stored=stored):
                self.assertEqual(at_boot(up_active=True, down_active=True, stored=stored),
                                 cover_state.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
