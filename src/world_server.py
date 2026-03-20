"""
Angels Online Game World Server
==================================
Handles the game world protocol after login redirect.

Flow (matches real server sequence from pcap stream 34):
  1. S->C: Hello (sends XOR key)
  2. C->S: Auth (session token from redirect)
  3. C->S: 0x0003 ACK (4B) — client sends ACK ~37ms after auth
  4. S->C: World init packets (2 compressed packets)
  5. S->C: 0x0003 ACK response (64B) — server acknowledges client ACK
  6. S->C: Skill data (~1s after ACK)
  7. S->C: Keepalives every ~1s (0x018A, 12 bytes)
  8. Bidirectional: game packets (movement, chat, combat, etc.)
"""

import asyncio
import struct
import logging
import socket

from crypto import CryptXOR, CryptXORIV
from packet import (
    PacketBuilder, PacketFramer, PacketReader,
    decode_header, HEADER_SIZE,
)
try:
    from world_init_builder import build_init_packets_for_player, build_skill_data
except Exception:
    # Fallback to legacy hex-file loading
    from world_init_data import get_init_packets as _legacy_init, get_skill_packet as _legacy_skill
    build_init_packets_for_player = lambda player: _legacy_init()
    build_skill_data = _legacy_skill
from player_tracker import PlayerTracker
from area_entity_data import get_area_packets, get_seed_entity_registry
from packet_builders import (
    build_ack_response, build_keepalive_tick, build_keepalive_timer,
    pack_sub,
)
import config
import database
from game_data import get_dialog_manager, get_map, get_npc_db, get_monster_db
from handlers import movement, npc, combat, social, misc

log = logging.getLogger("world_server")


# ---------------------------------------------------------------------------
# Opcode dispatch table — maps C->S opcodes to handler functions.
# Each handler signature: async (server, writer, builder, session, payload, addr)
# ---------------------------------------------------------------------------
OPCODE_HANDLERS = {
    0x0004: movement.handle_movement,    # MOVEMENT_REQ
    0x0006: misc.handle_entity_select,   # ENTITY_SELECT
    0x0009: combat.handle_stop_action,   # STOP_ACTION
    0x000d: npc.handle_entity_action,    # ENTITY_ACTION
    0x000f: combat.handle_target_mob,    # TARGET_MOB
    0x0012: misc.handle_buy_sell,        # BUY_SELL
    0x0016: combat.handle_use_skill,     # USE_SKILL
    0x001a: social.handle_request_player_details,  # REQUEST_PLAYER_DETAILS
    0x002e: social.handle_chat_send,     # CHAT_SEND
    0x003e: misc.handle_toggle_action,   # TOGGLE_ACTION
    0x0044: npc.handle_npc_dialog,       # NPC_DIALOG
    0x0143: misc.handle_zone_ready,      # ZONE_READY
    0x0150: social.handle_emote,         # EMOTE
}

# Opcodes that need special inline handling (not standard handler signature)
_ENTITY_ACTION_OPCODES = {0x0005, 0x0019}  # also route to npc.handle_entity_action


