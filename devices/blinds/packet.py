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


class Reader:
    BAUD_RATE_1M = 0
    BAUD_RATE_0_5M = 1
    BAUD_RATE_250K = 2
    BAUD_RATE_128K = 3
    BAUD_RATE_115200 = 4
    BAUD_RATE_76800 = 5
    BAUD_RATE_57600 = 6
    BAUD_RATE_38400 = 7

    def __init__(self, uart):
        self.uart = uart
        self._uart_errors = {}

    def output_settings(self, id):
        print(f"Id: {self.read_1_byte(id, Address.ID)}")
        print(f"Baud rate: {self.read_1_byte(id, Address.BAUD_RATE)}")
        print(f"Min ang: {self.read_2_bytes(id, Address.MIN_ANGLE_LIMIT_L)}")
        print(f"Max ang: {self.read_2_bytes(id, Address.MAX_ANGLE_LIMIT_L)}")
        print(f"Enable torq: {self.read_1_byte(id, Address.TORQUE_ENABLE)}")
        print(f"Lock: {self.read_1_byte(id, Address.LOCK)}")
        print(f"Goal time: {self.read_2_bytes(id, Address.GOAL_TIME_L)}")


    def set_id(self, id, new_id):
        print("Unlocking eprom...")
        self.write_byte(id, Address.LOCK, 0)
        print(f"Setting new id to {new_id}...")
        self.write_byte(id, Address.ID, new_id)
        print("Locking eprom...")
        self.write_byte(id, Address.LOCK, 1)

    def set_baud_rate(self, id, baud_rate):
        print("Unlocking eprom...")
        self.write_byte(id, Address.LOCK, 0)
        print(f"Setting baud rate to {baud_rate}")
        self.write_byte(id, Address.BAUD_RATE, baud_rate)

        print("Locking eprom...")
        self.write_byte(id, Address.LOCK, 1)

    def set_as_motor(self, id):
        print("Unlocking eprom...")
        self.write_byte(id, Address.LOCK, 0)
        print("Setting min limit...")
        self.write_word(id, Address.MIN_ANGLE_LIMIT_L, 0)
        print("Setting max limit...")
        self.write_word(id, Address.MAX_ANGLE_LIMIT_L, 0)
        print("Locking eprom...")
        self.write_byte(id, Address.LOCK, 1)

    def flush_buffer(self):
        count = self.uart.in_waiting
        if count:
            self.uart.read(count)

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

    def read_angle_and_speed(self, scs_id):
        """The servo angle and PRESENT_SPEED in one block read, or None if
        no good reply came. The speed is sign and magnitude, with the sign in
        bit 15."""
        reply = self.read(scs_id, Address.PRESENT_POSITION_L, 4)
        if reply is None:
            return None
        data = reply[1]
        angle = (data[0] << 8) | data[1]
        speed = ((data[2] & 0x7F) << 8) | data[3]
        return angle, (-speed if data[2] & 0x80 else speed)

    def set_position(self, scs_id, position):
        return self.write_word(scs_id, Address.GOAL_POSITION_L, position)

    def get_time(self, scs_id):
        return self.read_2_bytes(scs_id, Address.GOAL_TIME_L)

    def set_time(self, scs_id, time):
        return self.write_word(scs_id, Address.GOAL_TIME_L, time)

    def set_speed(self, scs_id, speed):
        return self.write_word(scs_id, Address.GOAL_SPEED_L, speed)

    def uart_errors(self, scs_id):
        """How many transactions with this servo got no good reply."""
        return self._uart_errors.get(scs_id, 0)

    def _transaction(self, scs_id, instruction, params, n, timeout=READ_TIMEOUT_S):
        """Send one request and read its reply, 6 + n bytes, in one read.
        The servos don't echo the request, so nothing needs skipping."""
        body = bytes((scs_id, len(params) + 2, instruction)) + bytes(params)
        self.uart.timeout = timeout
        self.uart.reset_input_buffer()
        self.uart.write(b"\xff\xff" + body + bytes((checksum(body),)))
        reply = self.uart.read(6 + n)
        problem = reply_problem(reply, scs_id, n)
        if problem:
            self._uart_errors[scs_id] = self.uart_errors(scs_id) + 1
            print(f"Servo {scs_id}: {problem}: {reply!r}")
            return None
        return reply[4], reply[5:5 + n]
