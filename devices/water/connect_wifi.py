import os
import asyncio
import time
import wifi
from blink import blink, pixel, Color # For indicating connection status


async def wifi_supervisor(state, 
                          on_connection_ok, 
                          on_disconnected,
                          on_connected):
    """
    Supervisor to monitor wifi state.
    """
    DISCONNECT_TIMEOUT = 30  # seconds without connection → reconnect

    last_ok = time.monotonic()

    while state.running:
        print("WIFI supervisor is alive.")
        try:    
            if wifi.radio.connected:
                print("Wifi is connected.")
                last_ok = time.monotonic()
                await on_connection_ok()
            else:
                print("Wifi is disconnected.")
                await on_disconnected()
                print("Wifi supervisor: mqtt disconnected.")
                if time.monotonic() - last_ok > DISCONNECT_TIMEOUT:
                    print("Trying to reconnect wifi.")
                    await blink(Color.YELLOW, 3)
                    wifi.radio.enabled = False
                    await asyncio.sleep(2)
                    wifi.radio.enabled = True
                    if wifi.radio.connected:
                        last_ok = time.monotonic()
                        print("WiFi supervisor: reconnected to wifi")
                        await blink(Color.GREEN, 3)
                        await on_connected()
                    else:
                        await blink(Color.RED, 3)
                        await connect_wifi()
        except Exception as e:
            print("Wifi supervisor: exception:", e)
            
        await asyncio.sleep(15)
        
async def connect_wifi():
    """
    Try to connect to wifi network. Indicate status with neopixel.
    """
    if wifi.radio.connected:
        print(f"Already connected to wifi.")
        return True
    
    ssid = os.getenv("CIRCUITPY_WIFI_SSID")
    pwd = os.getenv("CIRCUITPY_WIFI_PASSWORD")

    print("Connecting Wifi...")    
    pixel[0] = Color.BLUE
    try:
        for network in wifi.radio.start_scanning_networks():
            print(f"\t{network.ssid}\t\tRSSI: {network.rssi:d}\tChannel: {network.channel:d}")
        
        wifi.radio.stop_scanning_networks()
        wifi.radio.connect(ssid, pwd)
        print("Connected to wifi.")
        await blink(Color.GREEN, 3)
        return True

    except Exception as e:
        print(f"Connecting to wifi {ssid} failed: {e}")
        if "Unknown failure" in e.errno:
            # Blink to indicate error code returned
            code = int(e.errno[e.errno.rfind(" "):])
            await blink(Color.ORANGE, code)
        else:
            await blink(Color.RED, 3)
        return False
