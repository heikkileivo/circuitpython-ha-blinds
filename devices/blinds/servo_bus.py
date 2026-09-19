"""The servo bus settings: the lift's and the tilt's IDs, and the bus baud
rate. Every servo and every UART on the bus uses these, and onboarding a
replacement servo writes them into it (#99).

Constants, not settings: a mistyped setting would stop boot.py and
safemode.py from reaching the lift. It imports nothing, so they stay light.
"""

LIFT_ID = 1
TILT_ID = 2
BAUD_RATE = 250000
