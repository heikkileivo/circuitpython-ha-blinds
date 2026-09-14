"""The cover state a move of the blind leaves, from how the move ended (#46).
An open or close ends open or closed only when it reached the end sensor.
"""

import unittest

import cover_state
from cover_state import after_move


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


if __name__ == "__main__":
    unittest.main()
