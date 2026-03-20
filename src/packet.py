"""
Angels Online Packet Module
==============================
Handles packet framing, construction, and parsing.

PACKET STRUCTURE (decoded from client binary FUN_0081e900 / FUN_0081ef80):

  6-byte OBFUSCATED header + optionally encrypted payload

  Header format:
    Byte 0-1: payload_length XOR 0x1357 (LE16)
    Byte 2-3: sequence_number XOR payload_length (LE16)
    Byte 4:   flags XOR (payload_length & 0xFF)
              - bit 0: encrypted
              - bit 7: compressed
    Byte 5:   checksum of plaintext payload

  Total wire size = 6 + padded_payload_length
    (if encrypted, payload rounds up to 16-byte boundary)

  Sequence numbers:
    - Increment from 1 to 0x7FFE per packet sent
    - Wrap from 0x7FFF back to 1
    - Value 0xFFFF = special (hello/key exchange)

  Payload contains sub-messages:
    [LE16 sub_msg_length][sub_msg_data] repeated

  Checksum (FUN_0081dbf0):
    1. Start with 0xD31F
    2. XOR all LE16 words of plaintext payload
    3. Rotate 16-bit result left by (result & 0xF) bits
    4. Return low_byte XOR high_byte
"""

import struct
import logging
from typing import Optional

log = logging.getLogger("packet")

HEADER_SIZE = 6
HEADER_XOR = 0x1357
SEQ_HELLO = 0xFFFF
SEQ_MAX = 0x7FFE
FLAG_ENCRYPTED = 0x01
FLAG_COMPRESSED = 0x80


def compute_checksum(payload: bytes, length: int) -> int:
    """Compute packet checksum (FUN_0081dbf0).

    Operates on plaintext (decrypted) payload.
    """
    val = 0xD31F

    # If odd length, skip last byte
    check_len = length & ~1

    for i in range(0, min(check_len, len(payload)), 2):
        word = struct.unpack_from('<H', payload, i)[0]
        val ^= word

    val &= 0xFFFF

    # Rotate left by (low nibble of val)
    shift = val & 0xF
    if shift > 0:
        val = ((val << shift) | (val >> (16 - shift))) & 0xFFFF

    # Return low_byte XOR high_byte
    return (val & 0xFF) ^ ((val >> 8) & 0xFF)


def encode_header(payload_length: int, sequence: int, flags: int, checksum: int) -> bytes:
    """Build a 6-byte obfuscated packet header."""
    header = bytearray(HEADER_SIZE)
    struct.pack_into('<H', header, 0, payload_length ^ HEADER_XOR)
    struct.pack_into('<H', header, 2, sequence ^ payload_length)
    header[4] = (flags ^ (payload_length & 0xFF)) & 0xFF
    header[5] = checksum & 0xFF
    return bytes(header)


def decode_header(raw: bytes) -> dict:
    """Decode a 6-byte obfuscated packet header.

    Returns dict with: payload_length, sequence, flags, encrypted,
    compressed, checksum, padded_length, total_length
    """
    raw_01 = struct.unpack_from('<H', raw, 0)[0]
    raw_23 = struct.unpack_from('<H', raw, 2)[0]

    payload_length = raw_01 ^ HEADER_XOR
    sequence = raw_23 ^ payload_length
    flags = raw[4] ^ (payload_length & 0xFF)
    checksum = raw[5]

    encrypted = bool(flags & FLAG_ENCRYPTED)
    compressed = bool(flags & FLAG_COMPRESSED)

    if encrypted:
        padded_length = ((payload_length + 0xF) >> 4) << 4
    else:
        padded_length = payload_length

    return {
        'payload_length': payload_length,
        'padded_length': padded_length,
        'total_length': HEADER_SIZE + padded_length,
        'sequence': sequence,
        'flags': flags,
        'encrypted': encrypted,
        'compressed': compressed,
        'checksum': checksum,
    }


