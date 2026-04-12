# Protocol & Packet Format

## Wire Format Overview

Every packet on the wire follows this structure:

```
[6-byte obfuscated header][payload (optionally encrypted, optionally compressed)]
```

Total wire size = 6 + padded_payload_length

## Packet Header (6 Bytes)

The header is **XOR-obfuscated** (not encrypted). Each field depends on the payload length:

```
Byte 0-1 (LE16):  payload_length XOR 0x1357
Byte 2-3 (LE16):  sequence_number XOR payload_length
Byte 4:           flags XOR (payload_length & 0xFF)
Byte 5:           checksum (single byte, NOT obfuscated)
```

### Decoding

```python
HEADER_XOR = 0x1357

raw_01 = LE16(header[0:2])
raw_23 = LE16(header[2:4])

payload_length = raw_01 ^ 0x1357
sequence       = raw_23 ^ payload_length
flags          = header[4] ^ (payload_length & 0xFF)
checksum       = header[5]

encrypted  = bool(flags & 0x01)   # Bit 0
compressed = bool(flags & 0x80)   # Bit 7
```

### Encoding

```python
header[0:2] = LE16(payload_length ^ 0x1357)
header[2:4] = LE16(sequence ^ payload_length)
header[4]   = (flags ^ (payload_length & 0xFF)) & 0xFF
header[5]   = checksum
```

### Padded Length

If the encrypted flag is set, the payload is padded to a 16-byte boundary:

```python
if encrypted:
    padded_length = ((payload_length + 15) // 16) * 16
else:
    padded_length = payload_length
```

## Flags

| Bit | Mask | Name | Meaning |
|-----|------|------|---------|
| 0 | 0x01 | `FLAG_ENCRYPTED` | Payload is XOR-encrypted |
| 7 | 0x80 | `FLAG_COMPRESSED` | Payload is LZO-compressed |

## Sequence Numbers

- **Range**: 1 to 0x7FFE (32,766)
- **Wrapping**: After 0x7FFE, wraps back to 1 (0x7FFF is never used normally)
- **Special**: 0xFFFF = Hello/key exchange packet
- **Increment**: Each sent packet increments the sequence by 1
- **Per-direction**: Server and client maintain independent counters

## Checksum Algorithm

Computed on the **plaintext payload** (before encryption, after compression if applicable).

```python
def compute_checksum(payload: bytes, length: int) -> int:
    val = 0xD31F                          # Initial seed

    check_len = length & ~1               # Round down to even
    for i in range(0, check_len, 2):
        word = LE16(payload[i:i+2])
        val ^= word
    val &= 0xFFFF

    shift = val & 0xF                     # Rotate left by low nibble
    if shift > 0:
        val = ((val << shift) | (val >> (16 - shift))) & 0xFFFF

    return (val & 0xFF) ^ ((val >> 8) & 0xFF)  # XOR low and high bytes
```

Returns a single byte (0x00-0xFF). Reverse-engineered from client function `FUN_0081dbf0`.

## Encryption

Two XOR cipher variants are used, one for each direction:

### CryptXOR (Server-to-Client)

Static 16-byte repeating XOR cipher. Key does not change during the session.

```python
class CryptXOR:
    def __init__(self, key: bytes):   # key = 16 bytes
        self.key = key

    def encrypt(self, data: bytes) -> bytes:
        return bytes(data[i] ^ self.key[i % 16] for i in range(len(data)))

    decrypt = encrypt   # XOR is symmetric
```

### CryptXORIV (Client-to-Server)

Evolving 16-byte XOR cipher. After each packet, the key mutates by adding the padded payload length to each 32-bit DWORD of the key.

