# capture_adc.py - temporary diagnostic for the water-meter optical sensor.
#
# Streams the ANALOG output of the laser/photo sensor so we can see the raw
# signal in three states:
#   1. no flow, impeller parked normally
#   2. the dither / false-reading error (impeller parked on the threshold)
#   3. real flow, at a couple of different rates
#
# Captures showed two superimposed signals: a slow (~0.5 Hz at 1 L/min)
# rotation modulation, and a fast ~2.5% "wobble" present even at rest (likely
# ambient-light flicker, possibly parked-impeller dither). So each window
# reports three numbers:
#   rot_env - envelope of the *windowed mean* over ENV_MS. Averaging kills the
#             fast wobble, leaving only rotation: large => turning, small =>
#             parked. ~0.4% parked vs ~5% at 1 L/min. This is the rotation gate.
#   wobble  - raw per-window peak-to-peak: the fast fluctuation on the level.
#   freq    - dominant wobble frequency, from how often the signal crosses the
#             previous window's mean (deadband DEAD). ~100/120 Hz => mains
#             flicker (easy to filter); a few Hz => overlaps rotation (harder).
#
# How to run:
#   1. Wire the sensor's ANALOG output to PIN below (D1).
#   2. Back up the device's code.py, then copy this file onto CIRCUITPY as
#      code.py and reboot. (Self-contained: needs no other project files.)
#   3. Open the serial console and watch the summary lines.
#   4. When done, restore normal firmware:  python3 deploy.py water
#
# This program enables no WiFi and no watchdog, so it won't reset on you
# mid-capture.

import time
import board
import analogio
import supervisor

# --- config ----------------------------------------------------------------
PIN = board.D1        # sensor ANALOG output
REPORT_MS = 250       # how often to print a summary window
ENV_MS = 4500         # rolling window for the modulation-depth envelope;
                      # must span >= 1 rotation period (~2s at 1 L/min) to
                      # read the true depth, so keep margin for low flow
RAW = False           # True: dump every sample as "ms,value" CSV for plotting
RAW_HZ = 200          # sample rate in RAW mode
OVERSAMPLE = 4        # averaged reads per sample (tames ESP32 ADC noise)
BAR_WIDTH = 50        # width of the ASCII band visualization
DEAD = 400            # crossing-counter deadband (counts): above the noise
                      # floor, below the wobble, so only the wobble is counted
FULL_SCALE = 65535    # analogio.AnalogIn.value is always scaled to 16-bit
# ---------------------------------------------------------------------------

adc = analogio.AnalogIn(PIN)
vref = adc.reference_voltage


def read():
    """One oversampled ADC reading, 0..FULL_SCALE."""
    total = 0
    for _ in range(OVERSAMPLE):
        total += adc.value
    return total // OVERSAMPLE


def bar(lo, hi):
    """ASCII band showing the [lo..hi] swing within the full 0..FULL_SCALE range."""
    a = int(lo / FULL_SCALE * BAR_WIDTH)
    b = int(hi / FULL_SCALE * BAR_WIDTH)
    if b < a:
        a, b = b, a
    return "".join("#" if a <= i <= b else "." for i in range(BAR_WIDTH))


def run():
    print("ADC capture on %s  vref=%.3fV  mode=%s" %
          (PIN, vref, "RAW" if RAW else "SUMMARY"))

    if RAW:
        print("ms,value")
        period_ms = 1000 // RAW_HZ
        next_t = supervisor.ticks_ms()
        while True:
            now = supervisor.ticks_ms()
            if now - next_t >= 0:
                print("%d,%d" % (now, read()))
                next_t += period_ms
            time.sleep(0.001)
        return

    # SUMMARY mode: separate the slow rotation from the fast wobble (see the
    # header comment for rot_env / wobble / freq).
    env_windows = max(1, ENV_MS // REPORT_MS)
    means = []        # ring of recent window means -> rotation envelope
    prev_mean = 0     # reference level for the crossing counter
    state = 0         # which side of the deadband we're on (+1 / -1)
    crossings = 0     # deadband crossings this window
    win_start = supervisor.ticks_ms()
    lo = FULL_SCALE
    hi = 0
    total = 0
    n = 0
    while True:
        v = read()
        if v < lo:
            lo = v
        if v > hi:
            hi = v
        total += v
        n += 1

        d = v - prev_mean
        if d > DEAD:
            if state != 1:
                state = 1
                crossings += 1
        elif d < -DEAD:
            if state != -1:
                state = -1
                crossings += 1

        elapsed = supervisor.ticks_ms() - win_start
        if elapsed >= REPORT_MS:
            wobble = hi - lo
            mean = total // n if n else 0
            rate = n * 1000 // max(1, elapsed)
            freq = crossings * 1000 // (2 * elapsed)  # 2 crossings per cycle

            means.append(mean)
            if len(means) > env_windows:
                means.pop(0)
            rot_lo = min(means)
            rot_hi = max(means)
            rot_env = rot_hi - rot_lo

            print("rot_env=%5d (%4.1f%%)  wobble=%5d (%4.1f%%)  ~%3dHz  mean=%5d  ~%dHz_smp  [%s]" %
                  (rot_env, rot_env * 100.0 / FULL_SCALE,
                   wobble, wobble * 100.0 / FULL_SCALE,
                   freq, mean, rate, bar(rot_lo, rot_hi)))

            prev_mean = mean
            win_start = supervisor.ticks_ms()
            lo = FULL_SCALE
            hi = 0
            total = 0
            n = 0
            crossings = 0


run()
