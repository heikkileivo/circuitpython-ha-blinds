import board
import asyncio
import neopixel
import tinys3

pixel = neopixel.NeoPixel(board.NEOPIXEL,
            1,
            brightness=0.3,
            auto_write=True,
            pixel_order=neopixel.RGB)
tinys3.set_pixel_power(True)


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
        times -= 1
