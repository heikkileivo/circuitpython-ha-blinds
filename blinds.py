from packet import Address
from time import sleep
import microcontroller
import asyncio, digitalio
import math
import os

class ServoException(BaseException):
    def __init__(self, message):
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
        return self._reader.read_1_byte(self._id, Address.MOVING) == 1

    def read_value(self, address, previous_value):
        value = self._reader.read_2_bytes(self._id, address)
        diff = None
        if value:
            if previous_value:
                diff = previous_value - value
        return (value, diff)

    async def start(self, speed):
        print(f"Starting servo...")
        try:
            self.speed = speed
        except ServoCommFailure as e:
            print(f"Starting servo failed: {e}")
            return False

        while self.is_moving == False:
            await asyncio.sleep(0)
        print(f"Servo started moving.")
        return True

    async def stop(self):
        print(f"Stopping servo...")
        try:
            self.speed = 0
        except ServoCommFailure as e:
            print(f"Stopping servo failed: {e}")
            return False
        while self.is_moving:
            await asyncio.sleep(0)
        print(f"Servo stopped moving.")
        return True

    def __repr__(self):
        repr = "Motor {_id}: Pos: {_pos} V: {_v} I: {_i} L: {_l} U: {_u} T: {_t}"
        return repr.format(**self.__dict__)

def get_pin_value(pin):
    with digitalio.DigitalInOut(pin) as input:
        input.direction = digitalio.Direction.INPUT
        input.pull = digitalio.Pull.DOWN
        return input.value

async def poll_pin(pin, finish_event, callback):
    print("Starting poller for %s..." % pin)
    with digitalio.DigitalInOut(pin) as input:
        input.direction = digitalio.Direction.INPUT
        input.pull = digitalio.Pull.DOWN

        previous_value = False
        while True:
            new_value = input.value
            if previous_value==False and new_value == True:
                callback()

            previous_value = new_value
            if finish_event.is_set():
                break
            await asyncio.sleep(0)
    print("Completed polling for pin %s." % pin)

async def count_revolutions(servo, finish_event, comparer, callback):
    old_position = 0
    revolutions = 0
    print("Counting revolutions for servo %s..." % servo.id)
    is_rotating = False
    stop_counter = 10
    while True:
        new_position, _ = servo.position
        if new_position:
            if is_rotating:
                if old_position == new_position:
                    stop_counter -= 1
                    if stop_counter == 0:
                        print("Servo has stopped.")
                        finish_event.set()
                        break
            else:
                if is_rotating == False:
                    if old_position and old_position != new_position:
                        print("Servo started rotating.")
                        is_rotating = True

            if comparer(old_position, new_position):
                revolutions += 1
                callback(revolutions)
            old_position = new_position
        if finish_event.is_set():
            break

        await asyncio.sleep(0)
    print("Completed counting revolutions for servo %s." % servo.id)

async def wait(finish_event, timeout):
    print("Waiting for %s seconds..." % timeout)
    try:
        await asyncio.sleep(timeout)
        print("Timeout reached.")
        finish_event.set()
    except asyncio.CancelledError:
        print("Waiting was cancelled.")
    print("Completed waiting.")




