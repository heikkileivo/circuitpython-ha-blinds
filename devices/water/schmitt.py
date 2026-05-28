class AdaptiveSchmitt:
    """Adaptive Schmitt trigger for the analog water-meter optical pickup.

    The two jobs are intentionally split (merging them into one big band breaks
    counting on clipped / asymmetric waveforms):

      EDGE TIMING - small hysteresis around the running envelope midpoint
        (baseline). Every rotation passes through its own midpoint, so every
        cycle is counted regardless of the clipped bright peak or variable
        dip depth. Hysteresis just rejects the <1% flow-time wobble at the
        crossing.

      DITHER REJECTION - a gate on the raw envelope swing (env_hi - env_lo).
        Count only when `swing > min_swing`. Rate-independent: parked impeller
        sits at <=2.5% (including the bad vane-edge dither), real flow swings
        ~6-8% of full scale at every rate. The small hysteresis alone would
        let the dither through, so the gate is essential.

    Each bright/dark vane cycle produces two flips (both edges) - matches the
    old digital both-edge count, so `pulses_per_unit` carries over.
    """

    def __init__(self, env_windows, min_swing, hyst, full_scale=65535):
        self.env_windows = env_windows
        self.min_swing = min_swing
        self.hyst = hyst
        self.full_scale = full_scale
        self._win = []                  # ring of (lo, hi) per sub-window
        self._win_lo = full_scale
        self._win_hi = 0
        self.baseline = full_scale // 2
        self.hi_th = self.baseline + hyst // 2
        self.lo_th = self.baseline - hyst // 2
        self.state = 0                  # +1 above hi_th, -1 below lo_th
        self.flips = 0
        self.swing = 0
        self.gated = False

    def feed(self, v):
        """One ADC sample. Counts a flip on each genuine high<->low transition
        of the small midpoint hysteresis, but only while gated."""
        if v < self._win_lo:
            self._win_lo = v
        if v > self._win_hi:
            self._win_hi = v
        if v > self.hi_th:
            if self.state == -1 and self.gated:
                self.flips += 1
            self.state = 1
        elif v < self.lo_th:
            if self.state == 1 and self.gated:
                self.flips += 1
            self.state = -1

    def close_window(self):
        """Update envelope, baseline, gate and thresholds. Call every sub-window."""
        self._win.append((self._win_lo, self._win_hi))
        if len(self._win) > self.env_windows:
            self._win.pop(0)
        env_lo = min(w[0] for w in self._win)
        env_hi = max(w[1] for w in self._win)
        self.swing = env_hi - env_lo
        self.baseline = (env_lo + env_hi) // 2
        self.gated = self.swing > self.min_swing
        half = self.hyst // 2
        self.hi_th = self.baseline + half
        self.lo_th = self.baseline - half
        self._win_lo = self.full_scale
        self._win_hi = 0
