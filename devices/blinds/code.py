import time, gc, os, sys, json, traceback
from time import sleep
import microcontroller
from watchdog import WatchDogMode
import board, busio
import tinys3
import supervisor
import wifi
import asyncio
import keypad
import time
from blinds import Blinds, ServoException
from end_sensors import EndSensors
from packet import Reader
from components import blinds_discovery
import servo_bus
import servo_health
import status_led
import reset_cause
import recovery
from blink import blink, Color, pixel
from mqtt import Mqtt
from mqtt_pace import Pace
import storage

# Right after the imports, so the one restart after a watchdog reset comes
# early. After a real watchdog reset, boot.py has stopped the servos. The
# cause stays pending until a connect publishes it, once per boot however
# often main() runs.
pending_reset_cause = reset_cause.at_boot()

try:
    storage.disable_usb_drive()
except Exception as e:
    print(f"Failed to disable usb drive: {e}")

# The MQTT service task sleeps this long between loop() calls. loop() blocks
# for its timeout to twice that (0.25-0.5 s by default), so while the blind is
# idle a call starts about every 0.5 s.
MQTT_SERVICE_SLEEP_S = 0.25
# While the blind opens or closes, loop() runs at most this often, in ms, so
# the end sensor and stall checks lose little time (#55).
MQTT_MOVING_LOOP_MS = os.getenv("mqtt_moving_loop_ms", 1000)
# Meanwhile the task checks this often whether loop() is due, as it may block
# only right after some of the lift's samples: about as often as they come,
# by default.
MQTT_MOVING_SLEEP_S = 0.05

