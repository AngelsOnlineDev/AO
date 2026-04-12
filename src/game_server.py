"""
Angels Online Login Server
============================
Handles the login protocol flow:
  1. S->C: Hello (28 bytes, sends XOR key)
  2. C->S: Login (credentials)
  3. S->C: Login Response (MOTD + account data, LZO-compressed)
  4. C->S: PIN/Session confirmation
  5. S->C: Redirect (34 bytes, game world IP:port)

Protocol uses 6-byte XOR-obfuscated header (see packet.py for details).
"""

import asyncio
import os
import struct
import logging
import socket

from lzallright import LZOCompressor

import config
from crypto import CryptXOR, CryptXORIV
from packet import (
    PacketBuilder, PacketFramer, PacketReader,
    decode_header, HEADER_SIZE, SEQ_HELLO,
)

log = logging.getLogger("login_server")

_lzo = LZOCompressor()

import database

# Pending session tokens: maps 4-byte token -> entity_id
# Populated by login server, consumed by world server
_pending_sessions: dict[bytes, int] = {}


def consume_session(token: bytes) -> int | None:
    """Pop and return the entity_id for a session token, or None."""
    return _pending_sessions.pop(token, None)


# Default starting stats for new Novice characters
_NEW_CHAR_DEFAULTS = {
    'level': 1,
    'class_id': 0,     # Novice
    'hp': 294, 'hp_max': 294,
    'mp': 280, 'mp_max': 280,
    'gold': 500,
    'pos_x': 1040, 'pos_y': 720,
    'map_id': 3,
}


# ============================================================================
# Login Response Builder
#
# The login response is an LZO-compressed payload containing 2 sub-messages:
#   1. MOTD (opcode 0x000C): text message displayed on login screen
#   2. Account data (opcode 0x0000): character info, stats, server list
# ============================================================================

MOTD_TEXT = (
    r"\n Dear Angels,"
    r"\n \n Thank you for your continued support and companionship"
    r" in Angels Online.\n\n After thorough consideration within our"
    r" team and company, we have made the difficult decision to end"
    r" the operation of the game.\n\n Please carefully read the"
    r" following details regarding the service termination schedule"
    r" and related arrangements:\n\n \n 1. Service Termination"
    r" Schedule\n\n Termination of Top-up Services: Top-up services"
    r" will end on December 31, 2025.\n\n Closure of New Account"
    r" Registration: New account creation will no longer be accepted"
    r" starting on the same date.\n\n Official Termination of Game"
    r" Service / Server Shutdown: All servers are scheduled to shut"
    r" down on February 27, 2026 at 12:00 AM (GMT-5).\n\n \n 2."
    r" After Service Termination\n Once the servers are closed,"
    r" players will no longer be able to log in or access any"
    r" in-game features.\n \n 3. Our Gratitude and Future Outlook\n"
    r"  Since the launch of Angels Online, it has been our greatest"
    r" honor to journey together with all of you.\n\n However, due"
    r" to various operational considerations, we have been left with"
    r" no choice but to make this regrettable decision.\n\n We"
    r" sincerely thank every Angel for your passion, support, and"
    r" the many years of companionship and feedback.\n\n Should"
    r" there be any future plans related to Angels Online, we truly"
    r" hope to meet you once again in the lands of Eden.\n\n  Thank"
    r" you once again for your understanding and support. We wish"
    r" you all the best in the future.\n"
)

# ============================================================================
# Account data sub-message layout (opcode 0x0000)
#
# Reverse-engineered from client parser sub_4C4C60.
# The client copies 144 bytes from sub-msg offset 4 into a per-slot
# character struct (base 0x958C48, 147 bytes/slot, 3 slots).
#
# Sub-message layout:
#   [0-1]    LE16   opcode (0x0000)
#   [2-3]    LE16   error code (0=success, non-zero=string ID to show)
#   --- character data block (copied to struct) ---
#   [4]      byte   slot index (0-2)
#   [5-8]    LE32   level
#   [9]      byte   class_id
#   [10-14]  bytes  appearance (gender, hair, face, model, etc.)
#   [15-18]  LE32   stage_id (1-1000, zone lookup — crash if 0!)
#   [19-22]  LE32   flags
#   [23-38]  bytes  misc fields
#   [39-74]  str    character_name (null-padded, 36 bytes)
#   [75-80]  bytes  unknown
#   [81-112] LE32×8 equipment slot IDs
#   [113-116]LE32   hp
#   [117-120]LE32   unknown
#   [121-124]LE32   mp
#   [125-147]bytes  more fields
#   --- end of 144-byte copy ---
#   [148-150]bytes  extra char fields (copied separately)
#   [151-153]bytes  appearance flags (stored in separate arrays)
#   [154+]          account-level data (not fully mapped)
# ============================================================================
_SLOT_MSG_SIZE = 154  # client reads sub-message offsets 0-153


