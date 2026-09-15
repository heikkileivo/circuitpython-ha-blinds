"""One open or close at a time, and STOP for the one under way (#55). Pure,
so the host tests run it.

An open or close is a run of drives of the lift, which re_seat plans. A
second OPEN or CLOSE while one is under way would start a concurrent move, so
it's refused. STOP ends the drive under way, which stops through its stop
sequence, or the next one as it starts, as when STOP comes between a drive and
its re-seat."""


class MoveControl:
    def __init__(self):
        self._under_way = False
        self._stop_drive = None     # Ends the drive under way, if one is
        self._stopping = False      # STOP has come for the move under way

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
        self._stop_drive = None

    def drive(self, stop_drive):
        """A drive of the open or close under way begins. STOP ends it by
        calling stop_drive, at once if STOP has come already."""
        self._stop_drive = stop_drive
        if self._stopping:
            stop_drive()

    def drive_ended(self):
        """The drive under way has ended, its lift stopped."""
        self._stop_drive = None

    def stop(self):
        """STOP: end the open or close under way. Returns whether one was;
        if not, the blind runs its own stop."""
        if not self._under_way:
            return False
        self._stopping = True
        if self._stop_drive is not None:
            self._stop_drive()
        return True
