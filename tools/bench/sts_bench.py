#!/usr/bin/env python3
"""Ad hoc bench for a factory-fresh Feetech STS3215 on a blind's servo bus (#142).

    sts_bench.py <device-name> interrupt        stop code.py, enter the REPL
    sts_bench.py <device-name> scan [--full]    find the servo, dump its registers
    sts_bench.py <device-name> onboard          set ID 1, 250,000 baud, wheel mode
    sts_bench.py <device-name> drive [--speeds 300 600 ...] [--hold 2]
    sts_bench.py <device-name> stall [--limit 200] [--speed 600] [--stall-hold 6]
                                                run into a hand-held output with the
                                                torque limit (RAM, address 48) lowered
    sts_bench.py <device-name> wiggle [--tilt-delta 40] [--lift-speed 300] [--lift-hold 1]
                                                STS lift (ID 1) and SCS tilt (ID 2) on one
                                                bus: tilt out and back, lift each way, braked
    sts_bench.py <device-name> stop             speed 0, torque 1 (brake)

Everything runs as code pasted into the REPL over the /cp/serial/ websocket.
Nothing is written to the device's filesystem, and the blind's own modules
aren't used, so the blind needn't run any particular firmware. Only ONE servo
may be on the bus. Only `stall` writes address 48 (the STS's torque limit, RAM, restored
after), and nothing writes torque
enable 2 (unverified) or 128 (recalibrates).

STS facts used (Feetech FTServo SMS_STS, see #100): words are little-endian;
MODE 33 (1 = wheel); LOCK 55; goal speed 46-47 with the sign in bit 15;
present block 56-70: position, speed (sign bit 15), load (sign bit 10),
voltage 0.1 V, temperature, current at 69-70.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from repl import Repl, device, has_traceback  # noqa: E402

HELPERS = '''
import board, busio, time
BAUDS = (1000000, 500000, 250000, 128000, 115200, 76800, 57600, 38400)
def mk(baud):
    return busio.UART(board.TX, board.RX, baudrate=baud, receiver_buffer_size=64, timeout=0.02)
def tx(u, i, ins, params=(), n=0, wait=0.02):
    body = bytes((i, len(params) + 2, ins)) + bytes(params)
    u.timeout = wait
    u.reset_input_buffer()
    u.write(b"\\xff\\xff" + body + bytes((~sum(body) & 0xFF,)))
    r = u.read(6 + n)
    if r is None or len(r) != 6 + n or r[0] != 255 or r[1] != 255 or r[2] != i:
        return None
    if r[-1] != (~sum(r[2:-1]) & 0xFF):
        return None
    return r[4], bytes(r[5:5 + n])
def rd(u, i, a, n):
    r = tx(u, i, 2, (a, n), n)
    return None if r is None else r[1]
def wr(u, i, a, data):
    r = tx(u, i, 3, [a] + list(data), 0, 0.1 if a < 40 else 0.02)
    return None if r is None else r[0]
def w16(v):
    return [v & 255, (v >> 8) & 255]
def sgn(v, bit):
    return -(v & ((1 << bit) - 1)) if v & (1 << bit) else v
def sample(u, i):
    d = rd(u, i, 56, 15)
    if d is None:
        return None
    return dict(pos=d[0] | d[1] << 8, spd=sgn(d[2] | d[3] << 8, 15), load=sgn(d[4] | d[5] << 8, 10),
                v=d[6] / 10, t=d[7], st=d[9], mov=d[10], cur=d[13] | d[14] << 8)
def speed_word(v):
    return w16((-v | 0x8000) if v < 0 else v)
'''

SCAN = '''
u = None
try:
    for baud in (BAUDS if FULL else (1000000, 250000)):
        u = mk(baud)
        for i in (range(254) if FULL else range(1, 11)):
            r = rd(u, i, 0, 5)
            if r is not None:
                print("FOUND baud", baud, "id", i, "regs0-4", list(r))
        u.deinit(); u = None
    print("scan done")
finally:
    if u: u.deinit()
'''

# BAUD, ID: where it was found.
ONBOARD = '''
u = mk(BAUD)
try:
    regs = rd(u, ID, 0, 5)
    print("regs0-4", None if regs is None else list(regs))
    if regs is None or list(regs[3:5]) != [9, 3]:
        raise SystemExit("not an STS3215 (model 777): refusing to write")
    print("torque off", wr(u, ID, 40, [0]))
    print("unlock (55=0)", wr(u, ID, 55, [0]))
    print("mode 1 (wheel)", wr(u, ID, 33, [1]))
    if ID != 1:
        print("id ->1", wr(u, ID, 5, [1]))
        time.sleep(0.2)
    print("lock (55=1)", wr(u, 1, 55, [1]))
    if BAUD != 250000:
        print("unlock", wr(u, 1, 55, [0]))
        print("baud code 2 (250000)", wr(u, 1, 6, [2]))
        time.sleep(0.3)
        u.deinit(); u = mk(250000)
        print("lock", wr(u, 1, 55, [1]))
    print("ping at 250000, id 1:", rd(u, 1, 0, 5) is not None)
    print("mode", rd(u, 1, 33, 1), "id", rd(u, 1, 5, 1), "baud code", rd(u, 1, 6, 1),
          "lock", rd(u, 1, 55, 1), "torque limit(48-49)", rd(u, 1, 48, 2),
          "accel(41)", rd(u, 1, 41, 1), "return delay(7)", rd(u, 1, 7, 1),
          "angle limits(9-12)", rd(u, 1, 9, 4), "max torque(16)", rd(u, 1, 16, 2))
    print("The servo's EEPROM is written: power-cycle it, then run drive.")
finally:
    if u: u.deinit()
'''

DRIVE = '''
u = mk(250000)
def stop():
    for _ in range(3):
        if wr(u, 1, 46, [0, 0]) is not None:
            break
    wr(u, 1, 40, [1])
try:
    print("mode", rd(u, 1, 33, 1), "(must be [1])")
    if rd(u, 1, 33, 1) != bytes([1]):
        raise SystemExit("not in wheel mode: run onboard first")
    s = sample(u, 1)
    print("idle", s)
    print("torque on", wr(u, 1, 40, [1]), "speed 0", wr(u, 1, 46, [0, 0]))
    for v in SPEEDS:
        print("--- speed", v, "for", HOLD, "s")
        wr(u, 1, 46, speed_word(v))
        t0 = time.monotonic(); misses = 0
        while time.monotonic() - t0 < HOLD:
            s = sample(u, 1)
            if s is None:
                misses += 1
                print("no reply")
                if misses >= 3:
                    raise SystemExit("servo stopped replying")
                continue
            print("%.2f" % (time.monotonic() - t0), s)
            if s["v"] < 5.0 or s["t"] > 60:
                raise SystemExit("abort: supply %s V, %s C" % (s["v"], s["t"]))
        wr(u, 1, 46, [0, 0])
        print("--- stopped (speed 0, torque 1): drift")
        p0 = None
        for _ in range(6):
            s = sample(u, 1)
            if s:
                p0 = s["pos"] if p0 is None else p0
                print(s, "drift", s["pos"] - p0)
            time.sleep(0.15)
finally:
    stop()
    print("stopped: speed 0, torque 1")
    u.deinit()
'''

STALL = '''
u = mk(250000)
hot = 0
full = None
try:
    full = rd(u, 1, 16, 2)
    print("max torque (16)", list(full), "limit now", list(rd(u, 1, 48, 2)))
    if rd(u, 1, 33, 1) != bytes([1]):
        raise SystemExit("not in wheel mode: run onboard first")
    print("limit ->", LIMIT, wr(u, 1, 48, w16(LIMIT)), "torque on", wr(u, 1, 40, [1]))
    print("Hold the output when told. Starting in 3 s.")
    time.sleep(3)
    wr(u, 1, 46, speed_word(SPEED))
    t0 = time.monotonic(); said = False; misses = 0
    while time.monotonic() - t0 < HOLD:
        if not said and time.monotonic() - t0 > 1.0:
            print("*** HOLD THE OUTPUT NOW"); said = True
        s = sample(u, 1)
        if s is None:
            misses += 1
            print("no reply")
            if misses >= 3:
                raise SystemExit("servo stopped replying")
            continue
        print("%.2f" % (time.monotonic() - t0), s)
        hot = hot + 1 if (s["v"] < 5.0 or s["t"] > 60) else 0
        if hot >= 2:
            raise SystemExit("abort: supply %s V, %s C" % (s["v"], s["t"]))
finally:
    for _ in range(3):
        if wr(u, 1, 46, [0, 0]) is not None:
            break
    wr(u, 1, 48, list(full) if full else w16(1000))
    wr(u, 1, 40, [1])
    print("stopped: speed 0, torque limit restored", rd(u, 1, 48, 2), "torque 1")
    u.deinit()
'''

WIGGLE = '''
u = mk(250000)
def be(v):
    return [(v >> 8) & 255, v & 255]
def tilt_pos():
    d = rd(u, 2, 56, 2)
    return None if d is None else (d[0] << 8) | d[1]
tilt_start = None
def lift_stop():
    for _ in range(3):
        if wr(u, 1, 46, [0, 0]) is not None:
            break
    wr(u, 1, 40, [1])
def lift_run(v, hold):
    print("--- lift speed", v, "for", hold, "s")
    wr(u, 1, 46, speed_word(v))
    t0 = time.monotonic(); bad = 0; p0 = None
    while time.monotonic() - t0 < hold:
        s = sample(u, 1)
        if s is None:
            raise SystemExit("lift stopped replying")
        p0 = s["pos"] if p0 is None else p0
        print("%.2f" % (time.monotonic() - t0), s)
        if abs(s["load"]) > 700 or s["v"] < 5.0 or s["t"] > 60:
            raise SystemExit("abort: load %s, supply %s V, %s C" % (s["load"], s["v"], s["t"]))
        bad = bad + 1 if (time.monotonic() - t0 > 0.4 and abs(s["spd"]) < 50) else 0
        if bad >= 3:
            raise SystemExit("abort: commanded %s but not turning (blocked?)" % v)
    lift_stop()
    time.sleep(0.5)
    s = sample(u, 1)
    print("--- stopped at", s["pos"], "moved", (((s["pos"] - p0 + 2048) & 0xFFF) - 2048), "steps")
try:
    if rd(u, 1, 33, 1) != bytes([1]):
        raise SystemExit("lift not in wheel mode")
    tilt_start = tilt_pos()
    print("tilt at", tilt_start)
    if tilt_start is None:
        raise SystemExit("tilt doesn't reply")
    print("=== tilt: torque on, out by", TILT, "and back")
    wr(u, 2, 40, [1]); wr(u, 2, 44, [0, 0]); wr(u, 2, 46, be(300))
    for target in (tilt_start + TILT, tilt_start - TILT, tilt_start):
        wr(u, 2, 42, be(target))
        time.sleep(1.2)
        print("tilt goal", target, "now", tilt_pos())
    print("=== lift")
    lift_run(SPEED, HOLD)
    lift_run(-SPEED, HOLD)
    lift_run(-SPEED, HOLD)
    lift_run(SPEED, HOLD)
finally:
    lift_stop()
    wr(u, 2, 40, [0])
    print("done: lift speed 0 torque 1 (braked), tilt limp at", tilt_pos())
    u.deinit()
'''

STOP = '''
u = None
for baud in (250000, 1000000):
    u = mk(baud)
    try:
        for i in (1, 2):
            if wr(u, i, 46, [0, 0]) is not None:
                print("id", i, "at", baud, "speed 0; torque 1:", wr(u, i, 40, [1]))
    finally:
        u.deinit()
'''


def run(repl, body, timeout, **names):
    consts = "".join(f"{k} = {v!r}\n" for k, v in names.items())
    out = repl.run(HELPERS + consts + body, timeout)
    if has_traceback(out):
        sys.exit(1)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("device")
    p.add_argument("command", choices=("interrupt", "scan", "onboard", "drive", "stall", "wiggle", "stop"))
    p.add_argument("--full", action="store_true", help="scan: all IDs at all baud rates (~50 s)")
    p.add_argument("--baud", type=int, default=1000000, help="onboard: the servo's current baud rate")
    p.add_argument("--id", type=int, default=1, help="onboard: the servo's current ID")
    p.add_argument("--speeds", type=int, nargs="+", default=[300, 600, 1200, -600],
                   help="drive: goal speeds in steps/s, signed (default: %(default)s)")
    p.add_argument("--hold", type=float, default=2.0, help="drive: seconds at each speed")
    p.add_argument("--limit", type=int, default=200, help="stall: torque limit, 0-1000 (0.1%%)")
    p.add_argument("--speed", type=int, default=600, help="stall: goal speed in steps/s")
    p.add_argument("--stall-hold", dest="stall_hold", type=float, default=6.0,
                   help="stall: seconds to run")
    p.add_argument("--tilt-delta", type=int, default=40, help="wiggle: tilt counts out and back")
    p.add_argument("--lift-speed", type=int, default=300, help="wiggle: lift steps/s")
    p.add_argument("--lift-hold", type=float, default=1.0, help="wiggle: seconds per lift move")
    a = p.parse_args()
    repl = Repl(device(a.device))
    if a.command == "interrupt":
        repl.interrupt()
    elif a.command == "scan":
        run(repl, SCAN, 90 if a.full else 20, FULL=a.full)
    elif a.command == "onboard":
        run(repl, ONBOARD, 30, BAUD=a.baud, ID=a.id)
    elif a.command == "drive":
        run(repl, DRIVE, 20 + len(a.speeds) * (a.hold + 3) + 10, SPEEDS=a.speeds, HOLD=a.hold)
    elif a.command == "stall":
        run(repl, STALL, 30 + a.stall_hold, LIMIT=a.limit, SPEED=a.speed, HOLD=a.stall_hold)
    elif a.command == "wiggle":
        run(repl, WIGGLE, 40 + 4 * (a.lift_hold + 2), TILT=a.tilt_delta, SPEED=a.lift_speed,
            HOLD=a.lift_hold)
    else:
        run(repl, STOP, 20)


if __name__ == "__main__":
    main()
