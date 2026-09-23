from end_sensors import UP as UP_SENSOR, DOWN as DOWN_SENSOR
from move_control import MoveControl
from packet import Address
from servo_health import MoveFigures
from tilt import read_at_boot
import servo_bus
import servo_health
import cover_state
import lift_stop
import persist
import re_seat
import servo_wait
import speed_profile
import stall
import travel
from time import monotonic_ns, sleep
import microcontroller
import asyncio
import math
import env

def now_ms():
    """The time in ms for the servo waits. monotonic() loses precision within
    hours of uptime; monotonic_ns() doesn't."""
    return monotonic_ns() // 1_000_000

# An Exception, so the except Exception handlers catch it.
class ServoException(Exception):
    def __init__(self, message):
        super().__init__(message)
        self._message = message

    @property
    def message(self):
        return self._message

class ServoTimeout(ServoException):
    def __init__(self, message):
        super().__init__(message)

class ServoCommFailure(ServoException):
    def __init__(self, message):
        super().__init__(message)

def _encode_duty(value):
    """The duty as GOAL_TIME takes it: its magnitude in bits 0-9, and the
    direction in bit 10."""
    return abs(value) | (1 << 10) if value < 0 else value

class Servo:
    def __init__(self, id, reader, scale=1.0):
        self._id = id
        self._reader = reader
        self._scale = scale
        self._pos = 0               # Previous position
        self._v = 0                 # Previous Velocity
        self._i = 0                 # Previous Current
        self._l = 0                 # Previous Load
        self._u = 0                 # Previous Voltage
        self._t = 0                 # Previous Temperature
        self._duty = 0              # Duty last commanded
        self.move = MoveFigures()   # This move's figures, fed by every sample

    @property
    def id(self):
        return self._id

    @property
    def position(self):
        self._pos, diff = self.read_value(Address.PRESENT_POSITION_L, self._pos)
        return self._pos, diff

    @position.setter
    def position(self, value):
        pos = int(value * self._scale)
        self._reader.set_position(self._id, pos)


    @property
    def speed(self):
        v = self._reader.get_time(self._id)
        sign = v & (1<<10)
        if sign:
            return -(v & (1<<10)-1)
        else:
            return v

    @speed.setter
    def speed(self, value):
        self._duty = value
        value = _encode_duty(value)
        retries = 3
        while True:
            self._reader.set_time(self._id, value)
            v = self._reader.get_time(self._id)
            if v == value:
                print(f"Successfully set speed to {value}.")
                break
            else:
                if retries:
                    print(f"Failed to set speed to {value}, returned {v}, retrying...")
                    retries -= 1
                else:
                    raise ServoCommFailure(f"Failed to set servo speed to {value}.")

    def set_duty(self, value):
        """Write the duty without the read-back check the speed setter makes,
        for the speed profile's updates during a drive (#57): the duty is
        verified at the start and at the stop instead. Returns whether the
        servo replied."""
        self._duty = value
        return self._reader.set_time(self._id, _encode_duty(value)) is not None

    @property
    def commanded_duty(self):
        """The duty last written through speed, negative for up, whether
        or not the servo confirmed it."""
        return self._duty

    @property
    def angle_and_speed(self):
        """The servo angle and PRESENT_SPEED in one read, or None. The same
        read feeds the load and supply voltage to the move's figures."""
        sample = self._reader.read_motion(self._id)
        if sample is None:
            return None
        angle, speed, load, voltage = sample
        self.move.feed(voltage, load)
        return angle, speed


    @property
    def current(self):
        self._i, diff = self.read_value(Address.PRESENT_CURRENT_L, self._i)
        return self._i, diff

    @property
    def load(self):
        self._l, diff = self.read_value(Address.PRESENT_LOAD_L, self._l)
        return self._l, diff

    @property
    def voltage(self):
        return self._reader.read_1_byte(self._id, Address.PRESENT_VOLTAGE)

    @property
    def temperature(self):
        return self._reader.read_1_byte(self._id, Address.PRESENT_TEMPERATURE)

    @property
    def enable_torque(self):
        return self._reader.read_1_byte(self._id, Address.TORQUE_ENABLE) != 0

    @enable_torque.setter
    def enable_torque(self, value):
        raw_value = 1 if value else 0
        self._reader.write_byte(self._id, Address.TORQUE_ENABLE, raw_value)

    @property
    def is_moving(self):
        """Whether the servo is moving, or None if it didn't reply. The same
        read feeds the load and supply voltage to the move's figures."""
        sample = self._reader.read_moving(self._id)
        if sample is None:
            return None
        load, voltage, moving = sample
        self.move.feed(voltage, load)
        return moving

    def read_value(self, address, previous_value):
        value = self._reader.read_2_bytes(self._id, address)
        diff = None
        if value:
            if previous_value:
                diff = previous_value - value
        return (value, diff)

    async def start(self, speed):
        """Command the duty, and wait for the servo to start moving, at most
        lift_start_deadline_ms. Returns whether it started."""
        print(f"Starting servo...")
        try:
            self.speed = speed
        except ServoCommFailure as e:
            print(f"Starting servo failed: {e}")
            return False

        deadline_ms = env.integer("lift_start_deadline_ms", servo_wait.START_DEADLINE_MS)
        if not await servo_wait.until_moving(lambda: self.is_moving, now_ms, deadline_ms):
            print(f"Servo {self._id} didn't start moving within {deadline_ms} ms.")
            return False
        print(f"Servo started moving.")
        return True

    async def stop(self):
        """Stop the lift with its stop sequence (lift_stop): duty 0, then
        the brake once it has stopped moving, or once lift_stop_deadline_ms
        has passed. A duty 0 that isn't confirmed leaves it limp instead, at
        once. Returns how the sequence ended."""
        print(f"Stopping servo...")
        duty_0_outcome = self._write_duty_0()
        if duty_0_outcome == lift_stop.CONFIRMED:
            deadline_ms = env.integer("lift_stop_deadline_ms", servo_wait.STOP_DEADLINE_MS)
            if not await servo_wait.until_still(lambda: self.is_moving, now_ms, deadline_ms):
                print(f"Servo {self._id} didn't stop moving within {deadline_ms} ms.")
                # It may have missed the first duty 0.
                duty_0_outcome = self._write_duty_0()
        ended = servo_health.stop_lift(self._reader, duty_0_outcome)
        print(f"Servo {self._id} stopped: {ended}.")
        return ended

    def _write_duty_0(self):
        """Write duty 0, and read it back. Returns the read-back's outcome,
        one of lift_stop's."""
        self._duty = 0
        return servo_health.write_duty_0(self._reader, self._id)

    def torque_off(self):
        """Turn the torque off, and read it back. Returns whether it's
        confirmed off."""
        return servo_health.torque_off(self._reader, self._id)

    def __repr__(self):
        return f"Motor {self._id}: Pos: {self._pos} V: {self._v} I: {self._i} L: {self._l} U: {self._u} T: {self._t}"

