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
from world_init_data import get_init_packets, get_skill_packet
from area_entity_data import get_area_packets, get_seed_entity_registry
from packet_builders import (
    build_ack_response, build_movement_resp,
    build_keepalive_tick, build_keepalive_timer,
    build_entity_status, build_entity_action, build_entity_move,
    build_combat_action, build_world_chat, build_chat_msg,
    build_player_appears, build_player_title, build_buff_info,
    build_pet_status_tick, build_entity_despawn,
    pack_sub,
)
import config
import database
from dialog_manager import DialogAction, DialogManager, DialogState
from map_loader import MapData, load_map_from_game_dir, load_npc_xml, load_monster_xml
from quest_manager import QuestManager, QuestState

log = logging.getLogger("world_server")


# ---------------------------------------------------------------------------
# Module-level game-data singletons (loaded once at process start)
# ---------------------------------------------------------------------------

# Dialog engine — loads msg.xml + spmsg.xml at first use
_dialog_manager: DialogManager | None = None

# Map cache — maps are loaded from game PAK on demand, cached by map_id
_map_cache: dict[int, MapData] = {}

# NPC database -- npc_type_id -> {name, sprite_id}, loaded once from npc.xml
_npc_db: dict[int, dict] | None = None

# Monster database -- monster_id -> {name, sprite_id, level, hp}, from monster.xml
_monster_db: dict[int, dict] | None = None

# Quest engine -- loads quest.xml at first use
_quest_manager: QuestManager | None = None


# ---------------------------------------------------------------------------
# Hardcoded NPC behaviors (for seed-data NPCs whose bindings aren't in XMLs)
# ---------------------------------------------------------------------------
# Maps npc_type_id → dict with keys:
#   'type': 'dialog' | 'shop' | 'totem' | 'gate'
#   'dialog_id': starting dialog node (for dialog/totem types)
#   'shop_id':   shop ID from SHOP.XML (for shop types)
#   'msg':       fallback text if dialog_id is not in dialog manager
NPC_BEHAVIORS: dict[int, dict] = {
    # Tutorial area NPCs (from seed init packets)
    2006: {
        'type': 'quest_npc',
        'quest_id': 100,  # "Registration at Angels' Tutor"
        'dialog_id': 1,   # EVENT.XML node 1 (msg_id=10701, Census Angel tutorial)
        'msg': "Welcome to Eden! I am the Census Angel. "
               "I can help you choose your class and begin your adventure.",
    },
    2429: {
        'type': 'shop',
        'shop_id': 1,
        'msg': "Welcome! Take a look at my wares.",
    },
    8804: {
        'type': 'totem',
        'msg': "The beam of light which symbolizes Aurora Totem "
               "transmits a blinding light...",
    },
    1553: {
        'type': 'gate',
        'dest_map': 3,
        'spawn_point': 0,
        'msg': "I am House Pickets. I shall send you on your way!",
    },
    1554: {
        'type': 'gate',
        'dest_map': 3,
        'spawn_point': 0,
        'msg': "I am the Gaoler Angel. Safe travels, adventurer.",
    },
    1938: {
        'type': 'totem',
        'msg': "The black cat which symbolizes the Dark City Totem "
               "transmits an evil smile...",
    },
    1940: {
        'type': 'totem',
        'msg': "The vine which symbolizes the Breeze Totem "
               "transmits a limitless vital force...",
    },
}


def _get_dialog_manager() -> DialogManager:
    global _dialog_manager
    if _dialog_manager is None:
        _dialog_manager = DialogManager()
        if config.GAME_XML_DIR.exists():
            _dialog_manager.load(config.GAME_XML_DIR)
        else:
            log.warning(f"GAME_XML_DIR not found: {config.GAME_XML_DIR}")
    return _dialog_manager


def _get_quest_manager() -> QuestManager:
    global _quest_manager
    if _quest_manager is None:
        _quest_manager = QuestManager()
        quest_path = config.GAME_XML_DIR / 'quest.xml'
        if quest_path.exists():
            _quest_manager.load(quest_path)
        else:
            log.warning(f"quest.xml not found: {quest_path}")
    return _quest_manager


def _get_map(map_id: int) -> MapData | None:
    """Load and cache a MapData for the given map_id."""
    if map_id in _map_cache:
        return _map_cache[map_id]
    if map_id == 0:
        return None
    md = load_map_from_game_dir(config.GAME_DIR, map_id)
    if md is None:
        log.warning(f"Map {map_id} not found in {config.GAME_DIR}")
    else:
        log.info(f"Map {map_id} loaded: {len(md.npcs)} NPCs, "
                 f"{len(md.monsters)} monsters, "
                 f"{len(md.npc_dialogs)} NPC→dialog mappings")
    _map_cache[map_id] = md  # cache even if None to avoid repeated attempts
    return md


def _get_npc_db() -> dict[int, dict]:
    """Load and cache the NPC database from npc.xml."""
    global _npc_db
    if _npc_db is None:
        if config.GAME_XML_DIR.exists():
            _npc_db = load_npc_xml(config.GAME_XML_DIR)
        else:
            log.warning(f"GAME_XML_DIR not found: {config.GAME_XML_DIR}")
            _npc_db = {}
    return _npc_db


def _get_monster_db() -> dict[int, dict]:
    """Load and cache the monster database from monster.xml."""
    global _monster_db
    if _monster_db is None:
        if config.GAME_XML_DIR.exists():
            _monster_db = load_monster_xml(config.GAME_XML_DIR)
        else:
            log.warning(f"GAME_XML_DIR not found: {config.GAME_XML_DIR}")
            _monster_db = {}
    return _monster_db


