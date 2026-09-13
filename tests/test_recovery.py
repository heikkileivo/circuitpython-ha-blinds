"""Host test 4: the last-resort recovery. The MQTT escalation restarts the
blind once its liveness echo (its own uptime_seconds, published every 10 s)
has been missing for 5 min, but never mid-move. The restart loop restarts it
after 3 quick failed runs of main() in a row.

Time is in milliseconds, as code.py passes it.
"""

import unittest

from recovery import Escalation, RestartLoop

MIN_MS = 60_000


class EscalationTest(unittest.TestCase):
    def test_no_echo_for_5_min_while_idle_escalates(self):
        escalation = Escalation(0)

        self.assertFalse(escalation.due(5 * MIN_MS - 1, moving=False))
        self.assertTrue(escalation.due(5 * MIN_MS, moving=False))

    def test_while_moving_it_escalates_only_once_the_move_ends(self):
        # A restart mid-move would leave the lift driving through the deep
        # sleep.
        escalation = Escalation(0)

        self.assertFalse(escalation.due(5 * MIN_MS, moving=True))
        self.assertFalse(escalation.due(6 * MIN_MS, moving=True))
        self.assertTrue(escalation.due(6 * MIN_MS + 500, moving=False))

    def test_an_echo_restarts_the_window(self):
        escalation = Escalation(0)

        escalation.echo(4 * MIN_MS)

        self.assertFalse(escalation.due(5 * MIN_MS, moving=False))
        self.assertFalse(escalation.due(9 * MIN_MS - 1, moving=False))
        self.assertTrue(escalation.due(9 * MIN_MS, moving=False))


class RestartLoopTest(unittest.TestCase):
    def test_three_quick_failures_in_a_row_restart(self):
        # Each run of main() fails 2 s in, and the next starts 10 s later.
        restart_loop = RestartLoop()

        self.assertFalse(restart_loop.failed(0, 2_000))
        self.assertFalse(restart_loop.failed(12_000, 14_000))
        self.assertTrue(restart_loop.failed(24_000, 26_000))

    def test_a_run_longer_than_the_escalation_window_resets_the_count(self):
        # A run that stayed up 6 min proves main() can come up, so it ends
        # the quick failures in a row, and doesn't count as one.
        restart_loop = RestartLoop()
        long_run_ends = 24_000 + 6 * MIN_MS

        self.assertFalse(restart_loop.failed(0, 2_000))
        self.assertFalse(restart_loop.failed(12_000, 14_000))
        self.assertFalse(restart_loop.failed(24_000, long_run_ends))
        self.assertFalse(restart_loop.failed(long_run_ends + 10_000, long_run_ends + 12_000))
        self.assertFalse(restart_loop.failed(long_run_ends + 22_000, long_run_ends + 24_000))
        self.assertTrue(restart_loop.failed(long_run_ends + 34_000, long_run_ends + 36_000))


if __name__ == "__main__":
    unittest.main()
