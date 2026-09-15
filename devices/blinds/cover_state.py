"""A blind's cover state, as the Blinds.POSITION_* values: the one a move
leaves, from how the move ended, and the one at boot, from the end sensors
and the state stored in NVM. Pure, so the host tests run it."""

UNKNOWN = -1
STOPPED = 0
DOWN = 1            # closed
UP = 2              # open
MOVING_UP = 3       # opening
MOVING_DOWN = 4     # closing

# The cover states a blind rests in once a move has ended.
SETTLED = (UP, DOWN, STOPPED)

# How an open or close ended: Blinds.operate()'s result.
REACHED = "end sensor reached"
STALLED = "stall stop"
TRAVEL_LIMIT = "travel limit reached"
# A crawl turned its most without its end sensor going active (re_seat).
CRAWL_LIMIT = "crawl limit reached"
TIMED_OUT = "timeout"
# A STOP command ended it (#55).
STOP_COMMAND = "STOP command"
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


def at_boot(up_active, down_active, stored):
    """The cover state at boot, from whether each end sensor is active and
    the cover state stored in NVM, UNKNOWN for a blank or invalid record.

    An active end sensor wins. With neither active, a stored open, closed or
    stopped holds: the blind may have settled off its end sensor. A stored
    opening or closing is an interrupted move, so the state is unknown until
    an end sensor re-anchors it. So is a blank record: never closed by
    default. Both end sensors active can't be, so neither is trusted."""
    if up_active and down_active:
        return UNKNOWN
    if up_active:
        return UP
    if down_active:
        return DOWN
    if stored in SETTLED:
        return stored
    return UNKNOWN
