"""Recover from safe mode, where code.py never runs. CircuitPython runs this
on entering safe mode for any reason but a user's button press.

A lift stall can brown out the controller while the lift servo keeps
driving, because the servo's SRAM survives. So this first writes duty 0 to
the lift, over the UART. It doesn't broadcast, because the tilt servo is in
position mode. Then it waits, so a genuinely failing supply can't reboot the
board more often than that, and restarts with its cause. The boot after it
runs boot.py, which stops both servos properly.

The wait isn't a deep sleep: CircuitPython 9.1.1 fakes every deep sleep on
the blinds (#82). Storing the cause writes NVM, which is flash, so it comes
after the wait, with the lift stopped and the supply recovered.

Stays tiny: it doesn't import packet.py. The decision is pure, so the host
tests run it, and the functions that touch the chip import their modules
themselves.
"""

import reset_cause

# Duty 0 (GOAL_TIME = 0) written to the lift servo, ID 1: FF FF ID LEN
# WRITE address 44 (GOAL_TIME_L), then 00 00 and the checksum. No reply is
# awaited, and the torque is left as it is.
STOP_LIFT = b"\xff\xff\x01\x05\x03\x2c\x00\x00\xca"

WAIT_S = 30


def decision(reason):
    """What to do in safe mode, given the name of its reason, such as
    "BROWNOUT". Returns whether to stop the lift, the cause to restart with,
    and how long to wait before restarting, in seconds."""
    if reason == "BROWNOUT":
        return True, reset_cause.BROWNOUT, WAIT_S
    return True, reset_cause.OTHER_SAFE_MODE, WAIT_S


def recover():
    """Stop the lift, wait, then restart with the cause. Doesn't return."""
    import board
    import busio
    import time
    reason = _reason()
    stop_lift, cause, wait_s = decision(reason)
    print(f"Safe mode ({reason}): restarting in {wait_s} s.")
    if stop_lift:
        try:
            uart = busio.UART(board.TX, board.RX, baudrate=250000)
            uart.write(STOP_LIFT)
        except Exception as e:
            # The wait and the restart must still happen.
            print(f"Failed to stop the lift: {e!r}")
    time.sleep(wait_s)
    reset_cause.restart(cause)


def _reason():
    """The name of the safe-mode reason if it's a brownout, otherwise the
    reason as it prints."""
    import supervisor
    reason = supervisor.runtime.safe_mode_reason
    if reason == supervisor.SafeModeReason.BROWNOUT:
        return "BROWNOUT"
    return str(reason)


if __name__ == "__main__":
    recover()
