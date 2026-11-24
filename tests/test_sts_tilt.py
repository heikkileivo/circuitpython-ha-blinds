"""An STS3215 tilt servo (#141): the tilt around the servo's midpoint at
2048, a position move with a speed, acceleration and torque limit, and the
boot read. Run on the fake servo bus, with the device-only modules stubbed
so blinds.py imports on the host."""

import sys
import types
import unittest

sys.modules.setdefault("microcontroller", types.SimpleNamespace(nvm=bytearray(256)))

import blinds
from packet import Address, Reader
from servo_bus import TILT_ID
from servo_health import detect_series
from tests.test_servo_health import FakeBus, FakeServo
from tilt import STS_MIDDLE, STS_STEPS_PER_TILT, from_servo_angle, read_at_boot, to_servo_angle

STS_REGISTERS = (0, 0, 0, 9, 3)


def word(servo, address):
    return servo.memory[address] | servo.memory[address + 1] << 8


def sts_tilt(position=None):
    servo = FakeServo(TILT_ID, registers=STS_REGISTERS, torque=0)
    if position is not None:
        servo.memory[Address.PRESENT_POSITION_L] = position & 0xFF
        servo.memory[Address.PRESENT_POSITION_H] = position >> 8
    reader = Reader(FakeBus(servo))
    detect_series(reader)
    return blinds.Servo(TILT_ID, reader, scale=10.0), servo, reader


class MappingTest(unittest.TestCase):
    def test_a_tilt_of_50_is_the_midpoint(self):
        self.assertEqual(to_servo_angle(50, 10.0, "sts"), STS_MIDDLE)
        self.assertEqual(from_servo_angle(STS_MIDDLE, 10.0, "sts"), 50)

    def test_the_ends_are_inside_one_turn(self):
        self.assertEqual(to_servo_angle(0, 10.0, "sts"), 48)
        self.assertEqual(to_servo_angle(100, 10.0, "sts"), 4048)

    def test_the_angle_is_clamped_to_one_turn(self):
        self.assertEqual(to_servo_angle(100, 10.0, "sts", 100), 4095)
        self.assertEqual(to_servo_angle(0, 10.0, "sts", 100), 0)

    def test_every_tilt_round_trips(self):
        for tilt in range(101):
            with self.subTest(tilt=tilt):
                angle = to_servo_angle(tilt, 10.0, "sts")
                self.assertEqual(from_servo_angle(angle, 10.0, "sts"), tilt)

    def test_the_servo_settling_a_few_steps_off_still_reads_the_tilt(self):
        for angle in (2048 + 6, 2048 - 6, 3048 + 5, 3048 - 5):
            with self.subTest(angle=angle):
                self.assertEqual(from_servo_angle(angle, 10.0, "sts"), round((angle - 2048) / 40) + 50)

    def test_an_angle_past_the_ends_is_the_end_tilt(self):
        self.assertEqual(from_servo_angle(4095, 10.0, "sts"), 100)
        self.assertEqual(from_servo_angle(0, 10.0, "sts"), 0)

    def test_an_scs_is_the_tilt_times_the_scale_as_before(self):
        self.assertEqual(to_servo_angle(75, 10.0), 750)
        self.assertEqual(from_servo_angle(750, 10.0), 75)


class DriveTest(unittest.TestCase):
    def test_a_position_move_writes_its_profile_then_the_goal(self):
        tilt, servo, reader = sts_tilt()

        tilt.position = 75

        self.assertEqual(word(servo, Address.GOAL_POSITION_L), 2048 + 25 * STS_STEPS_PER_TILT)
        self.assertEqual(word(servo, Address.GOAL_SPEED_L), blinds.STS_TILT_SPEED)
        self.assertEqual(servo.memory[Address.STS_ACCELERATION], blinds.STS_TILT_ACCELERATION)
        self.assertEqual(word(servo, Address.STS_TORQUE_LIMIT_L), blinds.STS_TILT_TORQUE_LIMIT)

    def test_an_sts_lift_gets_no_goal_position(self):
        servo = FakeServo(1, registers=STS_REGISTERS, torque=1)
        reader = Reader(FakeBus(servo))
        detect_series(reader)

        self.assertIsNone(reader.set_position(1, 2048))
        self.assertEqual(word(servo, Address.GOAL_POSITION_L), 0)

    def test_an_scs_tilt_has_no_profile_written(self):
        servo = FakeServo(TILT_ID, registers=(0, 0, 0, 5, 0), torque=0)
        reader = Reader(FakeBus(servo))
        detect_series(reader)

        self.assertIsNone(reader.set_position_profile(TILT_ID, 600, 10, 300))
        self.assertIsNone(reader.set_sts_position(TILT_ID, 2048))


class DeadlineTest(unittest.TestCase):
    def test_an_sts_tilt_may_take_as_long_as_a_full_turn_at_its_speed(self):
        tilt, servo, reader = sts_tilt()

        # 4096 steps at 600 steps/s is 6.8 s, so the SCS's 3 s would cut off
        # the 2,000-step move from 75 to 25 (3.3 s), as it did on Middle.
        self.assertGreater(tilt.tilt_deadline_ms, 4096 * 1000 // blinds.STS_TILT_SPEED)
        self.assertGreater(tilt.tilt_deadline_ms, 2000 * 1000 // blinds.STS_TILT_SPEED + 3000)

    def test_an_scs_tilt_keeps_its_deadline(self):
        servo = FakeServo(TILT_ID, registers=(0, 0, 0, 5, 0), torque=0)
        reader = Reader(FakeBus(servo))
        detect_series(reader)

        self.assertEqual(blinds.Servo(TILT_ID, reader, scale=10.0).tilt_deadline_ms,
                         blinds.servo_wait.TILT_DEADLINE_MS)


class BootReadTest(unittest.TestCase):
    def test_the_boot_tilt_is_the_one_the_sts_is_at(self):
        tilt, servo, reader = sts_tilt(position=2048 + 15 * 40)

        self.assertEqual(read_at_boot(reader, 10.0), 65)


if __name__ == "__main__":
    unittest.main()
