"""Waiting on a servo's MOVING flag, with a deadline on every wait.

The servo raises MOVING some milliseconds after a goal write, and one read
of it takes about 5 ms (#85). A read gives True or False, or None when the
servo didn't reply.
"""

import asyncio
import unittest

from servo_wait import until_moving, until_still

READ_MS = 5


class FakeServo:
    """MOVING reads from a list, each taking READ_MS on its clock. Past the
    list's end, the last read repeats."""

    def __init__(self, reads):
        self._reads = list(reads)
        self.t_ms = 0
        self.count = 0

    def is_moving(self):
        self.t_ms += READ_MS
        self.count += 1
        return self._reads[min(self.count, len(self._reads)) - 1]

    def clock(self):
        return self.t_ms


def run(wait):
    return asyncio.run(wait)


class UntilStillTest(unittest.TestCase):
    def test_a_tilt_that_rises_after_the_first_read_is_waited_for(self):
        # #85: the first read, 5 ms after the goal write, comes before
        # MOVING is up. The wait ends when MOVING falls, not at once.
        servo = FakeServo([False, False, True, True, False])

        arrived = run(until_still(servo.is_moving, servo.clock, deadline_ms=3000, rise_ms=100))

        self.assertTrue(arrived)
        self.assertEqual(servo.count, 5)

    def test_a_tilt_already_at_its_goal_ends_once_the_rise_window_is_over(self):
        servo = FakeServo([False])

        arrived = run(until_still(servo.is_moving, servo.clock, deadline_ms=3000, rise_ms=100))

        self.assertTrue(arrived)
        self.assertEqual(servo.t_ms, 100)

    def test_a_blocked_tilt_gives_up_at_the_deadline(self):
        # A servo that can't reach its goal keeps reporting MOVING.
        servo = FakeServo([False, True])

        arrived = run(until_still(servo.is_moving, servo.clock, deadline_ms=3000, rise_ms=100))

        self.assertFalse(arrived)
        self.assertEqual(servo.t_ms, 3000)

    def test_a_servo_that_doesnt_reply_gives_up_at_the_deadline(self):
        # No reply isn't "not moving": a silent tilt isn't at its goal.
        servo = FakeServo([None])

        arrived = run(until_still(servo.is_moving, servo.clock, deadline_ms=3000, rise_ms=100))

        self.assertFalse(arrived)
        self.assertEqual(servo.t_ms, 3000)

    def test_a_stop_ends_at_the_first_reply_that_its_not_moving(self):
        # A stop waits for MOVING to fall, with no rise window.
        cases = {"still moving": ([True, None, True, False], 4),
                 "already still": ([False], 1)}
        for name, (reads, count) in cases.items():
            with self.subTest(name):
                servo = FakeServo(reads)

                self.assertTrue(run(until_still(servo.is_moving, servo.clock, deadline_ms=2000)))
                self.assertEqual(servo.count, count)


class UntilMovingTest(unittest.TestCase):
    def test_a_start_ends_at_the_first_read_that_its_moving(self):
        servo = FakeServo([False, None, False, True])

        self.assertTrue(run(until_moving(servo.is_moving, servo.clock, deadline_ms=2000)))
        self.assertEqual(servo.count, 4)

    def test_a_servo_that_never_starts_gives_up_at_the_deadline(self):
        # start() used to spin forever on a servo that didn't reply (#6).
        for reads in ([False], [None]):
            with self.subTest(reads[0]):
                servo = FakeServo(reads)

                self.assertFalse(run(until_moving(servo.is_moving, servo.clock, deadline_ms=2000)))
                self.assertEqual(servo.t_ms, 2000)


if __name__ == "__main__":
    unittest.main()
