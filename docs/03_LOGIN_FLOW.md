# Login Flow

## Overview

```
Phase 1:  S->C  Hello         (28 bytes, XOR key)
Phase 2:  C->S  Login         (encrypted, username + password hash)
Phase 3:  S->C  Login Response (encrypted + LZO compressed, MOTD + account data)
Phase 4:  C->S  PIN/Session   (encrypted, 8 bytes, acknowledged but not parsed)
Phase 5:  S->C  Redirect      (encrypted, 34 bytes, world server IP:port + token)
```

## Phase 1: Hello

Server generates a random 16-byte XOR key and sends it unencrypted.

```
Payload (22 bytes):
  [LE16 remaining_length = 20]
  [LE16 key_length = 16]
  [LE16 reserved = 0x0000]
  [16B  XOR key]

Header: sequence=0xFFFF, flags=0x00
Total wire: 28 bytes
```

After sending Hello, the server creates a `CryptXOR(key)` instance for all subsequent S->C encryption.

## Phase 2: Login (C->S)

Client sends encrypted credentials.

```
Payload structure:
  [LE16 sub_msg_length]      # Length of sub-message data
  [LE16 sub_type = 0x0002]   # Login sub-type
  [8B   username]             # Null-padded ASCII (8 bytes fixed)
  [var  password_hash]        # Remaining bytes = raw password hash

Total observed: 93 bytes
```

### Username Parsing

- Read 8 bytes, find first null byte, decode as ASCII
- Example: `70 6C 61 79 65 72 00 00` = "player"

### Password Handling

- The client sends a binary hash (not plaintext)
- Server stores it as hex string: `remaining_bytes.hex()`
- On subsequent logins, the same hash is compared
- New accounts are auto-registered with the provided hash

## Phase 3: Login Response (S->C)

LZO-compressed payload containing two sub-messages.

### Sub-Message 1: MOTD (Opcode 0x000C)

```
[LE16 opcode = 0x000C]
[LE32 flags = 0x69402978]
[LE32 text_length]
[text (ASCII) + null terminator]
```

The MOTD text uses `\n` for newlines. Displayed on the client login screen.

### Sub-Message 2: Account Data (Opcode 0x0000)

654-byte template with player-specific fields patched in:

```
Offset  Type    Field               Patched?
0       LE16    opcode (0x0000)     No (always 0x0000)
4       LE16    status              Yes (1=success, 0=fail)
6       LE32    field               No (always 3)
10-11   bytes   flags               No (0x05, 0x01)
16      LE32    account_id          Yes
36      LE32    entity_ref          No (0x0020A0C3)
40-48   9B      display_name        Yes (null-padded)
49      LE32    character_id        Yes (entity_id)
53      LE32    field               No (154)
57-60   4B      session_key         No (0x6009447E)
61-70   10B     login_name          Yes (null-padded)
86      LE32    level               Yes
90      LE32    class_id            Yes
114     LE32    hp_max              No (294)
118     LE32    mp_max              No (280)
122     LE32    stat                No (185)
126     LE32    stat                No (154)
455+    LE32x6  stats block         No
479+    LE32x6  server_ids          No ([1, 5, 6, 7, 8, 34])
```

### Assembly

```python
payload = (
    LE16(len(motd_sub)) + motd_sub +
    LE16(len(acct_sub)) + acct_sub
)
compressed = lzo.compress(payload)
packet = builder.build_packet(compressed, compressed=True)
```

### Failed Login

On bad password, send the same structure but with `status=0` and a failure MOTD.

## Phase 4: PIN/Session (C->S)

Client sends 8 bytes. Currently logged but not parsed or validated.

```
Example: 66 00 11 00 08 03 00 00
```

This appears to be a PIN or session confirmation. The server proceeds regardless.

## Phase 5: Redirect (S->C)

Tells the client to connect to the world server.

### Session Token

```python
session_token = os.urandom(4)
_pending_sessions[session_token] = entity_id
```

The 4-byte random token links the login to the world server connection. The world server calls `consume_session(token)` to retrieve the entity_id.

### Redirect Payload (34 bytes)

```
[LE16 sub_length = 32]        Offset 0-1

Sub-data (32 bytes):
  Offset 0-1:   LE16 type = 0x0004
  Offset 2-3:   LE16 padding = 0x0000
  Offset 4-7:   4B   session_token (random)
  Offset 8:     1B   flag = 0x01
  Offset 9-24:  16B  IP string (null-terminated, zero-padded)
  Offset 25-26: LE16 port (world server)
  Offset 27-31: 5B   trailing zeros
```

### Post-Redirect

After sending the redirect, the login server waits for the client to close the connection (up to 30 seconds). This is critical — closing too early causes the client to abort the world server connection.

```python
try:
    await asyncio.wait_for(reader.read(1), timeout=30.0)
except asyncio.TimeoutError:
    pass
```

## Authentication Flow

```
Client sends username + password_hash
    |
    v
database.get_account(username)
    |
    +-- None (new account) --> create_account() + create_character()
    |
    +-- Found --> verify_password()
                    |
                    +-- Bad  --> send failure response, disconnect
                    +-- Good --> get_characters_for_account()
                                    |
                                    +-- Empty --> create_character()
                                    +-- Found --> use first character
```

### New Account Defaults

| Field | Value |
|-------|-------|
| Level | 1 |
| Class | 0 (Novice) |
| HP/HP Max | 294 |
| MP/MP Max | 280 |
| Gold | 500 |
| Position | (1040, 720) |
| Map | 0 (unset, defaults to 2 on world connect) |

## Error Handling

| Error | Handling |
|-------|----------|
| Client disconnect during login | Caught, session cleaned up |
| Timeout (30s no data) | Warning logged, session closed |
| Bad password | Failure response sent (status=0), disconnect |
| Windows socket errors (64, 10053, 10054) | Treated as disconnect |
| Any other exception | Logged with full traceback |

## Timing (from captures)

```
T+0.000s  Client connects
T+0.001s  S->C Hello (28 bytes)
T+0.030s  C->S Login (~93 bytes)
T+0.015s  S->C Login Response (~342 bytes)
T+5.000s  C->S PIN/Session (8 bytes)
T+0.003s  S->C Redirect (34 bytes)
T+1.000s  Login session ends (client disconnects)
T+2.000s  Client connects to world server
```