def _fill_slot_struct(buf: bytearray, slot_base: int, slot_idx: int, player):
    """Write the per-slot character struct into `buf` at `slot_base`.

    Layout within the 147-byte slot (offsets relative to slot_base):
      [+0]      slot index (byte_958C48 — first byte of slot struct;
                sub_4C4C60 reads this from the CREATE-response packet to
                determine which slot to update)
      [+5]      class_id (byte_958C4D)
      [+6..10]  appearance bytes (byte_958C4E..52) — drive the 3D model
                shown in the character-select rotating view
      [+11..14] stage_id LE32 (dword_958C53) — CRITICAL for map loading
      [+31..34] slot_occupancy LE32 (dword_958C67) — non-zero = slot
                has a character; clicking selects instead of creating
      [+35+]    in-struct name (byte_958C6B), null-terminated, up to ~30B
    """
    buf[slot_base + 0] = slot_idx & 0xFF
    buf[slot_base + 5] = player['class_id'] & 0xFF
    # Appearance — use stored bytes if present (new DB rows), else zero.
    # sqlite3.Row.keys() lets us probe without KeyError on legacy rows.
    keys = player.keys() if hasattr(player, 'keys') else []
    for i in range(5):
        col = f'app{i}'
        buf[slot_base + 6 + i] = (player[col] & 0xFF) if col in keys else 0
    struct.pack_into('<I', buf, slot_base + 11, 129)  # stage_id (map 129)
    struct.pack_into('<I', buf, slot_base + 31,
                     player['entity_id'] & 0xFFFFFFFF)
    name_short = player['name'].encode('ascii', errors='replace')[:16]
    buf[slot_base + 35:slot_base + 35 + len(name_short)] = name_short
    buf[slot_base + 35 + len(name_short)] = 0


def build_slot_update(slot: int, player) -> bytes:
    """Build a slot-update sub-message (opcode 0x0001).

    Parsed by client sub_4C4C60, which unconditionally copies 144 bytes
    from sub-msg offset 4 into unk_958C48[147*slot], then re-renders all
    slots and auto-selects the slot whose index is at offset 4.

    Layout (154 bytes total):
      [0-1]      opcode 0x0001
      [2-3]      error code (0 = success; non-zero triggers error popup)
      [4]        slot index (also first byte of the slot struct)
      [5..147]   remaining 143 bytes of the 147-byte slot struct
      [148..150] final 3 bytes of the slot struct
      [151]      flag → byte_958E08[32*slot]
      [152]      flag → byte_958E09[32*slot]
      [153]      flag → byte_958E0A[32*slot]

    If `player` is None, writes a zeroed slot struct except for the slot
    index byte — this is how we signal DELETE to the client, because an
    empty in-struct name makes sub_4C5A70 treat the slot as empty.
    """
    sub = bytearray(_SLOT_MSG_SIZE)
    struct.pack_into('<H', sub, 0, 0x0001)  # opcode
    struct.pack_into('<H', sub, 2, 0)       # error = success
    sub[4] = slot & 0xFF
    if player is not None:
        _fill_slot_struct(sub, slot_base=4, slot_idx=slot, player=player)
    return struct.pack('<H', len(sub)) + bytes(sub)


def _pack_fixed_str(value: str, length: int) -> bytes:
    """Encode a string into a fixed-length null-padded field."""
    encoded = value.encode('ascii')[:length]
    return encoded + b'\x00' * (length - len(encoded))


