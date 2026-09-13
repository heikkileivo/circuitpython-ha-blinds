"""Servo health: whether the blind's servos answer and report no error, as
published to Home Assistant, and the boot re-init that stops the servos and
reads it. The classification is pure, and the re-init only needs a Reader,
so the host tests run both, the re-init on a fake servo bus."""

from packet import Address

LIFT_ID = 1
TILT_ID = 2
# Attempts at each boot stop write (each checked by reading it back) and at
# each health read transaction.
ATTEMPTS = 3

OK = "ok"
NO_REPLY = "no_reply"
ERROR = "error"
# Best first. No reply is the worst: it tells nothing, and the servo may
# still be driving. It's what HA power-cycles the outlet for.
HEALTHS = (OK, ERROR, NO_REPLY)


class ServoRead:
    """One servo's health read at boot. error is None when the servo gave no
    good reply."""

    def __init__(self, stopped, error=None, voltage=None, temperature=None,
                 status=None, uart_errors=0):
        self.stopped = stopped
        self.error = error
        self.voltage = voltage
        self.temperature = temperature
        self.status = status
        self.uart_errors = uart_errors


def boot_reinit(reader):
    """Stop both servos, which a controller reset leaves doing whatever
    they were doing. Returns the lift and tilt servos' health reads."""
    # Duty 0 first: it stops a stalled lift at once. Torque off then leaves
    # both limp, and is written even if the duty wasn't confirmed.
    lift_duty_0 = _write_zero(reader, LIFT_ID, Address.GOAL_TIME_L, 2)
    lift_limp = _write_zero(reader, LIFT_ID, Address.TORQUE_ENABLE, 1)
    tilt_limp = _write_zero(reader, TILT_ID, Address.TORQUE_ENABLE, 1)
    return (_health_read(reader, LIFT_ID, lift_duty_0 and lift_limp),
            _health_read(reader, TILT_ID, tilt_limp))


def _health_read(reader, scs_id, stopped):
    """Ping the servo, and read 62-66 (voltage, temperature, async write
    flag, status, moving) in one transaction."""
    ping = _first_reply(lambda: reader.ping(scs_id))
    block = None if ping is None else _first_reply(
        lambda: reader.read(scs_id, Address.PRESENT_VOLTAGE, 5))
    if block is None:
        print(f"Servo {scs_id}: no reply to the health read.")
        return ServoRead(stopped, uart_errors=reader.uart_errors(scs_id))
    error, data = block
    voltage, temperature, _, status, moving = data
    print(f"Servo {scs_id}: stopped {stopped}, ERROR {ping | error:#04x}, "
          f"status {status:#04x}, moving {moving}, {voltage / 10} V, {temperature} C")
    return ServoRead(stopped, error=ping | error, voltage=voltage,
                     temperature=temperature, status=status,
                     uart_errors=reader.uart_errors(scs_id))


def _first_reply(transaction):
    """Run a transaction up to ATTEMPTS times. Returns its first result that
    isn't None, or None."""
    for _ in range(ATTEMPTS):
        result = transaction()
        if result is not None:
            return result
    return None


def _write_zero(reader, scs_id, address, n):
    """Write 0 to an n-byte register and read it back, up to ATTEMPTS
    times. Returns True once it reads back 0."""
    for _ in range(ATTEMPTS):
        reader.write_mem(scs_id, address, [0] * n)
        reply = reader.read(scs_id, address, n)
        if reply is not None and not any(reply[1]):
            return True
    return False


def classify(read):
    """One servo's health: ok, no_reply or error."""
    if read.error is None:
        return NO_REPLY
    if read.error or read.status or not read.stopped:
        return ERROR
    return OK


def health_message(lift, tilt):
    """The servo_health entity's JSON message, from the lift and tilt
    servos' health reads: the health, the worse of the two, which the entity
    shows, and each servo's own figures as attributes."""
    lift_health = classify(lift)
    tilt_health = classify(tilt)
    return {"health": max(lift_health, tilt_health, key=HEALTHS.index),
            "lift": _attributes(lift, lift_health),
            "tilt": _attributes(tilt, tilt_health)}


def _attributes(read, health):
    # PRESENT_VOLTAGE is in 0.1 V. The read follows the boot stop, so the
    # supply is idle.
    return {"health": health,
            "temperature": read.temperature,
            "idle_voltage": None if read.voltage is None else read.voltage / 10,
            "uart_errors": read.uart_errors}
