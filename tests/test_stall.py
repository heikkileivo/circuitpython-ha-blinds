"""Host test 6, stall part: deciding that the lift servo has stalled.

The series come from the bench (#21, sections 3 and 5): duty 800 turns the
lift at about 1,000-1,450 counts/s. Going up (negative duty), the servo angle
counts up and PRESENT_SPEED is positive. At the head rail the speed read 0 and
the angle froze at 0 within about 100 ms. Around the wrap the angle reads a
stray mid value (532, 275) and the speed one absurd value (up to about
±30,000). The lift is sampled every 50 ms, lift_sample_ms's default.
"""

import unittest

from stall import StallDetector

SAMPLE_MS = 50


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


def head_rail():
    """Up from the up end sensor (angle 683) at duty 800, into the head rail
    after about 1.1 s."""
    return turning(-800, 1200, 683, 0, 1100) + stalled(-800, 0, 1100, 2000)


class StallDetectorTest(unittest.TestCase):
    def test_the_head_rail_series_is_stalled_within_150_ms(self):
        self.assertEqual(first_stall(head_rail()), (1250, 150))

    def test_a_single_outlier_in_a_stall_doesnt_restart_the_window(self):
        outliers = {"stray angle": (1150, 532, 0, -800),
                    "absurd speed": (1150, 0, 30986, -800)}
        for name, outlier in outliers.items():
            with self.subTest(name):
                samples = head_rail()
                samples[samples.index((1150, 0, 0, -800))] = outlier

                self.assertEqual(first_stall(samples), (1250, 150))

    def test_an_angle_frozen_at_the_wrap_is_still_frozen(self):
        # 1021 and 0 are 3 counts apart across the wrap.
        samples = head_rail()
        samples[samples.index((1100, 0, 0, -800))] = (1100, 1021, 0, -800)
        samples[samples.index((1150, 0, 0, -800))] = (1150, 1021, 0, -800)

        self.assertEqual(first_stall(samples), (1250, 150))

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
        # The bench didn't measure the speed at duty 300. Scaled from duty
        # 800's 1,000-1,450 counts/s it's about 400; 100 is well under that.
        for speed in (400, 100):
            with self.subTest(speed):
                samples = turning(-300, speed, 683, 0, 4000)

                self.assertIsNone(first_stall(samples))

    def test_a_servo_that_never_starts_is_stalled_after_the_grace(self):
        # Commanded duty 800 up while already against the head rail.
        samples = stalled(-800, 0, 0, 1000)

        self.assertEqual(first_stall(samples), (300, 300))

    def test_a_servo_at_duty_0_is_never_stalled(self):
        # Braking, still, before and after a move.
        self.assertIsNone(first_stall(stalled(0, 683, 0, 2000)))


if __name__ == "__main__":
    unittest.main()
