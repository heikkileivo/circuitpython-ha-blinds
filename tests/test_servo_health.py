"""Servo health: classifying each servo's boot health read, and the message
published to the servo_health entity.

The figures come from the bench (#21): the supply idles at 8.5 V, which
PRESENT_VOLTAGE reads as 85 (0.1 V units), and the servos sat at about 21 °C.
"""

import unittest

from packet import Address, Instruction, Reader, checksum
from servo_health import ServoRead, boot_reinit, classify, health_message

# MQTT_ATTRIBUTES_BLOCKED in homeassistant/components/mqtt/entity.py (dev,
# 2026-09-13), with its enum members spelt out.
BLOCKED_ATTRIBUTES = {
    "assumed_state", "available", "device_class", "device_info",
    "entity_category", "entity_id", "entity_picture",
    "entity_registry_enabled_default", "extra_state_attributes",
    "force_update", "friendly_name", "icon", "should_poll", "state",
    "supported_features", "unique_id", "unit_of_measurement",
}


def answered(**changes):
    """The health read of a servo that answered: 8.5 V, 21 °C, ERROR byte
    and status 0, its boot stop confirmed."""
    fields = dict(stop_confirmed=True, error=0, voltage=85, temperature=21, status=0,
                  uart_errors=0)
    fields.update(changes)
    return ServoRead(**fields)


class ClassifyTest(unittest.TestCase):
    def test_a_servo_that_answers_without_an_error_is_ok(self):
        self.assertEqual(classify(answered()), "ok")

    def test_a_servo_that_gives_no_reply_is_no_reply(self):
        # It didn't answer the boot stop either, so nothing confirmed it.
        self.assertEqual(classify(ServoRead(stop_confirmed=False, uart_errors=9)), "no_reply")

    def test_a_servo_that_reports_an_error_is_error(self):
        # Bit 5 of the ERROR byte is overload, bit 2 overheating.
        for error in (0x20, 0x04):
            with self.subTest(error=error):
                self.assertEqual(classify(answered(error=error)), "error")

    def test_a_servo_whose_status_register_shows_an_error_is_error(self):
        # The status register (65) carries the ERROR byte's bits.
        self.assertEqual(classify(answered(status=0x20)), "error")

    def test_a_servo_that_answers_but_whose_boot_stop_was_unconfirmed_is_error(self):
        # A servo left driving may still be driving, so HA has to hear of it.
        self.assertEqual(classify(answered(stop_confirmed=False)), "error")


class HealthMessageTest(unittest.TestCase):
    def test_the_health_is_the_worse_of_the_lift_and_tilt_servos(self):
        # Decided for #38: ok, then error, then no reply as the worst.
        no_reply = ServoRead(stop_confirmed=False)
        overloaded = answered(error=0x20)
        cases = [
            (answered(), answered(), "ok"),
            (overloaded, answered(), "error"),
            (answered(), overloaded, "error"),
            (answered(), no_reply, "no_reply"),
            (no_reply, overloaded, "no_reply"),
            (overloaded, no_reply, "no_reply"),
        ]
        for lift, tilt, expected in cases:
            with self.subTest(lift=classify(lift), tilt=classify(tilt)):
                self.assertEqual(health_message(lift, tilt)["health"], expected)

    def test_each_servo_has_its_own_attributes(self):
        # 74 is the 7.4 V the bench's stall pulled the supply down to.
        message = health_message(answered(voltage=74, temperature=31, uart_errors=2),
                                 ServoRead(stop_confirmed=False, uart_errors=9))

        self.assertEqual(message["lift"], {"health": "ok", "temperature": 31,
                                           "idle_voltage": 7.4, "uart_errors": 2})
        self.assertEqual(message["tilt"], {"health": "no_reply", "temperature": None,
                                           "idle_voltage": None, "uart_errors": 9})

    def test_no_attribute_is_one_home_assistant_blocks(self):
        # HA drops a JSON attribute that shadows an entity property. The
        # list is MQTT_ATTRIBUTES_BLOCKED in HA's mqtt integration.
        message = health_message(answered(), answered())

        self.assertEqual(set(message) & BLOCKED_ATTRIBUTES, set())


class FakeServo:
    """One servo on a FakeBus: its memory table, and how it misbehaves.

    ignored_writes writes get their reply but change nothing, missed_pings
    pings get no reply, and a servo that doesn't answer never replies. Every
    reply carries error as its ERROR byte.
    """

    def __init__(self, scs_id, duty=0, torque=0, voltage=85, temperature=21,
                 status=0, error=0, ignored_writes=0, missed_pings=0,
                 answers=True):
        self.id = scs_id
        self.memory = bytearray(Address.PRESENT_CURRENT_H + 1)
        # Words are big-endian: the high byte sits at the _L address.
        self.memory[Address.GOAL_TIME_L] = duty >> 8
        self.memory[Address.GOAL_TIME_H] = duty & 0xFF
        self.memory[Address.TORQUE_ENABLE] = torque
        self.memory[Address.PRESENT_VOLTAGE] = voltage
        self.memory[Address.PRESENT_TEMPERATURE] = temperature
        self.memory[Address.STATUS] = status
        self.error = error
        self.ignored_writes = ignored_writes
        self.missed_pings = missed_pings
        self.answers = answers

    @property
    def duty(self):
        return (self.memory[Address.GOAL_TIME_L] << 8) | self.memory[Address.GOAL_TIME_H]

    @property
    def torque(self):
        return self.memory[Address.TORQUE_ENABLE]