def build_login_response(
    motd: str = MOTD_TEXT,
    characters: list | None = None,
    login_name: str = "player",
    account_id: int = 41,
    status: int = 1,
) -> bytes:
    """Build the login response payload (LZO-compressed).

    Returns compressed bytes ready to pass to PacketBuilder.build_packet()
    with compressed=True.
    """
    # Sub-message 1: MOTD
    # Format: [LE16 opcode=0x000C][LE32 flags][LE32 text_len][text + null]
    motd_bytes = motd.encode('ascii') + b'\x00'
    motd_sub = (
        struct.pack('<H', 0x000C)
        + struct.pack('<I', 0x69402978)
        + struct.pack('<I', len(motd_bytes))
        + motd_bytes
    )

    # Account data sub-message (parsed by client sub_51B250).
    #
    # Full layout of the 0x0000 sub-message payload (bytes after the
    # [sub_len:2][opcode:2] header, so the `acct` bytearray starts here):
    #
    #   [0-1]      opcode 0x0000 (written by pack_sub wrapper)
    #   [2-3]      error code (0 = success)
    #   [4]        account flag (byte_958A39) — MUST be non-zero
    #   [5-445]    character structs (3 × 147 bytes) — copied via memcpy
    #              into unk_958C48 as a single 0x1B9-byte block
    #   [446-448]  byte_958E08[i] — per-slot flag byte (i=0..2)
    #   [449-451]  byte_958E09[i] — per-slot flag byte
    #   [452-454]  byte_958E0A[i] — per-slot flag byte
    #   [455-466]  dword_958E0C[i] — per-slot LE32 (3 values)
    #   [467-478]  dword_958E10[i] — per-slot LE32 (3 values)
    #   [479-586]  equipment arrays — 36 bytes/slot × 3 = 9 LE32 item IDs
    #              per slot. Drives class-icon derivation via sub_71C530.
    #   [587-590]  dword_9135B0 max character slots (LE32)
    #   [591-594]  server_info index (LE32, passed to sub_51AAB0)
    #   [595-645]  byte_958B90 — per-slot secondary names (17B × 3)
    #   [646+]     per-server level/requirement array (LE32 entries)
    #
    # Per-slot character struct layout (147 bytes, offset 5 + 147*i):
    #   [+5]       class_id (byte — byte_958C4D)
    #   [+6]       appearance byte 1 (byte_958C4E)
    #   [+7]       appearance byte 2 (byte_958C4F)
    #   [+8..+10]  more appearance bytes
    #   [+11..14]  stage_id (dword_958C53) — CRITICAL for map loading
    #   [+15..18]  flags (dword_958C57)
    #   [+31..34]  slot_occupancy / character_id (dword_958C67) — non-zero =
    #              slot has an existing character. Clicking the slot selects
    #              it instead of opening the creation dialog.
    #   [+35+]     in-struct name (byte_958C6B) — used by the avatar loader
    #              ("%02d_<name>.png") and by the "slot has character" check
    #
    # Buffer size is 654 bytes — just enough for all per-slot fields and the
    # secondary name array. The per-server level array at offset 646+ is only
    # read by sub_51AAB0 when a specific server entry is selected, so we can
    # leave it empty for now.
    acct = bytearray(654)
    if not status:
        struct.pack_into('<H', acct, 2, 1)
    acct[4] = 1  # account flag — MUST be non-zero
    struct.pack_into('<I', acct, 587, 3)  # 3 slots unlocked

    # Populate character slots from the DB list
    chars = characters or []
    for slot_idx in range(3):
        if slot_idx >= len(chars) or chars[slot_idx] is None:
            continue  # empty slot — creation available
        player = chars[slot_idx]
        _fill_slot_struct(acct, slot_base=5 + slot_idx * 147,
                          slot_idx=slot_idx, player=player)
        # Secondary name array at sub-msg offset 595 + 17*i. sub_51B250
        # reads this separately into byte_958B90 for the UI label.
        name_short = player['name'].encode('ascii', errors='replace')[:16]
        name_base = 595 + slot_idx * 17
        acct[name_base:name_base + len(name_short)] = name_short

    # Per-slot fields at 446-478 and 479+36*i are left zero — sub_51B250
    # only populates those globals from the packet when the slot-occupancy
    # DWORD is non-zero, and zero values degrade to a default class icon
    # via sub_71C530.

    payload = (
        struct.pack('<H', len(motd_sub)) + motd_sub
        + struct.pack('<H', len(acct)) + bytes(acct)
    )

    return _lzo.compress(payload)


