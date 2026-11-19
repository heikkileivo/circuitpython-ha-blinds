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

    def test_the_lifts_motion_sample_comes_from_one_block_read(self):
        # Closing at duty 800: angle 1000 (03e8), speed -1400. PRESENT_SPEED
        # is sign and magnitude, with the sign in bit 15 (8578). The load
        # reads the commanded duty, with its sign in bit 10 (0320), and the
        # supply 8.1 V (51).
        uart = FakeUart(reply("ffff0109" "00" "03e8" "8578" "0320" "51" "99"))
        reader = Reader(uart)

        result = reader.read_motion(1)

        self.assertEqual(uart.written, [bytes.fromhex("ffff0104023807b9")])
        self.assertEqual(result, (1000, -1400, 800, 81))

    def test_the_head_rail_stall_reads_a_negative_load_and_the_sagging_supply(self):
        # The bench's head-rail stall, going up at duty -800: angle and speed
        # 0, the load -800 (0720) and the supply down to 5.8 V (3a).
        reader = Reader(FakeUart(reply("ffff0109" "00" "0000" "0000" "0720" "3a" "94")))

        self.assertEqual(reader.read_motion(1), (0, 0, -800, 58))

    def test_the_moving_flag_comes_with_the_load_and_supply_in_one_block_read(self):
        # The tilt servo driving: load -150 (0496), 7.9 V (4f), 22 C,
        # status 0, moving.
        uart = FakeUart(reply("ffff0209" "00" "0496" "4f" "16" "00" "00" "01" "f4"))
        reader = Reader(uart)

        result = reader.read_moving(2)

        self.assertEqual(uart.written, [bytes.fromhex("ffff0204023c07b4")])
        self.assertEqual(result, (-150, 79, True))

    def test_the_register_helpers_return_none_without_a_good_reply(self):
        reader = Reader(FakeUart())

        self.assertIsNone(reader.read_2_bytes(1, Address.PRESENT_POSITION_L))
        self.assertIsNone(reader.read_1_byte(1, Address.PRESENT_TEMPERATURE))
        self.assertIsNone(reader.read_motion(1))
        self.assertIsNone(reader.read_moving(2))

    def test_a_ping_returns_the_error_byte(self):
        uart = FakeUart(reply("ffff010200fc"))
        reader = Reader(uart)

        error = reader.ping(1)

        self.assertEqual(uart.written, [bytes.fromhex("ffff010201fb")])
        self.assertEqual(error, 0)

    def test_a_ping_without_a_good_reply_is_none(self):
        reader = Reader(FakeUart())

        self.assertIsNone(reader.ping(1))
        self.assertEqual(reader.uart_errors(1), 1)

    def test_each_servo_keeps_its_own_uart_error_count(self):
        reader = Reader(FakeUart())

        reader.read(2, Address.PRESENT_TEMPERATURE, 1)
        reader.read(2, Address.PRESENT_TEMPERATURE, 1)

        self.assertEqual(reader.uart_errors(2), 2)
        self.assertEqual(reader.uart_errors(1), 0)


if __name__ == "__main__":
    unittest.main()


def status(scs_id, data):
    """A reply from servo scs_id with no error and these data bytes."""
    body = bytes([scs_id, len(data) + 2, 0]) + bytes(data)
    return (b"\xff\xff" + body + bytes([~sum(body) & 0xFF]), 0.002)


class SeriesTest(unittest.TestCase):
    """STS3215 values are little-endian, where the SCS's are big-endian (#138).
    The registers and bit positions come from Feetech's FTServo SMS_STS."""

    def sts_reader(self, *replies):
        uart = FakeUart(status(1, [9, 3]), *replies)
        reader = Reader(uart)
        self.assertEqual(reader.detect(1), "sts")
        uart.written.clear()
        return reader, uart

    def test_a_servo_is_an_scs_until_detected(self):
        self.assertEqual(Reader(FakeUart()).series(1), "scs")

    def test_model_number_777_is_an_sts_and_anything_else_that_replies_an_scs(self):
        reader = Reader(FakeUart(status(1, [9, 3]), status(2, [0, 0])))

        self.assertEqual(reader.detect(1), "sts")
        self.assertEqual(reader.detect(2), "scs")
        self.assertEqual((reader.series(1), reader.series(2)), ("sts", "scs"))

    def test_detect_reads_registers_3_and_4(self):
        uart = FakeUart(status(1, [9, 3]))

        Reader(uart).detect(1)

        self.assertEqual(uart.written, [bytes.fromhex("ffff010402030" "2f3")])

    def test_a_silent_servo_isnt_detected_and_keeps_its_series(self):
        reader = Reader(FakeUart(status(1, [9, 3])), log=False)
        reader.detect(1)
        reader.uart.replies.clear()

        self.assertIsNone(reader.detect(1))
        self.assertEqual(reader.series(1), "sts")

    def test_an_sts_reads_words_low_byte_first(self):
        reader, _ = self.sts_reader(status(1, [0xAB, 0x02]))

        self.assertEqual(reader.read_2_bytes(1, Address.PRESENT_POSITION_L), 683)

    def test_an_sts_writes_words_low_byte_first(self):
        reader, uart = self.sts_reader(status(1, []))

        reader.write_word(1, Address.GOAL_POSITION_L, 0x0FFF)

        self.assertEqual(uart.written[0][5:8], bytes([Address.GOAL_POSITION_L, 0xFF, 0x0F]))

    def test_an_sts_motion_sample_is_little_endian_with_the_same_sign_bits(self):
        # Angle 4000 (0fa0), speed -1400 (sign bit 15: 8578), load -800
        # (sign bit 10: 0720), supply 8.1 V.
        reader, _ = self.sts_reader(
            status(1, [0xA0, 0x0F, 0x78, 0x85, 0x20, 0x07, 0x51]))

        self.assertEqual(reader.read_motion(1), (4000, -1400, -800, 81))

    def test_an_sts_moving_sample_decodes_the_load_little_endian(self):
        reader, _ = self.sts_reader(
            status(1, [0x96, 0x04, 0x4F, 0x16, 0x00, 0x00, 0x01]))

        self.assertEqual(reader.read_moving(1), (-150, 79, True))

    def test_the_lock_is_at_48_on_an_scs_and_55_on_an_sts(self):
        reader, _ = self.sts_reader()

        self.assertEqual(reader.lock_address(1), 55)
        self.assertEqual(reader.lock_address(2), Address.LOCK)

    def test_nothing_that_moves_an_sts_is_sent_until_it_has_a_driver(self):
        reader, uart = self.sts_reader()

        self.assertIsNone(reader.set_time(1, 800))
        self.assertIsNone(reader.set_speed(1, 800))
        self.assertIsNone(reader.set_position(1, 100))
        self.assertEqual(uart.written, [])

    def test_the_scs_still_moves(self):
        uart = FakeUart(status(2, []))

        self.assertEqual(Reader(uart).set_time(2, 800), 0)
        self.assertEqual(len(uart.written), 1)
