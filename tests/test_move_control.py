"""One open or close at a time, and STOP for the one under way (#55).

An open or close is a run of drives of the lift (re_seat). A second OPEN or
CLOSE while one is under way is ignored, rather than starting a concurrent
move. STOP ends the drive under way, or the next one as it starts. The drive
under way also decides whether asyncio may block, as MQTT's loop() does.
"""

import unittest

from move_control import MoveControl

PAUSE_MS = 500


class OneMoveAtATimeTest(unittest.TestCase):
    def test_a_second_move_while_one_is_under_way_isnt_begun(self):
        control = MoveControl()

        self.assertTrue(control.begin())
        self.assertFalse(control.begin())

    def test_once_the_move_ends_the_next_one_begins(self):
        control = MoveControl()
        control.begin()

        control.end()
        self.assertTrue(control.begin())


class FakeDrive:
    """A drive of the lift, which records whether STOP ended it, and the
    pauses it was asked about."""

    def __init__(self, can_pause=True):
        self.stopped = False
        self.asked = []
        self._can_pause = can_pause

    def stop(self):
        self.stopped = True

    def can_pause(self, pause_ms):
        self.asked.append(pause_ms)
        return self._can_pause


def drive_under_way(control, drive):
    control.drive(drive.stop, drive.can_pause)


class StopTest(unittest.TestCase):
    def test_stop_ends_the_drive_under_way(self):
        control = MoveControl()
        control.begin()
        drive = FakeDrive()
        drive_under_way(control, drive)

        self.assertTrue(control.stop())
        self.assertTrue(drive.stopped)

    def test_stop_between_drives_ends_the_next_drive_as_it_starts(self):
        # Such as during the stop sequence, before a re-seat.
        control = MoveControl()
        control.begin()
        first = FakeDrive()
        drive_under_way(control, first)
        control.drive_ended()

        self.assertTrue(control.stop())
        self.assertFalse(first.stopped)
        second = FakeDrive()
        drive_under_way(control, second)
        self.assertTrue(second.stopped)

    def test_stop_with_no_move_under_way_is_left_to_the_blind(self):
        # The blind then runs its own stop, as it always has.
        control = MoveControl()

        self.assertFalse(control.stop())

    def test_a_stop_for_an_ended_move_doesnt_stop_the_next(self):
        # STOP came after the move's last drive had ended, or once the move
        # was over.
        for stop_after_end in (False, True):
            with self.subTest(stop_after_end=stop_after_end):
                control = MoveControl()
                control.begin()
                if not stop_after_end:
                    control.stop()
                control.end()
                if stop_after_end:
                    control.stop()

                control.begin()
                drive = FakeDrive()
                drive_under_way(control, drive)
                self.assertFalse(drive.stopped)


class PauseTest(unittest.TestCase):
    def test_with_no_drive_under_way_asyncio_may_pause(self):
        # Idle, or in a move between its drives, such as while a close
        # tilts the slats at its end: the lift isn't driving.
        control = MoveControl()
        self.assertTrue(control.may_pause(PAUSE_MS))

        control.begin()
        self.assertTrue(control.may_pause(PAUSE_MS))

    def test_the_drive_under_way_decides(self):
        control = MoveControl()
        control.begin()
        drive = FakeDrive(can_pause=False)
        drive_under_way(control, drive)

        self.assertFalse(control.may_pause(PAUSE_MS))
        self.assertEqual(drive.asked, [PAUSE_MS])
        control.drive_ended()
        self.assertTrue(control.may_pause(PAUSE_MS))


if __name__ == "__main__":
    unittest.main()
