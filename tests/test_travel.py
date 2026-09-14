"""Host test 6, travel part: the blind's travel, from the lift's servo-angle
wraps, and its full travel, learned from a run from one end sensor to the
other (#50).

The servo angle wraps once per turn. Around the pot's dead zone it reads
1000-1022, then 0, and sometimes a stray value: 532 or 275 on the bench (#21
section 5), 662 before a wrap going up and 797 or 973 right after one going
down on Middle's lift (#78, tests/data). Opening, the angle counts up;
closing, down.
"""

import math
import unittest

import cover_state
import travel
from persist import NAN
from travel import COUNTS_PER_TURN, HIGH_START, LOW_END, Tracker, WrapCounter

from .lift_samples import MEASURED_EVERY_MS, as_sampled, measured_phases

SAMPLE_MS = 50
# window_height 1800 mm over a 20 mm spindle, the settings.toml defaults.
ESTIMATE = 1800 / (20 * math.pi)
FULL = 27.5
MARGIN = 2
# The turns in each measured phase, down negative.
MEASURED_TURNS = {"up300": 3, "up500": 3, "up800": 2,
                  "down800": -2, "down500": -3, "down300": -3}


def turning(start, end, step=0.05):
    """The servo angles of the lift turning from start to end revolutions,
    one every step revolutions. The angle reads 0-1022; over the rest of each
    turn, the dead zone, it holds at 1022."""
    n = max(1, round(abs(end - start) / step))
    return [min(1022, int((start + (end - start) * i / n) % 1 * COUNTS_PER_TURN))
            for i in range(n + 1)]


def revs(counting_up, angles):
    counter = WrapCounter(counting_up)
    for angle in angles:
        counter.feed(angle)
    return counter.revs


def move(tracker, start, end, from_end=False):
    """A move from start to end revolutions, as far as the tracker sees it."""
    tracker.begin(end > start, from_end)
    for angle in turning(start, end):
        tracker.feed(angle)


class WrapCounterTest(unittest.TestCase):
    def test_a_stray_before_the_wrap_is_held_opening(self):
        counter = WrapCounter(True)
        for angle in (900, 960, 1010, 1021):
            counter.feed(angle)
        before = counter.revs

        counter.feed(532)
        self.assertEqual(counter.revs, before)
        for angle in (20, 80):
            counter.feed(angle)
        self.assertAlmostEqual(counter.revs, 1 + (80 - 900) / COUNTS_PER_TURN)

    def test_a_stray_before_the_wrap_is_held_closing(self):
        # 275 is in the first third of the turn, which closing wraps from.
        counter = WrapCounter(False)
        for angle in (120, 60, 5):
            counter.feed(angle)
        before = counter.revs

        counter.feed(275)
        self.assertEqual(counter.revs, before)
        for angle in (1015, 950):
            counter.feed(angle)
        self.assertAlmostEqual(counter.revs, -1 + (950 - 120) / COUNTS_PER_TURN)

    def test_the_first_angle_of_a_move_is_its_start(self):
        self.assertEqual(revs(True, [20]), 0.0)
        self.assertEqual(revs(False, [1000]), 0.0)

    def test_a_move_that_starts_in_the_dead_zone_counts_its_wrap(self):
        # The measured down800 phase started at rest at 0.
        self.assertAlmostEqual(revs(True, [1022, 1022, 0, 5]),
                               1 + (5 - 1022) / COUNTS_PER_TURN)
        self.assertAlmostEqual(revs(False, [0, 0, 1011]),
                               -1 + (1011 - 0) / COUNTS_PER_TURN)

    def test_a_late_sample_past_the_wrap_still_counts(self):
        # A sample about 220 ms late at full speed lands some 320 counts on.
        self.assertAlmostEqual(revs(True, [700, 1000, 300, 600]),
                               1 + (600 - 700) / COUNTS_PER_TURN)
        self.assertAlmostEqual(revs(False, [320, 30, 730, 430]),
                               -1 + (430 - 320) / COUNTS_PER_TURN)

    def test_every_turn_counts_once_despite_strays(self):
        angles = [300, 600, 900, 1021, 532, 20,
                  300, 600, 900, 1010, 30,
                  300, 600, 900, 1021, 275, 0, 60]

        self.assertAlmostEqual(revs(True, angles), 3 + (60 - 300) / COUNTS_PER_TURN)

    def test_the_measured_turns_count_from_every_sampling_offset(self):
        # Every phase, sampled every 50 ms from each 10 ms offset, so every
        # wrap and stray is seen as the firmware might see it. The phase's
        # last sample stands for the read at rest after the stop.
        for name, (duty, samples) in measured_phases().items():
            for offset in range(0, SAMPLE_MS, MEASURED_EVERY_MS):
                with self.subTest(phase=name, offset=offset):
                    angles = [angle for _, angle, _, _ in
                              as_sampled(duty, samples, offset, SAMPLE_MS)]
                    if angles[-1] != samples[-1][1]:
                        angles.append(samples[-1][1])

                    self.assertAlmostEqual(
                        revs(duty < 0, angles),
                        MEASURED_TURNS[name] + (angles[-1] - angles[0]) / COUNTS_PER_TURN)

    def test_a_measured_stray_never_moves_the_revs_half_a_turn(self):
        # A miscounted wrap would move them a whole turn.
        for name, (duty, samples) in measured_phases().items():
            for offset in range(0, SAMPLE_MS, MEASURED_EVERY_MS):
                with self.subTest(phase=name, offset=offset):
                    counter = WrapCounter(duty < 0)
                    before = 0.0
                    for _, angle, _, _ in as_sampled(duty, samples, offset, SAMPLE_MS):
                        counter.feed(angle)
                        self.assertLess(abs(counter.revs - before), 0.5)
                        before = counter.revs

    def test_a_turn_is_counts_per_turn_at_the_angles_own_rate(self):
        # The angle reads 0-1022, and holds through the dead zone for the
        # rest of the turn. The time from one wrap to the next, at the
        # angle's rate mid-turn, gives the counts in a whole turn.
        for name, (duty, samples) in measured_phases().items():
            with self.subTest(phase=name):
                up = duty < 0
                wraps = [t for (_, a0, _), (t, a, _) in zip(samples, samples[1:])
                         if ((a0 >= HIGH_START and a < LOW_END) if up
                             else (a0 < LOW_END and a >= HIGH_START))]
                if len(wraps) < 2:
                    continue
                mid = [(t, a) for t, a, _ in samples
                       if wraps[0] < t < wraps[1] and 200 <= a <= 800]
                rate = abs(mid[-1][1] - mid[0][1]) / (mid[-1][0] - mid[0][0])

                self.assertAlmostEqual(rate * (wraps[1] - wraps[0]) / COUNTS_PER_TURN,
                                       1, delta=0.02)


