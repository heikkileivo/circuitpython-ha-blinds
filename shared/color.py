# color.py
#
# The NeoPixel's colours, apart from blink.py, which drives the pixel, so the
# host tests can import them. The pixel is set up as RGB but takes GRB, so
# each tuple is in the pixel's byte order: a name gives the colour it says.


class Color:
    GREEN = (255, 0, 0)
    RED = (0, 255, 0)
    YELLOW = (255, 255, 0)
    BLUE = (0, 0, 255)
    CYAN = (255, 0, 255)
    WHITE = (255, 255, 255)
    ORANGE = (165, 255, 0)
    BLACK = (0, 0, 0)
