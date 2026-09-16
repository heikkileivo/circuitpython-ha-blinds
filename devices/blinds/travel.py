"""A blind's travel: how far it is from its bottom end, in revolutions of the
lift spindle, tracked from the lift's servo angle, which wraps once per turn.
And its full travel, learned from a run from one end sensor to the other
(#50). Pure, so the host tests run it."""

import math

import cover_state
from persist import NAN

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

# The servo angle reads up to this. Through the dead zone, which starts about
# here, it holds at about 1018-1022, then 0-1, until the wrap. So an angle
# within DEAD_ZONE_EDGE counts of the readable end ahead may be a held one,
# with the lift anywhere up to the wrap.
READ_MAX = 1022
DEAD_ZONE_EDGE = 10
# A turn of the lift takes at least this long at duty 800, in ms, and 800 /
# |duty| times it at other duties. Middle's lift took 961-1,203 ms at duty 800
# or its equivalent, fastest at duty 500 closing (#78). This leaves a margin
# for a lighter load, such as a blind closing from the top.
FASTEST_TURN_MS = 800
_FASTEST_TURN_DUTY = 800
# A pause in the samples ends at least this many revolutions short of the
# move's end, by travel: an end sensor that goes active in one stops the lift
# late, past its zone.
END_MARGIN_REVS = 1.0

_LOW = 0
_HIGH = 1

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
        self._direction = 1 if counting_up else -1
        # Opening, the angle counts up, so it wraps from high to low.
        self._wrap_from, self._wrap_to = (_HIGH, _LOW) if counting_up else (_LOW, _HIGH)
        self._zone = None           # The last angle's third, mid-turn aside
        self._angle = None          # The last angle taken
        self._start_angle = None    # The first angle taken
        self._turns = 0
        self._held = False          # Whether the last angle was held
        self.revs = 0.0

    def feed(self, angle):
        """Take one servo angle, unless it's a stray at the wrap."""
        zone = _zone(angle)
        self._held = True
        if self._zone == self._wrap_from:
            if zone is None:
                return
            if zone == self._wrap_from and (self._angle - angle) * self._direction > BACK_TOLERANCE:
                return
        self._held = False
        if self._start_angle is None:
            self._start_angle = angle
        if self._zone == self._wrap_from and zone == self._wrap_to:
            self._turns += self._direction
        if zone is not None:
            self._zone = zone
        self._angle = angle
        self.revs = self._turns + (angle - self._start_angle) / COUNTS_PER_TURN

    def can_skip(self, counts):
        """Whether the lift can turn counts on from the last angle taken,
        with none taken between, and its wraps still count: from before the
        turn's last third, up to the readable end; from within it, up to the
        end of the next turn's first third. Not before the first angle, nor
        after a stray, which leaves where the lift is unsure."""
        if self._angle is None or self._held:
            return False
        to_end = READ_MAX - self._angle if self._direction > 0 else self._angle
        if to_end <= DEAD_ZONE_EDGE:
            to_end = 0
        if _zone(self._angle) == self._wrap_from:
            # The next turn's first third, the narrower of the two.
            return counts < to_end + READ_MAX - HIGH_START
        return counts < to_end


def _zone(angle):
    """The servo angle's third of the turn: _LOW, _HIGH, or None mid-turn."""
    if angle < LOW_END:
        return _LOW
    if angle >= HIGH_START:
        return _HIGH
    return None


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
        """The full travel, or the estimate until it's learned."""
        return self._estimate if math.isnan(self.full_travel) else self.full_travel

    @property
    def moved(self):
        """The revolutions the move has turned, up positive."""
        return self._counter.revs

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

    def beyond_limit(self, margin):
        """Whether the move has gone margin revolutions past its end: above
        full travel opening, below the bottom closing. With the travel
        unknown, whether the move itself has turned full travel plus margin,
        more than any move needs."""
        if math.isnan(self.travel):
            return abs(self.moved) > self.full_or_estimate + margin
        return self.remaining < -margin

    def reached(self):
        """The move reached its end sensor. One that started at the other end
        learns the full travel; then the travel re-anchors at this end."""
        if self._from_end:
            self._learn(abs(self.moved))
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

    def can_pause(self, duty, gap_ms):
        """Whether the move's samples can pause for gap_ms after the last
        one, at duty, with its turns still counted, and short of its end by
        END_MARGIN_REVS, by travel. With the travel unknown, only the turns
        count: where the end is isn't known."""
        counts = (gap_ms * abs(duty) * COUNTS_PER_TURN
                  / (FASTEST_TURN_MS * _FASTEST_TURN_DUTY))
        if not self._counter.can_skip(counts):
            return False
        if math.isnan(self.travel):
            return True
        return self.remaining > counts / COUNTS_PER_TURN + END_MARGIN_REVS

    def _learn(self, run):
        if not LEARN_MIN * self._estimate <= run <= LEARN_MAX * self._estimate:
            return
        if math.isnan(self.full_travel) or abs(run - self.full_travel) > LEARN_TOLERANCE:
            self.full_travel = run


def at_boot(up_active, down_active, stored_state, stored_travel, full_travel, estimate):
    """The tracker at boot, from whether each end sensor is active and the
    record in NVM. The stored travel holds after a settled cover state, open,
    closed or stopped, but not after an interrupted move or with a blank
    record. An active end sensor re-anchors it. Both active can't be, so
    neither is trusted, nor the stored travel."""
    settled = stored_state in cover_state.SETTLED
    tracker = Tracker(stored_travel if settled else NAN, full_travel, estimate)
    if up_active and down_active:
        tracker.lose()
    elif up_active:
        tracker.anchor(cover_state.UP)
    elif down_active:
        tracker.anchor(cover_state.DOWN)
    return tracker