class TravelTest(unittest.TestCase):
    def test_opening_adds_the_turns_and_closing_takes_them_off(self):
        tracker = Tracker(3.2, FULL, ESTIMATE)

        move(tracker, 3.2, 8.7)
        self.assertAlmostEqual(tracker.travel, 8.7, places=2)
        move(tracker, 8.7, 1.35)
        self.assertAlmostEqual(tracker.travel, 1.35, places=2)

    def test_an_unknown_travel_stays_unknown_until_an_end_sensor(self):
        tracker = Tracker(NAN, FULL, ESTIMATE)

        move(tracker, 3.2, 8.7)
        self.assertTrue(math.isnan(tracker.travel))

    def test_a_stop_that_wasnt_confirmed_loses_the_travel(self):
        # The lift may still be driving.
        tracker = Tracker(3.2, FULL, ESTIMATE)

        tracker.lose()
        self.assertTrue(math.isnan(tracker.travel))


class ReAnchorTest(unittest.TestCase):
    def test_the_down_end_sensor_makes_the_travel_0(self):
        # From a travel that was off, and from an unknown one.
        for start in (1.3, NAN):
            with self.subTest(start=start):
                tracker = Tracker(start, FULL, ESTIMATE)
                move(tracker, 5.2, 0.4)

                tracker.reached()
                self.assertEqual(tracker.travel, 0.0)

    def test_the_up_end_sensor_makes_the_travel_the_full_travel(self):
        for start in (21.0, NAN):
            with self.subTest(start=start):
                tracker = Tracker(start, FULL, ESTIMATE)
                move(tracker, 20.0, 27.8)

                tracker.reached()
                self.assertEqual(tracker.travel, FULL)

    def test_the_up_end_sensor_leaves_the_travel_until_full_travel_is_learned(self):
        tracker = Tracker(20.0, NAN, ESTIMATE)
        move(tracker, 20.0, 27.8)

        tracker.reached()
        self.assertAlmostEqual(tracker.travel, 27.8, places=2)

    def test_an_active_end_sensor_anchors_without_a_move(self):
        tracker = Tracker(NAN, FULL, ESTIMATE)

        tracker.anchor(cover_state.UP)
        self.assertEqual(tracker.travel, FULL)
        tracker.anchor(cover_state.DOWN)
        self.assertEqual(tracker.travel, 0.0)


