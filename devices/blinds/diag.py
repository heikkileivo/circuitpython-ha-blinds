"""THROWAWAY for #82, never merged: what each boot looks like, so a real
deep sleep can be told from a faked one.

A faked deep sleep keeps the chip's reset reason and monotonic time, and it
only happens while usb_connected (tud_ready) or the serial websocket
(part of serial_connected) says a host is there. wifi says whether Wi-Fi was
already up when code.py started, which only the web workflow's own connect
does, so it's 0 when that connect failed and port 80 stayed off.
"""

import alarm
import supervisor
import time
import wifi

import reset_cause

# Sleep memory from offset 2: the state just before the last firmware
# restart, as a length byte and then ASCII. Offsets 0 and 1 are
# reset_cause's.
_PREV_AT = 2
_PREV_MAX = 200

_RUN_REASONS = ("STARTUP", "AUTO_RELOAD", "SUPERVISOR_RELOAD", "REPL_RELOAD")


def _run_reason():
    reason = supervisor.runtime.run_reason
    for name in _RUN_REASONS:
        if getattr(supervisor.RunReason, name, None) == reason:
            return name
    return str(reason)


def now(tag):
    """This moment's state, in one line."""
    memory = alarm.sleep_memory
    return (tag + " chip=" + reset_cause._chip_reason()
            + " run=" + _run_reason()
            + " mono=" + str(round(time.monotonic(), 1))
            + " mem=%02x%02x" % (memory[0], memory[1])
            + " usb=" + str(int(supervisor.runtime.usb_connected))
            + " ser=" + str(int(supervisor.runtime.serial_connected))
            + " wifi=" + str(int(wifi.radio.connected))
            + " wake=" + ("none" if alarm.wake_alarm is None else "alarm"))


def remember(tag):
    """Keep this moment's state in sleep memory, which a deep sleep keeps,
    for the next boot to publish."""
    line = now(tag).encode()[:_PREV_MAX]
    alarm.sleep_memory[_PREV_AT + 1:_PREV_AT + 1 + len(line)] = line
    alarm.sleep_memory[_PREV_AT] = len(line)


def take_previous():
    """The state remember() kept, or None. Cleared, so a soft reload doesn't
    report it again."""
    length = alarm.sleep_memory[_PREV_AT]
    if length == 0 or length > _PREV_MAX:
        return None
    line = bytes(alarm.sleep_memory[_PREV_AT + 1:_PREV_AT + 1 + length])
    alarm.sleep_memory[_PREV_AT] = 0
    try:
        return line.decode()
    except UnicodeError:
        return None
