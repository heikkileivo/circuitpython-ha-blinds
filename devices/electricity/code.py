import time, gc, os, sys, ssl
from time import sleep
import microcontroller
from watchdog import WatchDogMode
import digitalio
import board
import tinys3
import supervisor
import wifi, socketpool
import asyncio
import keypad
import time
from adafruit_datetime import timedelta
from adafruit_debouncer import Debouncer
from ringbuffer import RingBuffer
import supervisor
import storage
from blink import blink, Color, pixel
from mqtt import Mqtt, mqtt_publish
from connect_wifi import wifi_supervisor
from discovery import HADiscovery


try:
    # Disable storage if usb is disconnected to enable web api
    #storage.disable_usb_drive()
    pass
except Exception as e:
    print(f"Failed to disable usb drive: {e}")


class LoopState:
    def __init__(self):
        self.running = True
        self.msg_time = 0
        self.uptime_time = 0
        self.mqtt = None
        self.last_publish = 0
        self.last_connect = 0
        self.reconnects = 0
        self.mqtt_lock = asyncio.Lock()
        self.status_led = True


async def measure_uptime(state, disc):
    start_time = time.time()
    while state.running:
        try:
            t = time.time()
            uptime = t - start_time
            mem = gc.mem_free()
            uptime_str = str(timedelta(seconds=uptime))
            print(f"Publishing uptime {uptime_str}")
            if state.mqtt.on_connected.is_set():
                await mqtt_publish(state, disc.topic("uptime", "state"), uptime_str)
                await mqtt_publish(state, disc.topic("uptime_seconds", "state"), int(uptime))
                await mqtt_publish(state, disc.topic("memory", "state"), mem)
                await mqtt_publish(state, disc.topic("reconnects", "state"), state.mqtt.reconnects)
        except Exception as e:
            print(f"Failed to publish uptime: {repr(e)}")
        await asyncio.sleep(10)


async def status_blinker(state):
    while state.running:
        if state.status_led:
            await blink(Color.GREEN, 1, interval=0.1)
            await asyncio.sleep(2)
        else:
            pixel[0] = Color.BLACK
            await asyncio.sleep(1)


class Counter:
    def __init__(self):
        self.value = 0
        self.buffer = None
        self.pulses_per_unit = 1
        self.name = "Counter"
        self.interval = 5.0


async def poll_pin(pin, state, counter, debounce_time=0):
    input = digitalio.DigitalInOut(pin)
    input.direction = digitalio.Direction.INPUT
    input.pull = digitalio.Pull.UP
    debouncer = Debouncer(input, debounce_time)
    previous_time = 0
    print(f"Entering poller for {counter.name}")
    while state.running:
        debouncer.update()
        if debouncer.rose:
            counter.value += 1
            current_time = supervisor.ticks_ms()
            timedelta = current_time - previous_time
            previous_time = current_time
            if counter.buffer:
                counter.buffer.append(timedelta)
        await asyncio.sleep(0)
    print(f"Exiting poller for {counter.name}")


async def calculate_value(state, counter, disc):
    previous_value = counter.value
    previous_time = 0
    std_dev = 0
    total_units = 0
    units_per_pulse = 1 / counter.pulses_per_unit
    power_divident = os.getenv("power_dividend")
    print(f"Entering value loop for {counter.name}")
    while state.running:
        current_time = supervisor.ticks_ms()
        timedelta = current_time - previous_time
        previous_time = current_time
        current_value = counter.value
        pulses = current_value - previous_value
        previous_value = current_value
        std_dev = counter.buffer.std_dev
        pulses_per_s = 0

        if pulses >= 0 and timedelta > 0:
            avg_timedelta = timedelta
            avg_timedelta_s = avg_timedelta / 1000
            avg_timedelta_h = avg_timedelta_s / 3600
            pulses_per_s = 0
            power = 0

            avg_interval = counter.buffer.avg

            if avg_interval:
                power = power_divident / avg_interval

            if avg_timedelta_s:
                pulses_per_s = pulses / avg_timedelta_s

            missed_pulses = pulses_per_s * state.msg_time
            pulses_per_min = pulses_per_s * 60
            units_per_min = pulses_per_min / counter.pulses_per_unit
            total_value_increment = pulses * units_per_pulse

            if avg_timedelta_h:
                power2 = (total_value_increment / avg_timedelta_h) * 1000

            total_units += total_value_increment

            print(f"Total pulses: {counter.value}")
            print(f"Pulses measured: {pulses}")
            print(f"Interval: {avg_interval} ms")
            print(f"Power 1: {power}")
            print(f"Pulses / min: {pulses_per_min}")
            print(f"Units / min : {units_per_min}")
            print(f"Total units : {total_units}")
            print(f"Message time : {state.msg_time} s")
            print(f"Uptime time : {state.uptime_time} s")
            print(f"Dev : {std_dev}")

        # Send a new message
        pixel[0] = Color.BLUE
        print(f"Sending {counter.name} values...")
        try:
            if state.mqtt.on_connected.is_set():
                await mqtt_publish(state, disc.topic("power", "state"), power)
                await mqtt_publish(state, disc.topic("energy_total", "state"), total_units)
                await mqtt_publish(state, disc.topic("pulses", "state"), pulses_per_s)
                await mqtt_publish(state, disc.topic("interval", "state"), avg_timedelta)
            else:
                print("Unable to send counter values: mqtt not connected.")
            pixel[0] = Color.BLACK
        except Exception as e:
            print(f"Failed to send values for {counter.name}: {repr(e)}.")

        pixel[0] = Color.BLACK
        print(f"{counter.name} values sent.")
        end_time = supervisor.ticks_ms()
        time_elapsed = end_time - current_time
        state.msg_time = time_elapsed / 1000
        microcontroller.watchdog.feed()
        await asyncio.sleep(counter.interval)
    print(f"Exiting value loop for {counter.name}")


