"""MiniMQTT's socket send loop, bounded by a deadline.

Pure: imports nothing CircuitPython-only, so host tests run it on CPython.
"""

import errno

# supervisor.ticks_ms() wraps around every 2**29 ms, about 6.2 days.
_TICKS_PERIOD = 1 << 29


def send_all(sock, buffer, timeout_ms, ticks_ms):
    """Send all of buffer on sock, retrying EAGAIN for at most timeout_ms.

    Repeats MiniMQTT 8.1.0's `_send_bytes()` loop, plus the deadline.
    ticks_ms is a millisecond clock that wraps like supervisor.ticks_ms().
    Raises OSError(ETIMEDOUT) once the deadline passes.
    """
    start = ticks_ms()
    bytes_sent = 0
    bytes_to_send = len(buffer)
    view = memoryview(buffer)
    while bytes_sent < bytes_to_send:
        try:
            sent_now = sock.send(view[bytes_sent:])
            # Some versions of `Socket.send()` do not return the number of bytes sent.
            if not isinstance(sent_now, int):
                return
            bytes_sent += sent_now
        except OSError as exc:
            if exc.errno != errno.EAGAIN:
                raise
            if (ticks_ms() - start) % _TICKS_PERIOD >= timeout_ms:
                # Two args so CPython sets .errno too; CircuitPython reads args[0].
                raise OSError(errno.ETIMEDOUT, "send timed out")