class WorldServer:
    """Game world server for Angels Online."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.sessions: dict[str, dict] = {}  # addr -> session info
        self.tracker = PlayerTracker()

    async def start(self):
        server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        log.info(f"World server listening on {self.host}:{self.port}")
        async with server:
            await server.serve_forever()

    async def _handle_client(self, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter):
        addr = writer.get_extra_info("peername")
        log.info(f"[{addr}] New game world connection")

        sock = writer.get_extra_info("socket")
        if sock:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        try:
            await self._world_flow(reader, writer, addr)
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError) as e:
            log.info(f"[{addr}] Disconnected: {e}")
        except asyncio.IncompleteReadError:
            log.info(f"[{addr}] Client disconnected")
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
            session = self.sessions.pop(str(addr), None)
            if session:
                self.tracker.unregister(session.get('entity_id', 0))
            log.info(f"[{addr}] Session ended")

    async def _world_flow(self, reader: asyncio.StreamReader,
                          writer: asyncio.StreamWriter, addr):
        """Main game world connection handler."""

        # --- Phase 1: Send Hello ---
        xor_key = CryptXOR.generate_key()
        crypto = CryptXOR(xor_key)
        crypto_recv = CryptXORIV(xor_key)
        builder = PacketBuilder(crypto)

        hello = builder.build_hello(xor_key)
        writer.write(hello)
        await writer.drain()
        log.info(f"[{addr}] Sent Hello, key={xor_key.hex(' ')}")

        # --- Phase 2: Receive Auth + ACK ---
        framer = PacketFramer()
        early_packets = await self._receive_packets(
            reader, framer, crypto_recv, addr, timeout=5.0)
        if not early_packets:
            return

        auth_payload = early_packets[0]
        got_ack = False

        if len(auth_payload) >= 6:
            sub_len = struct.unpack_from('<H', auth_payload, 0)[0]
            sub_type = struct.unpack_from('<H', auth_payload, 2)[0]
            log.info(f"[{addr}] Auth packet: sub_len={sub_len}, "
                     f"sub_type=0x{sub_type:04x}")

            if len(auth_payload) >= 37:
                token = auth_payload[-4:]
                log.info(f"[{addr}] Session token: {token.hex(' ')}")

        for pkt_payload in early_packets[1:]:
            if len(pkt_payload) >= 4:
                opcode = struct.unpack_from('<H', pkt_payload, 2)[0]
                if opcode == 0x0003:
                    got_ack = True
                    log.info(f"[{addr}] C->S 0x0003 ACK (bundled with auth)")

        # Look up player by session token (from login server)
        from game_server import consume_session
        token = auth_payload[-4:] if len(auth_payload) >= 4 else b''
        player_entity_id = consume_session(token)
        if player_entity_id is not None:
            player = database.get_player(player_entity_id)
        else:
            player = None
        # Fallback for direct connections / debugging
        if player is None:
            player = database.get_connection().execute(
                "SELECT * FROM players LIMIT 1"
            ).fetchone()
        if player is None:
            log.error(f"[{addr}] No player in database! Run seed_database.py first.")
            return
        entity_id = player["entity_id"]
        pos_x = player["pos_x"]
        pos_y = player["pos_y"]

        # Load player's current map
        map_id = player["map_id"] if player["map_id"] else config.START_MAP_ID
        map_data = get_map(map_id)
        npc_db = get_npc_db()
        monster_db = get_monster_db()

        # Pre-seed entity registry from seed area packets
        entity_registry: dict[int, int] = dict(get_seed_entity_registry())

        # Merge map-specific dialogs
        dm = get_dialog_manager()
        if map_data is not None and map_data.local_dialogs:
            dm.merge_local_dialogs(map_data.local_dialogs)

        self.sessions[str(addr)] = {
            'crypto': crypto,
            'crypto_recv': crypto_recv,
            'builder': builder,
            'writer': writer,
            'entity_id': entity_id,
            'pos_x': pos_x,
            'pos_y': pos_y,
            'map_id': map_id,
            'map_data': map_data,
            'npc_db': npc_db,
            'monster_db': monster_db,
            'entity_registry': entity_registry,
            'dialog_state': None,
            'player_quests': {},
        }

        session = self.sessions[str(addr)]
        session['player_name'] = player['name']
        self.tracker.register(entity_id, map_id, session)

        log.info(f"[{addr}] Auth accepted (entity=0x{entity_id:08X}), "
                 f"sending world init data")

        # --- Phase 2b: Wait for ACK if not already received ---
        if not got_ack:
            ack_payload = await self._receive_packet(
                reader, framer, crypto_recv, addr)
            if ack_payload is not None and len(ack_payload) >= 4:
                opcode = struct.unpack_from('<H', ack_payload, 2)[0]
                if opcode == 0x0003:
                    got_ack = True
                    log.info(f"[{addr}] C->S 0x0003 ACK received")
                else:
                    log.info(f"[{addr}] Expected ACK, got 0x{opcode:04x}")

        # --- Phase 3: Send World Init Packets ---
        init_packets = build_init_packets_for_player(player)
        for i, (payload, compressed) in enumerate(init_packets):
            pkt = builder.build_packet(payload, compressed=compressed)
            writer.write(pkt)
            await writer.drain()
            log.info(f"[{addr}] Sent init packet {i+1}/{len(init_packets)} "
                     f"({len(payload)} bytes, compressed={compressed})")

        # --- Phase 3b: Send ACK Response ---
        ack_payload = self._build_ack_response(entity_id)
        ack_pkt = builder.build_packet(ack_payload)
        writer.write(ack_pkt)
        await writer.drain()
        log.info(f"[{addr}] Sent S->C ACK response ({len(ack_payload)}B)")

        # --- Phase 3d: Send Skill Data ---
        skill_pkt_data = build_skill_data()
        if skill_pkt_data:
            payload, compressed = skill_pkt_data
            pkt = builder.build_packet(payload, compressed=compressed)
            writer.write(pkt)
            await writer.drain()
            log.info(f"[{addr}] Sent skill data ({len(payload)} bytes, "
                     f"compressed={compressed})")

        # --- Phase 3e: Send Area Entity Packets ---
        session = self.sessions[str(addr)]
        area_pkts = get_area_packets(
            map_data=session.get('map_data'),
            npc_db=session.get('npc_db'),
            monster_db=session.get('monster_db'),
            entity_registry=session.get('entity_registry'),
        )
        for i, (payload, compressed) in enumerate(area_pkts):
            pkt = builder.build_packet(payload, compressed=compressed)
            writer.write(pkt)
        if area_pkts:
            await writer.drain()
            log.info(f"[{addr}] Sent {len(area_pkts)} area entity packets")

        log.info(f"[{addr}] World init complete, entering game loop")

        # --- Phase 4: Game Loop ---
        keepalive_task = asyncio.create_task(
            self._keepalive_loop(writer, builder, addr)
        )

        try:
            await self._game_loop(reader, writer, framer, crypto_recv, builder, addr)
        finally:
            keepalive_task.cancel()
            try:
                await keepalive_task
            except asyncio.CancelledError:
                pass

    async def _receive_packet(self, reader: asyncio.StreamReader,
                              framer: PacketFramer, crypto: CryptXOR,
                              addr) -> bytes | None:
        """Receive one packet from the client."""
        while True:
            data = await asyncio.wait_for(reader.read(65536), timeout=30.0)
            if not data:
                return None

            packets = framer.feed(data)
            for hdr, raw_packet in packets:
                payload_raw = raw_packet[HEADER_SIZE:HEADER_SIZE + hdr['padded_length']]

                if hdr['encrypted'] and crypto:
                    payload = crypto.decrypt(payload_raw)
                else:
                    payload = payload_raw

                payload = payload[:hdr['payload_length']]

                log.debug(f"[{addr}] Recv: len={hdr['payload_length']}, "
                         f"seq={hdr['sequence']}")
                return payload

    async def _receive_packets(self, reader: asyncio.StreamReader,
                               framer: PacketFramer, crypto: CryptXOR,
                               addr, timeout: float = 30.0) -> list[bytes]:
        """Receive all packets from a single TCP read."""
        while True:
            data = await asyncio.wait_for(reader.read(65536), timeout=timeout)
            if not data:
                return []

            parsed = framer.feed(data)
            if not parsed:
                continue

            results = []
            for hdr, raw_packet in parsed:
                payload_raw = raw_packet[HEADER_SIZE:HEADER_SIZE + hdr['padded_length']]

                if hdr['encrypted'] and crypto:
                    payload = crypto.decrypt(payload_raw)
                else:
                    payload = payload_raw

                payload = payload[:hdr['payload_length']]

                log.debug(f"[{addr}] Recv: len={hdr['payload_length']}, "
                         f"seq={hdr['sequence']}")
                results.append(payload)
            return results

    async def _keepalive_loop(self, writer: asyncio.StreamWriter,
                              builder: PacketBuilder, addr):
        """Send keepalive packets at regular intervals."""
        try:
            await asyncio.sleep(config.KEEPALIVE_INTERVAL)
            tick_count = 0
            minute_count = 0

            while True:
                tick_count += 1

                if tick_count % 60 == 0:
                    minute_count += 1
                    pkt = builder.build_packet(pack_sub(build_keepalive_timer(minute_count)))
                else:
                    pkt = builder.build_packet(pack_sub(build_keepalive_tick()))

                writer.write(pkt)
                await writer.drain()
                await asyncio.sleep(config.KEEPALIVE_INTERVAL)
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            pass
        except OSError as e:
            if hasattr(e, 'winerror') and e.winerror in (64, 10053, 10054):
                pass
            else:
                raise

    async def _game_loop(self, reader: asyncio.StreamReader,
                         writer: asyncio.StreamWriter,
                         framer: PacketFramer, crypto: CryptXORIV,
                         builder: PacketBuilder, addr):
        """Handle incoming game packets via dispatch table."""
        session = self.sessions[str(addr)]

        while True:
            data = await reader.read(65536)
            if not data:
                log.info(f"[{addr}] Client disconnected")
                break

            packets = framer.feed(data)
            for hdr, raw_packet in packets:
                payload_raw = raw_packet[HEADER_SIZE:HEADER_SIZE + hdr['padded_length']]

                if hdr['encrypted']:
                    payload = crypto.decrypt(payload_raw)
                else:
                    payload = payload_raw

                payload = payload[:hdr['payload_length']]

                if len(payload) < 4:
                    log.info(f"[{addr}] Game packet: len={hdr['payload_length']}, "
                            f"seq={hdr['sequence']}, data={payload.hex(' ')}")
                    continue

                opcode = struct.unpack_from('<H', payload, 2)[0]

                # --- ACK: handled inline (special response) ---
                if opcode == 0x0003:
                    ack_payload = self._build_ack_response(session['entity_id'])
                    ack_pkt = builder.build_packet(ack_payload)
                    writer.write(ack_pkt)
                    await writer.drain()
                    log.info(f"[{addr}] C->S ACK, sent S->C ACK "
                             f"({len(ack_payload)}B)")
                    continue

                # --- Entity action opcodes with length check ---
                if opcode in _ENTITY_ACTION_OPCODES:
                    if len(payload) >= 8:
                        await npc.handle_entity_action(
                            self, writer, builder, session, payload, addr)
                    else:
                        log.debug(f"[{addr}] C->S 0x{opcode:04X} ({len(payload)}B)")
                    continue

                # --- Dispatch table lookup ---
                handler = OPCODE_HANDLERS.get(opcode)
                if handler:
                    await handler(self, writer, builder, session, payload, addr)
                    continue

                # --- Silent opcodes (no response needed) ---
                if opcode in (0x0007, 0x000b, 0x000e, 0x015e):
                    pass  # ENTITY_POS_ACK, ENTITY_STATUS_ACK, ENTITY_SPAWN_ACK, PING
                elif opcode == 0x0034:
                    # CANCEL_ACTION
                    session['dialog_state'] = None
                    log.debug(f"[{addr}] C->S CANCEL_ACTION")
                elif opcode == 0x0152:
                    log.debug(f"[{addr}] C->S ANTI_AFK_TICK")
                elif opcode in (0x0011, 0x0017, 0x0018, 0x0048, 0x0049,
                                0x0101, 0x0122, 0x0127, 0x012b, 0x012d,
                                0x0139, 0x0027):
                    log.debug(f"[{addr}] C->S 0x{opcode:04X} ({len(payload)}B)")
                elif opcode == 0x002c:
                    if len(payload) >= 8:
                        target_eid = struct.unpack_from('<I', payload, 4)[0]
                        log.info(f"[{addr}] C->S INSPECT_PLAYER "
                                 f"0x{target_eid:08X}")
                    else:
                        log.debug(f"[{addr}] C->S INSPECT_PLAYER")
                elif opcode in (0x0128, 0x0133):
                    log.info(f"[{addr}] C->S 0x{opcode:04X} ({len(payload)}B): "
                             f"{payload.hex(' ')}")
                else:
                    log.info(f"[{addr}] C->S opcode=0x{opcode:04x}, "
                            f"len={hdr['payload_length']}, "
                            f"data={payload.hex(' ')}")

    def _build_ack_response(self, entity_id: int) -> bytes:
        """Build S->C 0x0003 ACK (64 bytes). Delegates to packet_builders."""
        return build_ack_response(entity_id)

    async def broadcast_to_zone(self, map_id: int, payload: bytes,
                                exclude_entity: int = 0):
        """Send a packet to all players in a zone."""
        for session in self.tracker.get_zone_sessions(map_id, exclude_entity):
            try:
                pkt = session['builder'].build_packet(payload)
                session['writer'].write(pkt)
                await session['writer'].drain()
            except (ConnectionResetError, BrokenPipeError, OSError):
                pass
