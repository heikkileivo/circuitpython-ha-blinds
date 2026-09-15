"""One open or close at a time, and STOP for the one under way (#55). Pure,
so the host tests run it.

An open or close is a run of drives of the lift, which re_seat plans. A
second OPEN or CLOSE while one is under way would start a concurrent move, so
it's refused. STOP ends the drive under way, which stops through its stop
sequence, or the next one as it starts, as when STOP comes between a drive and
its re-seat. The drive under way also decides whether asyncio may block, as
MQTT's loop() does."""


class MoveControl:
    def __init__(self):
        self._under_way = False
        self._stopping = False      # STOP has come for the move under way
        # The drive under way's, if one is: ends it, and says whether
        # asyncio may block for so many ms.
        self._stop_drive = None
        self._can_pause = None

    def begin(self):
        """Begin an open or close. Returns False, and begins nothing, if one
        is under way already."""
        if self._under_way:
            return False
        self._under_way = True
        self._stopping = False
        return True

    def end(self):
        """The open or close under way has ended."""
        self._under_way = False
        self.drive_ended()

    def drive(self, stop_drive, can_pause):
        """A drive of the open or close under way begins. STOP ends it by
        calling stop_drive, at once if STOP has come already. can_pause(ms)
        says whether asyncio may block for ms meanwhile."""
        self._stop_drive = stop_drive
        self._can_pause = can_pause
        if self._stopping:
            stop_drive()

    def drive_ended(self):
        """The drive under way has ended, its lift stopped."""
        self._stop_drive = None
        self._can_pause = None

    def stop(self):
        """STOP: end the open or close under way. Returns whether one was;
        if not, the blind runs its own stop."""
        if not self._under_way:
            return False
        self._stopping = True
        if self._stop_drive is not None:
            self._stop_drive()
        return True

    def may_pause(self, pause_ms):
        """Whether asyncio may block for pause_ms now: as the drive under way
        says, and always with none, as then the lift isn't driving."""
        return self._can_pause is None or self._can_pause(pause_ms)
