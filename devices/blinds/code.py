import time, gc, os, sys
from time import sleep
import microcontroller
from watchdog import WatchDogMode
import board, digitalio, busio
import tinys3
import supervisor
import wifi
import asyncio
import keypad
import time
from adafruit_debouncer import Debouncer
from blinds import Blinds
from packet import Reader
from components import blinds_discovery
from blink import blink, Color, pixel
from mqtt import Mqtt
import storage

try:
    storage.disable_usb_drive()
except Exception as e:
    print(f"Failed to disable usb drive: {e}")

# The MQTT service task sleeps this long between loop() calls. loop() blocks
# for its timeout to twice that (0.25-0.5 s by default), so while the blind is
# idle a call starts about every 0.5 s.
MQTT_SERVICE_SLEEP_S = 0.25

# The cover state HA is told for each of the blind's Blinds.POSITION_* values.
COVER_STATES = {Blinds.POSITION_UNKNOWN: "unknown",
                Blinds.POSITION_MOVING_DOWN: "closing",
                Blinds.POSITION_MOVING_UP: "opening",
                Blinds.POSITION_DOWN: "closed",
                Blinds.POSITION_UP: "open",
                Blinds.POSITION_STOPPED: "stopped"}


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
            wifi.radio.connect(ssid, pwd)
            print("Connected to wifi.")
            pixel[0] = Color.BLACK
            await blink(Color.GREEN, 3)
            return
        except Exception as e:
            print(f"Connecting to wifi {ssid} failed: {e}")
            if "Unknown failure" in e.errno:
                code = int(e.errno[e.errno.rfind(" "):])
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


async def service_mqtt(mqtt, blinds, loop_timeout):
    """
    Handle incoming MQTT messages while the blind is idle, and feed the
    watchdog.

    loop() blocks the asyncio loop, so it isn't called while the blind moves.
    The watchdog is fed on every pass, connected or not, so a broker outage
    doesn't end in a reset. A Wi-Fi loss while idle does, 16 s later: the
    radio gives up reconnecting by itself, and connect_wifi() at boot doesn't.
    """
    wifi_lost = False
    while True:
        if not blinds.is_moving:
            await mqtt.loop(loop_timeout)
        if blinds.is_moving or wifi.radio.connected:
            wifi_lost = False
            microcontroller.watchdog.feed()
        elif not wifi_lost:
            wifi_lost = True
            print("Wi-Fi lost while idle, leaving the watchdog to reset.")
        await asyncio.sleep(MQTT_SERVICE_SLEEP_S)


async def status_blinker(blinds):
    colors = { Blinds.POSITION_DOWN: Color.BLUE,
                  Blinds.POSITION_UP: Color.YELLOW,
                  Blinds.POSITION_MOVING_DOWN: Color.BLUE,
                  Blinds.POSITION_MOVING_UP: Color.YELLOW,
                  Blinds.POSITION_STOPPED: Color.CYAN,
                  Blinds.POSITION_UNKNOWN: Color.ORANGE}
    while True:
        color = colors[blinds.position]
        pixel[0] = color
        await blink(color, 2, interval=0.15)
        if blinds.is_moving:
            await asyncio.sleep(0.25)
        else:
            await asyncio.sleep(1)

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
    output_mem()

    # Turn on the power to the NeoPixel
    tinys3.set_pixel_power(True)
    uart = busio.UART(board.TX,
                            board.RX,
                            baudrate=250000,
                            receiver_buffer_size=32)

    reader = Reader(uart)
    reader.flush_buffer()
    print("Lift servo:")
    reader.output_settings(1)

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
        # to HA.
        print("Publishing discovery payload...")
        client.publish(disc.discovery_topic, disc.discovery_payload_json(), retain=True)
        topics = disc.command_topics()
        print(f"Subscribing to {topics}...")
        client.subscribe([(topic, 0) for topic in topics])
        for topic, value in state_messages(blinds):
            client.publish(topic, str(value), retain=True)

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

    blinds = Blinds(reader,
        report_state,
        on_opened,
        board.D1,
        board.D2,
        tilt_scale)
    blinds.find_out_current_state()
    await blink(Color.BLUE, 3)
    await connect_wifi()

    microcontroller.watchdog.timeout = 16
    microcontroller.watchdog.mode = WatchDogMode.RESET
    print("Watchdog enabled with 16s timeout.")

    # The supervisor owns connecting, and rebuilds the client when it drops.
    mqtt.start_supervisor()

    tasks = []
    tasks.append(asyncio.create_task(service_mqtt(mqtt, blinds, socket_timeout)))
    tasks.append(asyncio.create_task(status_blinker(blinds)))
    tasks.append(asyncio.create_task(publish_uptime(mqtt, disc)))

    await asyncio.gather(*tasks)

asyncio.run(main())