class LearnFullTravelTest(unittest.TestCase):
    def test_a_run_from_the_bottom_to_the_up_end_sensor_learns_it(self):
        tracker = Tracker(0.0, NAN, ESTIMATE)
        move(tracker, 0.0, 27.8, from_end=True)

        tracker.reached()
        self.assertAlmostEqual(tracker.full_travel, 27.8, places=2)
        self.assertEqual(tracker.travel, tracker.full_travel)

    def test_a_run_from_the_top_to_the_down_end_sensor_learns_it(self):
        # Even with the travel unknown, as after an interrupted move that
        # ended at the up end sensor.
        tracker = Tracker(NAN, NAN, ESTIMATE)
        move(tracker, 27.8, 0.0, from_end=True)

        tracker.reached()
        self.assertAlmostEqual(tracker.full_travel, 27.8, places=2)
        self.assertEqual(tracker.travel, 0.0)

    def test_a_run_that_didnt_start_at_an_end_learns_nothing(self):
        tracker = Tracker(10.0, NAN, ESTIMATE)
        move(tracker, 10.0, 27.8)

        tracker.reached()
        self.assertTrue(math.isnan(tracker.full_travel))

    def test_a_run_with_a_stop_in_between_learns_nothing(self):
        # It stops short of the end sensor (a timeout, stall or travel
        # limit), so the next move starts stopped, not at an end.
        tracker = Tracker(0.0, NAN, ESTIMATE)
        move(tracker, 0.0, 12.0, from_end=True)
        move(tracker, 12.0, 27.8)

        tracker.reached()
        self.assertTrue(math.isnan(tracker.full_travel))

    def test_it_changes_only_by_more_than_0_1_rev(self):
        for run, expected in ((27.58, FULL), (27.42, FULL), (27.65, 27.65)):
            with self.subTest(run=run):
                tracker = Tracker(0.0, FULL, ESTIMATE)
                move(tracker, 0.0, run, from_end=True)

                tracker.reached()
                self.assertAlmostEqual(tracker.full_travel, expected, places=2)
                self.assertEqual(tracker.travel, tracker.full_travel)

    def test_a_run_far_from_the_estimate_learns_nothing(self):
        # Such as an end sensor that fires before the lift has moved. Too
        # small a full travel would bound every open short of the up end
        # sensor, so no run could ever correct it.
        for run in (0.0, 2.0, 50.0):
            with self.subTest(run=run):
                tracker = Tracker(0.0, FULL, ESTIMATE)
                move(tracker, 0.0, run, from_end=True)

                tracker.reached()
                self.assertEqual(tracker.full_travel, FULL)


class ApproachTest(unittest.TestCase):
    def test_opening_slows_down_within_approach_revs_of_full_travel(self):
        tracker = Tracker(20.0, FULL, ESTIMATE)
        tracker.begin(True, False)
        self.assertFalse(tracker.approach_due(5))

        move(tracker, 20.0, 22.4)
        self.assertFalse(tracker.approach_due(5))
        move(tracker, 22.4, 22.6)
        self.assertTrue(tracker.approach_due(5))

    def test_closing_slows_down_within_approach_revs_of_the_bottom(self):
        tracker = Tracker(8.0, FULL, ESTIMATE)

        move(tracker, 8.0, 5.1)
        self.assertFalse(tracker.approach_due(5))
        move(tracker, 5.1, 4.9)
        self.assertTrue(tracker.approach_due(5))

    def test_the_estimate_stands_in_until_full_travel_is_learned(self):
        tracker = Tracker(20.0, NAN, ESTIMATE)

        move(tracker, 20.0, ESTIMATE - 5.1)
        self.assertFalse(tracker.approach_due(5))
        move(tracker, ESTIMATE - 5.1, ESTIMATE - 4.9)
        self.assertTrue(tracker.approach_due(5))

    def test_a_move_near_its_end_starts_at_approach_speed(self):
        tracker = Tracker(FULL - 1, FULL, ESTIMATE)

        tracker.begin(True, False)
        self.assertTrue(tracker.approach_due(5))

    def test_with_unknown_travel_the_whole_move_is_at_approach_speed(self):
        for opening in (True, False):
            with self.subTest(opening=opening):
                tracker = Tracker(NAN, FULL, ESTIMATE)
                tracker.begin(opening, False)
                self.assertTrue(tracker.approach_due(5))

                move(tracker, 10.0, 20.0 if opening else 0.0)
                self.assertTrue(tracker.approach_due(5))


