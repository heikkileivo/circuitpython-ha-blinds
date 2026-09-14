"""Re-seating the blind after every stop at an end, and approaching the top
only from below (#8, #53). See re-seat and head rail in CONTEXT.md.

An open or close is a run of drives of the lift, each until the move's end
sensor goes active. Plan decides the next one from the end sensor's level
once the last has stopped, the cover state stored in NVM, the travel and the
direction:

- A drive that reached the end sensor, but stopped past its zone, re-seats:
  it crawls back onto it, at most the re-seat budget. Going back over the up
  end sensor means crawling down.
- An open that may rest at the top off its up end sensor, as the stored cover
  state or the travel says, or after an interrupted opening, first crawls
  down, the safe direction, at most the crawl-down distance, looking for it.
  If it goes active, the blind had overshot above, and is at the top. If not,
  the blind was below, and crawls up to it. So the head rail is never
  approached at more than approach speed from an unknown position.

Every crawl is at approach speed, and the stall stop guards it. Pure, so the
host tests run it."""

import cover_state

# Starting values, in revolutions, which the re_seat_revs and crawl_down_revs
# settings override. On the bench the up end sensor's window was about 0.85 s
# at duty 800, roughly 1 rev, and a stop from duty 300 coasted about 40 counts
# (#21). Stage 7 (#54) measures the gap between the up end sensor and the head
# rail, and tunes them.
RE_SEAT_REVS = 1.0
CRAWL_DOWN_REVS = 1.0

# The drives of an open or close.
MOVE = "move"               # The move proper, toward the end.
CRAWL_DOWN = "crawl down"   # From the top, looking for the up end sensor.
CRAWL_UP = "crawl up"       # To the up end sensor the crawl down didn't find.
RE_SEAT = "re-seat"         # Back onto the end sensor the last drive stopped past.


class Drive:
    """One drive of the lift: its kind, whether it drives up, and the most it
    turns, in revolutions, or None for only the travel limit. Every drive
    but the move proper crawls, at approach speed."""

    def __init__(self, kind, up, revs=None):
        self.kind = kind
        self.up = up
        self.revs = revs

    def __eq__(self, other):
        return (isinstance(other, Drive)
                and (self.kind, self.up, self.revs) == (other.kind, other.up, other.revs))

    def __repr__(self):
        text = self.kind
        if self.kind in (MOVE, RE_SEAT):
            text += " up" if self.up else " down"
        if self.revs is not None:
            text += f", at most {self.revs} revolutions"
        return text


class Plan:
    """The drives of one open or close toward end, UP or DOWN, from the cover
    state stored before it, its travel, and top, the travel at the up end
    sensor: full travel, or the estimate until it's learned. A NaN travel is
    unknown."""

    def __init__(self, end, stored, travel, top,
                 crawl_down_revs=CRAWL_DOWN_REVS, re_seat_revs=RE_SEAT_REVS):
        self._up = end == cover_state.UP
        # At the top, the blind may rest on either side of its up end sensor:
        # as the stored cover state says, after an interrupted opening, or
        # with the travel within the crawl-down distance of the top or above
        # it. A NaN travel compares false.
        self._at_top = (stored in (cover_state.UP, cover_state.MOVING_UP)
                        or travel >= top - crawl_down_revs)
        self._crawl_down_revs = crawl_down_revs
        self._re_seat_revs = re_seat_revs
        self._last = None
        # How the move ended, once next() has returned None.
        self.result = None

    def next(self, active, result=None):
        """The next drive, or None once the move has ended. active is whether
        the end sensor is active: before the first drive, or once the last
        has stopped, ended so by result, one of cover_state's."""
        if self._last is None:
            if active:
                self.result = cover_state.REACHED
                return None
            if self._up and self._at_top:
                return self._drive(Drive(CRAWL_DOWN, False, self._crawl_down_revs))
            return self._drive(Drive(MOVE, self._up))
        if self._last.kind == CRAWL_DOWN and result == cover_state.CRAWL_LIMIT:
            # The blind was below the up end sensor.
            return self._drive(Drive(CRAWL_UP, True))
        # A re-seat stops from approach speed, which coasts far less than the
        # end sensor's zone, so its stop stands.
        if result == cover_state.REACHED and not active and self._last.kind != RE_SEAT:
            return self._drive(Drive(RE_SEAT, not self._last.up, self._re_seat_revs))
        self.result = result
        return None

    def _drive(self, drive):
        self._last = drive
        return drive