class PacketBuilder:
    """Builds and sends Angels Online protocol packets."""

    def __init__(self, crypto=None):
        self.sequence = 1
        self.crypto = crypto  # CryptXOR instance or None

    def build_packet(self, payload: bytes, sequence: int = None,
                     encrypt: bool = True, compressed: bool = False) -> bytes:
        """Build a complete packet with obfuscated header.

        Args:
            payload: plaintext payload data (may be pre-compressed)
            sequence: packet sequence number (auto-increment if None)
            encrypt: whether to encrypt the payload
            compressed: set the compressed flag (bit 7) in header
        """
        if sequence is None:
            sequence = self.sequence
            self.sequence += 1
            if self.sequence > SEQ_MAX:
                self.sequence = 1

        payload_length = len(payload)
        flags = 0
        if compressed:
            flags |= FLAG_COMPRESSED

        # Compute checksum on plaintext (pre-encryption, post-compression)
        checksum = compute_checksum(payload, payload_length)

        # Encrypt if requested and crypto is available
        if encrypt and self.crypto:
            flags |= FLAG_ENCRYPTED
            padded_length = ((payload_length + 0xF) >> 4) << 4
            # Pad payload to 16-byte boundary
            padded_payload = payload + b'\x00' * (padded_length - payload_length)
            encrypted_payload = self.crypto.encrypt(padded_payload)
        else:
            encrypted_payload = payload

        header = encode_header(payload_length, sequence, flags, checksum)
        return header + encrypted_payload

    def build_hello(self, xor_key: bytes) -> bytes:
        """Build the server Hello packet (sequence 0xFFFF, unencrypted).

        Payload (22 bytes):
          Byte 0-1: 14 00 (remaining_length = 20)
          Byte 2-3: 10 00 (key_length = 16)
          Byte 4-5: 00 00 (reserved)
          Byte 6-21: 16-byte XOR key
        """
        payload = struct.pack('<HH', 20, 16) + b'\x00\x00' + xor_key
        pkt = self.build_packet(payload, sequence=SEQ_HELLO, encrypt=False)
        # Hello still consumes a sequence slot (real server does this)
        self.sequence += 1
        if self.sequence > SEQ_MAX:
            self.sequence = 1
        return pkt


class PacketFramer:
    """Reassembles TCP stream into individual game packets.

    Handles the obfuscated header to determine packet boundaries.
    """

    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data: bytes) -> list[tuple[dict, bytes]]:
        """Feed raw TCP data, return list of (header_info, raw_packet) tuples."""
        self.buffer.extend(data)
        packets = []

        while len(self.buffer) >= HEADER_SIZE:
            hdr = decode_header(bytes(self.buffer[:HEADER_SIZE]))

            if hdr['payload_length'] == 0 or hdr['payload_length'] > 0x10000:
                log.warning(f"Invalid payload length {hdr['payload_length']}, "
                           f"dropping 1 byte")
                self.buffer = self.buffer[1:]
                continue

            total = hdr['total_length']
            if len(self.buffer) < total:
                break  # Need more data

            raw_packet = bytes(self.buffer[:total])
            self.buffer = self.buffer[total:]
            packets.append((hdr, raw_packet))

        return packets


class PacketReader:
    """Reads structured data from a packet buffer."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    @property
    def remaining(self) -> int:
        return len(self.data) - self.pos

    def read_bytes(self, count: int) -> bytes:
        if self.pos + count > len(self.data):
            raise ValueError(f"Buffer underflow: need {count}, have {self.remaining}")
        result = self.data[self.pos:self.pos + count]
        self.pos += count
        return result

    def read_uint8(self) -> int:
        return struct.unpack_from("<B", self.read_bytes(1))[0]

    def read_uint16(self) -> int:
        return struct.unpack_from("<H", self.read_bytes(2))[0]

    def read_uint32(self) -> int:
        return struct.unpack_from("<I", self.read_bytes(4))[0]

    def read_string(self, length: Optional[int] = None) -> str:
        if length is None:
            length = self.read_uint16()
        raw = self.read_bytes(length)
        return raw.rstrip(b"\x00").decode("utf-8", errors="replace")

    def read_fixed_string(self, length: int) -> str:
        raw = self.read_bytes(length)
        return raw.rstrip(b"\x00").decode("utf-8", errors="replace")


class PacketWriter:
    """Builds packet payload data."""

    def __init__(self):
        self.buffer = bytearray()

    def write_bytes(self, data: bytes):
        self.buffer.extend(data)

    def write_uint8(self, value: int):
        self.buffer.extend(struct.pack("<B", value))

    def write_uint16(self, value: int):
        self.buffer.extend(struct.pack("<H", value))

    def write_uint32(self, value: int):
        self.buffer.extend(struct.pack("<I", value))

    def write_fixed_string(self, value: str, length: int):
        encoded = value.encode("utf-8")[:length]
        self.buffer.extend(encoded)
        self.buffer.extend(b"\x00" * (length - len(encoded)))

    def get_data(self) -> bytes:
        return bytes(self.buffer)
