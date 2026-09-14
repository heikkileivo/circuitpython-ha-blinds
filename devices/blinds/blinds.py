from end_sensors import UP as UP_SENSOR, DOWN as DOWN_SENSOR
from packet import Address
from servo_health import MoveFigures
from tilt import read_at_boot
import servo_health
import cover_state
import persist
import servo_wait
import stall
import travel
from time import monotonic_ns, sleep
import microcontroller
import asyncio
import math
import os

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


    async def ramp_speed_to(self, target, steps, interval):
        speed = self.speed

        diff = target - speed
        step = int(diff/steps)
        print(f"Ramping speed of {speed} to {target} by step of {step}...")
        for i in range(steps):
            speed += step

            print(f"Speed ramp {i} = {speed}...")

            try:
                self.speed = speed
            except ServoCommFailure as e:
                print(f"Failed set servo speed: {e}")

            await asyncio.sleep_ms(interval)

        print(f"Completed ramping speed.")

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
        if value < 0:
            value = abs(value) | (1<<10)
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

        deadline_ms = os.getenv("lift_start_deadline_ms", servo_wait.START_DEADLINE_MS)
        if not await servo_wait.until_moving(lambda: self.is_moving, now_ms, deadline_ms):
            print(f"Servo {self._id} didn't start moving within {deadline_ms} ms.")
            return False
        print(f"Servo started moving.")
        return True

    async def stop(self):
        """Stop the servo: duty 0, then torque off once it has stopped
        moving, or once lift_stop_deadline_ms has passed. Returns whether
        the stop is confirmed: duty 0 and torque off both read back."""
        print(f"Stopping servo...")
        duty_0 = self._write_duty_0()
        deadline_ms = os.getenv("lift_stop_deadline_ms", servo_wait.STOP_DEADLINE_MS)
        if not await servo_wait.until_still(lambda: self.is_moving, now_ms, deadline_ms):
            print(f"Servo {self._id} didn't stop moving within {deadline_ms} ms.")
            # It may have missed the first duty 0.
            duty_0 = self._write_duty_0()
        limp = self.torque_off()
        print(f"Servo {self._id} stopped: duty 0 confirmed {duty_0}, torque off confirmed {limp}.")
        return duty_0 and limp

    def _write_duty_0(self):
        """Write duty 0, and read it back. Returns whether it's confirmed."""
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

