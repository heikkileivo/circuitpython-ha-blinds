"""When the blind calls MQTT's loop() (#5, #55). Each call blocks asyncio for
its timeout or more. While the blind is idle, the service task calls it on
every pass. While it moves, at most once per interval, so the end sensor and
stall checks lose little time, and only when the move's travel can take the
pause.

Time is in milliseconds, as code.py passes it.
"""

import unittest

from mqtt_pace import Pace

INTERVAL_MS = 1000


class IdleTest(unittest.TestCase):
    def test_idle_it_loops_on_every_pass(self):
        pace = Pace(INTERVAL_MS)

        for t_ms in (0, 500, 1000):
            self.assertTrue(pace.due(t_ms, moving=False, can_pause=False))
            pace.looped(t_ms)


class MovingTest(unittest.TestCase):
    def test_moving_it_loops_at_most_once_per_interval(self):
        # From the start of one call to the start of the next, idle calls
        # included: the move started 400 ms after the last idle one.
        pace = Pace(INTERVAL_MS)
        pace.looped(0)

        self.assertFalse(pace.due(400, moving=True, can_pause=True))
        self.assertFalse(pace.due(999, moving=True, can_pause=True))
        self.assertTrue(pace.due(1000, moving=True, can_pause=True))
        pace.looped(1000)
        self.assertFalse(pace.due(1600, moving=True, can_pause=True))
        self.assertTrue(pace.due(2000, moving=True, can_pause=True))

    def test_moving_it_waits_until_the_travel_can_take_the_pause(self):
        # Such as near the wrap at full speed, or near the end sensor. The
        # first pass that can, once the interval is over, loops.
        pace = Pace(INTERVAL_MS)
        pace.looped(0)

        self.assertFalse(pace.due(1000, moving=True, can_pause=False))
        self.assertFalse(pace.due(1250, moving=True, can_pause=False))
        self.assertTrue(pace.due(1300, moving=True, can_pause=True))


if __name__ == "__main__":
    unittest.main()