# The watchdog's timeout. Every blocking step must fit inside it, the Wi-Fi
# scan and connect at boot, which block back to back, included.
WATCHDOG_TIMEOUT_S = 16
# One Wi-Fi connect attempt gives up after this long.
WIFI_CONNECT_TIMEOUT_S = 8
# The watchdog is fed, and the escalation checked, this often.
WATCHDOG_FEED_S = 1
# The restart loop runs main() again this long after it fails.
RESTART_LOOP_DELAY_S = 10
# The escalation window, in seconds: the blind restarts once its liveness
# echo has been missing this long. A run of main() that lasts longer resets
# the restart loop's count.
ESCALATION_S = os.getenv("mqtt_escalation_s", recovery.WINDOW_MS // 1000)

# After a move, the supply gets this long to recover before the idle read.
SERVO_SETTLE_S = 2
# While the blind is idle, the servos' health is read and published this
# often, so the idle voltage and temperature stay current.
SERVO_IDLE_READ_S = 600

# keypad scans the end sensors this often. At full speed the blind crosses
# the lower zone of Middle's up end sensor, about 0.3 revolutions of the lift,
# in about 0.3 s (#54).
END_SENSOR_SCAN_S = 0.01
# Whether keypad's Keys.reset() reports the pressed keys, as it does since
# CircuitPython 9.2.1. Before, it reports the released ones.
KEYS_RESET_REPORTS_PRESSED = sys.implementation.version >= (9, 2, 1)

# The cover state HA is told for each of the blind's Blinds.POSITION_* values.
COVER_STATES = {Blinds.POSITION_UNKNOWN: "unknown",
                Blinds.POSITION_MOVING_DOWN: "closing",
                Blinds.POSITION_MOVING_UP: "opening",
                Blinds.POSITION_DOWN: "closed",
                Blinds.POSITION_UP: "open",
                Blinds.POSITION_STOPPED: "stopped"}


def now_ms():
    """The time in ms for the recovery decisions. monotonic() loses precision
    within hours of uptime; monotonic_ns() doesn't."""
    return time.monotonic_ns() // 1_000_000


def arm_watchdog():
    # A later run of main() finds it armed already, and only feeds it.
    if recovery.arm_watchdog(microcontroller.watchdog, WATCHDOG_TIMEOUT_S, WatchDogMode.RESET):
        print(f"Watchdog enabled with {WATCHDOG_TIMEOUT_S}s timeout.")


def unknown_failure_code(e):
    """The code in a Wi-Fi connect's "Unknown failure 205" error, or None.
    Not every error's errno is a string: an OSError's is an int, and most
    exceptions have none."""
    errno = getattr(e, "errno", None)
    if not isinstance(errno, str) or "Unknown failure" not in errno:
        return None
    try:
        return int(errno[errno.rfind(" "):])
    except ValueError:
        return None


async def connect_wifi():
    if wifi.radio.connected:
        print(f"Already connected to wifi.")
        return
    while True:
        print("Connecting Wifi...")
        pixel[0] = Color.BLUE
        ssid = os.getenv("CIRCUITPY_WIFI_SSID")
        pwd = os.getenv("CIRCUITPY_WIFI_PASSWORD")
        try:
            for network in wifi.radio.start_scanning_networks():
                print(f"\t{network.ssid}\t\tRSSI: {network.rssi:d}\tChannel: {network.channel:d}")

            wifi.radio.stop_scanning_networks()
            wifi.radio.connect(ssid, pwd, timeout=WIFI_CONNECT_TIMEOUT_S)
            print("Connected to wifi.")
            pixel[0] = Color.BLACK
            await blink(Color.GREEN, 3)
            return
        except Exception as e:
            print(f"Connecting to wifi {ssid} failed: {e!r}")
            code = unknown_failure_code(e)
            if code is not None:
                await blink(Color.ORANGE, code)
            else:
                await blink(Color.RED, 3)


def publish_if_connected(mqtt,topic, value, retain=False):
    """
    Publish while MQTT is connected, and skip it otherwise. A failed publish
    is only logged: Mqtt flags it, and the supervisor rebuilds the client.
    """
    if not mqtt.on_connected.is_set():
        return
    try:
        mqtt.publish(topic, str(value), retain=retain)
    except Exception as e:
        print(f"Failed to publish {topic}: {e!r}")


async def publish_uptime(mqtt, disc):
    """
    Publish uptime and the reconnect count every 10 s, while moving too.
    Something then goes out well within keep_alive, so loop() never reaches
    its blocking ping(). uptime_seconds is the liveness echo, so it's never
    retained.
    """
    start_time = time.time()
    while True:
        uptime = int(time.time() - start_time)
        print(f"Publishing uptime {uptime} s...")
        publish_if_connected(mqtt,disc.topic("uptime_seconds", "state"), uptime)
        publish_if_connected(mqtt,disc.topic("reconnects", "state"), mqtt.reconnects, retain=True)
        await asyncio.sleep(10)


async def service_mqtt(mqtt, blinds, socket_timeout):
    """
    Handle incoming MQTT messages, while the blind opens or closes too, so
    STOP works mid-travel. loop() takes the least timeout it allows,
    socket_timeout, and blocks the asyncio loop for up to twice that. So
    while the blind moves, it runs at most once every MQTT_MOVING_LOOP_MS,
    and only when the lift's travel can take the pause: its turns still
    counted, and its end sensor not due meanwhile.
    """
    pause_ms = int(2 * socket_timeout * 1000)
    pace = Pace(MQTT_MOVING_LOOP_MS)
    while True:
        moving = blinds.is_moving
        t_ms = now_ms()
        if pace.due(t_ms, moving, blinds.may_pause(pause_ms)):
            pace.looped(t_ms)
            await mqtt.loop(socket_timeout)
        await asyncio.sleep(MQTT_MOVING_SLEEP_S if moving else MQTT_SERVICE_SLEEP_S)


async def escalate_and_feed_watchdog(mqtt, blinds, escalation, boot_connect_done):
    """
    Feed the watchdog, and restart once the liveness echo has been missing
    for the escalation window, as soon as the blind isn't in a move.

    It starts before the Wi-Fi connect at boot, so there the watchdog only
    resets a frozen loop, and the escalation ends a connect that never
    succeeds. From then on the watchdog is fed on every pass, connected or
    not, so a broker outage ends in the escalation. A Wi-Fi loss while idle
    ends in a watchdog reset: the radio gives up reconnecting by itself, and
    connect_wifi() only runs at boot. Neither restarts the blind in a move,
    a tilt-only one included, which would leave a servo driving.
    """
    last_echo = mqtt.last_echo
    wifi_lost = False
    while True:
        t_ms = now_ms()
        in_move = blinds.in_move
        if mqtt.last_echo != last_echo:
            last_echo = mqtt.last_echo
            escalation.echo(t_ms)
        if escalation.due(t_ms, in_move):
            print(f"MQTT escalation: no liveness echo for {ESCALATION_S} s, restarting.")
            reset_cause.restart(reset_cause.MQTT_ESCALATION)
        if in_move or wifi.radio.connected or not boot_connect_done.is_set():
            wifi_lost = False
            microcontroller.watchdog.feed()
        elif not wifi_lost:
            wifi_lost = True
            print("Wi-Fi lost while idle, leaving the watchdog to reset.")
        await asyncio.sleep(WATCHDOG_FEED_S)


async def show_status(blinds, mqtt, current_health):
    """
    Show the blind's status on the LED, as status_led.decision() works it
    out: dim and solid while all is well, a slow blink on an attention
    condition. Once the main loop runs, this is the pixel's only writer.
    current_health returns the servo health the blind last worked out.
    """
    shown = None
    last = None
    lit = False
    while True:
        decided = status_led.decision(
            current_health(), mqtt.on_connected.is_set(), blinds.position, blinds.in_move)
        color, mode, brightness = decided
        # A new condition shows at once; the same blink alternates.
        lit = not lit if decided == last and mode == status_led.BLINK else True
        last = decided
        wanted = (color if lit else Color.BLACK, brightness)
        if wanted != shown:
            shown = wanted
            pixel.brightness = brightness
            pixel[0] = wanted[0]
        await asyncio.sleep(status_led.BLINK_S)


async def read_servos_while_idle(blinds, publish_servo_health):
    """Read and publish the servos' health every SERVO_IDLE_READ_S while
    the blind is idle. A move skips it, as the move's own idle read follows."""
    while True:
        await asyncio.sleep(SERVO_IDLE_READ_S)
        if not blinds.in_move:
            publish_servo_health()


def output_mem():
    # Show available memory
    print("Memory Info - gc.mem_free()")
    print("---------------------------")
    print(f"{gc.mem_free()} Bytes\n")

    flash = os.statvfs('/')
    flash_size = flash[0] * flash[2]
    flash_free = flash[0] * flash[3]
    # Show flash size
    print("Flash - os.statvfs('/')")
    print("---------------------------")
    print(f"Size: {flash_size} Bytes\nFree: {flash_free} Bytes\n")


async def main():
    arm_watchdog()
    uart = busio.UART(board.TX,
                            board.RX,
                            baudrate=servo_bus.BAUD_RATE,
                            receiver_buffer_size=32)
    reader = Reader(uart)
    keys = None
    try:
        # The end sensors, up then down: active high, with pull-downs.
        keys = keypad.Keys((board.D1, board.D2), value_when_pressed=True,
                           interval=END_SENSOR_SCAN_S)
        await run_blind(reader, EndSensors(keys, KEYS_RESET_REPORTS_PRESSED))
    finally:
        # Only a failure ends a run, and its move no longer runs: stop the
        # servos, which leaves the lift braking, or limp if its duty 0 isn't
        # confirmed. Then free the UART and the end sensors' pins for the
        # next run.
        try:
            servo_health.stop_servos(reader)
        except Exception as e:
            print(f"Failed to stop the servos: {e!r}")
        uart.deinit()
        if keys is not None:
            keys.deinit()


async def run_blind(reader, end_sensors):
    # A controller reset leaves the servos doing whatever they were doing.
    # On a hard reset boot.py has stopped them already. Stop them again,
    # which also covers a soft reload, and read their health, which is
    # published once connected.
    reader.flush_buffer()

    # The latest servo_health message and servo_min_voltage, which every
    # connect republishes. Until the first move there's only the boot read.
    servo_states = {"servo_health": None, "servo_min_voltage": None}
    # The latest servo health, ok, no_reply or error, for the status LED.
    health = None

    def update_servo_health(*figures):
        nonlocal health
        message = servo_health.health_message(*figures)
        health = message["health"]
        servo_states["servo_health"] = json.dumps(message, separators=(",", ":"))

    update_servo_health(*servo_health.boot_reinit(reader))
    print(f"Servo health: {servo_states['servo_health']}")

    output_mem()

    # Turn on the power to the NeoPixel
    tinys3.set_pixel_power(True)
    print("Lift servo:")
    reader.output_settings(servo_bus.LIFT_ID)

    device_name = os.getenv("device_name", "Blinds")
    tilt_scale = os.getenv("tilt_scale", 10.0)

    disc = blinds_discovery(device_name)

    def state_messages(blinds):
        """The cover, tilt and speed state as (topic, value) pairs. It's
        published retained, so HA keeps it across its own restarts."""
        return ((disc.topic("cover", "state"), COVER_STATES[blinds.position]),
                (disc.topic("tilt", "state"), blinds.tilt),
                (disc.topic("speed", "state"), blinds.speed))

    def on_connect(client):
        # Deferred on-connect work, which the supervisor runs after connect()
        # returns and after Mqtt publishes "online": discovery first, then
        # every command topic in one SUBSCRIBE, then the state. Republishing
        # the state on every connect also gets the state worked out at boot
        # to HA. The reset cause goes once per boot.
        global pending_reset_cause
        print("Publishing discovery payload...")
        client.publish(disc.discovery_topic, disc.discovery_payload_json(), retain=True)
        topics = disc.command_topics()
        print(f"Subscribing to {topics}...")
        client.subscribe([(topic, 0) for topic in topics])
        for topic, value in state_messages(blinds):
            client.publish(topic, str(value), retain=True)
        for entity, value in servo_states.items():
            if value is not None:
                client.publish(disc.topic(entity, "state"), str(value), retain=True)
        if pending_reset_cause is not None:
            client.publish(disc.topic("reset_cause", "state"), pending_reset_cause, retain=True)
            pending_reset_cause = None

    def on_message(client, topic, message):
        if topic == disc.topic("cover", "set"):
            try:
                if message == "OPEN":
                    asyncio.create_task(blinds.open())
                elif message == "CLOSE":
                    asyncio.create_task(blinds.close())
                elif message == "STOP":
                    asyncio.create_task(blinds.stop())
            except Exception as e:
                print(f"Failed to handle cover command: {e!r}")
        elif topic == disc.topic("speed", "set"):
            try:
                speed = int(float(message))
                blinds.speed = speed
            except Exception as e:
                print(f"Failed to parse speed: {e!r}")
        elif topic == disc.topic("tilt", "set"):
            try:
                tilt = int(float(message))
                blinds.tilt = tilt
            except Exception as e:
                print(f"Failed to parse tilt: {e!r}")

    # settings.toml takes no floats, so a fractional value must be quoted.
    socket_timeout = float(os.getenv("mqtt_socket_timeout", 0.25))
    mqtt = Mqtt(on_connect_callback=on_connect,
                on_message_callback=on_message,
                client_id=disc.device_id,
                socket_timeout=socket_timeout,
                recv_timeout=3,
                # One attempt: MiniMQTT's retries sleep past the watchdog.
                connect_retries=1,
                # The blind's own uptime, which publish_uptime() sends.
                echo_topic=disc.topic("uptime_seconds", "state"),
                echo_timeout=float(os.getenv("mqtt_echo_timeout", 45)),
                paused=lambda: blinds.is_moving,
                availability_topic=disc.availability_topic)

    def report_state(blinds):
        try:
            print(f"Reporting state as {COVER_STATES[blinds.position]}")
            for topic, value in state_messages(blinds):
                publish_if_connected(mqtt, topic, value, retain=True)
        except Exception as e:
            print(f"Failed to post mqtt status: {e!r}")

    def on_opened(blinds):
        publish_if_connected(mqtt, disc.topic("opened_count", "state"), blinds.opened_count, retain=True)

    # The lift and tilt servos' figures from the last move.
    last_moves = (None, None)

    def publish_servo_health(lift_stop_confirmed=True):
        # Read both servos idle, and publish their health with the last
        # move's figures.
        lift_read, tilt_read = servo_health.idle_reads(reader, lift_stop_confirmed)
        update_servo_health(lift_read, tilt_read, *last_moves)
        publish_if_connected(mqtt, disc.topic("servo_health", "state"),
                             servo_states["servo_health"], retain=True)

    async def publish_servo_health_settled():
        # The idle read, once the supply has settled, gives the idle voltage
        # and the temperature at the move's end.
        await asyncio.sleep(SERVO_SETTLE_S)
        # A move that started meanwhile has its own idle read to come, with
        # its own figures.
        if not blinds.in_move:
            publish_servo_health()

    def on_moved(blinds):
        nonlocal last_moves
        last_moves = blinds.move_figures
        min_voltage = servo_health.servo_min_voltage(*last_moves)
        if min_voltage is not None:
            servo_states["servo_min_voltage"] = min_voltage
            publish_if_connected(mqtt, disc.topic("servo_min_voltage", "state"), min_voltage, retain=True)
        asyncio.create_task(publish_servo_health_settled())

    async def fail_on_unconfirmed_stop():
        # A lift stop that wasn't confirmed has left the lift limp, if it
        # could. Publish the servo health as an error, then fail main(): the
        # restart loop takes over, and its next run re-runs the boot re-init.
        await blinds.stop_failed.wait()
        publish_servo_health(lift_stop_confirmed=False)
        raise ServoException("The lift's stop wasn't confirmed.")

    # The blind works out its cover state at boot, which the first connect
    # publishes.
    blinds = Blinds(reader,
        report_state,
        on_opened,
        on_moved,
        end_sensors,
        tilt_scale)

    # From here on this task feeds the watchdog, through the Wi-Fi connect's
    # retries too.
    boot_connect_done = asyncio.Event()
    escalation = recovery.Escalation(now_ms(), ESCALATION_S * 1000)
    tasks = [asyncio.create_task(
        escalate_and_feed_watchdog(mqtt, blinds, escalation, boot_connect_done))]
    # A failed run's status LED may have left the pixel dim.
    pixel.brightness = status_led.FULL
    await blink(Color.BLUE, 3)
    await connect_wifi()
    boot_connect_done.set()

    # The supervisor owns connecting, and rebuilds the client when it drops.
    mqtt.start_supervisor()

    tasks.append(asyncio.create_task(service_mqtt(mqtt, blinds, socket_timeout)))
    tasks.append(asyncio.create_task(show_status(blinds, mqtt, lambda: health)))
    tasks.append(asyncio.create_task(publish_uptime(mqtt, disc)))
    tasks.append(asyncio.create_task(read_servos_while_idle(blinds, publish_servo_health)))
    tasks.append(asyncio.create_task(fail_on_unconfirmed_stop()))

    await asyncio.gather(*tasks)


# main() only ends by failing. The restart loop runs it again, until it fails
# restart_loop_max times in a row, each run shorter than the escalation
# window; then the blind restarts.
restart_loop = recovery.RestartLoop(os.getenv("restart_loop_max", recovery.MAX_FAILURES),
                                    ESCALATION_S * 1000)
while True:
    started_ms = now_ms()
    try:
        asyncio.run(main())
    except Exception as e:
        # With its traceback: repr(e) alone didn't say where it failed (#103).
        print(f"main() failed: {e!r}")
        traceback.print_exception(e)
    if restart_loop.failed(started_ms, now_ms()):
        print("main() keeps failing, restarting.")
        reset_cause.restart(reset_cause.RESTART_LOOP)
    # asyncio.run() leaves the failed run's tasks queued: drop them, so the
    # next run doesn't run them too.
    asyncio.new_event_loop()
    gc.collect()
    print(f"Running main() again in {RESTART_LOOP_DELAY_S} s...")
    # main() armed the watchdog first thing, and its next run feeds it.
    microcontroller.watchdog.feed()
    sleep(RESTART_LOOP_DELAY_S)
