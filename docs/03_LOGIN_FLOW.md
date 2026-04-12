# Login flow

The login server runs at `:16768`. Its whole job is to authenticate, let the user pick/create/delete a character, and then hand the client off to the world server with a session token.

```
Phase 1   S→C  Hello                    28B, XOR key
Phase 2   C→S  Login                    ~77B, username + hash
Phase 3   S→C  Login response           compressed: MOTD + slot list
Phase 4   C→S  Slot ops (loop)          CREATE / DELETE / SELECT-preview / ENTER_WORLD
Phase 5   S→C  Redirect                 world host:port + 4B session token
          C    disconnect, reconnect to world
```

## Phase 1 — Hello (S→C) ✅

Server sends 28 unencrypted bytes:

```
Header (6B):   length=22, sequence=0xFFFF, flags=0
Payload (22B):
  00-01  LE16  0x0014    remaining length (20)
  02-03  LE16  0x0010    key length (16)
  04-05  LE16  0x0000    reserved
  06-21  16B             random XOR key (this seeds both CryptXOR and CryptXORIV)
```

## Phase 2 — Login request (C→S) ✅

First encrypted packet. Single sub-message, opcode `0x0002`:

```
[LE16 sub_len][LE16 0x0002][8B username][var password_hash]
```

- `username` is 8 bytes, null-terminated, ASCII.
- `password_hash` is the remainder of the sub-message. The client hashes the typed password (the exact algo we haven't traced but it's deterministic per-typed-string). We just store whatever bytes arrive as a hex string and compare byte-for-byte on subsequent logins.
- New usernames auto-register an account and a starter Novice character.

## Phase 3 — Login response (S→C) ✅

LZO-compressed payload with two sub-messages.

### 0x000C — MOTD

```
[LE16 0x000C][LE32 flags=0x69402978][LE32 text_len][text + NUL]
```

Shown on the login screen. Supports `\n` for linebreaks.

### 0x0000 — Account + slot list ✅

A 654-byte template (captured and replayed) with per-player fields patched in. Internally the client parses this via `sub_51B250` — we have the full decompile in our RE notes.

Top-level fields patched by [game_server.py](../src/game_server.py) `build_login_response`:

| Offset | Type | Field |
|---|---|---|
| 4 | LE16 | status (1 = OK, 0 = failure) |
| 16 | LE32 | account_id |
| 40 | 9B | display_name |
| 49 | LE32 | character_id (primary entity_id) |
| 61 | 10B | login_name |
| 86 | LE32 | level |
| 90 | LE32 | class_id |

