"""Servo health: whether the blind's servos answer and report no error, as
published to Home Assistant, with the servo diagnostics behind it: each
servo's lowest supply voltage and peak load during a move, and its idle
voltage and temperature. Also the servos' stop writes, with the lift's stop
sequence, the boot re-init that stops the servos, and the health reads at
boot and while idle. The classification and the move figures are pure, and
the reads and writes only need a Reader, so the host tests run them all, the
reads and writes on a fake servo bus."""

from packet import Address
import lift_stop
from servo_bus import LIFT_ID, TILT_ID

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
    """One servo's health read, at boot or while idle. error is None when
    the servo gave no good reply."""

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


# Each write of the lift's stop sequence, as (address, data).
_STOP_WRITES = {lift_stop.DUTY_0: (Address.GOAL_TIME_L, [0, 0]),
                lift_stop.BRAKE: (Address.TORQUE_ENABLE, [2]),
                lift_stop.FALLBACK_BRAKE: (Address.TORQUE_ENABLE, [1]),
                lift_stop.LIMP: (Address.TORQUE_ENABLE, [0])}


def stop_servos(reader):
    """Stop both servos, which a controller reset leaves doing whatever
    they were doing: the lift with its stop sequence, which leaves it
    braking, and the tilt limp. Returns how the lift's stop sequence ended,
    and whether the tilt's torque off was confirmed."""
    return stop_lift(reader), torque_off(reader, TILT_ID)


def stop_lift(reader, duty_0_outcome=None):
    """Carry out the lift's stop sequence (lift_stop). duty_0_outcome is the
    outcome of a duty 0 the caller has written already, or None to start
    with it. Returns how the sequence ended."""
    step, outcome = ((None, None) if duty_0_outcome is None
                     else (lift_stop.DUTY_0, duty_0_outcome))
    while True:
        step = lift_stop.next_step(step, outcome)
        if step in lift_stop.ENDS:
            break
        outcome = _write_checked(reader, LIFT_ID, *_STOP_WRITES[step])
        print(f"Lift stop: {step} {outcome}.")
    if step == lift_stop.BRAKE_UNCONFIRMED:
        # The motor has stopped anyway, so main() doesn't fail.
        reader.count_uart_error(LIFT_ID)
    print(f"Lift stop: {step}.")
    return step


def write_duty_0(reader, scs_id):
    """Write duty 0 to a servo in wheel mode, and read it back. Returns the
    read-back's outcome, one of lift_stop's."""
    return _write_checked(reader, scs_id, *_STOP_WRITES[lift_stop.DUTY_0])


def torque_off(reader, scs_id):
    """Turn a servo's torque off, leaving it limp, and read it back. Returns
    whether it's confirmed off."""
    return _write_checked(reader, scs_id, *_STOP_WRITES[lift_stop.LIMP]) == lift_stop.CONFIRMED


def boot_reinit(reader):
    """Stop both servos, which leaves the lift braking, then read their
    health. Returns the lift and tilt servos' health reads."""
    lift_end, tilt_stop_confirmed = stop_servos(reader)
    return (_health_read(reader, LIFT_ID, lift_stop.stopped(lift_end)),
            _health_read(reader, TILT_ID, tilt_stop_confirmed))


def idle_reads(reader, lift_stop_confirmed=True):
    """Read both servos' health while neither is driving: after a move has
    settled, and now and then while the blind is idle. Returns the lift and
    tilt servos' health reads. The moving flag is only logged, as at boot.
    After a lift stop that wasn't confirmed, lift_stop_confirmed is False, so
    the lift's health is an error, or no reply."""
    return (_health_read(reader, LIFT_ID, lift_stop_confirmed),
            _health_read(reader, TILT_ID, True))


def _health_read(reader, scs_id, stop_confirmed):
    """Ping the servo, and read 62-66 (voltage, temperature, async write
    flag, status, moving) in one transaction."""
    ping_error = first_reply(lambda: reader.ping(scs_id))
    block = None if ping_error is None else first_reply(
        lambda: reader.read(scs_id, Address.PRESENT_VOLTAGE, 5))
    if block is None:
        print(f"Servo {scs_id}: no reply to the health read.")
        return ServoRead(stop_confirmed, uart_errors=reader.uart_errors(scs_id))
    error, data = block
    voltage, temperature, _, status, moving = data
    # CircuitPython rejects two adjacent f-strings, so + joins them.
    print(f"Servo {scs_id}: stop_confirmed {stop_confirmed}, ERROR {ping_error | error:#04x}, "
          + f"status {status:#04x}, moving {moving}, {voltage / 10} V, {temperature} C")
    return ServoRead(stop_confirmed, error=ping_error | error, voltage=voltage,
                     temperature=temperature, status=status,
                     uart_errors=reader.uart_errors(scs_id))


def first_reply(transaction):
    """Run a transaction up to ATTEMPTS times. Returns its first result that
    isn't None, or None."""
    for _ in range(ATTEMPTS):
        result = transaction()
        if result is not None:
            return result
    return None


def _write_checked(reader, scs_id, address, data):
    """Write data from address onwards and read it back, up to ATTEMPTS
    times. Returns lift_stop's CONFIRMED once it reads back as written.
    Otherwise REFUSED if the servo ever answered with another value, as a
    write may have been lost, or NO_REPLY."""
    outcome = lift_stop.NO_REPLY
    for _ in range(ATTEMPTS):
        reader.write_mem(scs_id, address, data)
        reply = reader.read(scs_id, address, len(data))
        if reply is not None:
            if list(reply[1]) == data:
                return lift_stop.CONFIRMED
            outcome = lift_stop.REFUSED
    return outcome


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