```python
class CryptXORIV:
    def __init__(self, key: bytes):
        self.key = bytearray(key)      # Mutable, evolves per packet

    def decrypt(self, data: bytes) -> bytes:
        result = bytes(data[i] ^ self.key[i % 16] for i in range(len(data)))
        self._update_key(len(data))    # Evolve key AFTER decrypt
        return result

    def _update_key(self, padded_len: int):
        """Add padded_len to each DWORD of the 16-byte key."""
        for i in range(0, 16, 4):
            dword = LE32(self.key[i:i+4])
            dword = (dword + padded_len) & 0xFFFFFFFF
            self.key[i:i+4] = LE32_bytes(dword)
```

### Key Exchange

The XOR key is transmitted in the **Hello packet** (unencrypted):

```
Hello payload (22 bytes):
  Byte 0-1:  LE16 = 20 (remaining length)
  Byte 2-3:  LE16 = 16 (key length)
  Byte 4-5:  0x0000 (reserved)
  Byte 6-21: 16-byte XOR key
```

Both server and client derive their crypto contexts from this same key:
- **Server sends** (CryptXOR): static key, client decrypts with same key
- **Client sends** (CryptXORIV): evolving key, server tracks evolution

## LZO Compression

Large payloads (init packets, login responses) use LZO compression:

- Library: `lzallright` (Python binding for LZO)
- The `FLAG_COMPRESSED` (0x80) bit indicates compression
- Compression is applied **before** encryption
- Checksum is computed on the **compressed** payload (what gets encrypted)
- The receiver must decrypt first, then decompress

## Sub-Message Framing

Within a payload, data is structured as concatenated sub-messages:

```
[LE16 sub_len_1][sub_data_1][LE16 sub_len_2][sub_data_2]...
```

Each sub-message starts with its opcode:

```
sub_data = [LE16 opcode][opcode-specific fields...]
```

Helper functions:

```python
def pack_sub(data: bytes) -> bytes:
    """Wrap one sub-message with its length prefix."""
    return struct.pack('<H', len(data)) + data

def assemble_payload(sub_messages: list[bytes]) -> bytes:
    """Concatenate multiple sub-messages with length prefixes."""
    return b''.join(struct.pack('<H', len(m)) + m for m in sub_messages)
```

## TCP Framing (PacketFramer)

The `PacketFramer` reassembles TCP stream fragments into complete packets:

1. Accumulate received bytes in a buffer
2. When buffer >= 6 bytes, decode header to get `total_length`
3. If buffer >= total_length, extract complete packet
4. Repeat until buffer is too short

**Validation**:
- `payload_length == 0`: Invalid, drop 1 byte and resync
- `payload_length > 0x10000` (65536): Invalid, drop 1 byte and resync
- These guards handle stream desynchronization

## Hello Packet (28 Bytes)

The first packet in every connection. Sent by server, unencrypted.

```
Header (6 bytes):
  Sequence = 0xFFFF (special)
  Flags = 0x00 (no encryption, no compression)

Payload (22 bytes):
  Offset 0-1:  LE16 = 0x0014 (20, remaining length)
  Offset 2-3:  LE16 = 0x0010 (16, key length)
  Offset 4-5:  LE16 = 0x0000 (reserved)
  Offset 6-21: 16-byte XOR key (random, generated per session)

Total wire: 28 bytes
```

## Complete Packet Processing Pipeline

### Sending (Server -> Client)

```
1. Build plaintext payload (sub-messages)
2. If compressing: LZO compress the payload
3. Compute checksum on (compressed) plaintext
4. Set flags (encrypted=0x01, compressed=0x80)
5. Pad payload to 16-byte boundary (if encrypting)
6. Encrypt padded payload with CryptXOR
7. Encode 6-byte header (XOR obfuscation)
8. Send: header + encrypted_payload
```

### Receiving (Client -> Server)

```
1. Accumulate TCP bytes in PacketFramer buffer
2. Decode 6-byte header (XOR deobfuscation)
3. Wait for full packet (header.total_length bytes)
4. Extract padded payload from after header
5. Decrypt with CryptXORIV (key evolves after each call)
6. Trim to actual payload_length (remove padding)
7. If compressed: LZO decompress
8. Parse sub-messages from payload
```
