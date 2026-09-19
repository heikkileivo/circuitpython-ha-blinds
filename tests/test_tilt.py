"""The tilt a blind starts from at boot: the tilt servo's angle, read after
the boot re-init has stopped the servos, as the tilt HA is told.

The tilt servo is driven to the tilt times tilt_scale, 10 by default, so a
tilt of 75 leaves it at a servo angle of about 750.
"""

import unittest

from packet import Address, Instruction, Reader, checksum
from servo_bus import TILT_ID
from tilt import from_servo_angle, read_at_boot
from tests.test_packet import FakeUart

# A request that gets no reply, as FakeUart scripts it.
UNANSWERED = (b"", 0.002)


def angle_reply(angle):
    """The tilt servo's reply to a read of its servo angle, as FakeUart
    scripts it."""
    body = bytes((TILT_ID, 4, 0, angle >> 8, angle & 0xFF))
    return (b"\xff\xff" + body + bytes((checksum(body),)), 0.002)


class FromServoAngleTest(unittest.TestCase):
    def test_the_tilt_is_the_servo_angle_divided_by_the_scale(self):
        self.assertEqual(from_servo_angle(750, 10), 75)

    def test_the_tilt_is_rounded_to_the_nearest_whole_number(self):
        # The servo settles a few steps off the angle it was driven to.
        for angle, expected in ((754, 75), (746, 75), (756, 76), (755, 76)):
            with self.subTest(angle=angle):
                self.assertEqual(from_servo_angle(angle, 10), expected)

    def test_a_fractional_scale_still_gives_a_whole_tilt(self):
        # settings.toml's tilt_scale defaults to 10.0, and the tilt is
        # published as str(tilt): HA must get "75", not "75.0".
        self.assertEqual(str(from_servo_angle(750, 10.0)), "75")

    def test_an_angle_past_a_tilt_of_100_is_a_tilt_of_100(self):
        # The servo angle reads up to 1023, and HA's tilt stops at 100.
        for angle in (1006, 1023):
            with self.subTest(angle=angle):
                self.assertEqual(from_servo_angle(angle, 10), 100)

    def test_no_reply_gives_no_tilt(self):
        # Reader.read_2_bytes() returns None when no good reply came.
        self.assertIsNone(from_servo_angle(None, 10))


class ReadAtBootTest(unittest.TestCase):
    def test_the_blind_starts_from_the_tilt_its_tilt_servo_is_at(self):
        uart = FakeUart(angle_reply(750))

        self.assertEqual(read_at_boot(Reader(uart), 10.0), 75)
        # One read of the tilt servo's two servo angle bytes.
        body = bytes((TILT_ID, 4, Instruction.READ, Address.PRESENT_POSITION_L, 2))
        self.assertEqual(uart.written, [b"\xff\xff" + body + bytes((checksum(body),))])

    def test_a_missed_reply_is_retried(self):
        # Like the servo health reads: one lost reply mustn't lose the tilt.
        uart = FakeUart(UNANSWERED, angle_reply(750))

        self.assertEqual(read_at_boot(Reader(uart), 10.0), 75)

    def test_a_tilt_servo_that_never_replies_leaves_the_tilt_at_50(self):
        # A FakeUart with no replies scripted never answers.
        uart = FakeUart()

        self.assertEqual(read_at_boot(Reader(uart), 10.0), 50)
        # Every attempt was made first.
        self.assertEqual(len(uart.written), 3)


if __name__ == "__main__":
    unittest.main()
