# How long a transaction waits for the reply (busio.UART's timeout, for the
# first byte and between bytes). A reply takes about 2 ms.
READ_TIMEOUT_S = 0.01
# Addresses below this are EEPROM. The bench's first EEPROM write replied
# after more than 10 ms, so a write there waits longer.
EEPROM_END = 40
EEPROM_TIMEOUT_S = 0.1

class Instruction:
    PING = 1
    READ = 2
    WRITE = 3


class Address:
    VERSION_L = 3
    VERSION_H = 3

    ID = 5
    BAUD_RATE = 6
    MIN_ANGLE_LIMIT_L = 9
    MIN_ANGLE_LIMIT_H = 10
    MAX_ANGLE_LIMIT_L = 11
    MAX_ANGLE_LIMIT_H = 12
    CW_DEAD = 26
    CCW_DEAD = 27

    TORQUE_ENABLE = 40
    GOAL_POSITION_L = 42
    GOAL_POSITION_H = 43
    GOAL_TIME_L = 44
    GOAL_TIME_H = 45
    GOAL_SPEED_L = 46
    GOAL_SPEED_H = 47
    LOCK = 48

    PRESENT_POSITION_L  = 56
    PRESENT_POSITION_H = 57
    PRESENT_SPEED_L = 58
    PRESENT_SPEED_H = 59
    PRESENT_LOAD_L = 60
    PRESENT_LOAD_H = 61
    PRESENT_VOLTAGE = 62
    PRESENT_TEMPERATURE = 63
    STATUS = 65
    MOVING = 66
    PRESENT_CURRENT_L = 69
    PRESENT_CURRENT_H = 70


def checksum(body):
    return ~sum(body) & 0xFF


def reply_problem(reply, scs_id, n):
    """What's wrong with a reply that should carry n data bytes, or None."""
    if reply is None:
        return "no reply"
    if len(reply) != 6 + n:
        return "short reply"
    if reply[0] != 0xFF or reply[1] != 0xFF:
        return "bad header"
    if reply[2] != scs_id:
        return "wrong id"
    if reply[3] != n + 2:
        return "wrong length"
    if reply[-1] != checksum(reply[2:-1]):
        return "bad checksum"
    return None


def _load(high, low):
    """PRESENT_LOAD from its two bytes: sign and magnitude, with the sign in
    bit 10. In wheel mode it reads the commanded duty."""
    load = ((high & 0x03) << 8) | low
    return -load if high & 0x04 else load


class Reader:
    def __init__(self, uart, log=True):
        """log: whether a transaction that got no good reply prints why."""
        self.uart = uart
        self._log = log
        self._uart_errors = {}
        # What was wrong with the last transaction's reply, or None.
        self.problem = None

    def flush_buffer(self):
        count = self.uart.in_waiting
        if count:
            self.uart.read(count)

    def ping(self, scs_id):
        """Ping a servo. Returns the reply's ERROR byte, or None if no good
        reply came."""
        reply = self._transaction(scs_id, Instruction.PING, (), 0)
        return None if reply is None else reply[0]

    def read(self, scs_id, address, n):
        """Read n bytes from address onwards in one transaction. Returns
        (ERROR byte, data), or None if no good reply came."""
        return self._transaction(scs_id, Instruction.READ, (address, n), n)

    def write_mem(self, scs_id, address, data):
        """Write data from address onwards. Returns the reply's ERROR byte,
        or None if no good reply came."""
        timeout = EEPROM_TIMEOUT_S if address < EEPROM_END else READ_TIMEOUT_S
        reply = self._transaction(scs_id, Instruction.WRITE, [address] + data, 0, timeout)
        return None if reply is None else reply[0]

    def write_byte(self, scs_id, address, value):
        data = [value & 0xFF]
        return self.write_mem(scs_id, address, data)


    def write_word(self, scs_id, address, value):
        l = value & 0xFF
        h = (value >> 8) & 0xFF
        data = [h, l]
        return self.write_mem(scs_id, address, data)

    def read_1_byte(self, scs_id, address):
        reply = self.read(scs_id, address, 1)
        return None if reply is None else reply[1][0]

    def read_2_bytes(self, scs_id, address):
        reply = self.read(scs_id, address, 2)
        if reply is None:
            return None
        h, l = reply[1]
        return (h << 8) | l

    def read_motion(self, scs_id):
        """The servo angle, PRESENT_SPEED, PRESENT_LOAD and PRESENT_VOLTAGE
        (0.1 V) in one block read of 56-62, or None if no good reply came.
        The speed is sign and magnitude with the sign in bit 15, the load
        with the sign in bit 10."""
        reply = self.read(scs_id, Address.PRESENT_POSITION_L, 7)
        if reply is None:
            return None
        data = reply[1]
        angle = (data[0] << 8) | data[1]
        speed = ((data[2] & 0x7F) << 8) | data[3]
        return (angle, -speed if data[2] & 0x80 else speed,
                _load(data[4], data[5]), data[6])

    def read_moving(self, scs_id):
        """PRESENT_LOAD, PRESENT_VOLTAGE (0.1 V) and whether the servo is
        moving, in one block read of 60-66, or None if no good reply came."""
        reply = self.read(scs_id, Address.PRESENT_LOAD_L, 7)
        if reply is None:
            return None
        data = reply[1]
        return _load(data[0], data[1]), data[2], data[6] == 1

    def set_position(self, scs_id, position):
        return self.write_word(scs_id, Address.GOAL_POSITION_L, position)

    def get_time(self, scs_id):
        return self.read_2_bytes(scs_id, Address.GOAL_TIME_L)

    def set_time(self, scs_id, time):
        return self.write_word(scs_id, Address.GOAL_TIME_L, time)

    def set_speed(self, scs_id, speed):
        return self.write_word(scs_id, Address.GOAL_SPEED_L, speed)

    def uart_errors(self, scs_id):
        """How many transactions with this servo got no good reply, and
        other UART errors counted with count_uart_error()."""
        return self._uart_errors.get(scs_id, 0)

    def count_uart_error(self, scs_id):
        """Count a UART error with this servo that no transaction counted,
        such as a lift brake that couldn't be confirmed."""
        self._uart_errors[scs_id] = self.uart_errors(scs_id) + 1

    def _transaction(self, scs_id, instruction, params, n, timeout=READ_TIMEOUT_S):
        """Send one request and read its reply, 6 + n bytes, in one read.
        The servos don't echo the request, so nothing needs skipping."""
        body = bytes((scs_id, len(params) + 2, instruction)) + bytes(params)
        self.uart.timeout = timeout
        self.uart.reset_input_buffer()
        self.uart.write(b"\xff\xff" + body + bytes((checksum(body),)))
        reply = self.uart.read(6 + n)
        problem = self.problem = reply_problem(reply, scs_id, n)
        if problem:
            self.count_uart_error(scs_id)
            if self._log:
                print(f"Servo {scs_id}: {problem}: {reply!r}")
            return None
        return reply[4], reply[5:5 + n]
