"""MiniMQTT client whose socket sends give up at a deadline.

From 7.11 on, MiniMQTT's `_send_bytes()` retries EAGAIN with no timeout. On
CircuitPython 9.1.x and 10.x alike, `send()` raises EAGAIN as soon as the send
buffer is full, so a publish on a dead link can hold the CPU past the watchdog.

Both halves were re-checked for the 10.3.1 upgrade (#128), and neither moved:
`socketpool_socket_send()` is byte-identical between 9.1.3 and 10.3.1, still
non-blocking and still ignoring the socket's timeout on send, and MiniMQTT
8.1.0 is still the newest release with the unbounded retry unfixed upstream.

This override is safe only because lib/ pins MiniMQTT 8.1.0. Any MiniMQTT bump
means re-checking it against the new `_send_bytes()`. On 7.10.0, which has no
`_send_bytes()`, the override is never called.
"""

import supervisor
import adafruit_minimqtt.adafruit_minimqtt as MQTT
from send_loop import send_all


class BoundedMQTT(MQTT.MQTT):
    """MQTT.MQTT whose sends raise OSError(ETIMEDOUT) after send_timeout seconds."""

    def __init__(self, *, send_timeout=2, **kwargs):
        self._send_timeout_ms = int(send_timeout * 1000)
        super().__init__(**kwargs)

    def _send_bytes(self, buffer):
        send_all(self._sock, buffer, self._send_timeout_ms, supervisor.ticks_ms)
