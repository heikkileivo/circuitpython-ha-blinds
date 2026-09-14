"""The lift's stop sequence: every move ends with it, and so does the boot
re-init. Decided in #23, and built in #52."""

import unittest

from lift_stop import (BRAKE, FALLBACK_BRAKE, BRAKED, BRAKE_UNCONFIRMED, CONFIRMED, DUTY_0, LIMP,
                       NO_REPLY, REFUSED, STOP_UNCONFIRMED, next_step, stopped)


class NextStepTest(unittest.TestCase):
    def test_it_starts_with_duty_0(self):
        self.assertEqual(next_step(), DUTY_0)

    def test_a_confirmed_duty_0_is_followed_by_the_brake(self):
        self.assertEqual(next_step(DUTY_0, CONFIRMED), BRAKE)

    def test_a_duty_0_that_cant_be_confirmed_is_followed_by_torque_0(self):
        # Torque 0 is the one write known to cut the drive, and a limp blind
        # is fine in a fault. Torque 2 never follows, since it may not
        # override a non-zero duty.
        for outcome in (REFUSED, NO_REPLY):
            with self.subTest(outcome=outcome):
                self.assertEqual(next_step(DUTY_0, outcome), LIMP)

    def test_torque_0_ends_the_stop_unconfirmed_whether_or_not_it_reads_back(self):
        # Either way main() fails: a limp blind is fine in a fault.
        for outcome in (CONFIRMED, REFUSED, NO_REPLY):
            with self.subTest(outcome=outcome):
                self.assertEqual(next_step(LIMP, outcome), STOP_UNCONFIRMED)

    def test_a_confirmed_torque_2_leaves_the_lift_braked(self):
        self.assertEqual(next_step(BRAKE, CONFIRMED), BRAKED)

    def test_a_servo_that_doesnt_accept_torque_2_falls_back_to_torque_1(self):
        # It reads back another value. Torque 1 with duty 0 brakes too (#21).
        self.assertEqual(next_step(BRAKE, REFUSED), FALLBACK_BRAKE)

    def test_a_torque_2_with_no_reply_ends_stopped_with_the_brake_unconfirmed(self):
        # Duty 0 is confirmed, so the motor has stopped anyway. The write
        # was retried already, and it counts as a UART error.
        self.assertEqual(next_step(BRAKE, NO_REPLY), BRAKE_UNCONFIRMED)

    def test_the_fallback_brake_leaves_the_lift_braked_once_it_reads_back(self):
        self.assertEqual(next_step(FALLBACK_BRAKE, CONFIRMED), BRAKED)
        for outcome in (REFUSED, NO_REPLY):
            with self.subTest(outcome=outcome):
                self.assertEqual(next_step(FALLBACK_BRAKE, outcome), BRAKE_UNCONFIRMED)


class StoppedTest(unittest.TestCase):
    def test_the_lift_is_stopped_once_its_duty_0_is_confirmed_braked_or_not(self):
        self.assertTrue(stopped(BRAKED))
        self.assertTrue(stopped(BRAKE_UNCONFIRMED))

    def test_a_stop_unconfirmed_hasnt_stopped_the_lift(self):
        self.assertFalse(stopped(STOP_UNCONFIRMED))


if __name__ == "__main__":
    unittest.main()
