# Protocol & packet format

Everything in this file is ✅ **confirmed** unless a paragraph says otherwise. It's all been reverse-engineered from the client binary (`FUN_0081dbf0` et al.) and verified against live captures.

## Wire format

```
[ 6-byte obfuscated header ][ payload (optionally compressed, optionally encrypted) ]
```

`total_wire_size = 6 + padded_payload_length`

## Header (6 bytes) ✅

The header is **XOR-obfuscated**, not encrypted. Each field's deobfuscation uses the payload length as part of the key, which is a neat way to force the decoder to parse fields in order.

```
off  size  field        deobfuscation
───  ────  ──────────   ─────────────────────────────────
0-1  LE16  length       ^ 0x1357
2-3  LE16  sequence     ^ length
4    u8    flags        ^ (length & 0xFF)
5    u8    checksum     plain (not obfuscated)
```

```python
HEADER_XOR = 0x1357
length   = LE16(hdr[0:2]) ^ 0x1357
sequence = LE16(hdr[2:4]) ^ length
flags    = hdr[4]         ^ (length & 0xFF)
checksum = hdr[5]
```

Encoding is the same XOR pattern in reverse.

### Flags ✅

| Bit | Mask | Meaning |
|---|---|---|
| 0 | `0x01` | payload is encrypted |
| 7 | `0x80` | payload is LZO-compressed |

Other bits have never been observed set; treat them as reserved.

### Padded length ✅

If the encrypted flag is set, the payload is padded up to the next 16-byte boundary before encryption:

```python
padded_length = ((length + 15) // 16) * 16 if encrypted else length
```

The header carries the *unpadded* length. The padding bytes are whatever the XOR cipher emits for zero input — the receiver trims them by reading only `length` bytes post-decrypt.

### Sequence numbers ✅

- Range 1 … 0x7FFE. After 0x7FFE it wraps back to 1.
- `0xFFFF` is reserved for the **Hello** handshake.
- Server and client keep **independent** counters, one per direction.
- Any packet we've observed with out-of-range sequence was a framing desync, not a valid packet.

### Checksum ✅

Single byte. Computed on the plaintext *after* compression but *before* encryption:

```python
def compute_checksum(payload: bytes, length: int) -> int:
    val = 0xD31F
    check_len = length & ~1          # round down to even
    for i in range(0, check_len, 2):
        val ^= LE16(payload[i:i+2])
    val &= 0xFFFF
    shift = val & 0xF                # rotate left by low nibble
    if shift:
        val = ((val << shift) | (val >> (16 - shift))) & 0xFFFF
    return (val & 0xFF) ^ ((val >> 8) & 0xFF)
```

Ported from client `FUN_0081dbf0`.

## Crypto

Two cipher variants, one per direction. Both seed from the same 16-byte key received in the Hello packet.

### CryptXOR — server → client ✅

Stateless repeating-XOR against the 16-byte key. Never mutates. See [crypto.py](../src/crypto.py).

### CryptXORIV — client → server ✅

Stateful. After each decrypt, the key mutates by adding the **padded** payload length to each of the four 32-bit DWORDs in the key:

```python
def _update_key(self, padded_len: int):
    for i in range(0, 16, 4):
        dword = (LE32(self.key[i:i+4]) + padded_len) & 0xFFFFFFFF
        self.key[i:i+4] = to_le32_bytes(dword)
```

**Gotcha**: you must track `padded_len`, not raw `length`, or the key diverges on the first encrypted packet. This cost us several hours during phase-4 implementation.

## LZO compression ✅

- Library: `lzallright` (Python binding).
- Ordering: **compress, then encrypt**. Receiver reverses: decrypt, then decompress.
- Checksum is computed on the *compressed* bytes (the thing that gets encrypted).
- Used for large payloads: login response, world init packets 1–4.

## Sub-message framing ✅

Within a payload, multiple "sub-messages" are concatenated:

```
[LE16 sub_len_1][sub_1]  [LE16 sub_len_2][sub_2]  ...
```

Each sub-message starts with its own LE16 opcode:

```
sub = [LE16 opcode][opcode-specific body…]
```

Helpers in [packet_builders.py](../src/packet_builders.py):

```python
pack_sub(data)          # prepend LE16 length
assemble_payload(subs)  # concat multiple with length prefixes
```

Single-sub packets still go through `pack_sub`. Don't hand-emit the prefix — we got burned by that when adding 0x0042.

## TCP framing ✅

[packet.py](../src/packet.py) `PacketFramer` reassembles stream fragments:

1. Accumulate bytes in a buffer.
2. Once ≥ 6 bytes, deobfuscate the header to get `total_length = 6 + padded_payload_length`.
3. Once the buffer has ≥ `total_length` bytes, cut the packet out and yield it.
4. Loop.

**Resync guards**: a decoded `payload_length` of 0 or >65536 is treated as a desync — drop one byte and retry. This has never triggered in normal play; only during fuzzing.

## Hello packet (28 bytes) ✅

The first packet on every fresh connection. Sent by server, unencrypted, sequence `0xFFFF`:

```
Header (6B):  length=22, sequence=0xFFFF, flags=0, checksum=computed
Payload (22B):
  00-01  LE16 = 0x0014  (20, remaining length)
  02-03  LE16 = 0x0010  (16, key length)
  04-05  LE16 = 0x0000  (reserved)
  06-21  16B           random XOR key
```

Both sides seed their crypto contexts from the same 16-byte key.

## Send / receive pipelines ✅

### Send (S → C)

```
1. Build plaintext payload (concatenated sub-messages)
2. If compressing: LZO compress
3. Compute checksum on compressed plaintext
4. Set flags (0x01 encrypted, 0x80 compressed)
5. Pad to 16-byte boundary (if encrypted)
6. CryptXOR encrypt
7. Build header (with XOR obfuscation)
8. Send header + ciphertext
```

### Receive (C → S)

```
1. PacketFramer buffers bytes
2. Deobfuscate header
3. Read `padded_payload_length` bytes of ciphertext
4. CryptXORIV decrypt (key evolves after this call!)
5. Trim to `length` (drops pad)
6. If compressed: LZO decompress
7. Parse sub-messages
```

## Known unknowns

- ❌ **Some flags bits besides 0x01 / 0x80**. Never observed set. Could be reserved, could be optional features we've never hit.
- ❌ **Why sequence 0x7FFF is skipped**. The client code avoids it but the reason isn't obvious from the decompile.
- ❌ **Any non-CryptXOR cipher path**. The client has code we haven't exercised — Hello is always XOR, but there's a branch in `FUN_0081ec20` we haven't traced.
