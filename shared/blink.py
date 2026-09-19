import board
import asyncio
import neopixel
import tinys3
from color import Color

pixel = neopixel.NeoPixel(board.NEOPIXEL,
            1,
            brightness=0.3,
            auto_write=True,
            pixel_order=neopixel.RGB)
tinys3.set_pixel_power(True)


async def blink(color, times, interval=0.3):
    while times:
        pixel[0] = color
        await asyncio.sleep(interval)
        pixel[0] = Color.BLACK
        await asyncio.sleep(interval)
        times -= 1
