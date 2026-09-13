"""Revolution counting from the lift's servo angle, which wraps once per
turn. Pure, so the host tests run it."""

# The servo angle reads 0-1023 over a turn. A turn ends where it wraps from
# the last third of the turn into the first (opening, when it counts up) or
# the other way (closing). Samples in the middle third are never part of a
# wrap, and one stray value between the two sides of it (532 or 275 between
# 1021 and 0) adds no count wherever it lands. A sample can come up to a third
# of a turn late, about 235 ms at full speed, and the wrap still counts.
LOW_END = 341
HIGH_START = 683

_LOW = 0
_HIGH = 1


class RevolutionCounter:
    """Counts the lift's revolutions during one move."""

    def __init__(self, counting_up):
        # Opening, the angle counts up, so it wraps from high to low.
        self._wrap_from, self._wrap_to = (_HIGH, _LOW) if counting_up else (_LOW, _HIGH)
        self._zone = None
        self.count = 0

    def feed(self, angle):
        """Take one servo angle. Returns True if it completes a revolution."""
        if angle < LOW_END:
            zone = _LOW
        elif angle >= HIGH_START:
            zone = _HIGH
        else:
            return False
        wrapped = self._zone == self._wrap_from and zone == self._wrap_to
        self._zone = zone
        if wrapped:
            self.count += 1
        return wrapped
