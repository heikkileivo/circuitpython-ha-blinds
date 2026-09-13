"""Host test 8: servo packets encoded and replies parsed, with a fake UART.

The expected bytes are worked out by hand from the SCS protocol: a request
is FF FF ID LEN INSTRUCTION params CHK, a reply FF FF ID LEN ERROR data CHK,
and CHK is ~(sum of ID..last param) & 0xFF. The bench's PING reply
ffff010200fc is one real reply.
"""

import unittest

from packet import Address, Reader

# The bench's reply to a PING of the lift servo: ID 1, no error.
OK_STATUS_REPLY = bytes.fromhex("ffff010200fc")


class FakeUart:
    """A servo bus that answers each request with the next scripted reply.

    A reply is (bytes, latency in seconds). read() returns what has arrived,
    at most the bytes asked for, and None when nothing arrived within
    timeout, like busio.UART.
    """

    def __init__(self, *replies):
        self.replies = list(replies)
        self.pending = bytearray()
        self.latency = 0.0
        self.written = []
        self.timeout = 1.0

    def reset_input_buffer(self):
        self.pending = bytearray()

    def write(self, data):
        self.written.append(bytes(data))
        if self.replies:
            reply, self.latency = self.replies.pop(0)
            self.pending += reply

    def read(self, nbytes):
        if not self.pending or self.latency > self.timeout:
            return None
        data = bytes(self.pending[:nbytes])
        del self.pending[:nbytes]
        return data


def reply(data, latency=0.002):
    return (bytes.fromhex(data), latency)


class ReaderTest(unittest.TestCase):
    def test_block_read_returns_the_error_byte_and_the_data(self):
        uart = FakeUart(reply("ffff0106" "00" "02ab03e8" "60"))
        reader = Reader(uart)

        result = reader.read(1, Address.PRESENT_POSITION_L, 4)

        self.assertEqual(uart.written, [bytes.fromhex("ffff0104023804bc")])
        self.assertEqual(result, (0, b"\x02\xab\x03\xe8"))

    def test_the_error_byte_is_passed_through(self):
        # Bit 5 is overload. The reply is still good, so the data comes back.
        uart = FakeUart(reply("ffff0103" "20" "1f" "bc"))
        reader = Reader(uart)

        result = reader.read(1, Address.PRESENT_TEMPERATURE, 1)

        self.assertEqual(result, (0x20, b"\x1f"))
        self.assertEqual(reader.uart_errors(1), 0)

    def test_a_bad_reply_is_no_reply_and_counts_a_uart_error(self):
        # The good reply to this read would be ffff0103001fdc.
        bad_replies = {
            "bad checksum": [reply("ffff0103001fdd")],
            "status reply instead of data": [reply("ffff010200fc")],
            "cut off": [reply("ffff0103")],
            "no reply": [],
            "wrong id": [reply("ffff0203001fdb")],
            "bad header": [reply("fffe0103001fdc")],
        }
        for name, replies in bad_replies.items():
            with self.subTest(name):
                reader = Reader(FakeUart(*replies))

                result = reader.read(1, Address.PRESENT_TEMPERATURE, 1)

                self.assertIsNone(result)
                self.assertEqual(reader.uart_errors(1), 1)

    def test_a_word_write_sends_the_high_byte_first_and_returns_the_error_byte(self):
        uart = FakeUart(reply("ffff0102" "20" "dc"))
        reader = Reader(uart)

        error = reader.write_word(1, Address.GOAL_TIME_L, 800)

        self.assertEqual(uart.written, [bytes.fromhex("ffff0105032c0320a7")])
        self.assertEqual(error, 0x20)

    def test_an_eeprom_write_waits_longer_for_its_reply(self):
        # The bench's first EEPROM write replied after more than 10 ms. A
        # read straight after it is back on the short timeout.
        uart = FakeUart(reply("ffff010200fc", latency=0.03),
                        reply("ffff0103001fdc", latency=0.03))
        reader = Reader(uart)

        error = reader.write_byte(1, Address.ID, 1)
        result = reader.read(1, Address.PRESENT_TEMPERATURE, 1)

        self.assertEqual(error, 0)
        self.assertIsNone(result)

    def test_the_register_helpers_return_the_value(self):
        # 683 is the lift's servo angle at the bench's up end sensor.
        reader = Reader(FakeUart(reply("ffff0104" "00" "02ab" "4d"),
                                 reply("ffff0103001fdc")))

        self.assertEqual(reader.read_2_bytes(1, Address.PRESENT_POSITION_L), 683)
        self.assertEqual(reader.read_1_byte(1, Address.PRESENT_TEMPERATURE), 31)

    def test_the_servo_angle_and_speed_come_from_one_block_read(self):
        # Closing at duty 800: angle 1000 (03e8), speed -1400. PRESENT_SPEED
        # is sign and magnitude, with the sign in bit 15 (8578).
        uart = FakeUart(reply("ffff0106" "00" "03e8" "8578" "10"))
        reader = Reader(uart)

        result = reader.read_angle_and_speed(1)

        self.assertEqual(uart.written, [bytes.fromhex("ffff0104023804bc")])
        self.assertEqual(result, (1000, -1400))

    def test_the_register_helpers_return_none_without_a_good_reply(self):
        reader = Reader(FakeUart())

        self.assertIsNone(reader.read_2_bytes(1, Address.PRESENT_POSITION_L))
        self.assertIsNone(reader.read_1_byte(1, Address.PRESENT_TEMPERATURE))
        self.assertIsNone(reader.read_angle_and_speed(1))

    def test_each_servo_keeps_its_own_uart_error_count(self):
        reader = Reader(FakeUart())

        reader.read(2, Address.PRESENT_TEMPERATURE, 1)
        reader.read(2, Address.PRESENT_TEMPERATURE, 1)

        self.assertEqual(reader.uart_errors(2), 2)
        self.assertEqual(reader.uart_errors(1), 0)


if __name__ == "__main__":
    unittest.main()
