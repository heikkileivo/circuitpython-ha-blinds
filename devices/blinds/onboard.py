"""Onboarding a new servo for its role in the blind, lift or tilt (#99).

Deployed with the firmware, but never imported at boot. The host tool
(tools/onboard.py, #113) runs it from the REPL once Ctrl-C has stopped
code.py, and the operator confirms each manual step there.

Each step prints one marker line, MARKER then JSON with the step, whether it
went through ("ok") and what it read, so the host tool can find it among the
REPL's echo. A step that fails says so there, and nothing after it runs. A
half-done onboarding is recovered by running it again from the start: the
scan finds the servo wherever it was left.

The steps take the UART and the persistence store they use, so the host
tests run them on a fake servo bus.
"""

import json

from packet import Address, Reader
import persist
import servo_bus

MARKER = "ONBOARD "

# Feetech's baud rates, by the code at the baud rate register: 0 is 1 Mbps,
# which a factory servo starts at.
BAUD_RATES = (1000000, 500000, 250000, 128000, 115200, 76800, 57600, 38400)

# Every ID a servo can have: 254 is the broadcast ID.
IDS = range(254)

# Each role's ID and angle limits, min and max. The lift runs in wheel mode,
# which limits of 0/0 select. The tilt couldn't reach the bottom of its range
# at the SCS15's factory 20/1003.
ROLES = {"lift": (servo_bus.LIFT_ID, (0, 0)),
         "tilt": (servo_bus.TILT_ID, (10, 1000))}


def _big_endian(value):
    return [value >> 8, value & 0xFF]


def _from_big_endian(data):
    return (data[0] << 8) | data[1]


# Where each series keeps what onboarding writes, and how it encodes a word,
# by the series' name. STS joins with #100: its LOCK isn't at 48, which is
# its torque limit.
SERIES = {"scs": {"id": Address.ID,
                  "baud_rate": Address.BAUD_RATE,
                  "angle_limits": Address.MIN_ANGLE_LIMIT_L,
                  "lock": Address.LOCK,
                  "baud_rates": BAUD_RATES,
                  "encode": _big_endian,
                  "decode": _from_big_endian}}

# Registers 3-4 of an STS3215: model number 777, little-endian. Registers 0-4
# don't name an SCS model, so anything else is taken as an SCS.
STS3215_MODEL = [9, 3]


def series(registers):
    """The series of a servo whose registers 0-4 read registers."""
    return "sts" if list(registers[3:5]) == STS3215_MODEL else "scs"


def on_device():
    """An Onboarding on the blind's servo bus and its record in NVM, for the
    host tool to run from the REPL."""
    import board
    import busio
    import microcontroller
    uart = busio.UART(board.TX, board.RX, baudrate=servo_bus.BAUD_RATE,
                      receiver_buffer_size=32)
    return Onboarding(uart, persist.Store(microcontroller.nvm))