class LoginServer:
    """Login server for Angels Online."""

    def __init__(self, host: str, port: int,
                 world_host: str = "127.0.0.1", world_port: int = 27901):
        self.host = host
        self.port = port
        self.world_host = world_host
        self.world_port = world_port

    async def start(self):
        server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        log.info(f"Login server listening on {self.host}:{self.port}")
        log.info(f"Redirecting clients to {self.world_host}:{self.world_port}")
        async with server:
            await server.serve_forever()

    async def _handle_client(self, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter):
        addr = writer.get_extra_info("peername")
        log.info(f"New connection from {addr}")

        # TCP_NODELAY
        sock = writer.get_extra_info("socket")
        if sock:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        try:
            await self._login_flow(reader, writer, addr)
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError) as e:
            log.info(f"[{addr}] Disconnected: {e}")
        except asyncio.IncompleteReadError as e:
            log.info(f"[{addr}] Client disconnected (got {len(e.partial)} bytes)")
        except asyncio.TimeoutError:
            log.warning(f"[{addr}] Timeout")
        except OSError as e:
            _WINDOWS_DISCONNECT = (64, 10053, 10054)
            if hasattr(e, 'winerror') and e.winerror in _WINDOWS_DISCONNECT:
                log.info(f"[{addr}] Disconnected: {e}")
            else:
                log.exception(f"[{addr}] Error: {e}")
        except Exception as e:
            log.exception(f"[{addr}] Error: {e}")
        finally:
            if not writer.is_closing():
                writer.close()
            log.info(f"[{addr}] Session ended")

    async def _login_flow(self, reader: asyncio.StreamReader,
                          writer: asyncio.StreamWriter, addr):
        """Execute the full login handshake."""

        # --- Phase 1: Send Hello ---
        # Server uses plain CryptXOR for sending (S→C), but the client
        # encrypts C→S with the stateful CryptXORIV variant — its key
        # mutates after each packet sent. We need a separate decryptor
        # that mirrors that behaviour, otherwise Phase 4 decryption
        # corrupts the name-hash bytes.
        xor_key = CryptXOR.generate_key()
        crypto = CryptXOR(xor_key)
        crypto_recv = CryptXORIV(xor_key)
        builder = PacketBuilder(crypto)

        hello = builder.build_hello(xor_key)
        writer.write(hello)
        await writer.drain()
        log.info(f"[{addr}] Sent Hello ({len(hello)} bytes), "
                 f"key={xor_key.hex(' ')}")

        # --- Phase 2: Receive Login ---
        framer = PacketFramer()
        login_payload = await self._receive_packet(reader, framer, crypto_recv, addr)
        if login_payload is None:
            return

        # Parse login sub-message
        username, password = self._parse_login(login_payload, addr)
        if username is None:
            return

        log.info(f"[{addr}] Login: username='{username}'")

        # --- Phase 3: Authenticate or create account, then send Login Response ---
        account = database.get_account(username)
        if account is None:
            # New account: auto-register with the provided password
            account = database.create_account(username, password)
            log.info(f"[{addr}] Created new account '{username}' (id={account['id']})")
        else:
            # Existing account: verify password
            if not database.verify_password(account, password):
                log.warning(f"[{addr}] Bad password for '{username}'")
                fail_resp = build_login_response(
                    motd=r"\n Invalid username or password.\n",
                    login_name=username,
                    status=0,
                )
                fail_pkt = builder.build_packet(fail_resp, compressed=True)
                writer.write(fail_pkt)
                await writer.drain()
                return
            log.info(f"[{addr}] Authenticated account '{username}' (id={account['id']})")

        # Get existing character(s) for this account. If none, leave the
        # character slots empty so the client shows the creation dialog.
        characters = database.get_characters_for_account(account['id'])
        log.info(f"[{addr}] Account has {len(characters)} character(s)")
        for c in characters:
            log.info(f"[{addr}]   - '{c['name']}' (class={c['class_id']}, "
                     f"level={c['level']}, entity=0x{c['entity_id']:08X})")

        login_resp = build_login_response(
            motd=config.LOGIN_MOTD,
            characters=list(characters),
            login_name=username,
            account_id=account['id'],
        )
        resp_packet = builder.build_packet(login_resp, compressed=True)
        writer.write(resp_packet)
        await writer.drain()
        log.info(f"[{addr}] Sent Login Response ({len(resp_packet)} bytes)")

        # --- Phase 4: Handle post-login packets (character creation, select) ---
        # Confirmed from IDA disassembly of the client (ANGEL.DAT):
        #   sub_4C4AC0 (select):  sub_len=3, opcode=0x0005, body=[slot:1]
        #   sub_4C4A20 (create):  sub_len=37, opcode=0x0006,
        #                          body=[slot:1][account_flag:1][32B data]
        #                          where 32B is from unk_958A3C (create
        #                          dialog buffer — contains name + appearance)
        #   sub_4C4990 (delete):  sub_len=36, opcode=0x0024, body=[slot:1][32B]
        #   sub_4C4B40 / 4BD0:    sub_len=13/14, opcode=0x0014, rename ops
        # Loop processing packets until we see a select/create that commits
        # the player to entering the world, then send redirect.
        player_entity_id = await self._handle_character_phase(
            reader, writer, framer, crypto_recv, builder, addr,
            account, list(characters))
        if player_entity_id == 0:
            return  # client disconnected during character phase

        # --- Phase 5: Send Redirect ---
        redirect_payload = self._build_redirect(player_entity_id)
        redirect_packet = builder.build_packet(redirect_payload)
        writer.write(redirect_packet)
        await writer.drain()
        log.info(f"[{addr}] Sent Redirect -> {self.world_host}:{self.world_port}")

        # Wait for the client to close the login connection after redirect.
        # The client needs ~2s to process the redirect and connect to the
        # world server; closing early causes it to abort the flow.
        try:
            await asyncio.wait_for(reader.read(1), timeout=30.0)
        except asyncio.TimeoutError:
            pass

    async def _handle_character_phase(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        framer: PacketFramer,
        crypto: CryptXOR,
        builder: PacketBuilder,
        addr,
        account,
        characters: list,
    ) -> int:
        """Process post-login packets until the client commits to entering
        the world.

        Opcodes (confirmed from IDA decompile of the LIVE client):
          0x0003 + sub_len=59   CREATE  (sub_4C0C50) — body layout:
              [0]      slot index
              [1..5]   appearance bytes (from byte_958C4E..52)
              [6..21]  null-padded name (16 bytes)
              [22..56] equipment / extra fields
          0x0003 + sub_len<59   generic ACK (no action)
          0x0005 + sub_len=3    SELECT  (sub_4C4AC0) — body=[slot:1]
          0x0024                DELETE  (sub_4C4990)

        Returns the entity_id of the character the player is using, or 0 if
        the client disconnected before committing.
        """
        while True:
            payload = await self._receive_packet(reader, framer, crypto, addr)
            if payload is None:
                return 0  # disconnect

            if len(payload) < 4:
                continue

            # Sub-message frame: [sub_len:2][opcode:2][body...]
            sub_len = struct.unpack_from('<H', payload, 0)[0]
            opcode = struct.unpack_from('<H', payload, 2)[0]
            body = payload[4:4 + max(0, sub_len - 2)]

            log.info(f"[{addr}] Phase4 pkt: opcode=0x{opcode:04X} "
                     f"sub_len={sub_len} body={body[:40].hex(' ')}")

            if opcode == 0x0003 and sub_len >= 20:
                # Character CREATE. sub_4C0C50 writes:
                #   body[0]      slot index
                #   body[1..5]   appearance bytes (byte_958C4E..52)
                #   body[6..21]  null-padded name (16 bytes)
                #   body[22..56] equipment / extra fields (partly unmapped)
                if len(body) < 22:
                    log.warning(f"[{addr}] CREATE body too short ({len(body)}B)")
                    continue
                slot = body[0]
                appearance = tuple(body[1:6])
                name_bytes = body[6:22].split(b'\x00', 1)[0]
                try:
                    new_name = name_bytes.decode('ascii')
                except UnicodeDecodeError:
                    log.warning(f"[{addr}] CREATE name not ASCII: "
                                f"{name_bytes.hex(' ')}")
                    continue
                if not new_name:
                    log.warning(f"[{addr}] CREATE name was empty")
                    continue
                log.info(f"[{addr}] Character CREATE slot={slot} "
                         f"name='{new_name}' appearance={appearance}")
                new_player = database.create_character(
                    account_id=account['id'],
                    name=new_name,
                    class_id=0,
                    appearance=appearance,
                )
                log.info(f"[{addr}] Created '{new_player['name']}' "
                         f"(entity=0x{new_player['entity_id']:08X})")
                # Pad list so characters[slot] points at the new row.
                while len(characters) <= slot:
                    characters.append(None)
                characters[slot] = new_player

                # Send CREATE RESPONSE (opcode 0x0001, parsed by client
                # sub_4C4C60). Without this the client is stuck waiting
                # on the creation dialog and never sends SELECT.
                resp_payload = build_slot_update(slot, new_player)
                resp_pkt = builder.build_packet(resp_payload)
                writer.write(resp_pkt)
                await writer.drain()
                log.info(f"[{addr}] Sent CREATE RESPONSE for slot={slot}")
                # Client sends a SELECT next — keep looping.

            elif opcode == 0x0006:
                # ENTER WORLD (sub_4C4A20). Sent when the user clicks the
                # "Start Game" button, password dialog confirms. Body:
                #   [0]       slot index
                #   [1]       account flag (byte_958A38, unused here)
                #   [2..33]   32-byte ASCII MD5 hex of the password the
                #             user typed in the enter-world dialog
                # This is the commit — return the entity_id of the
                # chosen slot so Phase 5 sends the redirect.
                slot = body[0] if len(body) >= 1 else 0
                if 0 <= slot < len(characters) and characters[slot] is not None:
                    chosen = characters[slot]
                    log.info(f"[{addr}] ENTER WORLD slot={slot} → "
                             f"'{chosen['name']}' (entity=0x{chosen['entity_id']:08X})")
                    return chosen['entity_id']
                log.warning(f"[{addr}] ENTER WORLD slot={slot} invalid "
                            f"(have {len(characters)} chars)")
                return 0

            elif opcode == 0x0005:
                # Slot preview (sub_4C4AC0) — the client sends this when
                # the user clicks a slot in the selection screen. Purely
                # a UI ping; no server action needed.
                slot = body[0] if len(body) >= 1 else 0
                log.debug(f"[{addr}] Slot preview slot={slot}")

            elif opcode == 0x000B:
                # Avatar file registration — the client's file-server
                # upload handshake references the new character's PNG
                # ("16_<name>.png"). Nothing to do on the login server.
                log.debug(f"[{addr}] C→S 0x000B avatar registration")

            elif opcode == 0x0004:
                # Character DELETE (sub_4C4990). Body layout:
                #   [0]       slot index
                #   [1..32]   32-byte ASCII MD5 hex of the password the
                #             user typed into the confirmation dialog
                # For now we skip password verification — the account is
                # already authenticated, so delete any character in the
                # specified slot.
                slot = body[0] if len(body) >= 1 else 0
                if not (0 <= slot < len(characters)) or characters[slot] is None:
                    log.warning(f"[{addr}] DELETE slot={slot} has no character")
                    continue
                target = characters[slot]
                log.info(f"[{addr}] Character DELETE slot={slot} → "
                         f"'{target['name']}' (entity=0x{target['entity_id']:08X})")
                database.delete_character(target['entity_id'])
                characters[slot] = None

                # Send a slot-update packet (opcode 0x0001) with a zeroed
                # slot struct. sub_4C4C60 will memcpy the zeros into the
                # slot's global struct, and when sub_4C5A70 re-renders
                # it'll see an empty name at byte_958C6B[147*slot] and
                # early-exit — visually deleting the slot. This avoids
                # resending the full login response (opcode 0x0000), which
                # the client doesn't handle mid-session.
                resp_payload = build_slot_update(slot, player=None)
                resp_pkt = builder.build_packet(resp_payload)
                writer.write(resp_pkt)
                await writer.drain()
                log.info(f"[{addr}] Sent slot-update (clear) for slot={slot}")

            elif opcode == 0x0003:
                log.debug(f"[{addr}] C→S 0x0003 ACK (ignored)")

            else:
                log.info(f"[{addr}] Unhandled Phase4 opcode 0x{opcode:04X} "
                         f"sub_len={sub_len}")

    async def _receive_packet(self, reader: asyncio.StreamReader,
                              framer: PacketFramer, crypto: CryptXOR,
                              addr) -> bytes | None:
        """Receive and decode one packet from the client.

        Returns the decrypted payload, or None on disconnect/error.
        """
        while True:
            data = await asyncio.wait_for(reader.read(65536), timeout=30.0)
            if not data:
                log.info(f"[{addr}] Client disconnected")
                return None

            packets = framer.feed(data)
            for hdr, raw_packet in packets:
                log.debug(f"[{addr}] Received packet: len={hdr['payload_length']}, "
                         f"seq={hdr['sequence']}, flags=0x{hdr['flags']:02x}")

                payload_raw = raw_packet[HEADER_SIZE:HEADER_SIZE + hdr['padded_length']]

                if hdr['encrypted'] and crypto:
                    payload = crypto.decrypt(payload_raw)
                else:
                    payload = payload_raw

                # Trim to actual length (remove padding)
                payload = payload[:hdr['payload_length']]

                log.debug(f"[{addr}] Payload ({len(payload)} bytes): "
                         f"{payload[:64].hex(' ')}")
                return payload

    def _parse_login(self, payload: bytes, addr) -> tuple[str | None, str]:
        """Parse login packet payload. Returns (username, password) or (None, '')."""
        if len(payload) < 10:
            log.warning(f"[{addr}] Login payload too short: {len(payload)}")
            return None, ''

        # Payload structure: [LE16 sub_msg_len][sub_msg_data]
        # Sub-message: [02 00][username(8)][password_hash...]
        reader = PacketReader(payload)
        sub_len = reader.read_uint16()

        if sub_len + 2 != len(payload):
            log.warning(f"[{addr}] Sub-message length mismatch: "
                       f"{sub_len}+2 != {len(payload)}")

        # Sub-message content
        sub_type = reader.read_uint16()  # 0x0002
        log.debug(f"[{addr}] Login sub-type: 0x{sub_type:04x}")

        # Username: 8 bytes null-padded
        username_raw = reader.read_bytes(8)
        null_idx = username_raw.find(0)
        if null_idx >= 0:
            username = username_raw[:null_idx].decode('ascii', errors='replace')
        else:
            username = username_raw.decode('ascii', errors='replace')

        # Password: remaining bytes after username
        # The client sends [13 bytes session metadata][password hash].
        # The first 13 bytes vary per session (counters/nonce) and must be
        # skipped — only the fixed hash portion is used as the password key.
        remaining = payload[reader.pos:]
        CRED_SKIP = 13
        if len(remaining) > CRED_SKIP:
            password = remaining[CRED_SKIP:].hex()
        else:
            password = remaining.hex() if remaining else username
        log.debug(f"[{addr}] Credential bytes ({len(remaining)}): "
                  f"session={remaining[:CRED_SKIP].hex(' ')} "
                  f"hash={remaining[CRED_SKIP:].hex(' ')}")

        return username, password

    def _build_redirect(self, entity_id: int = 0) -> bytes:
        """Build the redirect payload pointing to our game world server.

        Fixed-layout struct (confirmed from pcap byte analysis):
          Payload (34 bytes) = [LE16 sub_len=32] + sub_data(32)

          Sub-data layout (32 bytes, fixed offsets):
            Offset 0-1:  type (0x0004)
            Offset 2-3:  padding (0x0000)
            Offset 4-7:  session token (4 bytes)
            Offset 8:    flag (0x01)
            Offset 9-24: IP string (16 bytes, null-terminated, zero-padded)
            Offset 25-26: port (LE16)
            Offset 27-31: trailing zeros
        """
        session_token = os.urandom(4)
        if entity_id:
            _pending_sessions[session_token] = entity_id

        # IP field: 16 bytes fixed, null-terminated + zero-padded
        ip_encoded = self.world_host.encode('ascii') + b'\x00'
        ip_field = (ip_encoded + b'\x00' * 16)[:16]

        sub = bytearray(32)
        struct.pack_into('<H', sub, 0, 0x0004)       # type
        struct.pack_into('<H', sub, 2, 0x0000)        # padding
        sub[4:8] = session_token                       # session token
        sub[8] = 0x01                                  # flag
        sub[9:25] = ip_field                           # IP (16 bytes fixed)
        struct.pack_into('<H', sub, 25, self.world_port)  # port at fixed offset

        # Full payload: [LE16 sub_len=32][sub_data(32)]
        payload = struct.pack('<H', 32) + bytes(sub)
        return payload


# Keep backward compatibility with server.py import
GameServer = LoginServer
