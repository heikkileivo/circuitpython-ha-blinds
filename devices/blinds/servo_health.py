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

    def __init__(self, stop_confirmed, error=None, voltage=None, temperature=None,
                 status=None, uart_errors=0):
        self.stop_confirmed = stop_confirmed
        self.error = error
        self.voltage = voltage
        self.temperature = temperature
        self.status = status
        self.uart_errors = uart_errors


class MoveFigures:
    """One servo's figures during one move: the lowest supply voltage it
    reported, in 0.1 V, and its peak load, whichever way it drove. Both are
    None until a sample comes."""

    def __init__(self):
        self.min_voltage = None
        self.peak_load = None

    def feed(self, voltage, load):
        """Take one sample: the supply voltage in 0.1 V, and the load."""
        if self.min_voltage is None or voltage < self.min_voltage:
            self.min_voltage = voltage
        load = abs(load)
        if self.peak_load is None or load > self.peak_load:
            self.peak_load = load


def servo_min_voltage(lift_move, tilt_move):
    """The servo_min_voltage sensor's value in V: the lowest supply voltage
    either servo reported during the move, or None if neither reported
    one."""
    voltages = [move.min_voltage for move in (lift_move, tilt_move)
                if move.min_voltage is not None]
    return _volts(min(voltages)) if voltages else None


def stop_servos(reader):
    """Stop both servos, which a controller reset leaves doing whatever
    they were doing. Returns whether the lift's and the tilt's stops were
    confirmed."""
    # Duty 0 first: it stops a stalled lift at once. Torque off then leaves
    # both limp, and is written even if the duty wasn't confirmed.
    lift_duty_0 = _write_zero(reader, LIFT_ID, Address.GOAL_TIME_L, 2)
    lift_limp = _write_zero(reader, LIFT_ID, Address.TORQUE_ENABLE, 1)
    tilt_limp = _write_zero(reader, TILT_ID, Address.TORQUE_ENABLE, 1)
    return lift_duty_0 and lift_limp, tilt_limp


def boot_reinit(reader):
    """Stop both servos, then read their health. Returns the lift and tilt
    servos' health reads."""
    lift_stop_confirmed, tilt_stop_confirmed = stop_servos(reader)
    return (_health_read(reader, LIFT_ID, lift_stop_confirmed),
            _health_read(reader, TILT_ID, tilt_stop_confirmed))


def idle_reads(reader):
    """Read both servos' health while neither is driving: after a move has
    settled, and now and then while the blind is idle. Returns the lift and
    tilt servos' health reads. A servo that still moves wasn't stopped."""
    return _health_read(reader, LIFT_ID), _health_read(reader, TILT_ID)


def _health_read(reader, scs_id, stop_confirmed=None):
    """Ping the servo, and read 62-66 (voltage, temperature, async write
    flag, status, moving) in one transaction. Without a stop_confirmed from
    a stop just written, the stop is confirmed if the servo isn't moving."""
    ping_error = _first_reply(lambda: reader.ping(scs_id))
    block = None if ping_error is None else _first_reply(
        lambda: reader.read(scs_id, Address.PRESENT_VOLTAGE, 5))
    if block is None:
        print(f"Servo {scs_id}: no reply to the health read.")
        return ServoRead(bool(stop_confirmed), uart_errors=reader.uart_errors(scs_id))
    error, data = block
    voltage, temperature, _, status, moving = data
    if stop_confirmed is None:
        stop_confirmed = not moving
    print(f"Servo {scs_id}: stop_confirmed {stop_confirmed}, ERROR {ping_error | error:#04x}, "
          f"status {status:#04x}, moving {moving}, {voltage / 10} V, {temperature} C")
    return ServoRead(stop_confirmed, error=ping_error | error, voltage=voltage,
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
    if read.error or read.status or not read.stop_confirmed:
        return ERROR
    return OK


def health_message(lift, tilt, lift_move=None, tilt_move=None):
    """The servo_health entity's JSON message, from the lift and tilt
    servos' health reads and their figures from the last move, if there's
    been one: the health, the worse of the two, which the entity shows, and
    each servo's own figures as attributes."""
    lift_health = classify(lift)
    tilt_health = classify(tilt)
    return {"health": max(lift_health, tilt_health, key=HEALTHS.index),
            "lift": _attributes(lift, lift_health, lift_move or MoveFigures()),
            "tilt": _attributes(tilt, tilt_health, tilt_move or MoveFigures())}


def _attributes(read, health, move):
    # PRESENT_VOLTAGE is in 0.1 V. The health read is only taken with both
    # servos stopped, so its voltage is the idle supply.
    return {"health": health,
            "temperature": read.temperature,
            "idle_voltage": _volts(read.voltage),
            "min_voltage": _volts(move.min_voltage),
            "peak_load": move.peak_load,
            "uart_errors": read.uart_errors}


def _volts(voltage):
    return None if voltage is None else voltage / 10