class Onboarding:
    """The steps of onboarding, on one UART. It changes the UART's baud rate
    as the scan and the servo need."""

    def __init__(self, uart, store, out=print):
        self._uart = uart
        self._reader = Reader(uart, log=False)
        self._store = store
        self._out = out

    def onboard(self, role):
        """Set up the one servo on the bus for role, "lift" or "tilt": scan
        for it, identify it, then write its settings, in the order that
        keeps a new tilt off ID 1 on the bus. Goes through if every step
        did."""
        if role not in ROLES:
            return self._mark("role", False, role=role)
        if not self.scan():
            return False
        table = self._identify()
        return table is not None and self._write(role, table)

    def scan(self):
        """Ping every ID at every baud rate. Goes through if exactly one
        servo answered, with no reply garbled, and leaves the UART at its
        baud rate. Each unanswered ping waits out the reply timeout, so a
        scan takes about 20 s."""
        found, garbled = self._scan()
        ok = len(found) == 1 and not garbled
        if ok:
            self._uart.baudrate = found[0]["baud_rate"]
            self._id = found[0]["id"]
        return self._mark("scan", ok, found=found, garbled=garbled)

    def verify(self, role):
        """After the operator has power-cycled the servo: rescan. Goes
        through if exactly one servo answers, at its role's ID and the bus
        rate, with its role's angle limits."""
        if role not in ROLES:
            return self._mark("role", False, role=role)
        role_id, limits = ROLES[role]
        found, garbled = self._scan()
        self._uart.baudrate = servo_bus.BAUD_RATE
        read = None
        if found == [{"baud_rate": servo_bus.BAUD_RATE, "id": role_id}] and not garbled:
            read = self._read_limits(role_id, self._table(role_id)[2])
        return self._mark("verify", read == list(limits), found=found, garbled=garbled,
                          angle_limits=read)

    def forget_travel(self, role):
        """Mark the stored travel unknown after a lift onboarding, keeping the
        full travel and the cover state: the spindle came out, so the travel
        is stale. A tilt onboarding leaves the store alone."""
        if role not in ROLES:
            return self._mark("role", False, role=role)
        if role != "lift":
            return self._mark("forget_travel", True, forgot=False)
        store = self._store
        # An unknown travel is left as it is: a blank record's unknown cover
        # state can't be saved.
        if store.travel == store.travel:
            store.save(store.state, persist.NAN, store.full_travel)
        full_travel = store.full_travel
        return self._mark("forget_travel", True, forgot=True,
                          full_travel=full_travel if full_travel == full_travel else None)

    def bus_check(self):
        """After the operator has plugged the other servo back in: rescan.
        Goes through if exactly the lift and the tilt answer, at their IDs
        and the bus rate. Where both are SCS, each must also have its role's
        angle limits."""
        found, garbled = self._scan()
        self._uart.baudrate = servo_bus.BAUD_RATE
        ok = not garbled and found == [
            {"baud_rate": servo_bus.BAUD_RATE, "id": servo_bus.LIFT_ID},
            {"baud_rate": servo_bus.BAUD_RATE, "id": servo_bus.TILT_ID}]
        limits = None
        if ok:
            # (registers, series name, table) by role.
            servos = {role: self._table(ROLES[role][0]) for role in ROLES}
            ok = all(servo[0] is not None for servo in servos.values())
            if all(servo[1] == "scs" for servo in servos.values()):
                limits = {role: self._read_limits(ROLES[role][0], servos[role][2])
                          for role in ROLES}
                ok = ok and all(limits[role] == list(ROLES[role][1]) for role in ROLES)
        return self._mark("bus_check", ok, found=found, garbled=garbled, angle_limits=limits)

    def _identify(self):
        """The found servo's series table, from its registers 0-4, or None
        if it has none."""
        registers, name, table = self._table(self._id)
        self._mark("identify", table is not None,
                   registers=registers and list(registers), series=name)
        return table

    def _table(self, scs_id):
        """A servo's registers 0-4, the name of its series and the series'
        table. The table is None for a series without one, and all three
        are None if no good reply came."""
        registers = self._read(scs_id, 0, 5)
        if registers is None:
            return None, None, None
        name = series(registers)
        return registers, name, SERIES.get(name)

    def _write(self, role, table):
        new_id, limits = ROLES[role]
        encode, decode = table["encode"], table["decode"]
        write = self._reader.write_mem

        write(self._id, table["lock"], [0])
        lock = self._read_byte(self._id, table["lock"])
        if not self._mark("lock_off", lock == 0, lock=lock):
            return False

        write(self._id, table["angle_limits"], encode(limits[0]) + encode(limits[1]))
        read = self._read_limits(self._id, table)
        if not self._mark("angle_limits", read == list(limits), angle_limits=read):
            return False

        # From here on, the servo answers at its role's ID. Its reply to the
        # write may come from either ID, so the read back decides.
        write(self._id, table["id"], [new_id])
        self._id = new_id
        read_id = self._read_byte(new_id, table["id"])
        if not self._mark("id", read_id == new_id, id=read_id):
            return False

        # Then at the bus rate. Its reply may come at either rate.
        code = table["baud_rates"].index(servo_bus.BAUD_RATE)
        write(new_id, table["baud_rate"], [code])
        self._uart.baudrate = servo_bus.BAUD_RATE
        read_code = self._read_byte(new_id, table["baud_rate"])
        if not self._mark("baud_rate", read_code == code,
                          baud_rate=self._baud_rate(table, read_code)):
            return False

        read_id = self._read_byte(new_id, table["id"])
        read_code = self._read_byte(new_id, table["baud_rate"])
        read = self._read_limits(new_id, table)
        if not self._mark("read_back", (read_id, read_code, read) == (new_id, code, list(limits)),
                          id=read_id, baud_rate=self._baud_rate(table, read_code),
                          angle_limits=read):
            return False

        write(new_id, table["lock"], [1])
        lock = self._read_byte(new_id, table["lock"])
        return self._mark("lock_on", lock == 1, lock=lock)

    def _scan(self):
        found, garbled = [], []
        for baud_rate in BAUD_RATES:
            self._uart.baudrate = baud_rate
            for scs_id in IDS:
                self._reader.ping(scs_id)
                problem = self._reader.problem
                if problem is None:
                    found.append({"baud_rate": baud_rate, "id": scs_id})
                elif problem != "no reply":
                    garbled.append({"baud_rate": baud_rate, "id": scs_id,
                                    "problem": problem})
        return found, garbled

    def _read(self, scs_id, address, n):
        """n bytes read from address onwards, or None if no good reply
        came."""
        reply = self._reader.read(scs_id, address, n)
        return None if reply is None else reply[1]

    def _read_byte(self, scs_id, address):
        data = self._read(scs_id, address, 1)
        return None if data is None else data[0]

    def _read_limits(self, scs_id, table):
        """The angle limits, [min, max], or None if no good reply came or
        the servo's series has no table."""
        if table is None:
            return None
        data = self._read(scs_id, table["angle_limits"], 4)
        return None if data is None else [table["decode"](data[0:2]),
                                          table["decode"](data[2:4])]

    @staticmethod
    def _baud_rate(table, code):
        """The rate a baud code sets, or None for no code or an unknown
        one."""
        rates = table["baud_rates"]
        return rates[code] if code is not None and code < len(rates) else None

    def _mark(self, step, ok, **values):
        """Print a step's marker line, and return whether it went through."""
        marker = {"step": step, "ok": ok}
        marker.update(values)
        self._out(MARKER + json.dumps(marker))
        return ok
