"""A blind's cover state, as the Blinds.POSITION_* values, and the one a move
leaves, from how the move ended. Pure, so the host tests run it."""

UNKNOWN = -1
STOPPED = 0
DOWN = 1            # closed
UP = 2              # open
MOVING_UP = 3       # opening
MOVING_DOWN = 4     # closing

# How an open or close ended: Blinds.operate()'s result.
REACHED = "end sensor reached"
STALLED = "stall stop"
TRAVEL_LIMIT = "travel limit reached"
TIMED_OUT = "timeout"
START_FAILED = "failed to start"
STOP_FAILED = "failed to stop"
ERROR = "error"


def after_move(result, end):
    """The cover state a move toward end, UP or DOWN, leaves: that end only
    if it reached the end sensor. Otherwise stopped, or unknown if the
    lift's stop wasn't confirmed, as it may still be driving."""
    if result == STOP_FAILED:
        return UNKNOWN
    return end if result == REACHED else STOPPED
