"""Stop the servos before anything else. boot.py runs on every hard reset,
before the web workflow's Wi-Fi connect, which blocks for up to 8 s. A
controller reset leaves the servos doing whatever they were doing, and a
lift left driving into the head rail must stop at once. code.py stops them
again and reads their servo health. Output goes to boot_out.txt."""

import board
import busio
from packet import Reader
import servo_health

uart = busio.UART(board.TX, board.RX, baudrate=250000, receiver_buffer_size=32)
try:
    lift, tilt = servo_health.stop_servos(Reader(uart))
    print(f"Boot stop confirmed: lift {lift}, tilt {tilt}")
finally:
    # Free the pins for code.py.
    uart.deinit()
