"""Re-seating after every stop at an end, and approaching the top only from
below (#53): the drives of an open or close, from the end sensor's level after
each drive, the cover state stored in NVM, the travel and the direction.
"""

import unittest

import cover_state
from persist import NAN
from re_seat import CRAWL_DOWN, CRAWL_UP, MOVE, RE_SEAT, Drive, Plan

FULL = 27.5


def plan(end=cover_state.UP, stored=cover_state.UP, travel=FULL):
    return Plan(end, stored, travel, FULL, crawl_down_revs=1.0, re_seat_revs=1.0)


class OvershootAboveTest(unittest.TestCase):
    def test_an_open_from_the_top_off_the_up_end_sensor_crawls_down_first(self):
        # The stored cover state says open, but the up end sensor is
        # inactive: the blind may have overshot above it, under the head rail.
        self.assertEqual(plan().next(active=False), Drive(CRAWL_DOWN, up=False, revs=1.0))

    def test_a_crawl_down_that_finds_the_up_end_sensor_stops_there(self):
        # The blind had overshot above: it's at the top now.
        moves = plan()
        moves.next(active=False)

        self.assertIsNone(moves.next(active=True, result=cover_state.REACHED))
        self.assertEqual(moves.result, cover_state.REACHED)


class ReSeatTest(unittest.TestCase):
    def test_an_open_that_overshot_the_up_end_sensor_crawls_back_down_onto_it(self):
        # Going back over the up end sensor means crawling down.
        moves = plan(stored=cover_state.DOWN, travel=0.0)
        moves.next(active=False)

        self.assertEqual(moves.next(active=False, result=cover_state.REACHED),
                         Drive(RE_SEAT, up=False, revs=1.0))
        self.assertIsNone(moves.next(active=True, result=cover_state.REACHED))
        self.assertEqual(moves.result, cover_state.REACHED)

    def test_a_re_seat_that_runs_out_of_budget_ends_the_move_stopped(self):
        moves = plan(stored=cover_state.DOWN, travel=0.0)
        moves.next(active=False)
        moves.next(active=False, result=cover_state.REACHED)

        self.assertIsNone(moves.next(active=False, result=cover_state.CRAWL_LIMIT))
        self.assertEqual(moves.result, cover_state.CRAWL_LIMIT)
        self.assertEqual(cover_state.after_move(moves.result, cover_state.UP), cover_state.STOPPED)


    def test_a_close_that_overshot_the_down_end_sensor_crawls_back_up_onto_it(self):
        moves = plan(end=cover_state.DOWN, stored=cover_state.UP)
        self.assertEqual(moves.next(active=False), Drive(MOVE, up=False))

        self.assertEqual(moves.next(active=False, result=cover_state.REACHED),
                         Drive(RE_SEAT, up=True, revs=1.0))

    def test_a_crawl_down_that_coasted_below_the_up_end_sensor_crawls_back_up_onto_it(self):
        moves = plan()
        moves.next(active=False)

        self.assertEqual(moves.next(active=False, result=cover_state.REACHED),
                         Drive(RE_SEAT, up=True, revs=1.0))

    def test_a_re_seat_isnt_re_seated(self):
        # It stops from approach speed, which coasts far less than the end
        # sensor's zone: its stop stands.
        moves = plan(stored=cover_state.DOWN, travel=0.0)
        moves.next(active=False)
        moves.next(active=False, result=cover_state.REACHED)

        self.assertIsNone(moves.next(active=False, result=cover_state.REACHED))
        self.assertEqual(moves.result, cover_state.REACHED)

    def test_a_move_that_stopped_short_of_the_end_sensor_isnt_re_seated(self):
        for result in (cover_state.STALLED, cover_state.TRAVEL_LIMIT, cover_state.TIMED_OUT,
                       cover_state.START_FAILED, cover_state.STOP_FAILED, cover_state.ERROR):
            with self.subTest(result=result):
                moves = plan(stored=cover_state.DOWN, travel=0.0)
                moves.next(active=False)

                self.assertIsNone(moves.next(active=False, result=result))
                self.assertEqual(moves.result, result)


