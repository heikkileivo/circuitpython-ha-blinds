"""Host test 6, stall part: deciding that the lift servo has stalled.

The series come from the bench (#21, sections 3 and 5): duty 800 turns the
lift at about 1,000-1,450 counts/s. Going up (negative duty), the servo angle
counts up and PRESENT_SPEED is positive. At the head rail the speed read 0 and
the angle froze at 0 within about 100 ms. Around the wrap the angle reads a
stray mid value (532, 275) and the speed one absurd value (up to about
±30,000). The lift is sampled every 50 ms, lift_sample_ms's default.

The pot's dead zone at the wrap (#78): while the shaft turns through it, the
angle holds at about 1018-1022, then 0-1, with the speed at 0. Middle's lift,
measured every 10 ms, is in tests/data.
"""

import unittest
from pathlib import Path

from stall import StallDetector

SAMPLE_MS = 50
MEASURED = Path(__file__).resolve().parent / "data" / "lift_wrap_samples_2026-09-13.txt"


def turning(duty, speed, angle, start_ms, end_ms):
    """Samples (time in ms, servo angle, speed, duty) of the lift turning
    steadily from angle at start_ms, the angle wrapping at 1024."""
    return [(t, (angle + speed * (t - start_ms) // 1000) % 1024, speed, duty)
            for t in range(start_ms, end_ms, SAMPLE_MS)]


def stalled(duty, angle, start_ms, end_ms):
    """Samples of the lift stalled: speed 0, servo angle frozen."""
    return [(t, angle, 0, duty) for t in range(start_ms, end_ms, SAMPLE_MS)]


def first_stall(samples, detector=None):
    """When the detector first decides "stalled", and how long the angle had
    been frozen then, as (time in ms, frozen ms). None if it never does."""
    detector = detector or StallDetector()
    for t, angle, speed, duty in samples:
        if detector.feed(t, angle, speed, duty):
            return t, detector.frozen_ms
    return None


def head_rail(frozen_angle=0):
    """Up from the up end sensor (angle 683) at duty 800, into the head rail
    after about 1.1 s. On the bench the angle froze at 0."""
    return turning(-800, 1200, 683, 0, 1100) + stalled(-800, frozen_angle, 1100, 2000)


def into_stall(duty, angle):
    """The lift turning for 1 s at duty, at about the duty in counts/s, then
    stalled with the servo angle at angle."""
    start = 200 if duty < 0 else 900
    return turning(duty, -duty, start, 0, 1000) + stalled(duty, angle, 1000, 2000)


def measured_phases():
    """The measured samples, as {phase: (duty, [(time in ms, servo angle,
    speed)])}."""
    phases = {}
    for line in MEASURED.read_text().splitlines():
        if line and not line.startswith("#"):
            name, duty, t, angle, speed, _voltage = line.split()
            phases.setdefault(name, (int(duty), []))[1].append((int(t), int(angle), int(speed)))
    return phases


def every_50_ms(duty, samples, offset):
    """The firmware's view of samples taken every 10 ms: one every SAMPLE_MS,
    the first at offset."""
    picked, due = [], offset
    for t, angle, speed in samples:
        if t >= due:
            picked.append((t, angle, speed, duty))
            due = t + SAMPLE_MS
    return picked


class StallDetectorTest(unittest.TestCase):
    def test_the_head_rail_series_is_stalled_within_200_ms(self):
        # It froze at 0, in the dead zone, whose window is 200 ms at duty 800.
        self.assertEqual(first_stall(head_rail()), (1300, 200))

    def test_a_stall_outside_the_dead_zone_is_stalled_within_150_ms(self):
        self.assertEqual(first_stall(head_rail(frozen_angle=600)), (1250, 150))

    def test_a_single_outlier_in_a_stall_doesnt_restart_the_window(self):
        outliers = {"stray angle": (1150, 532, 0, -800),
                    "absurd speed": (1150, 600, 30986, -800)}
        for name, outlier in outliers.items():
            with self.subTest(name):
                samples = head_rail(frozen_angle=600)
                samples[samples.index((1150, 600, 0, -800))] = outlier

                self.assertEqual(first_stall(samples), (1250, 150))

    def test_an_angle_frozen_at_the_wrap_is_still_frozen(self):
        # 1021 and 0 are 3 counts apart across the wrap.
        samples = head_rail()
        samples[samples.index((1100, 0, 0, -800))] = (1100, 1021, 0, -800)
        samples[samples.index((1150, 0, 0, -800))] = (1150, 1021, 0, -800)

        self.assertEqual(first_stall(samples), (1300, 200))

    def test_a_stall_in_the_dead_zone_gets_a_window_scaled_by_duty(self):
        # 200 ms at duty 800, and 800 / |duty| times that below it: 320 ms
        # at duty 500 and 533 ms at 300, caught at the next 50 ms sample.
        cases = {(-800, 0): (1200, 200),
                 (-500, 1020): (1350, 350),
                 (300, 0): (1550, 550),
                 (-300, 1022): (1550, 550)}
        for (duty, angle), expected in cases.items():
            with self.subTest(duty=duty, angle=angle):
                self.assertEqual(first_stall(into_stall(duty, angle)), expected)

    def test_a_stall_outside_the_dead_zone_isnt_scaled_by_duty(self):
        for duty in (-800, 300):
            with self.subTest(duty=duty):
                self.assertEqual(first_stall(into_stall(duty, 500)), (1150, 150))

    def test_free_running_at_duty_800_with_wrap_outliers_is_never_stalled(self):
        # Several turns each way, with the bench's strays at every wrap.
        directions = {"up": turning(-800, 1200, 683, 0, 4000),
                      "down": turning(800, -1400, 340, 0, 4000)}
        strays = [(532, 15036), (275, 30986), (1021, -16200), (0, 26186)]
        for name, samples in directions.items():
            with self.subTest(name):
                wraps = [i for i in range(1, len(samples))
                         if abs(samples[i][1] - samples[i - 1][1]) > 512]
                for i, (angle, speed) in zip(wraps, strays * len(wraps)):
                    t, _, _, duty = samples[i]
                    samples[i] = (t, angle, speed, duty)

                self.assertGreaterEqual(len(wraps), 4)
                self.assertIsNone(first_stall(samples))

    def test_running_at_approach_speed_is_never_stalled(self):
        # Measured at duty 300, PRESENT_SPEED reads about 333 (#78); 100 is
        # well under that.
        for speed in (333, 100):
            with self.subTest(speed):
                samples = turning(-300, speed, 683, 0, 4000)

                self.assertIsNone(first_stall(samples))

    def test_the_measured_turns_are_never_stalled_at_any_duty(self):
        # Every phase, sampled every 50 ms from each 10 ms offset, so every
        # wrap is seen as the firmware might see it.
        for name, (duty, samples) in measured_phases().items():
            for offset in range(0, SAMPLE_MS, 10):
                with self.subTest(phase=name, offset=offset):
                    self.assertIsNone(first_stall(every_50_ms(duty, samples, offset)))

    def test_a_start_from_rest_in_the_dead_zone_isnt_stalled(self):
        # Measured starts from rest: up300 at angle 996, the dead zone's
        # edge, which it then takes about 300 ms to cross at duty 300, and
        # down800 at angle 0, inside it.
        for name in ("up300", "down800"):
            duty, samples = measured_phases()[name]
            start = [sample for sample in samples if sample[0] < 1000]

            self.assertEqual(start[0][2], 0)
            for offset in range(0, SAMPLE_MS, 10):
                with self.subTest(phase=name, offset=offset):
                    self.assertIsNone(first_stall(every_50_ms(duty, start, offset)))

    def test_a_servo_that_never_starts_is_stalled_after_the_grace(self):
        # Commanded duty 800 up while already against the head rail.
        samples = stalled(-800, 0, 0, 1000)

        self.assertEqual(first_stall(samples), (300, 300))

    def test_a_servo_at_duty_0_is_never_stalled(self):
        # Braking, still, before and after a move.
        self.assertIsNone(first_stall(stalled(0, 683, 0, 2000)))


if __name__ == "__main__":
    unittest.main()
