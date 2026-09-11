import errno
import unittest

from send_loop import send_all

# supervisor.ticks_ms() wraps around every 2**29 ms, about 6.2 days.
TICKS_PERIOD = 1 << 29


class FakeClock:
    """A millisecond clock that only moves when the test moves it, and wraps
    like supervisor.ticks_ms()."""

    def __init__(self, now=0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, ms):
        self.now = (self.now + ms) % TICKS_PERIOD


class FullBufferSocket:
    """A socket whose send buffer never drains: every send() raises EAGAIN."""

    MAX_CALLS = 100_000

    def __init__(self, clock, ms_per_call):
        self.clock = clock
        self.ms_per_call = ms_per_call
        self.calls = 0

    def send(self, data):
        self.calls += 1
        if self.calls > self.MAX_CALLS:
            raise AssertionError("send_all never gave up")
        self.clock.advance(self.ms_per_call)
        raise OSError(errno.EAGAIN, "try again")


class TrickleSocket:
    """A socket that takes a few bytes per send() and raises EAGAIN on every
    other call, the way a nearly full send buffer drains."""

    def __init__(self, clock, bytes_per_call):
        self.clock = clock
        self.bytes_per_call = bytes_per_call
        self.received = bytearray()
        self.calls = 0

    def send(self, data):
        self.clock.advance(1)
        self.calls += 1
        if self.calls % 2 == 0:
            raise OSError(errno.EAGAIN, "try again")
        taken = bytes(data[: self.bytes_per_call])
        self.received += taken
        return len(taken)


class ResetSocket:
    """A socket whose peer has gone: every send() raises ECONNRESET."""

    def __init__(self, clock):
        self.clock = clock
        self.error = OSError(errno.ECONNRESET, "connection reset")

    def send(self, data):
        self.clock.advance(10)
        raise self.error


class NoCountSocket:
    """A socket whose send() returns no byte count, as some versions do."""

    def __init__(self):
        self.calls = 0

    def send(self, data):
        self.calls += 1
        return None


class SendAllTest(unittest.TestCase):
    def test_full_send_buffer_times_out_at_the_deadline(self):
        clock = FakeClock()
        sock = FullBufferSocket(clock, ms_per_call=10)

        with self.assertRaises(OSError) as caught:
            send_all(sock, b"x" * 100, 2000, clock)

        self.assertEqual(caught.exception.errno, errno.ETIMEDOUT)
        self.assertGreaterEqual(clock.now, 2000)
        self.assertLessEqual(clock.now, 2010)

    def test_deadline_holds_across_a_tick_wraparound(self):
        clock = FakeClock(now=TICKS_PERIOD - 500)
        sock = FullBufferSocket(clock, ms_per_call=10)

        with self.assertRaises(OSError) as caught:
            send_all(sock, b"x" * 100, 2000, clock)

        self.assertEqual(caught.exception.errno, errno.ETIMEDOUT)
        # 500 ms up to the wrap, then 1,500 ms past it.
        self.assertGreaterEqual(clock.now, 1500)
        self.assertLessEqual(clock.now, 1510)

    def test_partial_sends_deliver_the_whole_buffer_in_order(self):
        clock = FakeClock()
        sock = TrickleSocket(clock, bytes_per_call=7)
        payload = bytes(range(100))

        send_all(sock, payload, 2000, clock)

        self.assertEqual(bytes(sock.received), payload)

    def test_other_socket_errors_pass_through_unchanged(self):
        clock = FakeClock()
        sock = ResetSocket(clock)

        with self.assertRaises(OSError) as caught:
            send_all(sock, b"x" * 100, 2000, clock)

        self.assertIs(caught.exception, sock.error)

    def test_send_without_a_byte_count_is_taken_as_complete(self):
        sock = NoCountSocket()

        send_all(sock, b"x" * 100, 2000, FakeClock())

        self.assertEqual(sock.calls, 1)


if __name__ == "__main__":
    unittest.main()