Then a **slot block** of 147-byte per-slot structs (3 slots, slot struct layout documented in [game_server.py:98](../src/game_server.py#L98)). Each occupied slot carries the character's name, class, level, map id (currently pinned to 129), and 5 appearance bytes. Unoccupied slots are zeroed.

❓ Some fields in the template are still replayed verbatim from the capture (e.g. the stats block at offset 455, the server-id array at offset 479). They've worked every time so we haven't needed to regenerate them, but we don't know what most of the bytes mean.

### Failed login

Same sub-messages, `status = 0`, and a failure MOTD string. Client disconnects.

## Phase 4 — Slot ops (C→S, looped) ✅

After the login response, the login server enters a **slot-op loop** that handles character creation, deletion, preview, and "enter world" commit. The loop runs until the client either disconnects or sends ENTER_WORLD.

All sub-messages here are `[LE16 sub_len][LE16 opcode][body…]` and are encrypted with CryptXORIV (the evolving key). This tripped us up early on — the decrypt helper must track padded length, not plain length.

| Opcode | Name | Client fn | Body |
|---|---|---|---|
| `0x0003` | **CREATE** | `sub_4C0C50` | `[slot:1][app0..4:5][name:16][extras:35]` |
| `0x0004` | **DELETE** | `sub_4C4990` | `[slot:1][password_md5_hex:32]` |
| `0x0005` | **SELECT-preview** | `sub_4C4AC0` | `[slot:1]` — UI ping, no action |
| `0x0006` | **ENTER_WORLD** | `sub_4C4A20` | `[slot:1][flag:1][password_md5_hex:32]` |
| `0x000B` | Avatar reg | — | related to file server upload; we just log and ignore |

✅ All of these are reverse-engineered from the client decompile and empirically verified — CREATE and DELETE both work end-to-end against the real client.

### CREATE response

After CREATE, server sends a **slot-update sub-message** (opcode `0x0001`, 154 bytes) so the client's slot list reflects the new character. Without this the client hangs on the creation dialog.

The 147-byte slot struct is built by `_fill_slot_struct` in [game_server.py:127](../src/game_server.py#L127). DELETE uses the same packet with a zeroed slot struct — that's how we signal "slot now empty" to the client (no dedicated DELETE ack opcode).

### ENTER_WORLD — commit

This is the packet that ends Phase 4. The body carries the slot the user picked plus the MD5 hex of the password they typed in the confirmation dialog. We don't currently verify the password here because the account was already authenticated in Phase 2 — ❓ this is probably safe on a private server but would need to change for anything multi-user.

On receipt, the server returns the chosen character's `entity_id` and falls through to Phase 5.

## Phase 5 — Redirect (S→C) ✅

```
[LE16 sub_len=32]
  00-01  LE16  0x0004   redirect type
  02-03  LE16  0x0000   pad
  04-07  4B            session token (random, see below)
  08     u8            0x01 flag
  09-24  16B           world host as NUL-padded ASCII
  25-26  LE16          world port
  27-31  5B            zero pad
```

### Session token

```python
session_token = os.urandom(4)
_pending_sessions[session_token] = entity_id
```

4-byte random token. When the client reconnects to the world server, it sends this token in its auth packet; world server calls `consume_session(token)` to retrieve the `entity_id`.

Tokens are single-use and live in a process-local dict — if the login server restarts between redirect and world-auth, the session is lost. That's fine for a single-process setup.

### Post-redirect wait

The login server then waits for the client to close its side:

```python
try:
    await asyncio.wait_for(reader.read(1), timeout=30.0)
except asyncio.TimeoutError:
    pass
```

**Why**: if we close the socket too fast (e.g. `asyncio.sleep(1)`), the client races with its own redirect parsing and aborts. 30s gives it plenty of slack; the normal case returns in <100 ms when the client's `close()` arrives.

## New account defaults

| Field | Value | Source |
|---|---|---|
| level | 1 | |
| class_id | 0 (Novice) | |
| hp / hp_max | 294 / 294 | matches Soualz capture |
| mp / mp_max | 280 / 280 | matches Soualz capture |
| gold | 500 | |
| pos_x / pos_y | 1040 / 720 | |
| map_id | **0** (DB default) | see note ⬇ |
| app0..4 | all 0 | |

**Note on map_id**: the DB default is `0`, but [world_server.py:221](../src/world_server.py#L221) hardcodes `map_id = 129` at world-auth time regardless of what the DB says. The captured init packets are for map 129, so that's the only map we can "start" on right now. Persisted movement/zone transfers do respect the DB value for later sessions, but first-login always lands on 129. Fixing this needs a map loader — see [08_GAME_DATA.md](08_GAME_DATA.md).

## Error handling

| Error | Behavior |
|---|---|
| Client disconnect mid-login | caught, session cleaned up |
| 30s no-data timeout | warning logged, socket closed |
| Bad password hash | status=0 response, disconnect |
| WinError 64 / 10053 / 10054 | treated as clean disconnect |
| Any other exception | full traceback logged |

## Timing observed from captures

```
T+0.000   client connects
T+0.001   S→C Hello (28B)
T+0.030   C→S Login (~77B)
T+0.045   S→C Login response (~278B compressed)
T+5.000   C→S Phase-4 op (CREATE or SELECT-preview)
   ...    Phase-4 loop continues until ENTER_WORLD
T+0.003   S→C Redirect (38B total)
T+1.000   client disconnects from login
T+2.000   client connects to world
```

## Known unknowns

- ❓ **The password hash function.** We store and compare raw bytes; the client's hashing is deterministic but we haven't traced it. Don't try to generate hashes server-side — round-trip the client's bytes only.
- ❌ **What ENTER_WORLD's password byte (`body[1]` account flag) actually gates.** The client sets it based on `byte_958A38`, which we don't use.
- ❌ **Meaning of the stats and server-id blocks in the 0x0000 login-response template** (offsets 455, 479). They're replayed verbatim from the Soualz capture. Works fine — don't know why.
- ❓ **The CREATE body's trailing 35 bytes** (`[22..56]`) are labeled "equipment/extras" but we haven't mapped the individual fields. The client only sends zeros for new characters so we've never needed to parse it.
