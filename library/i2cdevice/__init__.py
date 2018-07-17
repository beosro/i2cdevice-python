def _mask_width(value, bit_width=8):
    """Get the width of a bitwise mask
    
    ie: 0b000111 = 3
    """
    value >>= _trailing_zeros(value, bit_width)
    return value.bit_length()

def _leading_zeros(value, bit_width=8):
    """Count leading zeros on a binary number with a given bit_width

    ie: 0b0011 = 2

    Used for shifting around values after masking.
    """
    count = 0
    for _ in range(bit_width):
        if value & (1 << (bit_width - 1)):
            return count
        count += 1
        value <<= 1
    return count

def _trailing_zeros(value, bit_width=8):
    """Count trailing zeros on a binary number with a given bit_width

    ie: 0b11000 = 3

    Used for shifting around values after masking.
    """
    count = 0
    for _ in range(bit_width):
        if value & 1:
            return count
        count += 1
        value >>= 1
    return count


def _int_to_bytes(value, length, endianness='big'):
    try:
        return value.to_bytes(length, endianness)
    except:
        output = bytearray()
        for x in range(length):
            offset = x * 8
            mask = 0xff << offset
            output.append((value & mask) >> offset)
        if endianness == 'big':
            output.reverse()
        return output

class MockSMBus:
    def __init__(self, i2c_bus):
        self.regs = [0 for _ in range(255)]

    def write_i2c_block_data(self, i2c_address, register, values):
        self.regs[register:register+len(values)] = values

    def read_i2c_block_data(self, i2c_address, register, length):
        return self.regs[register:register+length]


class _RegisterProxy(object):
    """Register Proxy
    
    This proxy catches lookups against non existent get_fieldname and set_fieldname methods
    and converts them into calls against the device's get_field and set_field methods with
    the appropriate options.

    This means device.register.set_field(value) and device.register.get_field(value) will work
    and also transparently update the underlying device without the register or field objects
    having to know anything about how data is written/read/stored.
    
    """
    def __init__(self, device, register):
        object.__init__(self)
        self.device = device
        self.register = register

    def __getattribute__(self, name):
        if name.startswith("get_"):
            name = name.replace("get_", "")
            return lambda: self.device.get_field(self.register.name, name)
        if name.startswith("set_"):
            name = name.replace("set_", "")
            return lambda value: self.device.set_field(self.register.name, name, value)
        return object.__getattribute__(self, name)


class Register():
    """Store information about an i2c register"""
    def __init__(self, name, address, fields=None, bit_width=8, read_only=False, volatile=True):
        self.name = name
        self.address = address
        self.bit_width = bit_width
        self.read_only = read_only
        self.volatile = volatile
        self.fields = {}

        for field in fields:
            self.fields[field.name] = field


class BitField():
    """Store information about a field or flag in an i2c register"""
    def __init__(self, name, mask, adapter=None, bit_width=8, read_only=False):
        self.name = name
        self.mask = mask
        self.adapter = adapter
        self.bit_width = bit_width
        self.read_only = read_only


class BitFlag(BitField):
    def __init__(self, name, bit, read_only=False):
        BitField.__init__(self, name, 1 << bit, read_only)


class Device(object):
    def __init__(self, i2c_address, i2c_dev=None, bit_width=8, registers=None):
        self._bit_width = bit_width

        self.registers = {}

        if type(i2c_address) is list:
            self._i2c_addresses = i2c_address
            self._i2c_address = i2c_address[0]
        else:
            self._i2c_addresses = [i2c_address]
            self._i2c_address = i2c_address
 
        self._i2c = i2c_dev

        if self._i2c is None:
            import smbus
            self._i2c = smbus.SMBus(1)

        for register in registers:
            self.registers[register.name] = register
            self.__dict__[register.name] = _RegisterProxy(self, register)

    def get_addresses(self):
        return self._i2c_addresses

    def select_address(self, address):
        if address in self._i2c_addresses:
            self._i2c_address = address
            return True
        raise ValueError("Address {:02x} invalid!".format(address))

    def next_address(self):
        next_addr = self._i2c_addresses.index(self._i2c_address) + 1
        next_addr += 1
        next_addr %= len(self._i2c_addresses)
        self._i2c_address = self._i2c_addresses[next_addr]
        return self._i2c_address

    def get_field(self, register, field):
        register = self.registers[register]
        field = register.fields[field]
        value = self._i2c_read(register.address, register.bit_width)
        value = (value & field.mask) >> _trailing_zeros(field.mask)

        if field.adapter is not None:
            value = field.adapter._decode(value)

        return value

    def set_field(self, register, field, value):
        register = self.registers[register]
        field = register.fields[field]

        if field.adapter is not None:
            value = field.adapter._encode(value)

        reg_value = self._i2c_read(register.address, register.bit_width)
        reg_value &= ~field.mask
        reg_value |= value << _trailing_zeros(field.mask)
        self._i2c_write(register.address, reg_value, register.bit_width)

    def get_register(self, register):
        register = self.registers[register]
        return self._i2c_read(register.address, register.bit_width)

    def _i2c_write(self, register, value, bit_width):
        values = _int_to_bytes(value, bit_width // self._bit_width, 'big')
        values = list(values)
        self._i2c.write_i2c_block_data(self._i2c_address, register, values)

    def _i2c_read(self, register, bit_width):
        value = 0
        for x in self._i2c.read_i2c_block_data(self._i2c_address, register, bit_width // self._bit_width):
            value <<= 8
            value |= x
        return value

    def __getattribute__(self, name):
        attr = object.__getattribute__(self, name)
        if isinstance(attr, Register):
            attr._value = self._read(attr.address, attr.bit_width)
        return attr