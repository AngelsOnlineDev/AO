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
from crypto import CryptXOR
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

# Account data template (654 bytes, decompressed)
# This is the raw account sub-message with known field offsets documented.
# Unknown fields are preserved from the original capture.
#
# Known field offsets (within sub-message data):
#   0:  LE16  opcode (0x0000)
#   4:  LE16  status (1 = success)
#   6:  LE32  field (3)
#  16:  LE32  account_id
#  36:  LE32  entity_ref (0x0020A0C3)
#  40:  9B    display_name (null-padded)
#  49:  LE32  character_id
#  57:  4B    session key
#  61:  10B   login_name (null-padded)
#  86:  LE32  level
#  90:  LE32  class_id
# 114:  LE32  hp_max (294)
# 118:  LE32  mp_max (280)
# 122:  LE32  stat (185)
# 126:  LE32  stat (154)
_ACCT_TEMPLATE = bytearray(654)
# Status = 1 (success)
struct.pack_into('<H', _ACCT_TEMPLATE, 4, 1)
# Field = 3
struct.pack_into('<I', _ACCT_TEMPLATE, 6, 3)
# Field at offset 10-11
_ACCT_TEMPLATE[10] = 0x05
_ACCT_TEMPLATE[11] = 0x01
# Account ID = 41
struct.pack_into('<I', _ACCT_TEMPLATE, 16, 41)
# Entity ref
struct.pack_into('<I', _ACCT_TEMPLATE, 36, 0x0020A0C3)
# Display name "Player"
_ACCT_TEMPLATE[40:46] = b"Player"
# Character ID
struct.pack_into('<I', _ACCT_TEMPLATE, 49, 0x00019D96)
# Field = 154
struct.pack_into('<I', _ACCT_TEMPLATE, 53, 154)
# Session key
_ACCT_TEMPLATE[57:61] = bytes.fromhex("6009447e")
# Login name "player"
_ACCT_TEMPLATE[61:67] = b"player"
# Fields at 71-76
_ACCT_TEMPLATE[71] = 0x05
_ACCT_TEMPLATE[73] = 0x05
_ACCT_TEMPLATE[75] = 0x05
# String "70Kee" at offset 77
_ACCT_TEMPLATE[77:82] = b"70Kee"
# Level = 26
struct.pack_into('<I', _ACCT_TEMPLATE, 86, 26)
# Class ID = 18 (Priest)
struct.pack_into('<I', _ACCT_TEMPLATE, 90, 18)
# Stats
struct.pack_into('<I', _ACCT_TEMPLATE, 98, 28)
struct.pack_into('<I', _ACCT_TEMPLATE, 102, 30)
struct.pack_into('<I', _ACCT_TEMPLATE, 114, 294)
struct.pack_into('<I', _ACCT_TEMPLATE, 118, 280)
struct.pack_into('<I', _ACCT_TEMPLATE, 122, 185)
struct.pack_into('<I', _ACCT_TEMPLATE, 126, 154)
# Timestamp/hash at 130
_ACCT_TEMPLATE[130:141] = bytes.fromhex("e5da9969001f690f30be1d")
# Field at 150
struct.pack_into('<I', _ACCT_TEMPLATE, 148, 0x00860000)
# Stats block at 455+
struct.pack_into('<I', _ACCT_TEMPLATE, 455, 294)
struct.pack_into('<I', _ACCT_TEMPLATE, 459, 0x3A61)
struct.pack_into('<I', _ACCT_TEMPLATE, 463, 0x08DC)
struct.pack_into('<I', _ACCT_TEMPLATE, 467, 185)
struct.pack_into('<I', _ACCT_TEMPLATE, 471, 0x1556)
struct.pack_into('<I', _ACCT_TEMPLATE, 475, 0x039B)
# Server IDs: 1, 5, 6, 7, 8, 34
for i, sid in enumerate([1, 5, 6, 7, 8, 34]):
    struct.pack_into('<I', _ACCT_TEMPLATE, 479 + i * 4, sid)
# Trailing fields
struct.pack_into('<I', _ACCT_TEMPLATE, 587, 1)
struct.pack_into('<I', _ACCT_TEMPLATE, 591, 2)
struct.pack_into('<I', _ACCT_TEMPLATE, 646, 3)
_ACCT_TEMPLATE = bytes(_ACCT_TEMPLATE)


def _pack_fixed_str(value: str, length: int) -> bytes:
    """Encode a string into a fixed-length null-padded field."""
    encoded = value.encode('ascii')[:length]
    return encoded + b'\x00' * (length - len(encoded))


def build_login_response(
    motd: str = MOTD_TEXT,
    display_name: str = "Player",
    login_name: str = "player",
    account_id: int = 41,
    character_id: int = 0x00019D96,
    level: int = 26,
    class_id: int = 18,
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

    # Sub-message 2: Account data (patch template with parameters)
    acct = bytearray(_ACCT_TEMPLATE)
    struct.pack_into('<I', acct, 16, account_id)
    acct[40:49] = _pack_fixed_str(display_name, 9)
    struct.pack_into('<I', acct, 49, character_id)
    acct[61:71] = _pack_fixed_str(login_name, 10)
    struct.pack_into('<I', acct, 86, level)
    struct.pack_into('<I', acct, 90, class_id)

    # Combine as sub-messages: [LE16 len][data] for each
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
        xor_key = CryptXOR.generate_key()
        crypto = CryptXOR(xor_key)
        builder = PacketBuilder(crypto)

        hello = builder.build_hello(xor_key)
        writer.write(hello)
        await writer.drain()
        log.info(f"[{addr}] Sent Hello ({len(hello)} bytes), "
                 f"key={xor_key.hex(' ')}")

        # --- Phase 2: Receive Login ---
        framer = PacketFramer()
        login_payload = await self._receive_packet(reader, framer, crypto, addr)
        if login_payload is None:
            return

        # Parse login sub-message
        username = self._parse_login(login_payload, addr)
        if username is None:
            return

        log.info(f"[{addr}] Login: username='{username}'")

        # --- Phase 3: Look up or create player, send Login Response ---
        player = database.get_player_by_name(username)
        if player is None:
            import random
            entity_id = random.randint(0x10000000, 0x7FFFFFFF)
            player = database.get_or_create_player(
                entity_id=entity_id, name=username)
        player_entity_id = player['entity_id']

        login_resp = build_login_response(
            motd=config.LOGIN_MOTD,
            display_name="Player",
            login_name=username,
        )
        resp_packet = builder.build_packet(login_resp, compressed=True)
        writer.write(resp_packet)
        await writer.drain()
        log.info(f"[{addr}] Sent Login Response ({len(resp_packet)} bytes)")

        # --- Phase 4: Receive PIN/Session confirmation ---
        pin_payload = await self._receive_packet(reader, framer, crypto, addr)
        if pin_payload is not None:
            log.info(f"[{addr}] Received PIN/Session ({len(pin_payload)} bytes): "
                     f"{pin_payload[:32].hex(' ')}")

        # --- Phase 5: Send Redirect ---
        redirect_payload = self._build_redirect(player_entity_id)
        redirect_packet = builder.build_packet(redirect_payload)
        writer.write(redirect_packet)
        await writer.drain()
        log.info(f"[{addr}] Sent Redirect -> {self.world_host}:{self.world_port}")

        # Give client time to read the redirect before closing
        await asyncio.sleep(1.0)

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

    def _parse_login(self, payload: bytes, addr) -> str | None:
        """Parse login packet payload. Returns username or None."""
        if len(payload) < 10:
            log.warning(f"[{addr}] Login payload too short: {len(payload)}")
            return None

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

        return username

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
