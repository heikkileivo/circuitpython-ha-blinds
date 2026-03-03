from time import sleep

DELAY = 0.015

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


class Packet:
    @staticmethod
    def is_valid(packet_data, scs_id, expected_packet_length=None):
        #print("Validating packet %s" % packet_data)
        if len(packet_data) < 5:
            print("Too short packet")
            return False
        if packet_data[0] != 255:
            print("Invalid header")
            return False
        if packet_data[1] != 255:
            print("Invalid header")
            return False
        if packet_data[2] != scs_id:
            print("Invalid id in packet")
            return False
        length = packet_data[3]
        if expected_packet_length:
            if length != expected_packet_length:
                print("Packet length not expected.")
                return False
        if length != len(packet_data) - 4:
            print("Invalid packet length")
            return False

        checksum = packet_data[-1]
        payload = packet_data[2:-1]
        actual_checksum = (~sum(payload) & 0xFF)

        if checksum != actual_checksum:
            print("Invalid checksum")
            return False

        return True

    def payload_of(packet_data):
        return packet_data[4:-1]


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
        self.ping_sent = False
        self.next_header_received = False

    def output_settings(self, id):
        print("Id: %s " % self.read_1_byte(id, Address.ID))
        print("Baud rate: %s " % self.read_1_byte(id, Address.BAUD_RATE))
        print("Min ang: %s " % self.read_2_bytes(id, Address.MIN_ANGLE_LIMIT_L))
        print("Max ang: %s " % self.read_2_bytes(id, Address.MAX_ANGLE_LIMIT_L))
        print("Enable torq: %s " % self.read_1_byte(id, Address.TORQUE_ENABLE))
        print("Lock: %s " % self.read_1_byte(id, Address.LOCK))
        print("Goal time: %s " % self.read_2_bytes(id, Address.GOAL_TIME_L))


    def set_id(self, id, new_id):
        print("Unlocking eprom...")
        self.write_byte(id, Address.LOCK, 0)
        print("Setting new id to %s..." % new_id)
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

    def raw_ping(self, scs_id):
        print("Sending raw ping...")

        data = [scs_id, 2, Instruction.PING]
        checksum = (~sum(data) & 0xFF)

        data = [255, 255] + data + [checksum]
        print("Data: %s" % data)
        self.uart.write(bytes(data))

    def read_byte(self, scs_id):
        sleep(0.018)
        #print("In waiting: %s" % self.uart.in_waiting)
        if self.uart.in_waiting:
            iw = self.uart.in_waiting
            byte = list(self.uart.read(1))[0]
            return byte
        else:
            if self.ping_sent:
                self.ping_sent = False
                return None
            else:
                print("Sending ping to pull more data...")
                self.ping_sent = True
                self.raw_ping(scs_id)
                return self.read_byte(scs_id)

    def write_mem(self, scs_id, address, data):
        count = len(data) + 3
        data = [scs_id, count, Instruction.WRITE, address] + data
        checksum = (~sum(data) & 0xFF)
        data = [255, 255] + data + [checksum]
        #print("Writing memory, data = %s" % data)
        #self.flush_buffer()
        self.uart.reset_input_buffer()
        self.uart.write(bytes(data))
        sleep(0.01)
        return self.read_packet(scs_id)

    def read_mem(self, scs_id, address, length):
        request_length = 4 # Length of request = 4 bytes
        data = [scs_id, request_length, Instruction.READ, address, length]
        checksum = (~sum(data) & 0xFF)
        data = [255, 255] + data + [checksum]
        #print("Reading address, request data: %s" % data)
        #self.flush_buffer()
        self.uart.reset_input_buffer()
        self.uart.write(bytes(data))
        return self.read_packet(scs_id)

    def write_byte(self, scs_id, address, value):
        data = [value & 0xFF]
        return self.write_mem(scs_id, address, data)


    def write_word(self, scs_id, address, value):
        l = value & 0xFF
        h = (value >> 8) & 0xFF
        data = [h, l]
        return self.write_mem(scs_id, address, data)

    def read_1_byte(self, scs_id, address):
        packet = self.read_mem(scs_id, address, 1)
        #print("Packet read: %s" % packet)
        if Packet.is_valid(packet, scs_id):
            return Packet.payload_of(packet)[-1]
        return None

    def read_2_bytes(self, scs_id, address):
        packet = self.read_mem(scs_id, address, 2)
        #print("Packet read: %s" % packet)
        if Packet.is_valid(packet, scs_id, 4):
            l = Packet.payload_of(packet)[-1]
            h = Packet.payload_of(packet)[-2]

            return (h << 8) | l

        return None


    def set_position(self, scs_id, position):
        return self.write_word(scs_id, Address.GOAL_POSITION_L, position)

    def get_time(self, scs_id):
        return self.read_2_bytes(scs_id, Address.GOAL_TIME_L)

    def set_time(self, scs_id, time):
        return self.write_word(scs_id, Address.GOAL_TIME_L, time)

    def set_speed(self, scs_id, speed):
        return self.write_word(scs_id, Address.GOAL_SPEED_L, speed)


    def read_packet(self, scs_id):
        result = []

        header_found = False
        id_read = False
        count_read = False
        expected_count = 0
        if self.next_header_received:
            #print("Header already received on previous read")
            self.next_header_received = False
            header_found = True
            result.extend([255, 255])

        while True:

            byte = self.read_byte(scs_id)
            #print("Read: %s" % byte)
            if byte is None:
                break

            if not header_found:
                if byte == 255:
                    #print("Potential 1st header byte")
                    byte = self.read_byte(scs_id)
                    if byte is None:
                        result.append(255)
                        break
                    if byte == 255:
                        header_found = True
                        result.extend([255, 255])
                        continue

            else:
                if id_read == False:
                    if byte is None:
                        #print("Invalid data: no id received.")
                        break
                    elif byte != scs_id:
                        #print("Invalid data: wrong id: %s." % byte)
                        break
                    else:
                        #print("Correct id received.")
                        result.append(byte)
                        id_read = True
                        continue

                if count_read == False:
                    if byte is None:
                        #print("Invalid data: no cout received.")
                        break
                    #print("Expecting %s bytes in payload." % byte)
                    result.append(byte)
                    expected_count = byte - 1
                    count_read = True
                    continue

                if expected_count == 0:
                    data = result[2:]
                    #print("Calculating chceksum for %s" % data)
                    checksum = (~sum(data)) & 0xFF
                    #print("Received checksum: %s, calculated: %s..." % (byte, checksum))
                    result.append(byte)
                    #if byte == checksum:
                    #    print("Checksum correct.")
                    #else:
                    #    print("Invalid checksum.")
                    break
                else:
                    expected_count -= 1
                    result.append(byte)
                    #print("Expecting %s more bytes..." % expected_count)
                    continue

                if byte == 255:
                    byte = self.read_byte(scs_id)
                    if byte is None:
                        result.append(255)
                        break
                    if byte == 255:
                        self.next_header_received = True
                        break

        if self.ping_sent:
            #print("Reading ping data..")
            while True:
                 byte = self.read_byte(scs_id)
                 if byte is None:
                    break
                 if byte == 255:
                    byte = self.read_byte(scs_id)
                    if byte is None:
                        break
                    if byte == 255:
                        self.next_header_received = True
                        break



        return result
