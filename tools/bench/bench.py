# Servo bench helpers for the SCS lift (1) and tilt (2) servos. REPL use only:
# stop code.py, copy this file to the device, then `from bench import *`.
# Never sends instruction 0x06 (factory reset) and never writes EEPROM.
import board, busio, digitalio, time

LIFT, TILT, ALL = 1, 2, 0xFE

u = busio.UART(board.TX, board.RX, baudrate=250000, receiver_buffer_size=64, timeout=0.01)
up = digitalio.DigitalInOut(board.D1)
up.pull = digitalio.Pull.DOWN
down = digitalio.DigitalInOut(board.D2)
down.pull = digitalio.Pull.DOWN

stats = {"ok": 0, "none": 0, "short": 0, "bad": 0}


def _chk(b):
    return ~sum(b) & 0xFF


def xfer(sid, inst, params=(), n=0, reply=True):
    """One request/reply. Returns (error byte, data, microseconds) or None."""
    body = bytes((sid, len(params) + 2, inst)) + bytes(params)
    u.reset_input_buffer()
    t0 = time.monotonic_ns()
    u.write(b"\xff\xff" + body + bytes((_chk(body),)))
    if sid == ALL or not reply:
        return None
    r = u.read(6 + n)
    us = (time.monotonic_ns() - t0) // 1000
    if r is None:
        stats["none"] += 1
        raise OSError("no reply")
    if len(r) < 6 + n:
        stats["short"] += 1
        raise OSError("short reply " + r.hex())
    if r[0] != 0xFF or r[1] != 0xFF or r[2] != sid or r[3] != n + 2 or r[-1] != _chk(r[2:-1]):
        stats["bad"] += 1
        raise OSError("bad reply " + r.hex())
    stats["ok"] += 1
    return r[4], r[5:5 + n], us


def raw(sid, inst, params=(), n=32):
    """Send a request and return whatever comes back, to check for echo."""
    body = bytes((sid, len(params) + 2, inst)) + bytes(params)
    u.reset_input_buffer()
    u.write(b"\xff\xff" + body + bytes((_chk(body),)))
    r = u.read(n)
    return r and r.hex()


def u16(d, i):
    return d[i] << 8 | d[i + 1]


def s15(v):
    return -(v & 0x7FFF) if v & 0x8000 else v


def s10(v):
    return -(v & 0x3FF) if v & 0x400 else v


def ping(sid):
    return xfer(sid, 1)[0]


def rd(sid, addr, n):
    err, d, _ = xfer(sid, 2, (addr, n), n)
    return err, d


def wr(sid, addr, data, reply=True):
    r = xfer(sid, 3, (addr,) + tuple(data), reply=reply)
    return r and r[0]


def w16(sid, addr, v, reply=True):
    return wr(sid, addr, ((v >> 8) & 0xFF, v & 0xFF), reply)


def regs(sid):
    """Print the EEPROM block 0-39 and the control block 40-48."""
    err, d = rd(sid, 0, 40)
    print("id", sid, "err", err)
    print("  0-4 versions", list(d[0:5]))
    print("  5 id", d[5], " 6 baud", d[6], " 7 return delay", d[7], " 8 status level", d[8])
    print("  9-12 angle limits", u16(d, 9), u16(d, 11))
    print("  13 max temp", d[13], " 14/15 max/min volt", d[14], d[15])
    print("  16 max torque", u16(d, 16), " 18", d[18], " 19 unloading", d[19], " 20 led alarm", d[20])
    print("  21-23 P D I", d[21], d[22], d[23], " 24 min start force", u16(d, 24), " 26/27 dead", d[26], d[27])
    print("  28-36", list(d[28:37]))
    print("  37 protect torque", d[37], " 38 protect time", d[38], " 39 overload torque", d[39])
    err, c = rd(sid, 40, 9)
    print("  40 torque", c[0], " 41", c[1], " 42 goal pos", u16(c, 2), " 44 goal time", u16(c, 4),
          " 46 goal speed", u16(c, 6), " 48 lock", c[8])


def fb(sid=LIFT):
    """Feedback block 56-70: err, pos, speed, load, volt, temp, status, moving, current, us."""
    err, d, us = xfer(sid, 2, (56, 15), 15)
    return (err, u16(d, 0), s15(u16(d, 2)), s10(u16(d, 4)), d[6], d[7], d[9], d[10],
            s15(u16(d, 13)), us)


def show(sid=LIFT):
    err, pos, spd, load, v, t, st, mv, i, us = fb(sid)
    print("err", err, "pos", pos, "spd", spd, "load", load, "volt", v, "temp", t,
          "status", st, "moving", mv, "current", i, "us", us, "up", up.value, "down", down.value)


