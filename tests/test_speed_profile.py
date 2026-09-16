"""Host test 7: the lift's speed profile (#57), which replaces the two-step
speed the moves drove at before: a soft start up to cruise, cruise, then a
smoothstep down to the approach speed over the last K revolutions, and the
approach speed from there until the end sensor stops the drive.

The profile is a pure function of the revolutions the drive has turned and
the revolutions left to its end, so every phase is checked here.
"""

import math
import unittest

from speed_profile import (MIN_SPEED, SOFT_START_REVS, SOFT_START_SPEED, UPDATE_MS,
                           Profile, Updates)

CRUISE = 800
APPROACH = 300
K = 3
STEP = 0.05


def profile(cruise=CRUISE, approach=APPROACH, slowdown_revs=K, **kwargs):
    return Profile(cruise, approach, slowdown_revs, **kwargs)


def driving(prof, start_remaining, end_remaining=0.0, moved=0.0, step=STEP):
    """The duties of a drive from start_remaining revolutions left to
    end_remaining, sampled every step revolutions, with moved revolutions
    turned before it."""
    n = max(1, round(abs(start_remaining - end_remaining) / step))
    duties = []
    for i in range(n + 1):
        remaining = start_remaining + (end_remaining - start_remaining) * i / n
        duties.append(prof.duty(moved + start_remaining - remaining, remaining))
    return duties


def non_decreasing(duties):
    return all(b >= a for a, b in zip(duties, duties[1:]))


def non_increasing(duties):
    return all(b <= a for a, b in zip(duties, duties[1:]))


class SoftStartTest(unittest.TestCase):
    def test_a_drive_starts_at_the_soft_start_speed(self):
        self.assertEqual(profile().duty(0.0, 20.0), SOFT_START_SPEED)

    def test_the_soft_start_rises_to_cruise_over_its_revolution(self):
        duties = [profile().duty(moved / 20, 20.0) for moved in range(21)]

        self.assertTrue(non_decreasing(duties), duties)
        self.assertEqual(duties[0], SOFT_START_SPEED)
        self.assertEqual(duties[-1], CRUISE)
        # It really ramps: halfway up it's between the two.
        self.assertTrue(SOFT_START_SPEED < duties[10] < CRUISE, duties[10])

    def test_it_cruises_between_the_soft_start_and_the_slowdown(self):
        self.assertEqual(profile().duty(SOFT_START_REVS, K + 1), CRUISE)
        self.assertEqual(profile().duty(8.0, 10.0), CRUISE)


class SlowdownTest(unittest.TestCase):
    def test_it_slows_from_cruise_to_the_approach_speed_over_the_last_revolutions(self):
        duties = driving(profile(), K, moved=SOFT_START_REVS)

        self.assertTrue(non_increasing(duties), duties)
        self.assertEqual(duties[0], CRUISE)
        self.assertEqual(duties[-1], APPROACH)
        self.assertTrue(APPROACH < duties[len(duties) // 2] < CRUISE)

    def test_it_holds_the_approach_speed_past_the_end_of_the_travel(self):
        # The end sensor, not the travel, ends the drive, and the travel is
        # an estimate until it's learned.
        for remaining in (0.0, -0.5, -3.0):
            with self.subTest(remaining=remaining):
                self.assertEqual(profile().duty(20.0, remaining), APPROACH)

    def test_a_drive_that_starts_within_the_last_revolutions_ramps_down_to_the_approach_speed(self):
        # A mid-height start, after a STOP or an interrupted move: it may
        # pick up speed at first, but never more than the distance left
        # allows, and from there it only slows.
        duties = driving(profile(), 2.0)
        allowed = [profile().duty(K, remaining) for remaining in
                   [2.0 - i * STEP for i in range(len(duties))]]

        self.assertTrue(all(d <= a for d, a in zip(duties, allowed)), duties)
        # Once the soft start is over, a revolution in, it only slows.
        self.assertTrue(non_increasing(duties[len(duties) // 2:]), duties)
        self.assertEqual(duties[-1], APPROACH)


class ClampTest(unittest.TestCase):
    def test_it_never_drives_below_the_minimum_speed(self):
        # An approach speed under the minimum would stall the servo.
        prof = profile(approach=100)
        duties = driving(prof, K + SOFT_START_REVS)

        self.assertTrue(all(duty >= MIN_SPEED for duty in duties), duties)
        self.assertEqual(prof.duty(20.0, 0.0), MIN_SPEED)

    def test_it_never_drives_faster_than_cruise(self):
        duties = driving(profile(cruise=400), K + SOFT_START_REVS)

        self.assertTrue(all(duty <= 400 for duty in duties), duties)

    def test_the_duties_are_whole_numbers(self):
        for duty in driving(profile(), K + SOFT_START_REVS):
            self.assertIsInstance(duty, int)


class DirectionTest(unittest.TestCase):
    def test_a_drive_up_mirrors_one_down(self):
        up = Profile(-CRUISE, -APPROACH, K)
        down = Profile(CRUISE, APPROACH, K)

        for moved in (0.0, 0.5, 2.0, 10.0):
            for remaining in (20.0, K, 1.0, 0.0):
                with self.subTest(moved=moved, remaining=remaining):
                    self.assertEqual(up.duty(moved, remaining),
                                     -down.duty(moved, remaining))


class UnknownTravelTest(unittest.TestCase):
    def test_it_drives_at_the_approach_speed_until_an_end_sensor_re_anchors(self):
        prof = profile(approach=500)
        duties = [prof.duty(moved / 2, math.nan) for moved in range(21)]

        self.assertTrue(all(duty <= 500 for duty in duties), duties)
        self.assertEqual(duties[0], SOFT_START_SPEED)
        self.assertEqual(duties[-1], 500)


class UpdatesTest(unittest.TestCase):
    def test_a_changed_duty_goes_out_once_the_interval_has_passed(self):
        updates = Updates(800, 1000)

        self.assertTrue(updates.due(1000 + UPDATE_MS, 700))

    def test_an_unchanged_duty_isnt_written_again(self):
        updates = Updates(800, 1000)

        self.assertFalse(updates.due(1000 + 10 * UPDATE_MS, 800))

    def test_a_change_within_the_interval_waits_for_it(self):
        updates = Updates(800, 1000)

        self.assertFalse(updates.due(1000 + UPDATE_MS - 1, 700))
        self.assertTrue(updates.due(1000 + UPDATE_MS, 700))

    def test_the_interval_runs_from_the_last_write(self):
        updates = Updates(800, 1000, update_ms=100)

        self.assertTrue(updates.due(1100, 700))
        self.assertFalse(updates.due(1150, 600))
        self.assertTrue(updates.due(1200, 600))


if __name__ == "__main__":
    unittest.main()
