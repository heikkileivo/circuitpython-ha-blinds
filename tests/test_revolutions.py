"""Revolution counting from the lift's servo angle.

The servo angle wraps once per turn: around the pot's dead zone it reads
1000-1021, then 0, and sometimes a stray mid value such as 532 or 275
(the bench, #21 section 5). Opening, the angle counts up; closing, down.
"""

import unittest

from revolutions import RevolutionCounter


def count(counting_up, angles):
    counter = RevolutionCounter(counting_up)
    for angle in angles:
        counter.feed(angle)
    return counter.count


class RevolutionCounterTest(unittest.TestCase):
    def test_a_stray_mid_value_at_the_wrap_counts_once_opening(self):
        angles = [900, 960, 1010, 1021, 532, 20, 80]

        self.assertEqual(count(True, angles), 1)

    def test_a_stray_mid_value_at_the_wrap_counts_once_closing(self):
        angles = [120, 60, 5, 275, 1015, 950]

        self.assertEqual(count(False, angles), 1)

    def test_the_first_angle_of_a_move_is_no_revolution(self):
        # A move can start anywhere in a turn, just past the wrap included.
        self.assertEqual(count(True, [20, 80]), 0)
        self.assertEqual(count(False, [1000, 940]), 0)

    def test_every_turn_counts_once_despite_strays(self):
        angles = [300, 600, 900, 1021, 532, 20,
                  300, 600, 900, 1010, 30,
                  300, 600, 900, 1021, 275, 0, 60]

        self.assertEqual(count(True, angles), 3)


if __name__ == "__main__":
    unittest.main()