async def watch_end_sensor(end_sensors, sensor, finish_event, callback):
    """Call callback once the end sensor has gone active since
    end_sensors.watch(sensor), unless finish_event is set first."""
    print(f"Watching end sensor {sensor}...")
    while not finish_event.is_set():
        if end_sensors.reached(sensor):
            callback()
            break
        await asyncio.sleep(0)
    print(f"Completed watching end sensor {sensor}.")

async def track_travel(servo, finish_event, tracker, sample_ms, on_sample, on_stall):
    """Sample the lift's servo angle and speed every sample_ms, feed the
    angle to the travel tracker, then call on_sample. Stop the lift at once
    if it stalls, then call on_stall."""
    sample_s = sample_ms / 1000
    detector = stall.StallDetector(
        window_ms=env.integer("stall_window_ms", stall.WINDOW_MS),
        max_speed=env.integer("stall_max_speed", stall.MAX_SPEED),
        max_angle_change=env.integer("stall_max_angle_change", stall.MAX_ANGLE_CHANGE),
        grace_ms=env.integer("stall_grace_ms", stall.GRACE_MS),
        dead_zone_window_ms=env.integer("stall_dead_zone_window_ms", stall.DEAD_ZONE_WINDOW_MS))
    print(f"Tracking travel for servo {servo.id}...")
    while True:
        # monotonic() loses precision within hours of uptime; monotonic_ns() doesn't.
        started = monotonic_ns()
        sample = servo.angle_and_speed
        if sample:
            angle, speed = sample
            if detector.feed(started // 1000000, angle, speed, servo.commanded_duty):
                # Stop the lift before anything else. operate()'s stop path
                # then ends the move.
                try:
                    servo.speed = 0
                except ServoCommFailure as e:
                    print(f"Failed to stop the stalled lift: {e}")
                print(f"STALL: the lift's servo angle was frozen at {detector.frozen_angle} for {detector.frozen_ms} ms.")
                on_stall()
                break
            tracker.feed(angle)
            on_sample()
        if finish_event.is_set():
            break

        await asyncio.sleep(max(0, sample_s - (monotonic_ns() - started) / 1e9))
    print(f"Completed tracking travel for servo {servo.id}.")

async def wait(timeout, on_timeout):
    print(f"Waiting for {timeout} seconds...")
    try:
        await asyncio.sleep(timeout)
        print("Timeout reached.")
        on_timeout()
    except asyncio.CancelledError:
        print("Waiting was cancelled.")
    print("Completed waiting.")




class Blinds:
        POSITION_UNKNOWN = cover_state.UNKNOWN
        POSITION_STOPPED = cover_state.STOPPED
        POSITION_DOWN = cover_state.DOWN
        POSITION_UP = cover_state.UP
        POSITION_MOVING_UP = cover_state.MOVING_UP
        POSITION_MOVING_DOWN = cover_state.MOVING_DOWN

        def __init__(self, reader, update_callback, on_opened, on_moved, end_sensors, tilt_scale):
            self._reader = reader
            self._update_callback = update_callback
            self._on_opened = on_opened
            self._on_moved = on_moved
            self._moves = 0             # Moves under way, tilt-only ones included
            self._lift_servo = Servo(servo_bus.LIFT_ID, reader)
            self._tilt_servo = Servo(servo_bus.TILT_ID, reader, scale=tilt_scale)
            self._end_sensors = end_sensors
            # The cover state starts as worked out at boot, from the end
            # sensors and the record in NVM. The record keeps an interrupted
            # move's direction.
            self._store = persist.Store(microcontroller.nvm)
            up_active = end_sensors.active(UP_SENSOR)
            down_active = end_sensors.active(DOWN_SENSOR)
            self._position = cover_state.at_boot(up_active, down_active, self._store.state)
            print(f"Cover state at boot: {self._position}, stored {self._store.state}.")
            # So does the travel, which an active end sensor re-anchors. Until
            # the full travel is learned, the window height over the
            # spindle's circumference stands in for it.
            h = env.number("window_height", 1800.0)
            d = env.number("spindle_diameter", 20.0)
            self._tracker = travel.at_boot(up_active, down_active, self._store.state,
                                           self._store.travel, self._store.full_travel,
                                           h / (d * math.pi))
            print(f"Travel at boot: {self._tracker.travel}, full travel {self._tracker.full_travel}.")
            self._tilt_scale = tilt_scale
            # code.py builds the blind after the boot re-init has stopped
            # the servos, and before the first connect publishes the tilt.
            self._tilt = read_at_boot(reader, tilt_scale)
            self._speed = env.integer("default_speed", 800)
            self._servo_position = 0
            self._revolutions = 0
            self._opened = 0
            self._tilt_move = None      # The tilt-only move's task, if one ran
            # One open or close at a time, STOP for it, and whether its drive
            # under way lets asyncio block.
            self._control = MoveControl()
            # Set once a lift stop isn't confirmed. code.py then fails main().
            self.stop_failed = asyncio.Event()


        @property
        def tilt(self):
            return self._tilt

        @tilt.setter
        def tilt(self, value):
            self._tilt = value
            if self._position == Blinds.POSITION_DOWN:
                # A tilt-only move, which publishes the state once it ends.
                self._cancel_tilt_move()
                self._tilt_move = asyncio.create_task(self._as_move(self._tilt_only(self._tilt)))
            else:
                # Not closed, the tilt is the next close's target, and no
                # servo drives to it now. Publish it at once.
                self.report_state()

        async def _tilt_only(self, value):
            """A tilt-only move to value, which publishes the state once it
            ends. A tilt that doesn't arrive leaves the cover state stopped,
            as after any timeout. A move that a later tilt command cancels
            publishes nothing: the later move publishes when it ends."""
            if not await self.drive_tilt(value):
                self._position = Blinds.POSITION_STOPPED
            # A cancel is raised in drive_tilt's wait, so never gets here.
            self.report_state()

        def _cancel_tilt_move(self):
            """Cancel a tilt-only move still under way, before a later drive
            of the tilt servo: the latest command wins. Left to run, the
            earlier move would turn the torque off under the later drive. It
            is cancelled in its wait, so it never does."""
            if self._tilt_move is not None:
                self._tilt_move.cancel()
                self._tilt_move = None

        async def _as_move(self, drive):
            """Await a coroutine that drives the servos, as one move. When
            the last move under way ends, on_moved gets the blind, whose
            move_figures are then the moves' figures."""
            self._begin_move()
            try:
                await drive
            finally:
                self._end_move()

        @property
        def opened_count(self):
            return self._opened

        async def drive_tilt(self, value):
            """Drive the tilt servo to value, and turn its torque off once it
            has arrived, or once tilt_deadline_ms has passed. Returns whether
            it arrived."""
            print(f"Driving tilt servo to {value}...")
            servo = self._tilt_servo
            servo.enable_torque = True
            servo.position = value
            deadline_ms = env.integer("tilt_deadline_ms", servo_wait.TILT_DEADLINE_MS)
            arrived = await servo_wait.until_still(
                lambda: servo.is_moving, now_ms, deadline_ms,
                env.integer("tilt_rise_ms", servo_wait.TILT_RISE_MS))
            if not arrived:
                print(f"The tilt servo didn't arrive at {value} within {deadline_ms} ms.")
            if not servo.torque_off():
                print("Failed to turn the tilt servo's torque off.")
            return arrived

        @property
        def speed(self):
            return self._speed

        @speed.setter
        def speed(self, value):
            self._speed = value

        @property
        def is_moving(self):
            return self._position in [Blinds.POSITION_MOVING_UP, Blinds.POSITION_MOVING_DOWN]

        @property
        def in_move(self):
            """Whether a move is driving either servo, a tilt-only one
            included. is_moving only covers an open or close."""
            return self._moves > 0

        def may_pause(self, pause_ms):
            """Whether asyncio may block for pause_ms now, as MQTT's loop()
            does, with the lift's turns still counted and its end sensor's
            stop on time. Always, unless a drive of the lift is under way."""
            return self._control.may_pause(pause_ms)

        @property
        def move_figures(self):
            """The lift and tilt servos' figures from the move under way.
            on_moved gets the blind while they're the finished move's."""
            return self._lift_servo.move, self._tilt_servo.move

        def _begin_move(self):
            # Moves can overlap, as each tilt command starts its own. The
            # first starts fresh figures, and the last to end reports them.
            if not self._moves:
                self._fresh_figures()
            self._moves += 1

        def _end_move(self):
            self._moves -= 1
            if not self._moves:
                self._on_moved(self)
                # Reads between moves, such as a stop while idle, then feed
                # figures nobody reports.
                self._fresh_figures()

        def _fresh_figures(self):
            self._lift_servo.move = MoveFigures()
            self._tilt_servo.move = MoveFigures()

        @property
        def position(self):
            return self._position

        @position.setter
        def position(self, value):
            self._position = value

        def _save_state(self, state):
            """Store a cover state in NVM, with the travel and full travel,
            while the lift servo is stopped. Unknown follows a stop that
            wasn't confirmed, when the lift may be driving, so it isn't
            stored. An opening or closing stores the travel as unknown,
            which it stays if the move is interrupted."""
            if state == Blinds.POSITION_UNKNOWN:
                return
            moving = state in (Blinds.POSITION_MOVING_UP, Blinds.POSITION_MOVING_DOWN)
            try:
                self._store.save(state, persist.NAN if moving else self._tracker.travel,
                                 self._tracker.full_travel)
            except Exception as e:
                print(f"Failed to store the cover state: {e!r}")

        async def operate(self, end, speed, approach_revs, timeout, state):
            """Drive the lift until its end sensor, and stop it. Returns how
            the move ended, one of cover_state's results: the first way to
            come, or STOP_FAILED if the lift's stop wasn't confirmed. Every
            exit, exceptions and cancellation included, ends with the lift's
            stop sequence, which leaves it braking, or limp if its duty 0
            isn't confirmed.

            The move is a run of drives, which re_seat plans from the end
            sensor after each: the move proper, and crawls at approach speed.
            An open that may rest at the top off its up end sensor first
            crawls down for it, at most crawl_down_revs, so it approaches the
            head rail only from below. A drive that reached the end sensor but
            stopped past it re-seats: it crawls back onto it, at most
            re_seat_revs.

            The move proper follows the speed profile (speed_profile): a
            soft start up to speed, then a ramp down to the approach speed
            over the last approach_revs of its travel, and the approach speed
            from there until the end sensor stops it. With the travel
            unknown, it runs at the approach speed all the way. Every drive
            stops once the travel is travel_margin_revs past the end. state
            is the cover state the move starts from: from the other end, the
            move proper reaching the end sensor learns the full travel.

            It stores the opening or closing just before the lift first
            starts, so a reset mid-move boots as an interrupted move, and the
            state the move leaves toward end, UP or DOWN, with the travel,
            after its last confirmed stop. A move whose lift never started,
            as its tilt didn't arrive or the move ended meanwhile, leaves the
            stored state as it was: the blind hasn't moved. Unless its end
            sensor went active meanwhile, which stores that end, as when it's
            active from the start, or STOP ended it, which stores stopped, as
            HA is told."""
            sensor = UP_SENSOR if end == Blinds.POSITION_UP else DOWN_SENSOR
            other_end = Blinds.POSITION_DOWN if end == Blinds.POSITION_UP else Blinds.POSITION_UP
            from_end = state == other_end
            tracker = self._tracker
            # Planned from the cover state the move starts from, and the one
            # stored, which after an interrupted move keeps its direction.
            plan = re_seat.Plan(end, state, self._store.state, tracker.travel, tracker.full_or_estimate,
                                env.number("crawl_down_revs", re_seat.CRAWL_DOWN_REVS),
                                env.number("re_seat_revs", re_seat.RE_SEAT_REVS))
            drive = plan.next(self._end_sensors.active(sensor))
            if drive is None:
                print("Already at stopped state.")
                tracker.anchor(end)
                self._save_state(end)
                return cover_state.REACHED
            # The first drive turns the slats to 50 before the lift moves.
            tilt_first = True
            any_started = False
            while drive is not None:
                print(f"Drive: {drive}.")
                result, started = await self._drive(drive, sensor, speed, approach_revs,
                                                    timeout, from_end, tilt_first)
                if result == cover_state.STOP_FAILED:
                    print("Failed to stop the lift servo.")
                    return result
                tilt_first = False
                any_started = any_started or started
                if result == cover_state.REACHED:
                    if started and not drive.crawls:
                        tracker.reached()
                    else:
                        # A crawl, or an end sensor that went active before
                        # the lift started: the blind is at the end.
                        tracker.anchor(end)
                drive = plan.next(self._end_sensors.active(sensor), result)
            result = plan.result
            if not any_started and result not in (cover_state.REACHED, cover_state.STOP_COMMAND):
                return result
            print(f"Travel {tracker.travel}, full travel {tracker.full_travel}.")
            self._save_state(cover_state.after_move(result, end))
            return result

        async def _drive(self, drive, sensor, speed, approach_revs, timeout, from_end, tilt_first):
            """One drive of a move, as re_seat plans it: drive the lift until
            the end sensor sensor goes active, and stop it. Returns how it
            ended, one of cover_state's results, and whether the lift
            started. Every exit, exceptions and cancellation included, ends
            with the lift's stop sequence.

            The move proper follows the speed profile, from a soft start up
            to speed and down to the approach speed over the last
            approach_revs, and gives up after timeout s. Its speed updates go
            out without the read-back check; the start and the stop are
            verified. A crawl drives at the approach speed all the way. The
            crawl down and the re-seat stop once they have turned drive.revs,
            and give up after crawl_timeout s. The crawl up, like the move proper with
            the travel unknown, gives up after approach_timeout s. Every
            drive stops at the travel limit, and at a stall. tilt_first is
            whether to turn the slats to 50 before the lift starts, with the
            end sensor watched, as the first drive does."""
            finish_event = asyncio.Event()
            result = None

            def finish(how):
                nonlocal result
                if result is None:
                    print(f"Drive ended: {how}.")
                    result = how
                finish_event.set()

            def sensor_reached():
                print("End sensor reached, stopping...")
                finish(cover_state.REACHED)

            tracker = self._tracker
            lift_started = False
            profile = None      # The speed profile, on every drive but a crawl
            updates = None      # Its speed writes, once the lift has started

            def handle_sample():
                if drive.revs is not None and abs(tracker.moved) >= drive.revs:
                    print(f"Crawled {tracker.moved} revolutions without the end sensor, stopping...")
                    finish(cover_state.CRAWL_LIMIT)
                elif tracker.beyond_limit(margin):
                    print(f"Travel {tracker.travel} is past its bound, stopping...")
                    finish(cover_state.TRAVEL_LIMIT)
                elif updates is not None:
                    duty = profile.duty(abs(tracker.moved), tracker.remaining)
                    if updates.due(now_ms(), duty):
                        try:
                            if not self._lift_servo.set_duty(duty):
                                print(f"The lift servo didn't reply to the duty {duty}.")
                        except Exception as e:
                            print(f"Failed to update the lift's duty: {e!r}")

            sample_ms = env.integer("lift_sample_ms", 50)

            def can_pause(pause_ms):
                # A pause holds up the drive's samples, and, once it has
                # ended, its stop. Before the lift starts, nothing turns
                # meanwhile. The crawl down and the re-seat run within a turn
                # or so of the end sensor, which the travel can't tell. The
                # drive's speed is the fastest it drives.
                if finish_event.is_set():
                    return False
                if not lift_started:
                    return True
                return drive.revs is None and tracker.can_pause(speed, pause_ms + sample_ms)

            tasks = []
            wait_task = None
            try:
                # STOP ends the drive from here on, at once if it has come
                # already, and the drive decides whether asyncio may block.
                self._control.drive(lambda: finish(cover_state.STOP_COMMAND), can_pause)
                # The end sensor is watched from here on: a press that comes
                # later, even before the lift starts, ends the drive. So does
                # one active already, and the lift doesn't start.
                if self._end_sensors.watch(sensor):
                    finish(cover_state.REACHED)
                tracker.begin(drive.up, from_end and not drive.crawls)
                margin = env.number("travel_margin_revs", 2)
                slow_speed = self._approach_speed(drive.up)
                if drive.crawls:
                    speed = slow_speed
                else:
                    # The profile drives from a soft start up to speed and
                    # back down to the approach speed over the last
                    # approach_revs. speed stays the fastest it can drive,
                    # which can_pause goes by.
                    profile = speed_profile.Profile(
                        speed, slow_speed, approach_revs,
                        soft_start_revs=env.number("soft_start_revs", speed_profile.SOFT_START_REVS),
                        soft_start_speed=env.integer("soft_start_speed", speed_profile.SOFT_START_SPEED),
                        min_speed=env.integer("lift_min_speed", speed_profile.MIN_SPEED))
                if drive.revs is not None:
                    timeout = env.integer("crawl_timeout", 15)
                elif drive.crawls or math.isnan(tracker.travel):
                    # All the way at approach speed takes longer.
                    timeout = env.integer("approach_timeout", 120)
                print(f"Travel {tracker.travel} of {tracker.full_or_estimate} revolutions, driving at {speed}.")

                tasks.append(asyncio.create_task(
                    watch_end_sensor(self._end_sensors,
                                     sensor,
                                     finish_event,
                                     sensor_reached)))

                tasks.append(asyncio.create_task(
                    track_travel(self._lift_servo,
                                 finish_event,
                                 tracker,
                                 sample_ms,
                                 handle_sample,
                                 lambda: finish(cover_state.STALLED))))

                wait_task = asyncio.create_task(
                    wait(timeout, lambda: finish(cover_state.TIMED_OUT)))

                if tilt_first and not await self.drive_tilt(50):
                    finish(cover_state.TIMED_OUT)
                elif finish_event.is_set():
                    # It ended during the tilt, at the travel limit or with
                    # its end sensor active, so the lift doesn't start.
                    print("The drive ended before the lift started.")
                else:
                    self._save_state(self._position)
                    lift_started = True
                    print("Starting lift servo...")
                    self._lift_servo.enable_torque = True

                    start_duty = speed if profile is None else profile.duty(0.0, tracker.remaining)
                    print(f"Starting the lift at duty {start_duty}...")

                    if not await self._lift_servo.start(start_duty):
                        print("Failed to start lift servo.")
                        finish(cover_state.START_FAILED)
                    else:
                        print("Lift servo started, waiting for completion...")
                        if profile is not None:
                            # The profile's updates run from the start's duty,
                            # which the servo confirmed.
                            updates = speed_profile.Updates(
                                start_duty, now_ms(),
                                update_ms=env.integer("speed_update_ms", speed_profile.UPDATE_MS))

                # Every task ends once finish_event is set.
                await asyncio.gather(*tasks)
            except Exception as e:
                print(f"Exception occurred while driving: {e!r}")
                finish(cover_state.ERROR)
            finally:
                finish_event.set()
                if wait_task is not None:
                    wait_task.cancel()
                # However the move ends, stop the lift.
                stopped = await self._stop_lift()
                self._control.drive_ended()

            if not stopped:
                return cover_state.STOP_FAILED, lift_started
            if lift_started:
                self._feed_rest_angle()
                print(f"Moved {tracker.moved} revolutions: travel {tracker.travel}.")
            return result, lift_started

        def _approach_speed(self, up):
            """The approach speed, as the duty for a drive up (negative) or
            down."""
            if up:
                return -env.integer("open_approach_speed", 500)
            return env.integer("close_approach_speed", 300)

        async def _stop_lift(self):
            """Stop the lift with its stop sequence, which leaves it braking.
            Returns whether it stopped: its duty 0 confirmed. If not, it's
            left limp if it could be, the travel is lost, and stop_failed is
            set."""
            # An exception from here would leave the cover state opening or
            # closing, and MQTT paused.
            try:
                ended = await self._lift_servo.stop()
            except Exception as e:
                # Run the sequence again from duty 0, without the wait, so
                # the lift still ends braking, or limp.
                print(f"Exception occurred while stopping: {e!r}")
                try:
                    ended = servo_health.stop_lift(self._reader)
                except Exception as e:
                    print(f"Exception occurred while stopping again: {e!r}")
                    ended = lift_stop.STOP_UNCONFIRMED
            if lift_stop.stopped(ended):
                return True
            self._tracker.lose()
            self.stop_failed.set()
            return False

        def _feed_rest_angle(self):
            """Feed the travel tracker the lift's servo angle once it has
            stopped, as it coasts a little past the last sample. The travel
            then ends, and re-anchors, where the lift rests."""
            try:
                sample = self._lift_servo.angle_and_speed
            except Exception as e:
                print(f"Failed to read the lift's servo angle at rest: {e!r}")
                return
            if sample is not None:
                self._tracker.feed(sample[0])

        async def close(self):
            await self._one_move("CLOSE", self._close)

        async def _one_move(self, command, move):
            """Run an open or close, move, as one move, unless one is under
            way already: a second would drive the lift concurrently. So it's
            ignored, and STOP must come first."""
            if not self._control.begin():
                print(f"Ignoring {command}: an open or close is under way. Send STOP first.")
                return
            try:
                await self._as_move(move())
            finally:
                self._control.end()

        async def _close(self):
            print("Closing blinds...")
            self._cancel_tilt_move()
            state = self._position
            self._position = Blinds.POSITION_MOVING_DOWN
            self.report_state()
            result = await self.operate(Blinds.POSITION_DOWN,                       # The end it moves toward, whose end sensor stops it
                                self._speed,                              # Drive in negative direction
                                env.number("close_approach_revs",          # Ramp down to the approach speed over the last this many revolutions
                                           speed_profile.APPROACH_REVS),
                                env.integer("close_timeout", 45),            # Timeout, with the travel known
                                state)                                   # The cover state it starts from
            # A close that didn't reach the end sensor gives up here. A tilt
            # that doesn't arrive at the end is a timeout, like any other.
            if result == cover_state.REACHED and not await self.drive_tilt(self._tilt):
                result = cover_state.TIMED_OUT
            self._position = cover_state.after_move(result, Blinds.POSITION_DOWN)
            self.report_state()
            print(f"Completed closing blinds: {result}.")

        async def open(self):
            await self._one_move("OPEN", self._open)

        async def _open(self):
            print("Opening blinds...")
            self._cancel_tilt_move()
            state = self._position
            self._position = Blinds.POSITION_MOVING_UP
            self.report_state()
            result = await self.operate(Blinds.POSITION_UP,                         # The end it moves toward, whose end sensor stops it
                                -self._speed,                               # Drive in positive direction
                                env.number("open_approach_revs",           # Ramp down to the approach speed over the last this many revolutions
                                           speed_profile.APPROACH_REVS),
                                env.integer("open_timeout", 45),              # Timeout, with the travel known
                                state)                                   # The cover state it starts from
            self._position = cover_state.after_move(result, Blinds.POSITION_UP)
            self.report_state()
            # Only an open that reached the end sensor counts.
            if self._position == Blinds.POSITION_UP:
                self._opened += 1
                self._on_opened(self)
            print(f"Completed opening blinds: {result}.")

        async def stop(self):
            # An open or close under way stops through its own stop sequence,
            # stores stopped with its travel, and reports it.
            if self._control.stop():
                print("Stopping the open or close under way...")
                return
            print("Stopping blinds...")
            stopped = await self._stop_lift()
            # Unknown if the stop wasn't confirmed: the lift may be driving.
            self._position = Blinds.POSITION_STOPPED if stopped else Blinds.POSITION_UNKNOWN
            self._save_state(self._position)
            self.report_state()
            print("Blinds stopped.")

        def report_state(self):
            self._update_callback(self)