class Blinds:
        POSITION_UNKNOWN = -1
        POSITION_STOPPED = 0
        POSITION_DOWN = 1
        POSITION_UP = 2
        POSITION_MOVING_UP = 3
        POSITION_MOVING_DOWN = 4

        def __init__(self, reader, update_callback, on_opening, on_opened, on_closing, on_closed, up_pin, down_pin, tilt_scale):
            self._position = Blinds.POSITION_DOWN
            self._reader = reader
            self._update_callback = update_callback
            self._on_opening = on_opening
            self._on_opened = on_opened
            self._on_closing = on_closing
            self._on_closed = on_closed
            self._lift_servo = Servo(1, reader)
            self._tilt_servo = Servo(2, reader, scale=tilt_scale)
            self._down_pin = down_pin
            self._up_pin = up_pin
            h = os.getenv("window_height", 1800.0)
            d = os.getenv("spindle_diameter", 20.0)
            self._max_revolutions = int( h / (d * 3.14159)) # ToDo: add settings
            self._tilt_scale = tilt_scale
            self._tilt = 50
            self._speed = os.getenv("default_speed", 800)
            self._servo_position = 0
            self._revolutions = 0
            self._opened = 0


        @property
        def tilt(self):
            return self._tilt

        @property
        def is_moving(self):
            return _lift_servo.is_moving()

        @tilt.setter
        def tilt(self, value):
            self._tilt = value
            if self._position == Blinds.POSITION_DOWN:
                asyncio.create_task(self.drive_tilt(self._tilt))

        @property
        def opened_count(self):
            return self._opened

        async def drive_tilt(self, value):
            print("Driving tilt servo to %s..." % value)
            self._tilt_servo.enable_torque = True
            self._tilt_servo.position = value
            while self._tilt_servo.is_moving:
                await asyncio.sleep(0)
            self._tilt_servo.enable_torque = False

        async def report_state(self):
            pass

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
        def position(self):
            return self._position

        @position.setter
        def position(self, value):
            self._position = value

        async def operate(self, stop_pin, wrong_pin, speed, max_revs, slow_speed, slow_revs, count_lambda, timeout):
            if get_pin_value(stop_pin):
                print("Already at stopped state.")
                return
            finish_event = asyncio.Event()

            def pin_reached():
                print("Stop pin reached, stopping...")
                finish_event.set()

            revs = max_revs
            fast_revs = revs - slow_revs
            def handle_count(count):
                print("Revolution count = %s" % count)
                if count >= revs:
                    print("Max count reached, stopping...")
                    finish_event.set()
                elif count == fast_revs:
                    print("Slowing down...")
                    try:
                        self._lift_servo.speed = slow_speed
                    except ServoCommFailure:
                        print(f"Failed to slow servo down: {e}")


            def wrong_pin_reached():
                print("Wrong pin reached, spooling in wrong direction...")
                try:
                    self._lift_servo.speed = 0
                except ServoCommFailure as e:
                    print("Failed to stop lift servo for reversing: {0}")

                try:
                    self._lift_servo.speed = -speed
                except ServoCommFailure as e:
                    print("Failed to start lift servo for reversing: {0}")

                revs = max_revs # Reset revolution counter

            try:
                tasks = []
                tasks.append(asyncio.create_task(
                    poll_pin(stop_pin,
                                finish_event,
                                pin_reached)))

                if wrong_pin:
                    tasks.append(asyncio.create_task(
                        poll_pin(wrong_pin,
                                    finish_event,
                                    wrong_pin_reached)))

                tasks.append(asyncio.create_task(
                    count_revolutions(self._lift_servo,
                                        finish_event,
                                        count_lambda,
                                        handle_count)))

                wait_task = asyncio.create_task(
                    wait(finish_event, timeout))


                await self.drive_tilt(50)

                # Set correct state (driving up/down)
                # Report state

                print("Starting lift servo...")
                self._lift_servo.enable_torque = True

                print(f"Ramping servo speed up to {speed}...")

                if await self._lift_servo.start(speed) == False:
                    print("Failed to start lift servo.")
                    return

                print("Lift servo started, waiting for completion...")
                await asyncio.gather(*tasks)

                print("Driving completed successfully.")
                if await self._lift_servo.stop() == False:
                    print(f"Failed to stop servo.")
                self._lift_servo.enable_torque = False
            except Exception as e:
                print("Exception occured while driving: %s" % repr(e))
            finally:
                finish_event.set()
                wait_task.cancel()

                # Report state

        async def close(self):
            print("Closing blinds...")
            self._on_closing(self)
            self._position = Blinds.POSITION_MOVING_DOWN
            self.report_state()
            await self.operate(self._down_pin,                              # Stop when down pin reached
                                None,                               # Reverse if up pin reached
                                self._speed,                               # Drive in negative direction
                                self._max_revolutions,                      # Stop when max reached
                                os.getenv("close_approach_speed", 300),    # Approach speed
                                os.getenv("close_approach_revs", 10),       # Approach revolutions
                                lambda old, new: new > old,                 # Count revolution when position flips from big to small value
                                os.getenv("close_timeout", 45))                                         # Timeout
            await self.drive_tilt(self._tilt)
            self._position = Blinds.POSITION_DOWN
            self.report_state()
            self._on_closed(self)
            print("Completed closing blinds.")

        async def open(self):
            print("Opening blinds...")
            self._on_opening(self)
            self._position = Blinds.POSITION_MOVING_UP
            self.report_state()
            await self.operate(self._up_pin,                                # Stop if up pin reached
                                None,                                       # Ignore up pin
                                -self._speed,                                # Drive in positive direction
                                self._max_revolutions,                      # Stop when max reached
                                -os.getenv("open_approach_speed", 300),      # Approach speed
                                os.getenv("open_approach_revs", 7),         # Approach revolutions
                                lambda old, new: new < old,                 # Count revolution when position flips from small to big value
                                os.getenv("open_timeout", 45))              # Timeout
            self._position = Blinds.POSITION_UP
            self.report_state()
            self._on_opened(self)
            self._opened += 1
            print("Completed opening blinds.")

        def report_state(self):
            self._update_callback(self)

        def store_position(self):
            print("Storing known position to nvm...")
            microcontroller.nvm[0:2] = bytes([1, self._position])

        def get_stored_position(self):
            data = list(microcontroller.nvm[0:2])
            if data[0] == 0:
                return None
            else:
                return data[1]

        def find_out_current_state(self):
            print("Finding out current state...")
            with digitalio.DigitalInOut(self._down_pin) as input:
                input.direction = digitalio.Direction.INPUT
                input.pull = digitalio.Pull.DOWN
                if input.value:
                    print("According to sensor, current position is down.")
                    self._position = Blinds.POSITION_DOWN
                    return

            with digitalio.DigitalInOut(self._up_pin) as input:
                input.direction = digitalio.Direction.INPUT
                input.pull = digitalio.Pull.DOWN
                if input.value:
                    print("According to sensor, current position is up.")
                    self._position = Blinds.POSITION_UP
                    return

            print("Position is unknown...")
            self._direction_unknown = True
            self.target = Blinds.POSITION_DOWN
            if False:
                stored_position = self.get_stored_position()
                if stored_position:
                    print("Previous stored position = %s, moving there." % stored_position)
                    self.target = stored_position
                else:
                    print("No stored position, moving down.")
                    self.target = Blinds.POSITION_DOWN

            self.report_state()
