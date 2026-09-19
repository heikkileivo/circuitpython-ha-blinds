"""The safe-mode recovery (host test 3, safe-mode part): what safemode.py
does for each safe-mode reason, and the stop packet it writes to the lift.

The cause is checked as the boot after the restart publishes it: restart()
stores it in NVM, then resets, so that boot's chip reason is SOFTWARE.

safemode.py doesn't import packet.py, to stay tiny, so its stop packet is a
constant. It must be exactly what packet.py sends to write duty 0.
"""

import unittest

from packet import Address, Reader
from reset_cause import OPTIONS, boot_decision, record
from safemode import REASONS, STOP_LIFT, decision
from servo_health import LIFT_ID
from tests.test_packet import FakeUart


def recovery(reason):
    """What safemode.py does for a safe-mode reason: whether it stops the
    lift, the cause the boot after its restart publishes, and how long it
    waits before restarting, in seconds."""
    stop_lift, cause, wait_s = decision(reason)
    published, _, _ = boot_decision(record(cause), "SOFTWARE")
    return stop_lift, published, wait_s


class DecisionTest(unittest.TestCase):
    def test_a_brownout_stops_the_lift_and_restarts_as_brownout_after_30_s(self):
        self.assertEqual(recovery("BROWNOUT"), (True, "brownout", 30))

    def test_each_named_reason_stops_the_lift_and_restarts_as_its_own_cause_after_30_s(self):
        # PROGRAMMATIC is how the stage 5 check enters safe mode. WATCHDOG is
        # CircuitPython's internal watchdog, not the chip's watchdog reset.
        for reason in ("FLASH_WRITE_FAIL", "GC_ALLOC_OUTSIDE_VM", "HARD_FAULT",
                       "INTERRUPT_ERROR", "NLR_JUMP_FAIL", "NO_HEAP", "PROGRAMMATIC",
                       "SDK_FATAL_ERROR", "STACK_OVERFLOW", "WATCHDOG"):
            with self.subTest(reason=reason):
                self.assertEqual(recovery(reason), (True, "safe_mode_" + reason.lower(), 30))

    def test_any_other_reason_stops_the_lift_and_restarts_as_other_safe_mode_after_30_s(self):
        # UNKNOWN is what _reason() returns when no member matches.
        for reason in ("UNKNOWN", "SOMETHING_NEW", "NO_CIRCUITPY"):
            with self.subTest(reason=reason):
                self.assertEqual(recovery(reason), (True, "other_safe_mode", 30))

    def test_every_cause_is_an_option_of_the_entity(self):
        for reason in REASONS + ("UNKNOWN",):
            with self.subTest(reason=reason):
                self.assertIn(recovery(reason)[1], OPTIONS)


class StopPacketTest(unittest.TestCase):
    def test_the_stop_packet_is_packet_pys_write_of_duty_0_to_the_lift(self):
        uart = FakeUart()

        Reader(uart).write_word(LIFT_ID, Address.GOAL_TIME_L, 0)

        self.assertEqual(uart.written, [STOP_LIFT])


if __name__ == "__main__":
    unittest.main()