class FakeBus:
    """A servo bus that works like the servos: a WRITE stores its bytes in
    the servo's memory table, a READ returns them, a PING just answers.
    read() returns None when nothing came, like busio.UART's."""

    def __init__(self, *servos):
        self.servos = {servo.id: servo for servo in servos}
        self.requests = []
        self.pending = b""
        self.timeout = 1.0

    def reset_input_buffer(self):
        self.pending = b""

    def write(self, request):
        scs_id, instruction, params = request[2], request[4], bytes(request[5:-1])
        self.requests.append((scs_id, instruction, params))
        servo = self.servos.get(scs_id)
        if servo is None or not servo.answers:
            return
        if instruction == Instruction.PING and servo.missed_pings:
            servo.missed_pings -= 1
            return
        data = b""
        if instruction == Instruction.WRITE:
            if servo.ignored_writes:
                servo.ignored_writes -= 1
            else:
                servo.memory[params[0]:params[0] + len(params) - 1] = params[1:]
        elif instruction == Instruction.READ:
            address, n = params
            data = bytes(servo.memory[address:address + n])
        body = bytes((scs_id, len(data) + 2, servo.error)) + data
        self.pending = b"\xff\xff" + body + bytes((checksum(body),))

    def read(self, nbytes):
        if not self.pending:
            return None
        data, self.pending = self.pending[:nbytes], self.pending[nbytes:]
        return data


class BootReinitTest(unittest.TestCase):
    def test_a_lift_left_driving_is_stopped_first_and_both_servos_left_limp(self):
        lift = FakeServo(1, duty=800, torque=1)
        tilt = FakeServo(2, torque=1)
        bus = FakeBus(lift, tilt)

        lift_read, tilt_read = boot_reinit(Reader(bus))

        # Duty 0 stops a stalled lift at once (#21), so it goes out first.
        self.assertEqual(bus.requests[0],
                         (1, Instruction.WRITE, bytes((Address.GOAL_TIME_L, 0, 0))))
        self.assertEqual((lift.duty, lift.torque, tilt.torque), (0, 0, 0))
        self.assertTrue(lift_read.stop_confirmed)
        self.assertTrue(tilt_read.stop_confirmed)

    def test_a_write_that_doesnt_take_is_retried_until_it_reads_back(self):
        lift = FakeServo(1, duty=800, torque=1, ignored_writes=2)
        tilt = FakeServo(2, torque=1)

        lift_read, _ = boot_reinit(Reader(FakeBus(lift, tilt)))

        self.assertEqual((lift.duty, lift.torque), (0, 0))
        self.assertTrue(lift_read.stop_confirmed)

    def test_a_stop_that_never_reads_back_is_unconfirmed(self):
        lift = FakeServo(1, duty=800, torque=1, ignored_writes=100)
        tilt = FakeServo(2, torque=1)

        lift_read, tilt_read = boot_reinit(Reader(FakeBus(lift, tilt)))

        self.assertFalse(lift_read.stop_confirmed)
        self.assertTrue(tilt_read.stop_confirmed)

    def test_the_health_read_reaches_the_message(self):
        # The lift warmed and pulled the supply down; the tilt reports
        # overload in its ERROR byte.
        lift = FakeServo(1, duty=800, torque=1, voltage=74, temperature=31)
        tilt = FakeServo(2, error=0x20)

        message = health_message(*boot_reinit(Reader(FakeBus(lift, tilt))))

        self.assertEqual(message["health"], "error")
        self.assertEqual(message["lift"], {"health": "ok", "temperature": 31,
                                           "idle_voltage": 7.4, "uart_errors": 0})
        self.assertEqual(message["tilt"]["health"], "error")

    def test_a_missed_ping_is_retried(self):
        # One lost reply mustn't have HA power-cycle the outlet.
        lift = FakeServo(1, missed_pings=1)

        message = health_message(*boot_reinit(Reader(FakeBus(lift, FakeServo(2)))))

        self.assertEqual(message["lift"]["health"], "ok")
        self.assertEqual(message["lift"]["uart_errors"], 1)

    def test_a_servo_that_never_answers_is_no_reply_and_the_other_still_stops(self):
        lift = FakeServo(1, duty=800, torque=1)
        tilt = FakeServo(2, answers=False)

        message = health_message(*boot_reinit(Reader(FakeBus(lift, tilt))))

        self.assertEqual((lift.duty, lift.torque), (0, 0))
        self.assertEqual(message["health"], "no_reply")
        self.assertEqual(message["lift"]["health"], "ok")
        self.assertEqual(message["tilt"]["temperature"], None)


if __name__ == "__main__":
    unittest.main()
