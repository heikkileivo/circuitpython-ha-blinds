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
import storage

try:
    storage.disable_usb_drive()
except Exception as e:
    print(f"Failed to disable usb drive: {e}")


pixel = neopixel.NeoPixel(board.NEOPIXEL, 1, brightness=0.3, auto_write=True, pixel_order=neopixel.RGB)

class Color:
    GREEN = (255, 0, 0, 0.5)
    RED = (0, 255, 0, 0.5)
    YELLOW = (255, 255, 0, 0.5)
    BLUE = (0, 0, 255, 0.5)
    CYAN = (255, 0, 255, 0.5)
    WHITE = (255, 255, 255, 0.5)
    ORANGE = (165, 255, 0, 0.5)
    BLACK = (0, 0, 0, 0.5)

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



async def measure_uptime(mqtt_client, on_connected, blinds):
    start_time = time.time()
    while True:
        await asyncio.sleep(1)
        t = time.time()
        uptime = t - start_time

        if uptime % 10 == 0:
            if blinds.is_moving == False and on_connected.is_set():
                try:
                    uptime_str = str(timedelta(seconds=uptime))
                    print("Publishing uptime %s..." % uptime_str)
                    uptime_feed = os.getenv("uptime_feed")
                    mqtt_client.publish(f"{uptime_feed}/seconds", uptime)
                    mqtt_client.publish(f"{uptime_feed}/str", uptime_str)
                except Exception as e:
                    print("Failed to publish uptime: %s" % repr(e))


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

async def connect_mqtt(blinds):
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
        client.subscribe(os.getenv("position_command_feed"))
        client.subscribe(os.getenv("position_speed_feed"))
        client.subscribe(os.getenv("tilt_command_feed"))

    def disconnected(client, userdata, rc):
        print("Disconnected from mqtt broker.")
        on_connected.reset()
        asyncio.create_task(blink(Color.ORANGE, 5))

    def message(client, topic, message):
        print(f"New message on topic {topic}: {message}")
        asyncio.create_task(blink(Color.GREEN, 2))
        if topic == os.getenv("position_command_feed"):
            try:
                if message == "open":
                    asyncio.create_task(blinds.open())
                elif message == "close":
                    asyncio.create_task(blinds.close())
                elif message == "stop":
                    asyncio.create_task(blinds.stop())
            except:
                print("Failed to parse value.")
        elif topic == os.getenv("position_speed_feed"):
            try:
                speed = int(message)
                blinds.speed = speed
            except:
                print("Failed to parse speed.")
        elif topic == os.getenv("tilt_command_feed"):
            try:
                tilt = int(message)
                blinds.tilt = tilt
            except:
                print("Failed to parse tilt.")

    print("Setting callbacks..")
    mqtt_client.on_connect = connected
    mqtt_client.on_disconnect = disconnected
    mqtt_client.on_message = message

    print("Connecting to MQTT broker...")

    mqtt_client.connect()
    print("Is connected: %s" % mqtt_client.is_connected())

    return mqtt_client, on_connected

async def poll_mqtt(mqtt_client, on_connected, blinds, interval):
    while True:
        try:
            if blinds.is_moving == False:
                await on_connected.wait()
                print("Updating mqtt, blinds state = %s" % blinds.position)
                mqtt_client.loop(timeout=5)
        except Exception as e:
            print("Failed to communicate with mqtt: %s, trying to reconnect..." % repr(e))
            await blink(Color.ORANGE, 3)

            try:
                await connect_wifi()
                mqtt_client.reconnect()
                await blink(Color.GREEN, 3)
            except:
                print("Failed to reconnect to mqtt.")

        await asyncio.sleep(interval)

def output_mem():
    # Show available memory
    print("Memory Info - gc.mem_free()")
    print("---------------------------")
    print("{} Bytes\n".format(gc.mem_free()))

    flash = os.statvfs('/')
    flash_size = flash[0] * flash[2]
    flash_free = flash[0] * flash[3]
    # Show flash size
    print("Flash - os.statvfs('/')")
    print("---------------------------")
    print("Size: {} Bytes\nFree: {} Bytes\n".format(flash_size, flash_free))


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
    #print("Tilt servo:")
    #reader.output_settings(2)
    #reader.set_baud_rate(1, Reader.BAUD_RATE_250K)
    #reader.set_baud_rate(2, Reader.BAUD_RATE_250K)
    #return
    #reader.set_as_motor(1)
    #reader.set_id(1, 2)
    #reader.set_position(2, 100)
    #return
    #return
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
            print("Reporting stateas %s" % state)
            if on_connected.is_set():
                mqtt_client.publish(os.getenv("position_value_feed"), state)
                mqtt_client.publish(os.getenv("tilt_value_feed"), str(blinds.tilt))
                mqtt_client.publish(os.getenv("position_speed_feed"), str(blinds.speed))
        except:
            print("Failed to post mqtt status.")

    def on_opening(blinds):
        if on_connected.is_set():
            mqtt_client.publish(os.getenv("group_value_feed"), "opening")

    def on_opened(blinds):
        if on_connected.is_set():
            mqtt_client.publish(os.getenv("opened_counter_feed"), str(blinds.opened_count))
            next_command_feed = os.getenv("next_command_feed")
            if next_command_feed:
                mqtt_client.publish(next_command_feed, "open")
            else:
                mqtt_client.publish(os.getenv("group_value_feed"), "open")

    def on_closing(blinds):
        if on_connected.is_set():
            next_command_feed = os.getenv("next_command_feed")
            if next_command_feed:
                mqtt_client.publish(next_command_feed, "opening")

    def on_closed(blinds):
        if on_connected.is_set():
            next_command_feed = os.getenv("next_command_feed")
            if next_command_feed:
                mqtt_client.publish(next_command_feed, "close")
            else:
                mqtt_client.publish(os.getenv("group_value_feed"), "closed")

    blinds = Blinds(reader,
        report_state,
        on_opening,
        on_opened,
        on_closing,
        on_closed,
        board.D1,
        board.D2,
        10.0)
    blinds.find_out_current_state()
    #try:
    await blink(Color.BLUE, 3)
    await connect_wifi()
    mqtt_client, on_connected = await connect_mqtt(blinds)
    await blink(Color.GREEN, 3)

    #except:
    #   await blink(Color.RED, 5)
        #microcontroller.reset()

    tasks = []
    tasks.append(asyncio.create_task(poll_mqtt(mqtt_client, on_connected, blinds, 4)))
    tasks.append(asyncio.create_task(status_blinker(blinds)))
    tasks.append(asyncio.create_task(measure_uptime(mqtt_client, on_connected, blinds)))

    await asyncio.gather(*tasks)

asyncio.run(main())