class WorldServer:
    """Game world server for Angels Online."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.sessions: dict[str, dict] = {}  # addr -> session info

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
            # WinError 64 (ERROR_NETNAME_DELETED) is raised by Windows IOCP
            # when a write completes on a connection the client already closed.
            # Also catch WSAECONNRESET (10054) and WSAECONNABORTED (10053) which
            # can appear as plain OSError on some Windows/Python combinations.
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
            self.sessions.pop(str(addr), None)
            log.info(f"[{addr}] Session ended")

    async def _world_flow(self, reader: asyncio.StreamReader,
                          writer: asyncio.StreamWriter, addr):
        """Main game world connection handler.

        Real server sequence (from sniffer):
          1. S->C: Hello
          2. C->S: Auth
          3. C->S: ACK (client sends ACK ~37ms after auth, BEFORE init data)
          4. S->C: Init packets (sent AFTER receiving ACK)
          5. S->C: ACK response (sent after init data)
          6. C->S: TOGGLE, PINGs, SKILL
          7. S->C: Entity data, movements
          8. S->C: First keepalive (~2 seconds after ACK exchange)
        """

        # --- Phase 1: Send Hello ---
        xor_key = CryptXOR.generate_key()
        crypto = CryptXOR(xor_key)          # S->C: static XOR
        crypto_recv = CryptXORIV(xor_key)   # C->S: evolving XOR (key mutates per packet)
        builder = PacketBuilder(crypto)

        hello = builder.build_hello(xor_key)
        writer.write(hello)
        await writer.drain()
        log.info(f"[{addr}] Sent Hello, key={xor_key.hex(' ')}")

        # --- Phase 2: Receive Auth + ACK ---
        # The client sends Auth then ACK in quick succession (~37ms apart).
        # They may arrive in the same TCP read, so we use _receive_packets
        # (plural) to collect all available packets without dropping any.
        framer = PacketFramer()
        early_packets = await self._receive_packets(
            reader, framer, crypto_recv, addr, timeout=5.0)
        if not early_packets:
            return

        auth_payload = early_packets[0]
        got_ack = False

        # Parse auth sub-message
        if len(auth_payload) >= 6:
            sub_len = struct.unpack_from('<H', auth_payload, 0)[0]
            sub_type = struct.unpack_from('<H', auth_payload, 2)[0]
            log.info(f"[{addr}] Auth packet: sub_len={sub_len}, "
                     f"sub_type=0x{sub_type:04x}")

            if len(auth_payload) >= 37:
                token = auth_payload[-4:]
                log.info(f"[{addr}] Session token: {token.hex(' ')}")

        # Check if ACK arrived with the auth read
        for pkt_payload in early_packets[1:]:
            if len(pkt_payload) >= 4:
                opcode = struct.unpack_from('<H', pkt_payload, 2)[0]
                if opcode == 0x0003:
                    got_ack = True
                    log.info(f"[{addr}] C->S 0x0003 ACK (bundled with auth)")

        # Look up player from database
        player = database.get_connection().execute(
            "SELECT * FROM players LIMIT 1"
        ).fetchone()
        if player is None:
            log.error(f"[{addr}] No player in database! Run seed_database.py first.")
            return
        entity_id = player["entity_id"]
        pos_x = player["pos_x"]
        pos_y = player["pos_y"]

        # Load player's current map and set up dialog manager for this session
        map_id = player["map_id"] if player["map_id"] else config.START_MAP_ID
        map_data = _get_map(map_id)
        npc_db = _get_npc_db()
        monster_db = _get_monster_db()

        # entity_registry: runtime_entity_id -> npc_type_id
        # Pre-seeded from the uncompressed seed area packets (covers NPCs that
        # are spawned by the init packets but not by our map-based area packets,
        # e.g. Battlefield Angel, House Pickets, Blessing Angel).
        # Map-based spawning adds more entries when area packets are generated.
        entity_registry: dict[int, int] = dict(get_seed_entity_registry())

        # Session-local dialog manager with map-specific dialogs merged in
        dm = _get_dialog_manager()
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
            'entity_registry': entity_registry,  # runtime_id -> npc_type_id
            'dialog_state': None,                 # active DialogState or None
            'player_quests': {},                   # quest_id → QuestState
        }

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
        init_packets = get_init_packets()
        for i, (payload, compressed) in enumerate(init_packets):
            pkt = builder.build_packet(payload, compressed=compressed)
            writer.write(pkt)
            await writer.drain()
            log.info(f"[{addr}] Sent init packet {i+1}/{len(init_packets)} "
                     f"({len(payload)} bytes, compressed={compressed})")

        # --- Phase 3b: Send ACK Response ---
        # Real server sends ACK response after init data (sniffer: 37.510, after
        # init at 37.478/37.499). This acknowledges the client's earlier ACK.
        ack_payload = self._build_ack_response(entity_id)
        ack_pkt = builder.build_packet(ack_payload)
        writer.write(ack_pkt)
        await writer.drain()
        log.info(f"[{addr}] Sent S->C ACK response ({len(ack_payload)}B)")

        # --- Phase 3d: Send Skill Data ---
        # Real server sends 0x0158 x34 skill data ~1s after ACK response,
        # as a separate packet (not part of the init sequence).
        skill_pkt_data = get_skill_packet()
        if skill_pkt_data:
            payload, compressed = skill_pkt_data
            pkt = builder.build_packet(payload, compressed=compressed)
            writer.write(pkt)
            await writer.drain()
            log.info(f"[{addr}] Sent skill data ({len(payload)} bytes, "
                     f"compressed={compressed})")

        # --- Phase 3e: Send Area Entity Packets (NPCs, mobs, scenery) ---
        # Generated from map PAK data if available; falls back to seed hex files.
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
        # Start keepalive sender (real server starts ~2s after ACK exchange)
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
        """Receive all packets from a single TCP read.

        Unlike _receive_packet (singular), this returns ALL packets parsed
        from the read, not just the first one. This prevents packets from
        being silently dropped when the client sends multiple packets in
        quick succession (e.g., Auth + ACK bundled together).
        """
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
        """Send keepalive packets at regular intervals.

        Real server sends two variants of 0x018A:
          - Every ~1s: 10B tick (type=0x04, no data)
          - Every ~60s: 14B timer (type=0x08, LE32 minute counter)
        """
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
                pass  # Client disconnected (Windows IOCP)
            else:
                raise

    async def _game_loop(self, reader: asyncio.StreamReader,
                         writer: asyncio.StreamWriter,
                         framer: PacketFramer, crypto: CryptXORIV,
                         builder: PacketBuilder, addr):
        """Handle incoming game packets."""
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

                # C->S: bytes 0-1 = obfuscated counter, bytes 2-3 = real opcode
                opcode = struct.unpack_from('<H', payload, 2)[0]

                if opcode == 0x0003:
                    # ACK — respond with ACK (zone transitions, handshake)
                    ack_payload = self._build_ack_response(session['entity_id'])
                    ack_pkt = builder.build_packet(ack_payload)
                    writer.write(ack_pkt)
                    await writer.drain()
                    log.info(f"[{addr}] C->S ACK, sent S->C ACK "
                             f"({len(ack_payload)}B)")
                elif opcode == 0x0004:
                    # MOVEMENT_REQ — click-to-move (destination X, Y)
                    await self._handle_movement(
                        writer, builder, session, payload, addr)
                elif opcode == 0x0005:
                    # POSITION — periodic position heartbeat / NPC interact
                    if len(payload) >= 8:
                        await self._handle_entity_action(
                            writer, builder, session, payload, addr)
                    else:
                        log.debug(f"[{addr}] C->S POSITION ({len(payload)}B)")
                elif opcode == 0x0006:
                    # ENTITY_SELECT — click/select entity or position
                    await self._handle_entity_select(
                        writer, builder, session, payload, addr)
                elif opcode == 0x0007:
                    # ENTITY_POS_ACK — 1-byte acknowledgment of server entity pos
                    pass  # Silent ack, no response needed
                elif opcode == 0x0009:
                    # STOP_ACTION — stop current action (4B)
                    await self._handle_stop_action(
                        writer, builder, session, payload, addr)
                elif opcode == 0x000b:
                    # ENTITY_STATUS_ACK — client acknowledges entity status update
                    # No server response needed; client just confirms receipt
                    pass
                elif opcode == 0x000d:
                    # ENTITY_ACTION — player interacts with entity (click/talk)
                    await self._handle_entity_action(
                        writer, builder, session, payload, addr)
                elif opcode == 0x000e:
                    # ENTITY_SPAWN_ACK — client acknowledges entity spawn
                    pass  # No response needed
                elif opcode == 0x000f:
                    # TARGET_MOB — target/attack mob (12B)
                    await self._handle_target_mob(
                        writer, builder, session, payload, addr)
                elif opcode == 0x0011:
                    # ENTITY_QUERY — request entity details (4B)
                    log.debug(f"[{addr}] C->S ENTITY_QUERY")
                elif opcode == 0x0012:
                    # BUY_SELL — shop transaction (8B)
                    await self._handle_buy_sell(
                        writer, builder, session, payload, addr)
                elif opcode == 0x0016:
                    # USE_SKILL — use skill/ability (9B)
                    await self._handle_use_skill(
                        writer, builder, session, payload, addr)
                elif opcode == 0x0017:
                    # TARGET_PLAYER_POS — click on another player
                    log.debug(f"[{addr}] C->S TARGET_PLAYER_POS")
                elif opcode == 0x0018:
                    # INSPECT_PLAYER_REQ — request full player info
                    log.debug(f"[{addr}] C->S INSPECT_PLAYER_REQ")
                elif opcode == 0x0019:
                    # NPC_INTERACT — interact with NPC (C->S variant)
                    if len(payload) >= 8:
                        await self._handle_entity_action(
                            writer, builder, session, payload, addr)
                    else:
                        log.debug(f"[{addr}] C->S NPC_INTERACT ({len(payload)}B)")
                elif opcode == 0x001a:
                    # REQUEST_PLAYER_DETAILS — client wants info about a player
                    await self._handle_request_player_details(
                        writer, builder, session, payload, addr)
                elif opcode == 0x002c:
                    # INSPECT_PLAYER — inspect another player by entity ID
                    if len(payload) >= 8:
                        target_eid = struct.unpack_from('<I', payload, 4)[0]
                        log.info(f"[{addr}] C->S INSPECT_PLAYER "
                                 f"0x{target_eid:08X}")
                    else:
                        log.debug(f"[{addr}] C->S INSPECT_PLAYER")
                elif opcode == 0x002e:
                    # CHAT_SEND — send chat message
                    await self._handle_chat_send(
                        writer, builder, session, payload, addr)
                elif opcode == 0x0034:
                    # CANCEL_ACTION — cancel current action/skill cast
                    session['dialog_state'] = None
                    log.debug(f"[{addr}] C->S CANCEL_ACTION")
                elif opcode == 0x003e:
                    # TOGGLE_ACTION — toggle sit/stand/meditate (5B)
                    await self._handle_toggle_action(
                        writer, builder, session, payload, addr)
                elif opcode == 0x0044:
                    # NPC_DIALOG — player selected a dialog option
                    await self._handle_npc_dialog(
                        writer, builder, session, payload, addr)
                elif opcode == 0x0048:
                    # EQUIP_ITEM — equip item (140B, full item data)
                    log.debug(f"[{addr}] C->S EQUIP_ITEM ({len(payload)}B)")
                elif opcode == 0x0049:
                    # EQUIP_ITEM2 — full equipment set change (140B)
                    log.debug(f"[{addr}] C->S EQUIP_ITEM2 ({len(payload)}B)")
                elif opcode == 0x0101:
                    # PET_COMMAND — pet/robot command (25B)
                    log.debug(f"[{addr}] C->S PET_COMMAND ({len(payload)}B)")
                elif opcode == 0x0122:
                    # SKILL_SLOT — assign skill to action bar slot (9B)
                    log.debug(f"[{addr}] C->S SKILL_SLOT")
                elif opcode == 0x0127:
                    # TRADE_DUEL — trade offer or duel challenge
                    log.debug(f"[{addr}] C->S TRADE_DUEL ({len(payload)}B)")
                elif opcode == 0x0128:
                    # C->S UNKNOWN_0128 — possible item use or craft
                    log.info(f"[{addr}] C->S 0x0128 ({len(payload)}B): "
                             f"{payload.hex(' ')}")
                elif opcode == 0x012b:
                    # INVENTORY_ACTION — inventory manipulation (9B)
                    log.debug(f"[{addr}] C->S INVENTORY_ACTION")
                elif opcode == 0x012d:
                    # INVENTORY_CLOSE — close inventory window (4B)
                    log.debug(f"[{addr}] C->S INVENTORY_CLOSE")
                elif opcode == 0x0133:
                    # UNKNOWN_0133 — social/guild action (11B)
                    log.info(f"[{addr}] C->S 0x0133 ({len(payload)}B): "
                             f"{payload.hex(' ')}")
                elif opcode == 0x0139:
                    # SHOP_CLOSE — close shop window (4B)
                    log.debug(f"[{addr}] C->S SHOP_CLOSE")
                elif opcode == 0x0143:
                    # ZONE_READY — client finished loading zone
                    await self._handle_zone_ready(
                        writer, builder, session, payload, addr)
                elif opcode == 0x0150:
                    # EMOTE — character emote (4B)
                    await self._handle_emote(
                        writer, builder, session, payload, addr)
                elif opcode == 0x0152:
                    # ANTI_AFK_TICK — periodic client heartbeat (~2-3 min, 6B)
                    # No response needed; just confirms client is alive
                    log.debug(f"[{addr}] C->S ANTI_AFK_TICK")
                elif opcode == 0x015e:
                    # PING — client ping/heartbeat (5B)
                    # No response needed
                    pass
                elif opcode == 0x0027:
                    # LOOT_PICKUP — pick up loot (16B, 3x LE32 values)
                    log.debug(f"[{addr}] C->S LOOT_PICKUP")
                else:
                    log.info(f"[{addr}] C->S opcode=0x{opcode:04x}, "
                            f"len={hdr['payload_length']}, "
                            f"data={payload.hex(' ')}")

    def _build_ack_response(self, entity_id: int) -> bytes:
        """Build S->C 0x0003 ACK (64 bytes). Delegates to packet_builders."""
        return build_ack_response(entity_id)

    async def _handle_entity_action(self, writer: asyncio.StreamWriter,
                                     builder, session: dict,
                                     payload: bytes, addr):
        """Handle C->S 0x000D ENTITY_ACTION — player interacts with an entity.

        C->S format (13B): [2B counter][2B opcode=0x000D][4B runtime_entity_id][3B unk]

        Flow:
          1. Extract runtime_entity_id from bytes 4-7.
          2. Look up npc_type_id in entity_registry (populated at spawn time).
          3. Check hardcoded NPC_BEHAVIORS for seed-data NPCs.
          4. Fall back to map's npc_dialogs for map-based NPCs.
          5. Send NPC speech via chat message (visible workaround while
             the real S->C dialog opcode remains unknown).
        """
        runtime_entity_id = 0
        if len(payload) >= 8:
            runtime_entity_id = struct.unpack_from('<I', payload, 4)[0]

        log.info(f"[{addr}] ENTITY_ACTION entity=0x{runtime_entity_id:08X} "
                 f"({len(payload)}B): {payload.hex(' ')}")

        # Store for use in _send_npc_chat
        session['last_npc_entity_id'] = runtime_entity_id

        # Look up NPC type ID from the entity registry
        entity_registry: dict[int, int] = session.get('entity_registry', {})
        npc_type_id = entity_registry.get(runtime_entity_id, 0)

        if npc_type_id == 0:
            log.info(f"[{addr}] ENTITY_ACTION 0x{runtime_entity_id:08X}: "
                     f"not in entity_registry")
            return

        # --- Resolve NPC name ---
        npc_db = session.get('npc_db', {})
        npc_info = npc_db.get(npc_type_id, {})
        npc_name = npc_info.get('name', f'NPC#{npc_type_id}')

        # --- Check hardcoded behaviors first ---
        behavior = NPC_BEHAVIORS.get(npc_type_id)
        if behavior:
            await self._handle_npc_behavior(
                writer, builder, session, runtime_entity_id,
                npc_type_id, npc_name, behavior, addr)
            return

        # --- Fall back to map event → dialog system ---
        map_data = session.get('map_data')
        dialog_id = 0
        if map_data is not None:
            dialog_id = map_data.npc_dialogs.get(npc_type_id, 0)

        if dialog_id == 0:
            log.info(f"[{addr}] NPC {npc_name} (type {npc_type_id}) "
                     f"has no behavior or dialog mapping")
            # Send a generic "nothing to say" chat message
            await self._send_npc_chat(
                writer, builder, session, npc_name,
                f"{npc_name} has nothing to say right now.", addr)
            return

        # Start dialog via the dialog manager
        dm = _get_dialog_manager()
        state = dm.start_dialog(dialog_id, runtime_entity_id)
        if state is None:
            log.warning(f"[{addr}] Dialog {dialog_id} not found for NPC "
                        f"type {npc_type_id}")
            return

        # Check if this dialog node immediately triggers an action (e.g. warp)
        if await self._process_dialog_actions(
                writer, builder, session, state, addr):
            return

        session['dialog_state'] = state

        # Send NPC speech as a chat message (workaround until real dialog
        # opcode is identified — 0x002B is actually QUEST_INFO).
        text = state.node.text or f"[Dialog {dialog_id}]"
        await self._send_npc_chat(
            writer, builder, session, npc_name, text, addr)

    async def _handle_npc_behavior(self, writer, builder, session: dict,
                                    runtime_entity_id: int,
                                    npc_type_id: int, npc_name: str,
                                    behavior: dict, addr):
        """Dispatch NPC interaction based on hardcoded behavior type."""
        btype = behavior.get('type', 'dialog')
        log.info(f"[{addr}] NPC {npc_name} (type {npc_type_id}): "
                 f"behavior={btype}")

        if btype == 'dialog':
            # Try dialog manager first, fall back to hardcoded msg
            dialog_id = behavior.get('dialog_id', 0)
            dm = _get_dialog_manager()
            state = dm.start_dialog(dialog_id, runtime_entity_id) if dialog_id else None

            if state:
                # Check for immediate actions (e.g. warp)
                if await self._process_dialog_actions(
                        writer, builder, session, state, addr):
                    return

                if state.node.text and not state.node.text.startswith('[msg:'):
                    text = state.node.text
                    session['dialog_state'] = state
                else:
                    text = behavior.get('msg', f'{npc_name} says hello.')
            else:
                text = behavior.get('msg', f'{npc_name} says hello.')

            await self._send_npc_chat(
                writer, builder, session, npc_name, text, addr)

        elif btype == 'totem':
            text = behavior.get('msg', 'A mystical totem glows before you.')
            await self._send_npc_chat(
                writer, builder, session, npc_name, text, addr)

        elif btype == 'shop':
            text = behavior.get('msg', 'Welcome to my shop!')
            await self._send_npc_chat(
                writer, builder, session, npc_name, text, addr)
            # TODO: Send S->C shop open packet once opcode is known

        elif btype == 'gate':
            dest_map = behavior.get('dest_map')
            if dest_map:
                text = behavior.get('msg', 'Transferring you now...')
                await self._send_npc_chat(
                    writer, builder, session, npc_name, text, addr)
                spawn_point = behavior.get('spawn_point', 0)
                await self._handle_zone_transfer(
                    writer, builder, session, dest_map, spawn_point, addr)
            else:
                text = behavior.get('msg', 'You may not pass yet.')
                await self._send_npc_chat(
                    writer, builder, session, npc_name, text, addr)

        elif btype == 'quest_npc':
            quest_id = behavior.get('quest_id', 0)
            qm = _get_quest_manager()
            player_quests = session.get('player_quests', {})
            qdef = qm.get_quest(quest_id) if quest_id else None

            if qdef and quest_id not in player_quests:
                # Offer quest
                text = f"{behavior.get('msg', '')}\n\n[Quest: {qdef.name}]\n{qdef.description}"
                qm.accept_quest(player_quests, quest_id)
                session['player_quests'] = player_quests
            elif qdef and quest_id in player_quests:
                qs = player_quests[quest_id]
                if qs.completed:
                    text = f"You have already completed '{qdef.name}'. Well done!"
                else:
                    step_text = qm.get_step_text(quest_id, qs.current_step)
                    text = f"[{qdef.name} - Step {qs.current_step}]\n{step_text}"
            else:
                text = behavior.get('msg', f'{npc_name} says hello.')

            await self._send_npc_chat(
                writer, builder, session, npc_name, text, addr)

        else:
            log.warning(f"[{addr}] Unknown behavior type: {btype}")

    async def _send_npc_chat(self, writer: asyncio.StreamWriter,
                              builder, session: dict,
                              npc_name: str, text: str, addr):
        """Send NPC speech as a 0x001E CHAT_MSG.

        Uses channel byte 0x01 (system/NPC channel) with the NPC's runtime
        entity ID so the client attributes the message to the NPC.
        """
        entity_id = session.get('entity_id', 0)
        pos_x = session.get('pos_x', 0)
        pos_y = session.get('pos_y', 0)

        # Use the NPC's runtime entity ID if available (from last interact)
        npc_entity_id = session.get('last_npc_entity_id', entity_id)

        display_text = text[:200]
        msg_bytes = display_text.encode('utf-8', errors='replace') + b'\x00'

        name8 = npc_name.encode('utf-8', errors='replace')[:7]
        name8 = name8 + b'\x00' * (8 - len(name8))

        subs = b''

        # 0x001E CHAT_MSG: use chat_type=0x0001, channel=0x01 (system/announce)
        # Previous attempt with chat_type=0x0017, channel=0x00 didn't display
        sub = struct.pack('<HHI', 0x001E, 0x0001, npc_entity_id)
        sub += name8
        sub += struct.pack('<IIB', pos_x, pos_y, 0x01)
        sub += msg_bytes
        subs += pack_sub(sub)

        pkt = builder.build_packet(subs)
        writer.write(pkt)
        try:
            await writer.drain()
        except OSError as e:
            if getattr(e, 'winerror', 0) in (64, 10053, 10054):
                log.debug(f"[{addr}] Client disconnected during NPC chat send")
                return
            raise
        log.info(f"[{addr}] Sent NPC chat: [{npc_name}] {display_text[:60]}")

    async def _handle_npc_dialog(self, writer: asyncio.StreamWriter,
                                  builder, session: dict,
                                  payload: bytes, addr):
        """Handle C->S 0x0044 NPC_DIALOG — player selected a dialog option.

        C->S payload layout (after 4B counter+opcode):
          Bytes 4-7:  LE32 dialog_id (current dialog node ID) — unconfirmed
          Byte  8:    B    option_index (0-based player choice) — unconfirmed

        NOTE: Layout is inferred; verify against pcap once dialog packets are captured.
        """
        log.info(f"[{addr}] NPC_DIALOG ({len(payload)}B): {payload.hex(' ')}")

        state: DialogState | None = session.get('dialog_state')
        if state is None:
            log.warning(f"[{addr}] NPC_DIALOG received but no active dialog session")
            return

        # Parse option index — best guess at byte 8 (after 4B counter+opcode + 4B unk)
        option_index = 0
        if len(payload) >= 9:
            option_index = payload[8]
        elif len(payload) >= 5:
            option_index = payload[4]

        dm = _get_dialog_manager()
        next_state = dm.select_option(state, option_index)

        if next_state is None:
            # Dialog closed
            session['dialog_state'] = None
            log.info(f"[{addr}] Dialog {state.dialog_id} closed")
            # Send close acknowledgment
            # TODO: Verify close packet format
            close_pkt = self._build_dialog_close(state.npc_entity_id)
            pkt = builder.build_packet(pack_sub(close_pkt))
            writer.write(pkt)
            await writer.drain()
        else:
            # Check if this dialog node triggers an action (e.g. zone warp)
            if await self._process_dialog_actions(
                    writer, builder, session, next_state, addr):
                return  # action executed, dialog consumed

            session['dialog_state'] = next_state
            # Send next dialog node
            resp = self._build_dialog_open(
                next_state.npc_entity_id, next_state.dialog_id,
                next_state.node.face)
            pkt = builder.build_packet(pack_sub(resp))
            writer.write(pkt)
            await writer.drain()

    @staticmethod
    def _build_dialog_open(npc_entity_id: int, dialog_id: int,
                            face_id: int) -> bytes:
        """Build S->C dialog open/advance packet (opcode 0x002B, placeholder layout).

        Placeholder layout (21 bytes — TODO: confirm from pcap):
          [LE16 opcode=0x002B]
          [LE32 npc_entity_id]
          [LE32 dialog_id]
          [LE32 face_id]
          [LE32 flags=0]
          [B    status=1]
        """
        # TODO: This is a best-guess layout. Capture a dialog exchange in pcap
        # to determine the actual field order and size (noted as 23B in protocol).
        return struct.pack('<HIIIIIB',
                           0x002B,
                           npc_entity_id,
                           dialog_id,
                           face_id,
                           0,        # flags
                           1)        # status = open

    @staticmethod
    def _build_dialog_close(npc_entity_id: int) -> bytes:
        """Build S->C dialog close packet (placeholder).

        TODO: Confirm opcode and format from pcap.
        """
        return struct.pack('<HI', 0x002B, npc_entity_id)

    async def _handle_movement(self, writer: asyncio.StreamWriter,
                                builder: PacketBuilder, session: dict,
                                payload: bytes, addr):
        """Handle C->S movement (0x0004) and send S->C response.

        C->S format (from openao):
          [2B counter][2B opcode=0x0004]
          [2B src_x][2B src_y][3B pad][2B dst_x][2B dst_y]

        S->C format (30 bytes, 2 sub-messages — confirmed from pcap):
          Sub 1: [LE16 2][006D] — MOVE_RESP flag (just opcode, no data)
          Sub 2: [LE16 24][0005 entity_id cur_x cur_y dst_x dst_y speed]
                  — ENTITY_ANIM with movement data
        """
        if len(payload) < 15:
            return

        log.info(f"[{addr}] Movement raw ({len(payload)}b): {payload.hex(' ')}")

        # Destination is at offset 11 (after 2B counter + 2B opcode + 2B src_x + 2B src_y + 3B pad)
        client_dest_x = struct.unpack_from('<H', payload, 11)[0]
        client_dest_y = struct.unpack_from('<H', payload, 13)[0]

        cur_x = session['pos_x']
        cur_y = session['pos_y']

        dx = client_dest_x - cur_x
        dy = client_dest_y - cur_y
        dist = (dx * dx + dy * dy) ** 0.5

        max_step = config.MAX_MOVE_STEP
        if dist > max_step and dist > 0:
            ratio = max_step / dist
            dest_x = int(cur_x + dx * ratio)
            dest_y = int(cur_y + dy * ratio)
        else:
            dest_x = client_dest_x
            dest_y = client_dest_y

        log.info(f"[{addr}] Movement: ({cur_x},{cur_y}) -> ({dest_x},{dest_y}) "
                 f"[client requested ({client_dest_x},{client_dest_y})]")

        entity_id = session['entity_id']

        resp_payload = build_movement_resp(
            entity_id, cur_x, cur_y, dest_x, dest_y, config.MOVE_SPEED)
        pkt = builder.build_packet(resp_payload)
        writer.write(pkt)
        await writer.drain()

        # Update position in session and database
        session['pos_x'] = dest_x
        session['pos_y'] = dest_y
        database.update_player_position(entity_id, dest_x, dest_y)

    async def _handle_zone_transfer(self, writer: asyncio.StreamWriter,
                                     builder: PacketBuilder, session: dict,
                                     dest_map_id: int, spawn_point: int,
                                     addr):
        """Transfer the player to a different map/zone.

        Steps:
          1. Resolve spawn position for the destination map.
          2. Load the new MapData (NPCs, events, dialogs).
          3. Merge the new map's local dialogs into the dialog manager.
          4. Reset the entity registry and generate new area entity packets.
          5. Send the area packets to the client.
          6. Update session state and persist to database.
        """
        old_map_id = session.get('map_id', config.START_MAP_ID)
        entity_id = session['entity_id']

        # --- Resolve spawn position ---
        spawn_pos = config.MAP_SPAWN_POINTS.get((dest_map_id, spawn_point))
        if spawn_pos is None:
            spawn_pos = config.MAP_SPAWN_POINTS.get((dest_map_id, 0))
        if spawn_pos is None:
            spawn_pos = config.DEFAULT_SPAWN
        dest_x, dest_y = spawn_pos

        log.info(f"[{addr}] Zone transfer: map {old_map_id} → {dest_map_id} "
                 f"(spawn_point={spawn_point}, pos=({dest_x},{dest_y}))")

        # --- Load new map ---
        new_map_data = _get_map(dest_map_id)
        npc_db = _get_npc_db()

        # Merge new map's local dialogs
        dm = _get_dialog_manager()
        if new_map_data is not None and new_map_data.local_dialogs:
            dm.merge_local_dialogs(new_map_data.local_dialogs)

        # If the map loaded, try to use its center as a better fallback position
        if new_map_data is not None and spawn_pos == config.DEFAULT_SPAWN:
            center_x = (new_map_data.width * new_map_data.tile_w) // 2
            center_y = (new_map_data.height * new_map_data.tile_h) // 2
            if center_x > 0 and center_y > 0:
                dest_x, dest_y = center_x, center_y
                log.info(f"[{addr}] Using map center as spawn: ({dest_x},{dest_y})")

        # --- Reset entity registry and generate new area packets ---
        new_entity_registry: dict[int, int] = {}
        area_pkts = get_area_packets(
            map_data=new_map_data,
            npc_db=npc_db,
            monster_db=session.get('monster_db'),
            entity_registry=new_entity_registry,
        )

        # --- Send area packets for the new zone ---
        for payload, compressed in area_pkts:
            pkt = builder.build_packet(payload, compressed=compressed)
            writer.write(pkt)
        if area_pkts:
            await writer.drain()
            log.info(f"[{addr}] Sent {len(area_pkts)} area packets for map {dest_map_id}")

        # --- Send updated position to client ---
        resp_payload = build_movement_resp(
            entity_id, dest_x, dest_y, dest_x, dest_y, config.MOVE_SPEED)
        pkt = builder.build_packet(resp_payload)
        writer.write(pkt)
        await writer.drain()

        # --- Update session ---
        session['map_id'] = dest_map_id
        session['pos_x'] = dest_x
        session['pos_y'] = dest_y
        session['map_data'] = new_map_data
        session['entity_registry'] = new_entity_registry
        session['dialog_state'] = None  # clear any active dialog

        # --- Persist to database ---
        database.update_player_map(entity_id, dest_map_id, dest_x, dest_y)

        log.info(f"[{addr}] Zone transfer complete: now on map {dest_map_id} "
                 f"at ({dest_x},{dest_y})")

    async def _process_dialog_actions(self, writer: asyncio.StreamWriter,
                                       builder: PacketBuilder, session: dict,
                                       state: DialogState, addr) -> bool:
        """Check a dialog state for executable actions (e.g. warp).

        Returns True if an action was executed (caller should stop dialog flow).
        """
        if not state or not state.node.actions:
            return False

        for action in state.node.actions:
            if action.action_type == 37 and action.params:
                # Action 37 = zone warp: params (map_id, spawn_point, flag)
                dest_map = action.params[0]
                spawn_point = action.params[1] if len(action.params) > 1 else 0
                session['dialog_state'] = None
                await self._handle_zone_transfer(
                    writer, builder, session, dest_map, spawn_point, addr)
                return True

        return False

    async def _handle_entity_select(self, writer: asyncio.StreamWriter,
                                     builder: PacketBuilder, session: dict,
                                     payload: bytes, addr):
        """Handle C->S 0x0006 ENTITY_SELECT — click/select entity or position.

        C->S format (20-39B): [2B counter][2B opcode=0x0006]
                               [4B target_entity_id or 0][remaining varies]
        Logs the selection. No server response is strictly required,
        but the real server may send entity details back.
        """
        if len(payload) >= 8:
            target_id = struct.unpack_from('<I', payload, 4)[0]
            log.info(f"[{addr}] C->S ENTITY_SELECT target=0x{target_id:08X}")
        else:
            log.debug(f"[{addr}] C->S ENTITY_SELECT ({len(payload)}B)")

    async def _handle_stop_action(self, writer: asyncio.StreamWriter,
                                   builder: PacketBuilder, session: dict,
                                   payload: bytes, addr):
        """Handle C->S 0x0009 STOP_ACTION — stop current action.

        C->S format (4B): [2B counter][2B opcode=0x0009]
        Clears any active dialog/combat state.
        """
        session['dialog_state'] = None
        log.debug(f"[{addr}] C->S STOP_ACTION — cleared active state")

    async def _handle_target_mob(self, writer: asyncio.StreamWriter,
                                  builder: PacketBuilder, session: dict,
                                  payload: bytes, addr):
        """Handle C->S 0x000F TARGET_MOB — target a mob for attack.

        C->S format (12B): [2B counter][2B opcode=0x000F]
                            [LE32 mob_entity_id][LE32 counter/nonce]

        Server should respond with combat actions once auto-attack begins.
        For now, logs the target and sends an entity status update to confirm
        the mob is targetable.
        """
        if len(payload) < 8:
            log.debug(f"[{addr}] C->S TARGET_MOB (too short)")
            return

        mob_id = struct.unpack_from('<I', payload, 4)[0]
        log.info(f"[{addr}] C->S TARGET_MOB 0x{mob_id:08X}")

        session['target_mob_id'] = mob_id

        # Send entity status to confirm mob is alive and targetable
        status_sub = build_entity_status(mob_id, status_a=1, status_b=1)
        pkt = builder.build_packet(pack_sub(status_sub))
        writer.write(pkt)
        await writer.drain()

    async def _handle_use_skill(self, writer: asyncio.StreamWriter,
                                 builder: PacketBuilder, session: dict,
                                 payload: bytes, addr):
        """Handle C->S 0x0016 USE_SKILL — use a skill/ability.

        C->S format (9B): [2B counter][2B opcode=0x0016]
                           [B skill_id][LE32 target_entity_id]

        The real server would validate the skill, check cooldowns/MP, then
        send 0x0019 COMBAT_ACTION with damage results. For now we log it
        and send a basic combat action response if targeting a mob.
        """
        if len(payload) < 9:
            log.debug(f"[{addr}] C->S USE_SKILL (too short)")
            return

        skill_id = payload[4]
        target_id = struct.unpack_from('<I', payload, 5)[0]
        entity_id = session['entity_id']
        log.info(f"[{addr}] C->S USE_SKILL id={skill_id} "
                 f"target=0x{target_id:08X}")

        # Send a combat action response (placeholder damage)
        if target_id != 0:
            combat_sub = build_combat_action(
                source_id=entity_id,
                target_id=target_id,
                skill_id=skill_id,
                damage=100,      # placeholder
                action_type=2,   # skill attack
                flags=0,
            )
            pkt = builder.build_packet(pack_sub(combat_sub))
            writer.write(pkt)
            await writer.drain()

    async def _handle_buy_sell(self, writer: asyncio.StreamWriter,
                                builder: PacketBuilder, session: dict,
                                payload: bytes, addr):
        """Handle C->S 0x0012 BUY_SELL — shop transaction.

        C->S format (8B): [2B counter][2B opcode=0x0012]
                           [LE16 item_id][LE16 quantity]
        """
        if len(payload) >= 8:
            item_id = struct.unpack_from('<H', payload, 4)[0]
            quantity = struct.unpack_from('<H', payload, 6)[0]
            log.info(f"[{addr}] C->S BUY_SELL item={item_id} qty={quantity}")
        else:
            log.debug(f"[{addr}] C->S BUY_SELL ({len(payload)}B)")

    async def _handle_chat_send(self, writer: asyncio.StreamWriter,
                                 builder: PacketBuilder, session: dict,
                                 payload: bytes, addr):
        """Handle C->S 0x002E CHAT_SEND — send a chat message.

        C->S format (variable): [2B counter][2B opcode=0x002E]
                                 [remaining bytes = channel + message data]

        For now, echoes the message back to the sender as a system message.
        In a full implementation, this would broadcast to nearby players.
        """
        if len(payload) < 5:
            log.debug(f"[{addr}] C->S CHAT_SEND (too short)")
            return

        # Extract message from payload (after 4B counter+opcode)
        msg_data = payload[4:]
        log.info(f"[{addr}] C->S CHAT_SEND ({len(msg_data)}B): "
                 f"{msg_data.hex(' ')}")

        # Try to extract readable text from the message data
        # Channel byte is likely first, then message text
        channel = msg_data[0] if len(msg_data) > 0 else 0

        # Find null-terminated message text
        text_start = 1
        text_end = msg_data.find(0x00, text_start)
        if text_end < 0:
            text_end = len(msg_data)
        message_text = msg_data[text_start:text_end].decode(
            'utf-8', errors='replace')

        if message_text:
            log.info(f"[{addr}] Chat (ch={channel}): {message_text}")

            # Echo back as a chat message
            entity_id = session['entity_id']
            chat_sub = build_chat_msg(
                sender_entity_id=entity_id,
                sender_name="Player",
                message=message_text,
                pos_x=session.get('pos_x', 0),
                pos_y=session.get('pos_y', 0),
                chat_type=0x0001,
                channel=0x00,
            )
            pkt = builder.build_packet(pack_sub(chat_sub))
            writer.write(pkt)
            await writer.drain()

    async def _handle_toggle_action(self, writer: asyncio.StreamWriter,
                                     builder: PacketBuilder, session: dict,
                                     payload: bytes, addr):
        """Handle C->S 0x003E TOGGLE_ACTION — toggle sit/stand/meditate.

        C->S format (5B): [2B counter][2B opcode=0x003E][B action_id]
          action_id: 0=stand, 1=sit, 2=meditate (theory)

        Server should broadcast this to nearby players via entity setting.
        For now, logs and sends an entity setting update.
        """
        action_id = payload[4] if len(payload) >= 5 else 0
        entity_id = session['entity_id']
        log.info(f"[{addr}] C->S TOGGLE_ACTION action={action_id}")

        # Broadcast via entity setting (setting_id for sit/stand state)
        from packet_builders import build_setting_16
        setting = build_setting_16(
            entity_id=entity_id,
            marker=0x3501,
            setting_id=0x074E,  # Same marker as ACK response
            value_lo=0,
            value=action_id,
            value_hi=0,
        )
        pkt = builder.build_packet(pack_sub(setting))
        writer.write(pkt)
        await writer.drain()

    async def _handle_request_player_details(
            self, writer: asyncio.StreamWriter,
            builder: PacketBuilder, session: dict,
            payload: bytes, addr):
        """Handle C->S 0x001A REQUEST_PLAYER_DETAILS.

        C->S format (12B): [2B counter][2B opcode=0x001A]
                            [LE32 entity_id][LE32 zeros]

        Sent when client sees a new player (after 0x0028 PLAYER_APPEARS).
        Server should respond with detailed player info. For now, logs it.
        """
        if len(payload) >= 8:
            target_eid = struct.unpack_from('<I', payload, 4)[0]
            log.info(f"[{addr}] C->S REQUEST_PLAYER_DETAILS "
                     f"0x{target_eid:08X}")
        else:
            log.debug(f"[{addr}] C->S REQUEST_PLAYER_DETAILS "
                      f"({len(payload)}B)")

    async def _handle_zone_ready(self, writer: asyncio.StreamWriter,
                                  builder: PacketBuilder, session: dict,
                                  payload: bytes, addr):
        """Handle C->S 0x0143 ZONE_READY — client finished loading zone.

        C->S format (4B): [2B counter][2B opcode=0x0143]

        Client has loaded all zone assets and is ready for gameplay.
        Server can now send entity updates, nearby player info, etc.
        """
        log.info(f"[{addr}] C->S ZONE_READY — client zone load complete")

        # Send any pending entity updates or player presence data
        # For now, just acknowledge by sending a keepalive
        tick_sub = build_keepalive_tick()
        pkt = builder.build_packet(pack_sub(tick_sub))
        writer.write(pkt)
        await writer.drain()

    async def _handle_emote(self, writer: asyncio.StreamWriter,
                             builder: PacketBuilder, session: dict,
                             payload: bytes, addr):
        """Handle C->S 0x0150 EMOTE — character emote animation.

        C->S format (4B): [2B counter][2B opcode=0x0150]
        Some emotes may have additional bytes for emote ID.

        Server should broadcast emote to nearby players.
        For now, logs the emote.
        """
        emote_id = 0
        if len(payload) >= 6:
            emote_id = struct.unpack_from('<H', payload, 4)[0]
        log.info(f"[{addr}] C->S EMOTE id={emote_id}")