async def track_travel(servo, finish_event, tracker, on_sample, on_stall):
    """Sample the lift's servo angle and speed every lift_sample_ms, feed the
    angle to the travel tracker, then call on_sample. Stop the lift at once
    if it stalls, then call on_stall."""
    sample_s = os.getenv("lift_sample_ms", 50) / 1000
    detector = stall.StallDetector(
        window_ms=os.getenv("stall_window_ms", stall.WINDOW_MS),
        max_speed=os.getenv("stall_max_speed", stall.MAX_SPEED),
        max_angle_change=os.getenv("stall_max_angle_change", stall.MAX_ANGLE_CHANGE),
        grace_ms=os.getenv("stall_grace_ms", stall.GRACE_MS),
        dead_zone_window_ms=os.getenv("stall_dead_zone_window_ms", stall.DEAD_ZONE_WINDOW_MS))
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
            self._lift_servo = Servo(1, reader)
            self._tilt_servo = Servo(2, reader, scale=tilt_scale)
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
            h = os.getenv("window_height", 1800.0)
            d = os.getenv("spindle_diameter", 20.0)
            self._tracker = travel.at_boot(up_active, down_active, self._store.state,
                                           self._store.travel, self._store.full_travel,
                                           h / (d * math.pi))
            print(f"Travel at boot: {self._tracker.travel}, full travel {self._tracker.full_travel}.")
            self._tilt_scale = tilt_scale
            # code.py builds the blind after the boot re-init has stopped
            # the servos, and before the first connect publishes the tilt.
            self._tilt = read_at_boot(reader, tilt_scale)
            self._speed = os.getenv("default_speed", 800)
            self._servo_position = 0
            self._revolutions = 0
            self._opened = 0
            self._tilt_move = None      # The tilt-only move's task, if one ran


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
            deadline_ms = os.getenv("tilt_deadline_ms", servo_wait.TILT_DEADLINE_MS)
            arrived = await servo_wait.until_still(
                lambda: servo.is_moving, now_ms, deadline_ms,
                os.getenv("tilt_rise_ms", servo_wait.TILT_RISE_MS))
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

        async def operate(self, end, speed, slow_speed, approach_revs, timeout, from_end):
            """Drive the lift until its end sensor, and stop it. Returns how
            the move ended, one of cover_state's results: the first way to
            come, or STOP_FAILED if the lift's stop wasn't confirmed.

            It drives at speed, then at slow_speed, the approach speed,
            within approach_revs of the end by travel, or all the way with
            the travel unknown. It stops once the travel is
            travel_margin_revs past the end. from_end is whether the blind
            starts at rest at the other end: reaching the end sensor then
            learns the full travel.

            It stores the opening or closing just before the lift starts, so
            a reset mid-move boots as an interrupted move, and the state the
            move leaves toward end, UP or DOWN, with the travel, after the
            confirmed stop. A move whose lift never started, as its tilt
            didn't arrive or the move ended meanwhile, leaves the stored
            state as it was: the blind hasn't moved. Unless its end sensor
            went active meanwhile, which stores that end, as when it's
            active from the start."""
            # The end sensor toward end is watched from here on: a press that
            # comes later, even before the lift starts, ends the move.
            sensor = UP_SENSOR if end == Blinds.POSITION_UP else DOWN_SENSOR
            if self._end_sensors.watch(sensor):
                print("Already at stopped state.")
                self._tracker.anchor(end)
                self._save_state(end)
                return cover_state.REACHED
            finish_event = asyncio.Event()
            result = None

            def finish(how):
                nonlocal result
                if result is None:
                    print(f"Move ended: {how}.")
                    result = how
                finish_event.set()

            def sensor_reached():
                print("End sensor reached, stopping...")
                finish(cover_state.REACHED)

            tracker = self._tracker
            tracker.begin(end == Blinds.POSITION_UP, from_end)
            # settings.toml takes no floats, so a fractional value must be quoted.
            margin = float(os.getenv("travel_margin_revs", 2))
            if tracker.approach_due(approach_revs):
                speed = slow_speed
            if math.isnan(tracker.travel):
                # All the way at approach speed takes longer.
                timeout = os.getenv("approach_timeout", 120)
            print(f"Travel {tracker.travel} of {tracker.full_or_estimate} revolutions, driving at {speed}.")
            slowed = speed == slow_speed
            lift_started = False

            def handle_sample():
                nonlocal slowed
                if tracker.beyond_limit(margin):
                    print(f"Travel {tracker.travel} is past its bound, stopping...")
                    finish(cover_state.TRAVEL_LIMIT)
                elif lift_started and not slowed and tracker.approach_due(approach_revs):
                    slowed = True
                    print(f"Slowing down at travel {tracker.travel}...")
                    try:
                        self._lift_servo.speed = slow_speed
                    except ServoCommFailure as e:
                        print(f"Failed to slow servo down: {e}")

            tasks = []
            wait_task = None
            try:
                tasks.append(asyncio.create_task(
                    watch_end_sensor(self._end_sensors,
                                     sensor,
                                     finish_event,
                                     sensor_reached)))

                tasks.append(asyncio.create_task(
                    track_travel(self._lift_servo,
                                 finish_event,
                                 tracker,
                                 handle_sample,
                                 lambda: finish(cover_state.STALLED))))

                wait_task = asyncio.create_task(
                    wait(timeout, lambda: finish(cover_state.TIMED_OUT)))

                if not await self.drive_tilt(50):
                    finish(cover_state.TIMED_OUT)
                elif finish_event.is_set():
                    # It ended during the tilt, at the travel limit or with
                    # its end sensor active, so the lift doesn't start.
                    print("The move ended before the lift started.")
                else:
                    self._save_state(self._position)
                    lift_started = True
                    print("Starting lift servo...")
                    self._lift_servo.enable_torque = True

                    print(f"Ramping servo speed up to {speed}...")

                    if not await self._lift_servo.start(speed):
                        print("Failed to start lift servo.")
                        finish(cover_state.START_FAILED)
                    else:
                        print("Lift servo started, waiting for completion...")

                # Every task ends once finish_event is set.
                await asyncio.gather(*tasks)
            except Exception as e:
                print(f"Exception occurred while driving: {e!r}")
                finish(cover_state.ERROR)
            finally:
                finish_event.set()
                if wait_task is not None:
                    wait_task.cancel()

            # However the move ended, stop the lift. An exception from here
            # would leave the cover state opening or closing, and MQTT paused.
            try:
                stopped = await self._lift_servo.stop()
            except Exception as e:
                print(f"Exception occurred while stopping: {e!r}")
                stopped = False
            if not stopped:
                print("Failed to stop the lift servo.")
                self._tracker.lose()
                return cover_state.STOP_FAILED
            if lift_started:
                self._feed_rest_angle()
                if result == cover_state.REACHED:
                    tracker.reached()
            elif result == cover_state.REACHED:
                # The end sensor went active before the lift started: the
                # blind was at the end already.
                tracker.anchor(end)
            else:
                return result
            print(f"Moved {tracker.moved} revolutions: travel {tracker.travel}, full travel {tracker.full_travel}.")
            self._save_state(cover_state.after_move(result, end))
            return result

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
            await self._as_move(self._close())

        async def _close(self):
            print("Closing blinds...")
            self._cancel_tilt_move()
            from_end = self._position == Blinds.POSITION_UP
            self._position = Blinds.POSITION_MOVING_DOWN
            self.report_state()
            result = await self.operate(Blinds.POSITION_DOWN,                       # The end it moves toward, whose end sensor stops it
                                self._speed,                              # Drive in negative direction
                                os.getenv("close_approach_speed", 300),    # Approach speed
                                os.getenv("close_approach_revs", 10),       # Approach within this many revolutions of the end
                                os.getenv("close_timeout", 45),            # Timeout, with the travel known
                                from_end)                                   # Whether it starts at the other end
            # A close that didn't reach the end sensor gives up here. A tilt
            # that doesn't arrive at the end is a timeout, like any other.
            if result == cover_state.REACHED and not await self.drive_tilt(self._tilt):
                result = cover_state.TIMED_OUT
            self._position = cover_state.after_move(result, Blinds.POSITION_DOWN)
            self.report_state()
            print(f"Completed closing blinds: {result}.")

        async def open(self):
            await self._as_move(self._open())

        async def _open(self):
            print("Opening blinds...")
            self._cancel_tilt_move()
            from_end = self._position == Blinds.POSITION_DOWN
            self._position = Blinds.POSITION_MOVING_UP
            self.report_state()
            result = await self.operate(Blinds.POSITION_UP,                         # The end it moves toward, whose end sensor stops it
                                -self._speed,                               # Drive in positive direction
                                -os.getenv("open_approach_speed", 300),      # Approach speed
                                os.getenv("open_approach_revs", 7),         # Approach within this many revolutions of the end
                                os.getenv("open_timeout", 45),              # Timeout, with the travel known
                                from_end)                                   # Whether it starts at the other end
            self._position = cover_state.after_move(result, Blinds.POSITION_UP)
            self.report_state()
            # Only an open that reached the end sensor counts.
            if self._position == Blinds.POSITION_UP:
                self._opened += 1
                self._on_opened(self)
            print(f"Completed opening blinds: {result}.")

        async def stop(self):
            print("Stopping blinds...")
            try:
                stopped = await self._lift_servo.stop()
            except Exception as e:
                print(f"Failed to stop lift servo: {e!r}")
                stopped = False
            # Unknown if the stop wasn't confirmed: the lift may be driving.
            self._position = Blinds.POSITION_STOPPED if stopped else Blinds.POSITION_UNKNOWN
            if not stopped:
                self._tracker.lose()
            self._save_state(self._position)
            self.report_state()
            print("Blinds stopped.")

        def report_state(self):
            self._update_callback(self)