def cur(sid=LIFT):
    try:
        err, d = rd(sid, 69, 2)
        return err, u16(d, 0)
    except OSError as e:
        return repr(e)


def pwm(sid, duty):
    return w16(sid, 44, (abs(duty) | 0x400) if duty < 0 else duty)


def torque(sid, v):
    return wr(sid, 40, (v,))


def stop(sid=LIFT):
    for f in (lambda: pwm(sid, 0), lambda: torque(sid, 0)):
        try:
            f()
        except OSError as e:
            print("stop:", repr(e))


def stop_all():
    """Broadcast duty 0 and torque off; no servo replies."""
    w16(ALL, 44, 0)
    wr(ALL, 40, (0,))


def run(duty, secs, hz=20, sid=LIFT, off=True, torque_on=True):
    """Drive the lift at a duty for secs, sampling feedback. Stops at the end,
    on a rising edge of either end sensor, or on Ctrl-C. Prints the samples
    after stopping so printing doesn't skew the timing."""
    rows = []
    prev = (up.value, down.value)
    t0 = time.monotonic()
    period = 1 / hz
    why = "time"
    try:
        if torque_on:
            torque(sid, 1)
        pwm(sid, duty)
        while True:
            t = time.monotonic() - t0
            if t >= secs:
                break
            now = (up.value, down.value)
            if (now[0] and not prev[0]) or (now[1] and not prev[1]):
                why = "sensor up" if now[0] else "sensor down"
                break
            prev = now
            try:
                rows.append((int(t * 1000),) + fb(sid) + (int(now[0]), int(now[1])))
            except OSError as e:
                rows.append((int(t * 1000), repr(e)))
            time.sleep(max(0, period - (time.monotonic() - t0 - t)))
    finally:
        if off:
            stop(sid)
        else:
            pwm(sid, 0)
    print("stopped by", why, "at", int((time.monotonic() - t0) * 1000), "ms")
    print("t_ms err pos spd load volt temp st mv cur us up dn")
    trip, vmin = None, 255
    for r in rows:
        print(*r)
        if len(r) > 2:
            vmin = min(vmin, r[5])
            if trip is None and (r[1] | r[7]) & 0x20:
                trip = r[0]
    print("overload at", trip, "ms; min volt", vmin)


def _row(t0, sid):
    try:
        r = fb(sid)
        print(int((time.monotonic() - t0) * 1000), *r[:9], int(up.value), int(down.value))
        return r
    except OSError as e:
        print(int((time.monotonic() - t0) * 1000), repr(e))


def _tripped(r):
    return r is not None and (r[0] | r[6]) & 0x20


def stall_test(duty=-800, secs=14, hz=10, sid=LIFT):
    """Drive with no end-sensor guard until overload protection trips or secs
    pass, printing live. On a trip, try the clearing steps in order and report
    which one clears the overload bit. Always ends with duty 0 and torque off."""
    print("t_ms err pos spd load volt temp st mv cur up dn")
    t0 = time.monotonic()
    try:
        torque(sid, 1)
        pwm(sid, duty)
        trip = None
        while time.monotonic() - t0 < secs:
            if _tripped(_row(t0, sid)):
                trip = time.monotonic() - t0
                break
            time.sleep(1 / hz)
        if trip is None:
            print("no overload in", secs, "s")
            return
        print("OVERLOAD at", int(trip * 1000), "ms; holding the duty for 2 s")
        for _ in range(10):
            _row(t0, sid)
            time.sleep(0.2)
        steps = (("duty 0", lambda: pwm(sid, 0)),
                 ("same duty again", lambda: pwm(sid, duty)),
                 ("duty 0 before torque", lambda: pwm(sid, 0)),
                 ("torque off", lambda: torque(sid, 0)),
                 ("torque on", lambda: torque(sid, 1)))
        for name, f in steps:
            print("step:", name, "-> reply err", f())
            time.sleep(0.3)
            r = _row(t0, sid)
            if not _tripped(r) and name != "duty 0 before torque":
                print("CLEARED by", name)
                return
        print("NOT CLEARED by any step")
    finally:
        stop(sid)
        print("stopped")
        show(sid)


def fast(sid=LIFT, n=300):
    """Time n feedback-block reads and count failures."""
    for k in stats:
        stats[k] = 0
    times = []
    for _ in range(n):
        try:
            times.append(fb(sid)[-1])
        except OSError:
            pass
    times.sort()
    if times:
        print("reads", n, "ok", len(times), "min", times[0], "median", times[len(times) // 2],
              "p99", times[int(len(times) * 0.99) - 1], "max", times[-1], "us")
    print(stats)
