import time, gc, os, sys, ssl
from time import sleep
import microcontroller
import neopixel
import board, digitalio, busio
import tinys3
import supervisor
import wifi, socketpool
import adafruit_minimqtt.adafruit_minimqtt as MQTT
import asyncio
import keypad
import time
from adafruit_datetime import timedelta
from adafruit_debouncer import Debouncer
from blinds import Blinds
from packet import Packet, Reader
from discovery import HADiscovery
import storage

try:
    storage.disable_usb_drive()
except Exception as e:
    print(f"Failed to disable usb drive: {e}")


pixel = neopixel.NeoPixel(board.NEOPIXEL, 1, brightness=0.3, auto_write=True, pixel_order=neopixel.RGB)

class Color:
    GREEN = (255, 0, 0)
    RED = (0, 255, 0)
    YELLOW = (255, 255, 0)
    BLUE = (0, 0, 255)
    CYAN = (255, 0, 255)
    WHITE = (255, 255, 255)
    ORANGE = (165, 255, 0)
    BLACK = (0, 0, 0)

async def blink(color, times, interval=0.3):
    while times:
        pixel[0] = color
        await asyncio.sleep(interval)
        pixel[0] = Color.BLACK
        await asyncio.sleep(interval)
        times-=1

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



async def measure_uptime(mqtt_client, on_connected, disc, blinds):
    start_time = time.time()
    while True:
        await asyncio.sleep(1)
        t = time.time()
        uptime = t - start_time

        if uptime % 10 == 0:
            if blinds.is_moving == False and on_connected.is_set():
                try:
                    uptime_str = str(timedelta(seconds=uptime))
                    print(f"Publishing uptime {uptime_str}...")
                    mqtt_client.publish(disc.uptime_state_topic, uptime_str)
                except Exception as e:
                    print(f"Failed to publish uptime: {e!r}")


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

async def connect_mqtt(disc, blinds):
    print("Setting up mqtt...")
    on_connected = asyncio.Event()
    pool = socketpool.SocketPool(wifi.radio)
    mqtt_client = MQTT.MQTT(
        broker=os.getenv("mqtt_broker"),
        port=os.getenv("mqtt_port"),
        username=os.getenv("mqtt_user"),
        password=os.getenv("mqtt_pwd"),
        socket_pool=pool,
        ssl_context=ssl.create_default_context())

    def connected(client, userdata, flags, rc):
        print("Connected to mqtt broker.")
        on_connected.set()
        asyncio.create_task(blink(Color.YELLOW, 3))
        print("Publishing discovery payload...")
        client.publish(disc.discovery_topic, disc.discovery_payload_json(), retain=True)
        for topic in disc.command_topics():
            print(f"Subscribing to {topic}...")
            client.subscribe(topic)

    def disconnected(client, userdata, rc):
        print("Disconnected from mqtt broker.")
        on_connected.reset()
        asyncio.create_task(blink(Color.ORANGE, 5))

    def message(client, topic, message):
        print(f"New message on topic {topic}: {message}")
        asyncio.create_task(blink(Color.GREEN, 2))
        if topic == disc.cover_command_topic:
            try:
                if message == "OPEN":
                    asyncio.create_task(blinds.open())
                elif message == "CLOSE":
                    asyncio.create_task(blinds.close())
                elif message == "STOP":
                    asyncio.create_task(blinds.stop())
            except Exception as e:
                print(f"Failed to handle cover command: {e!r}")
        elif topic == disc.speed_command_topic:
            try:
                speed = int(float(message))
                blinds.speed = speed
            except Exception as e:
                print(f"Failed to parse speed: {e!r}")
        elif topic == disc.tilt_command_topic:
            try:
                tilt = int(float(message))
                blinds.tilt = tilt
            except Exception as e:
                print(f"Failed to parse tilt: {e!r}")

    print("Setting callbacks..")
    mqtt_client.on_connect = connected
    mqtt_client.on_disconnect = disconnected
    mqtt_client.on_message = message

    print("Connecting to MQTT broker...")

    mqtt_client.connect()
    print(f"Is connected: {mqtt_client.is_connected()}")

    return mqtt_client, on_connected

async def poll_mqtt(mqtt_client, on_connected, blinds, interval):
    while True:
        try:
            if blinds.is_moving == False:
                await on_connected.wait()
                print(f"Updating mqtt, blinds state = {blinds.position}")
                mqtt_client.loop(timeout=5)
        except Exception as e:
            print(f"Failed to communicate with mqtt: {e!r}, trying to reconnect...")
            await blink(Color.ORANGE, 3)

            try:
                await connect_wifi()
                mqtt_client.reconnect()
                await blink(Color.GREEN, 3)
            except Exception as e:
                print(f"Failed to reconnect to mqtt: {e!r}")

        await asyncio.sleep(interval)

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

    disc = HADiscovery(device_name)

    mqtt_client = None
    on_connected = None

    def report_state(blinds):
        states = {Blinds.POSITION_UNKNOWN: "unknown",
                  Blinds.POSITION_MOVING_DOWN: "closing",
                  Blinds.POSITION_MOVING_UP: "opening",
                  Blinds.POSITION_DOWN: "closed",
                  Blinds.POSITION_UP: "open",
                  Blinds.POSITION_STOPPED: "stopped"}
        try:
            state = states[blinds.position]
            print(f"Reporting state as {state}")
            if on_connected.is_set():
                mqtt_client.publish(disc.cover_state_topic, state)
                mqtt_client.publish(disc.tilt_state_topic, str(blinds.tilt))
                mqtt_client.publish(disc.speed_state_topic, str(blinds.speed))
        except Exception as e:
            print(f"Failed to post mqtt status: {e!r}")

    def on_opened(blinds):
        if on_connected.is_set():
            mqtt_client.publish(disc.opened_count_state_topic, str(blinds.opened_count))

    blinds = Blinds(reader,
        report_state,
        on_opened,
        board.D1,
        board.D2,
        tilt_scale)
    blinds.find_out_current_state()
    await blink(Color.BLUE, 3)
    await connect_wifi()
    mqtt_client, on_connected = await connect_mqtt(disc, blinds)
    await blink(Color.GREEN, 3)

    tasks = []
    tasks.append(asyncio.create_task(poll_mqtt(mqtt_client, on_connected, blinds, 4)))
    tasks.append(asyncio.create_task(status_blinker(blinds)))
    tasks.append(asyncio.create_task(measure_uptime(mqtt_client, on_connected, disc, blinds)))

    await asyncio.gather(*tasks)

asyncio.run(main())