class AlreadyThereTest(unittest.TestCase):
    def test_an_active_end_sensor_ends_the_move_before_any_drive(self):
        for end in (cover_state.UP, cover_state.DOWN):
            with self.subTest(end=end):
                moves = plan(end=end)

                self.assertIsNone(moves.next(active=True))
                self.assertEqual(moves.result, cover_state.REACHED)


class TravelAtTheTopTest(unittest.TestCase):
    def test_an_open_with_the_travel_at_the_top_crawls_down_first(self):
        # Such as after a stall or the travel limit near the top, which leave
        # the cover state stopped. Within the crawl-down distance of full
        # travel, or above it.
        for travel in (FULL - 0.9, FULL, FULL + 2.1):
            with self.subTest(travel=travel):
                self.assertEqual(plan(stored=cover_state.STOPPED, travel=travel).next(active=False),
                                 Drive(CRAWL_DOWN, up=False, revs=1.0))

    def test_an_open_from_below_the_top_is_the_move_proper(self):
        for travel in (FULL - 1.1, 3.0, 0.0):
            with self.subTest(travel=travel):
                self.assertEqual(plan(stored=cover_state.STOPPED, travel=travel).next(active=False),
                                 Drive(MOVE, up=True))

    def test_an_unknown_travel_alone_isnt_the_top(self):
        # The move proper then runs at approach speed all the way (travel).
        for stored in (cover_state.UNKNOWN, cover_state.STOPPED, cover_state.MOVING_DOWN):
            with self.subTest(stored=stored):
                self.assertEqual(plan(stored=stored, travel=NAN).next(active=False),
                                 Drive(MOVE, up=True))


class InterruptedOpeningTest(unittest.TestCase):
    def test_an_open_after_an_interrupted_opening_crawls_down_first(self):
        # The blind may have reset against the head rail: never approach it
        # at more than approach speed from an unknown position.
        moves = plan(stored=cover_state.MOVING_UP, travel=NAN)

        self.assertEqual(moves.next(active=False), Drive(CRAWL_DOWN, up=False, revs=1.0))
        self.assertEqual(moves.next(active=False, result=cover_state.CRAWL_LIMIT),
                         Drive(CRAWL_UP, up=True))


class RestingJustBelowTest(unittest.TestCase):
    def test_a_crawl_down_that_doesnt_find_the_up_end_sensor_crawls_up_to_it(self):
        # The blind was below the up end sensor, so it's safe to drive up, at
        # approach speed, until the sensor goes active.
        moves = plan()
        moves.next(active=False)

        self.assertEqual(moves.next(active=False, result=cover_state.CRAWL_LIMIT),
                         Drive(CRAWL_UP, up=True))
        self.assertIsNone(moves.next(active=True, result=cover_state.REACHED))
        self.assertEqual(moves.result, cover_state.REACHED)

    def test_a_crawl_down_that_stalls_ends_the_move(self):
        moves = plan()
        moves.next(active=False)

        self.assertIsNone(moves.next(active=False, result=cover_state.STALLED))
        self.assertEqual(moves.result, cover_state.STALLED)


class DistancesTest(unittest.TestCase):
    def test_the_crawls_turn_at_most_the_distances_given(self):
        moves = Plan(cover_state.UP, cover_state.UP, FULL, FULL,
                     crawl_down_revs=0.6, re_seat_revs=1.4)

        self.assertEqual(moves.next(active=False), Drive(CRAWL_DOWN, up=False, revs=0.6))
        self.assertEqual(moves.next(active=False, result=cover_state.REACHED),
                         Drive(RE_SEAT, up=True, revs=1.4))

    def test_the_starting_values_are_1_rev_each(self):
        moves = Plan(cover_state.UP, cover_state.UP, FULL, FULL)

        self.assertEqual(moves.next(active=False), Drive(CRAWL_DOWN, up=False, revs=1.0))
        self.assertEqual(moves.next(active=False, result=cover_state.REACHED),
                         Drive(RE_SEAT, up=True, revs=1.0))


if __name__ == "__main__":
    unittest.main()