def create_tasks(state, disc,
                 on_connection_ok,
                 on_disconnected,
                 on_connected):
    tasks = []

    counter = Counter()
    counter.name = os.getenv("counter_name")
    counter.pulses_per_unit = os.getenv("pulses_per_unit")
    counter.interval = os.getenv("report_interval")
    counter.buffer = RingBuffer(os.getenv("ring_buffer_length"))
    pin = eval(os.getenv("sensor_pin"))
    debounce_time = os.getenv("debounce_time_ms") / 1000
    tasks.append(asyncio.create_task(poll_pin(pin, state, counter, debounce_time=debounce_time)))
    tasks.append(asyncio.create_task(calculate_value(state, counter, disc)))
    tasks.append(asyncio.create_task(measure_uptime(state, disc)))
    tasks.append(asyncio.create_task(status_blinker(state)))
    tasks.append(asyncio.create_task(wifi_supervisor(state,
                                                     on_connection_ok,
                                                     on_disconnected,
                                                     on_connected)))
    return tasks


def output_mem():
    print("Memory Info - gc.mem_free()")
    print("---------------------------")
    print("{} Bytes\n".format(gc.mem_free()))

    flash = os.statvfs('/')
    flash_size = flash[0] * flash[2]
    flash_free = flash[0] * flash[3]
    print("Flash - os.statvfs('/')")
    print("---------------------------")
    print("Size: {} Bytes\nFree: {} Bytes\n".format(flash_size, flash_free))


async def main():
    output_mem()

    tinys3.set_pixel_power(True)

    device_name = os.getenv("device_name", "Electricity Meter")

    disc = HADiscovery(device_name, "CircuitPython Electricity Meter", "electricity")
    disc.add_component("power", "sensor", {
        "name": "Power",
        "device_class": "power",
        "unit_of_measurement": "W",
        "state_class": "measurement",
    })
    disc.add_component("energy_total", "sensor", {
        "name": "Energy total",
        "device_class": "energy",
        "unit_of_measurement": "kWh",
        "state_class": "total_increasing",
    })
    disc.add_component("pulses", "sensor", {
        "name": "Pulses",
        "unit_of_measurement": "pulses/s",
        "entity_category": "diagnostic",
    })
    disc.add_component("interval", "sensor", {
        "name": "Interval",
        "unit_of_measurement": "ms",
        "entity_category": "diagnostic",
    })
    disc.add_component("uptime", "sensor", {
        "name": "Uptime",
        "entity_category": "diagnostic",
    })
    disc.add_component("uptime_seconds", "sensor", {
        "name": "Uptime seconds",
        "entity_category": "diagnostic",
        "device_class": "duration",
        "unit_of_measurement": "s",
        "state_class": "total_increasing",
    })
    disc.add_component("memory", "sensor", {
        "name": "Memory",
        "unit_of_measurement": "B",
        "entity_category": "diagnostic",
    })
    disc.add_component("reconnects", "sensor", {
        "name": "Reconnects",
        "entity_category": "diagnostic",
    })
    disc.add_component("status_led", "switch", {
        "name": "Status LED",
        "command_topic": True,
        "entity_category": "config",
    })

    state = LoopState()

    def on_connect(client):
        print("Publishing discovery payload...")
        client.publish(disc.discovery_topic, disc.discovery_payload_json(), retain=True)
        for topic in disc.command_topics():
            print(f"Subscribing to {topic}...")
            state.mqtt.subscribe(topic)
        state.mqtt.publish(disc.topic("status_led", "state"), "ON" if state.status_led else "OFF")

    def on_message(client, topic, message):
        if topic == disc.topic("status_led", "set"):
            state.status_led = message == "ON"
            state.mqtt.publish(disc.topic("status_led", "state"), message)

    state.mqtt = Mqtt(on_connect_callback=on_connect, on_message_callback=on_message)

    async def on_connection_ok():
        if state.mqtt.running == False:
            state.mqtt.start_supervisor()

    async def on_disconnected():
        await state.mqtt.stop_supervisor()
        state.mqtt.disconnect()

    async def on_connected():
        state.mqtt.init()
        state.mqtt.start_supervisor()

    microcontroller.watchdog.timeout = 60
    microcontroller.watchdog.mode = WatchDogMode.RESET
    print("Watchdog enabled with 60s timeout.")

    tasks = create_tasks(state, disc,
                         on_connection_ok,
                         on_disconnected,
                         on_connected)

    await asyncio.gather(*tasks)

while True:
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"Unhandled exception: {repr(e)}")
        print("Restarting...")