class TravelBoundTest(unittest.TestCase):
    def test_opening_stops_past_full_travel_plus_the_margin(self):
        tracker = Tracker(27.0, FULL, ESTIMATE)

        move(tracker, 27.0, FULL + 1.9)
        self.assertFalse(tracker.beyond_limit(MARGIN))
        move(tracker, FULL + 1.9, FULL + 2.1)
        self.assertTrue(tracker.beyond_limit(MARGIN))

    def test_closing_stops_the_margin_below_the_bottom(self):
        tracker = Tracker(1.0, FULL, ESTIMATE)

        move(tracker, 1.0, -1.9)
        self.assertFalse(tracker.beyond_limit(MARGIN))
        move(tracker, -1.9, -2.1)
        self.assertTrue(tracker.beyond_limit(MARGIN))

    def test_the_estimate_stands_in_until_full_travel_is_learned(self):
        tracker = Tracker(27.0, NAN, ESTIMATE)

        move(tracker, 27.0, ESTIMATE + 1.9)
        self.assertFalse(tracker.beyond_limit(MARGIN))
        move(tracker, ESTIMATE + 1.9, ESTIMATE + 2.1)
        self.assertTrue(tracker.beyond_limit(MARGIN))

    def test_with_unknown_travel_the_move_itself_is_bounded(self):
        # No move needs more than full travel plus the margin, wherever it
        # starts.
        for opening in (True, False):
            with self.subTest(opening=opening):
                sign = 1 if opening else -1
                tracker = Tracker(NAN, FULL, ESTIMATE)

                move(tracker, 40.0, 40.0 + sign * (FULL + 1.9))
                self.assertFalse(tracker.beyond_limit(MARGIN))
                tracker = Tracker(NAN, FULL, ESTIMATE)
                move(tracker, 40.0, 40.0 + sign * (FULL + 2.1))
                self.assertTrue(tracker.beyond_limit(MARGIN))


class AtBootTest(unittest.TestCase):
    def at_boot(self, up_active, down_active, stored_state,
                stored_travel=12.5, full_travel=FULL):
        return travel.at_boot(up_active, down_active, stored_state,
                              stored_travel, full_travel, ESTIMATE)

    def test_it_keeps_the_learned_full_travel(self):
        self.assertEqual(self.at_boot(False, False, cover_state.STOPPED).full_travel, FULL)

    def test_an_active_down_end_sensor_makes_the_travel_0(self):
        for stored in (cover_state.UNKNOWN, cover_state.MOVING_DOWN, cover_state.STOPPED):
            with self.subTest(stored=stored):
                self.assertEqual(self.at_boot(False, True, stored).travel, 0.0)

    def test_an_active_up_end_sensor_makes_the_travel_the_full_travel(self):
        for stored in (cover_state.UNKNOWN, cover_state.MOVING_UP, cover_state.STOPPED):
            with self.subTest(stored=stored):
                self.assertEqual(self.at_boot(True, False, stored).travel, FULL)

    def test_an_active_up_end_sensor_before_full_travel_is_learned_keeps_the_stored_travel(self):
        self.assertEqual(self.at_boot(True, False, cover_state.UP, 27.9, NAN).travel, 27.9)
        self.assertTrue(math.isnan(self.at_boot(True, False, cover_state.MOVING_UP, 27.9, NAN).travel))

    def test_a_settled_cover_state_keeps_the_stored_travel(self):
        for stored in (cover_state.UP, cover_state.DOWN, cover_state.STOPPED):
            with self.subTest(stored=stored):
                self.assertEqual(self.at_boot(False, False, stored).travel, 12.5)

    def test_an_interrupted_move_or_a_blank_record_leaves_it_unknown(self):
        for stored in (cover_state.MOVING_UP, cover_state.MOVING_DOWN, cover_state.UNKNOWN):
            with self.subTest(stored=stored):
                self.assertTrue(math.isnan(self.at_boot(False, False, stored).travel))

    def test_both_end_sensors_active_leaves_it_unknown(self):
        # That can't be, so neither is trusted.
        self.assertTrue(math.isnan(self.at_boot(True, True, cover_state.STOPPED).travel))


if __name__ == "__main__":
    unittest.main()
