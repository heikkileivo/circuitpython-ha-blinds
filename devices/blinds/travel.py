"""A blind's travel: how far it is from its bottom end, in revolutions of the
lift spindle, tracked from the lift's servo angle, which wraps once per turn.
And its full travel, learned from a run from one end sensor to the other
(#50). Pure, so the host tests run it."""

import math

import cover_state

# A turn of the lift at the servo angle's own rate, in counts: the time from
# one wrap to the next, at the angle's rate mid-turn. Middle's lift measured
# 1,114-1,133 at duties 300-800 each way (#78, tests/data). The angle reads
# 0-1022; over the rest of the turn, the dead zone, it holds.
COUNTS_PER_TURN = 1120

# A turn ends where the angle wraps from the last third of the turn into the
# first (opening, when it counts up) or the other way (closing). A sample can
# come up to a third of a turn late, about 235 ms at full speed, and the wrap
# still counts.
LOW_END = 341
HIGH_START = 683
# In the last third before the wrap, the angle only moves on toward it. One
# that reaches mid-turn, or goes back more than this many counts, is a stray
# at the wrap, and is held. The measured jitter there is at most 9.
BACK_TOLERANCE = 16

_LOW = 0
_HIGH = 1

# An unknown travel, and a full travel not learned yet.
NAN = float("nan")

# A learned full travel replaces the one in use only if it differs by more
# than this, in revolutions (#13).
LEARN_TOLERANCE = 0.1
# A run is learned only within these fractions of the settings.toml
# estimate. Too small a full travel would bound every open short of the up
# end sensor, and then no run could correct it.
LEARN_MIN = 0.5
LEARN_MAX = 1.5


class WrapCounter:
    """The revolutions the lift has turned during one move, up positive and
    fractional: the servo angle's wraps in the move's direction, plus its
    change within the turn."""

    def __init__(self, counting_up):
        self._turn = 1 if counting_up else -1
        # Opening, the angle counts up, so it wraps from high to low.
        self._wrap_from, self._wrap_to = (_HIGH, _LOW) if counting_up else (_LOW, _HIGH)
        self._zone = None           # The last angle's third, mid-turn aside
        self._angle = None          # The last angle taken
        self._turns = 0
        self._first = None
        self.revs = 0.0

    def feed(self, angle):
        """Take one servo angle, unless it's a stray at the wrap."""
        if angle < LOW_END:
            zone = _LOW
        elif angle >= HIGH_START:
            zone = _HIGH
        else:
            zone = None
        if self._zone == self._wrap_from:
            if zone is None:
                return
            if zone == self._wrap_from and (self._angle - angle) * self._turn > BACK_TOLERANCE:
                return
        if self._first is None:
            self._first = angle
        if self._zone == self._wrap_from and zone == self._wrap_to:
            self._turns += self._turn
        if zone is not None:
            self._zone = zone
        self._angle = angle
        self.revs = self._turns + (angle - self._first) / COUNTS_PER_TURN


class Tracker:
    """The blind's travel and full travel, in revolutions, kept across moves.
    NaN travel is unknown, and NaN full travel not learned yet: the
    settings.toml estimate stands in for it meanwhile."""

    def __init__(self, travel, full_travel, estimate):
        self.travel = travel
        self.full_travel = full_travel
        self._estimate = estimate
        self._opening = True
        self._from_end = False
        self._start = travel
        self._counter = WrapCounter(True)

    @property
    def full_or_estimate(self):
        return self._estimate if math.isnan(self.full_travel) else self.full_travel

    def begin(self, opening, from_end):
        """Start a move, opening or closing. from_end is whether the blind
        starts at rest at the other end, as its cover state says: only such
        a move that reaches its end sensor learns the full travel."""
        self._opening = opening
        self._from_end = from_end
        self._start = self.travel
        self._counter = WrapCounter(opening)

    def feed(self, angle):
        """Take one of the move's servo angles."""
        self._counter.feed(angle)
        self.travel = self._start + self._counter.revs

    @property
    def remaining(self):
        """The revolutions left to the end the move heads for, NaN if the
        travel is unknown."""
        if self._opening:
            return self.full_or_estimate - self.travel
        return self.travel

    def approach_due(self, approach_revs):
        """Whether the move is within approach_revs of its end, so runs at
        approach speed. With the travel unknown, it always is."""
        return not self.remaining > approach_revs

    def beyond_limit(self, margin):
        """Whether the move has gone margin revolutions past its end: above
        full travel opening, below the bottom closing. With the travel
        unknown, whether the move itself has turned full travel plus margin,
        more than any move needs."""
        if math.isnan(self.travel):
            return abs(self._counter.revs) > self.full_or_estimate + margin
        return self.remaining < -margin

    def reached(self):
        """The move reached its end sensor. One that started at the other end
        learns the full travel; then the travel re-anchors at this end."""
        if self._from_end:
            self._learn(abs(self._counter.revs))
        self.anchor(cover_state.UP if self._opening else cover_state.DOWN)

    def anchor(self, end):
        """The end sensor at end, UP or DOWN, is active: the travel becomes 0
        at the bottom, and the full travel at the top once it's learned."""
        if end == cover_state.DOWN:
            self.travel = 0.0
        elif not math.isnan(self.full_travel):
            self.travel = self.full_travel

    def lose(self):
        """The travel is unknown: the lift may be driving."""
        self.travel = NAN

    def _learn(self, run):
        if not LEARN_MIN * self._estimate <= run <= LEARN_MAX * self._estimate:
            print(f"Not learning a full travel of {run} revolutions, too far from the estimate {self._estimate}.")
            return
        if math.isnan(self.full_travel) or abs(run - self.full_travel) > LEARN_TOLERANCE:
            print(f"Learned full travel: {run} revolutions, was {self.full_travel}.")
            self.full_travel = run


def at_boot(up_active, down_active, stored_state, stored_travel, full_travel, estimate):
    """The tracker at boot, from whether each end sensor is active and the
    record in NVM. The stored travel holds after a settled cover state, open,
    closed or stopped, but not after an interrupted move or with a blank
    record. An active end sensor re-anchors it. Both active can't be, so
    neither is trusted, nor the stored travel."""
    settled = stored_state in (cover_state.UP, cover_state.DOWN, cover_state.STOPPED)
    tracker = Tracker(stored_travel if settled else NAN, full_travel, estimate)
    if up_active and down_active:
        tracker.lose()
    elif up_active:
        tracker.anchor(cover_state.UP)
    elif down_active:
        tracker.anchor(cover_state.DOWN)
    return tracker
