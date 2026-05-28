# test_schmitt.py - validate the adaptive-Schmitt flow counter on live ADC
# before folding it into the real firmware.
#
# Algorithm (two separate jobs - do NOT merge them into one big band):
#   1. EDGE TIMING: a Schmitt trigger at the envelope midpoint (baseline) with
#      a SMALL hysteresis (HYST_PCT, ~1.5%). Every rotation passes through its
#      own midpoint, so every cycle is counted even though the waveform clips
#      at the bright peak and the dips vary in depth. Small hysteresis just
#      rejects the <1% flow-time wobble at the crossing.
#   2. DITHER REJECTION: a gate on the raw envelope swing. Count only when
#      swing > MIN_SWING (~3.5%). Rate-independent: parked <=2.5% < 3.5% <
#      flow 6-8% at any rate. This is what stops the parked-impeller dither
#      from being counted (the small hysteresis alone would let it through).
#
# Each bright/dark vane cycle = 2 flips (both edges), matching the old pulse
# count, so flips/litre should be ~2x the configured pulses_per_unit.
#
# What to check:
#   1. Parked (benign + a dither spot if you can find one): gate "off", 0 flips.
#   2. Trickle / shower / full open: flips/s scales with flow and matches the
#      meter LED rate (each LED cycle ~= 2 flips).
#   3. Calibration: run a known volume -> flips/litre.
#
# Run: copy onto CIRCUITPY as code.py (back up the real one), open serial.
# Restore with: python3 deploy.py water

import time
import board
import analogio
import supervisor

# --- config ----------------------------------------------------------------
PIN = board.D1          # sensor ANALOG output
SAMPLE_MS = 2           # sampling period; keep well above the blade rate
SUB_WINDOW_MS = 250     # envelope sub-window (min/max accumulation)
ENV_MS = 4500           # rolling envelope length (>= 1 rotation period)
MIN_SWING_PCT = 3.5     # gate: count only when envelope swing exceeds this
                        # (above dither ~2.5%, below flow swing ~6-8%)
HYST_PCT = 1.5          # Schmitt hysteresis around the midpoint (small!)
OVERSAMPLE = 4          # averaged reads per sample (tames ADC noise)
REPORT_MS = 1000        # print cadence
FULL_SCALE = 65535
# ---------------------------------------------------------------------------

adc = analogio.AnalogIn(PIN)


def read():
    total = 0
    for _ in range(OVERSAMPLE):
        total += adc.value
    return total // OVERSAMPLE


class AdaptiveSchmitt:
    def __init__(self):
        self.env_windows = max(1, ENV_MS // SUB_WINDOW_MS)
        self.min_swing = int(MIN_SWING_PCT / 100 * FULL_SCALE)
        self.hyst = int(HYST_PCT / 100 * FULL_SCALE)
        self.win = []                 # ring of (lo, hi) per sub-window
        self.win_lo = FULL_SCALE
        self.win_hi = 0
        self.baseline = FULL_SCALE // 2
        self.hi_th = self.baseline + self.hyst // 2
        self.lo_th = self.baseline - self.hyst // 2
        self.state = 0                # +1 above hi_th, -1 below lo_th
        self.flips = 0
        self.swing = 0
        self.gated = False            # True when swing says "really flowing"

    def feed(self, v):
        """One sample. Counts a flip on each genuine high<->low transition,
        but only while gated (real flow)."""
        if v < self.win_lo:
            self.win_lo = v
        if v > self.win_hi:
            self.win_hi = v
        if v > self.hi_th:
            if self.state == -1 and self.gated:
                self.flips += 1
            self.state = 1
        elif v < self.lo_th:
            if self.state == 1 and self.gated:
                self.flips += 1
            self.state = -1

    def close_window(self):
        """Update envelope, baseline, gate and thresholds once per sub-window."""
        self.win.append((self.win_lo, self.win_hi))
        if len(self.win) > self.env_windows:
            self.win.pop(0)
        env_lo = min(w[0] for w in self.win)
        env_hi = max(w[1] for w in self.win)
        self.swing = env_hi - env_lo
        self.baseline = (env_lo + env_hi) // 2
        self.gated = self.swing > self.min_swing
        half = self.hyst // 2
        self.hi_th = self.baseline + half
        self.lo_th = self.baseline - half
        self.win_lo = FULL_SCALE
        self.win_hi = 0


def run():
    sch = AdaptiveSchmitt()
    print("Adaptive Schmitt test on %s  min_swing=%d (%.1f%%)  hyst=%d (%.1f%%)  env=%dms" %
          (PIN, sch.min_swing, MIN_SWING_PCT, sch.hyst, HYST_PCT, ENV_MS))

    last_total = 0
    win_t = supervisor.ticks_ms()
    rep_t = win_t
    while True:
        sch.feed(read())

        now = supervisor.ticks_ms()
        if now - win_t >= SUB_WINDOW_MS:
            sch.close_window()
            win_t = now

        if now - rep_t >= REPORT_MS:
            d = sch.flips - last_total
            last_total = sch.flips
            fps = d * 1000 // max(1, now - rep_t)
            gate = "FLOW" if sch.gated else " -- "
            print("swing=%5d (%4.1f%%)  base=%5d  th=[%5d..%5d]  gate=%s  flips/s=%2d  total=%5d" %
                  (sch.swing, sch.swing * 100.0 / FULL_SCALE,
                   sch.baseline, sch.lo_th, sch.hi_th,
                   gate, fps, sch.flips))
            rep_t = now

        time.sleep(SAMPLE_MS / 1000)


run()
