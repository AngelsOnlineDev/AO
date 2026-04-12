"""NPC interaction handlers — entity action, dialog, behavior dispatch."""

import struct
import logging

import database
from packet_builders import pack_sub
from game_data import get_dialog_manager, get_quest_manager
from dialog_manager import DialogAction, DialogState
from handlers.movement import handle_zone_transfer, process_dialog_actions

log = logging.getLogger('handlers.npc')

# ---------------------------------------------------------------------------
# Class selection data (Census Angel NPC, type 2006)
# ---------------------------------------------------------------------------
CLASS_INFO = {
    1:  ('Priest',      'Healing and defensive spells. Cloth armor.'),
    2:  ('Summoner',    'Curse and summoning spells. Cloth armor.'),
    3:  ('Wizard',      'Chaos and destructive spells. Cloth armor.'),
    4:  ('Magician',    'Earth spells, transformation magic. Cloth armor.'),
    5:  ('Protector',   'Shield defense tank. Heavy armor.'),
    6:  ('Warrior',     'Axe and hammer damage. Heavy armor.'),
    7:  ('Swordsman',   'Sword skills, fast attacks. Heavy armor.'),
    8:  ('Spearman',    'Spear piercing damage. Heavy armor.'),
    9:  ('Archer',      'Bow and long-range attacks. Light armor.'),
    10: ('Weaponsmith', 'Forges metal weapons. Light armor.'),
    11: ('Armorsmith',  'Forges light and heavy armor. Light armor.'),
    12: ('Tailor',      'Cloth armor and backpacks. Light armor.'),
    13: ('Technician',  'Robot accessories, ornaments. Leather armor.'),
    15: ('Chef',        'Cooking food buffs, fishing. Light armor.'),
}

# Census Angel dialog states per player
_census_states: dict[int, str] = {}  # entity_id -> state ('menu'|'confirm_<N>')


# ---------------------------------------------------------------------------
# Hardcoded NPC behaviors (for seed-data NPCs whose bindings aren't in XMLs)
# ---------------------------------------------------------------------------
# Maps npc_type_id -> dict with keys:
#   'type': 'dialog' | 'shop' | 'totem' | 'gate'
#   'dialog_id': starting dialog node (for dialog/totem types)
#   'shop_id':   shop ID from SHOP.XML (for shop types)
#   'msg':       fallback text if dialog_id is not in dialog manager
NPC_BEHAVIORS: dict[int, dict] = {
    # Tutorial area NPCs (from seed init packets)
    2006: {
        'type': 'census_angel',
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


async def handle_entity_action(server, writer, builder, session, payload, addr):
    """Handle C->S 0x000D ENTITY_ACTION -- player interacts with an entity.

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

    # Store for use in send_npc_chat
    session['last_npc_entity_id'] = runtime_entity_id

    # Look up NPC type ID from the entity registry
    entity_registry: dict[int, int] = session.get('entity_registry', {})
    npc_type_id = entity_registry.get(runtime_entity_id, 0)

    if npc_type_id == 0:
        log.info(f"[{addr}] ENTITY_ACTION 0x{runtime_entity_id:08X}: "
                 f"not in entity_registry")
        return

    # --- If the target is a combat mob (monster.xml id < 1500), route
    #     to the combat handler instead of the dialog flow. Players click
    #     mobs to attack, not to chat.
    monster_db = session.get('monster_db') or {}
    if npc_type_id < 1500 and npc_type_id in monster_db:
        from handlers.combat import handle_auto_attack
        await handle_auto_attack(
            server, writer, builder, session,
            runtime_entity_id, npc_type_id, addr)
        return

    # --- Resolve NPC name ---
    npc_db = session.get('npc_db', {})
    npc_info = npc_db.get(npc_type_id, {})
    npc_name = npc_info.get('name', f'NPC#{npc_type_id}')

    # --- Check hardcoded behaviors first ---
    behavior = NPC_BEHAVIORS.get(npc_type_id)
    if behavior:
        await _handle_npc_behavior(
            server, writer, builder, session, runtime_entity_id,
            npc_type_id, npc_name, behavior, addr)
        return

    # --- Fall back to map event -> dialog system ---
    map_data = session.get('map_data')
    dialog_id = 0
    if map_data is not None:
        dialog_id = map_data.npc_dialogs.get(npc_type_id, 0)

    if dialog_id == 0:
        log.info(f"[{addr}] NPC {npc_name} (type {npc_type_id}) "
                 f"has no behavior or dialog mapping")
        # Send a generic "nothing to say" chat message
        await send_npc_chat(
            writer, builder, session, npc_name,
            f"{npc_name} has nothing to say right now.", addr)
        return

    # Start dialog via the dialog manager
    dm = get_dialog_manager()
    state = dm.start_dialog(dialog_id, runtime_entity_id)
    if state is None:
        log.warning(f"[{addr}] Dialog {dialog_id} not found for NPC "
                    f"type {npc_type_id}")
        return

    # Check if this dialog node immediately triggers an action (e.g. warp)
    if await process_dialog_actions(
            server, writer, builder, session, state, addr):
        return

    session['dialog_state'] = state

    # Send NPC speech as a chat message (workaround until real dialog
    # opcode is identified -- 0x002B is actually QUEST_INFO).
    text = state.node.text or f"[Dialog {dialog_id}]"
    await send_npc_chat(
        writer, builder, session, npc_name, text, addr)


async def _handle_npc_behavior(server, writer, builder, session,
                                runtime_entity_id, npc_type_id,
                                npc_name, behavior, addr):
    """Dispatch NPC interaction based on hardcoded behavior type."""
    btype = behavior.get('type', 'dialog')
    log.info(f"[{addr}] NPC {npc_name} (type {npc_type_id}): "
             f"behavior={btype}")

    if btype == 'dialog':
        # Try dialog manager first, fall back to hardcoded msg
        dialog_id = behavior.get('dialog_id', 0)
        dm = get_dialog_manager()
        state = dm.start_dialog(dialog_id, runtime_entity_id) if dialog_id else None

        if state:
            # Check for immediate actions (e.g. warp)
            if await process_dialog_actions(
                    server, writer, builder, session, state, addr):
                return

            if state.node.text and not state.node.text.startswith('[msg:'):
                text = state.node.text
                session['dialog_state'] = state
            else:
                text = behavior.get('msg', f'{npc_name} says hello.')
        else:
            text = behavior.get('msg', f'{npc_name} says hello.')

        await send_npc_chat(
            writer, builder, session, npc_name, text, addr)

    elif btype == 'totem':
        text = behavior.get('msg', 'A mystical totem glows before you.')
        await send_npc_chat(
            writer, builder, session, npc_name, text, addr)

    elif btype == 'shop':
        text = behavior.get('msg', 'Welcome to my shop!')
        await send_npc_chat(
            writer, builder, session, npc_name, text, addr)
        # TODO: Send S->C shop open packet once opcode is known

    elif btype == 'gate':
        dest_map = behavior.get('dest_map')
        if dest_map:
            text = behavior.get('msg', 'Transferring you now...')
            await send_npc_chat(
                writer, builder, session, npc_name, text, addr)
            spawn_point = behavior.get('spawn_point', 0)
            await handle_zone_transfer(
                server, writer, builder, session, dest_map, spawn_point, addr)
        else:
            text = behavior.get('msg', 'You may not pass yet.')
            await send_npc_chat(
                writer, builder, session, npc_name, text, addr)

    elif btype == 'census_angel':
        await _handle_census_angel(
            server, writer, builder, session, npc_name, addr)

    elif btype == 'quest_npc':
        quest_id = behavior.get('quest_id', 0)
        qm = get_quest_manager()
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

        await send_npc_chat(
            writer, builder, session, npc_name, text, addr)

    else:
        log.warning(f"[{addr}] Unknown behavior type: {btype}")


async def _handle_census_angel(server, writer, builder, session, npc_name, addr):
    """Handle Census Angel interaction — class selection for Novice players."""
    entity_id = session.get('entity_id', 0)

    # Load current player from DB to check class
    player = database.get_player(entity_id)
    if player and player['class_id'] != 0:
        # Already has a class
        cls_name = CLASS_INFO.get(player['class_id'], (f"Class {player['class_id']}",))[0]
        text = (f"You are already a {cls_name}! "
                f"Go forth and explore the world of Eden.")
        await send_npc_chat(writer, builder, session, npc_name, text, addr)
        return

    # Show class menu
    state = _census_states.get(entity_id)

    if state and state.startswith('confirm_'):
        # They already got a confirm prompt — clicking NPC again resets to menu
        pass

    # Build the class list message
    lines = ["I am the Census Angel. Choose your path!\n"]
    lines.append("=== Combat Classes ===")
    for cid in [5, 6, 7, 8, 9, 1, 2, 3, 4]:
        name, desc = CLASS_INFO[cid]
        lines.append(f"  [{cid}] {name} - {desc}")
    lines.append("\n=== Crafting Classes ===")
    for cid in [10, 11, 12, 13, 15]:
        name, desc = CLASS_INFO[cid]
        lines.append(f"  [{cid}] {name} - {desc}")
    lines.append("\nWhisper me a number (1-13 or 15) to choose your class!")
    lines.append("Example: type /whisper Census Angel 7")

    _census_states[entity_id] = 'menu'
    text = '\n'.join(lines)
    await send_npc_chat(writer, builder, session, npc_name, text, addr)


async def handle_census_chat(server, writer, builder, session, message, addr):
    """Handle a whisper/chat to the Census Angel for class selection.

    Called from the chat handler when a player whispers "Census Angel" or
    sends a message while in census_angel menu state.

    Returns True if the message was consumed by the Census Angel.
    """
    entity_id = session.get('entity_id', 0)
    state = _census_states.get(entity_id)
    if state is None:
        return False

    npc_name = "Census Angel"
    msg = message.strip()

    if state == 'menu':
        # Expect a class number
        try:
            class_id = int(msg)
        except ValueError:
            await send_npc_chat(
                writer, builder, session, npc_name,
                "Please enter a class number (1-13 or 15).", addr)
            return True

        if class_id not in CLASS_INFO:
            await send_npc_chat(
                writer, builder, session, npc_name,
                f"Invalid class number '{class_id}'. Choose 1-13 or 15.", addr)
            return True

        cls_name, cls_desc = CLASS_INFO[class_id]
        _census_states[entity_id] = f'confirm_{class_id}'
        await send_npc_chat(
            writer, builder, session, npc_name,
            f"You want to become a {cls_name}?\n{cls_desc}\n\n"
            f"Type 'yes' to confirm or 'no' to go back.", addr)
        return True

    elif state.startswith('confirm_'):
        class_id = int(state.split('_')[1])
        cls_name = CLASS_INFO[class_id][0]

        if msg.lower() in ('yes', 'y', 'confirm'):
            # Apply class change in DB and refresh the session cache so
            # presence broadcasts see the new class immediately.
            database.update_player_class(entity_id, class_id)
            session['player'] = database.get_player(entity_id)
            _census_states.pop(entity_id, None)
            log.info(f"[{addr}] Player 0x{entity_id:08X} chose class "
                     f"{class_id} ({cls_name})")
            await send_npc_chat(
                writer, builder, session, npc_name,
                f"Congratulations! You are now a {cls_name}! "
                f"Your new abilities await. Relog to see your new stats "
                f"and appearance.", addr)
            return True
        elif msg.lower() in ('no', 'n', 'cancel'):
            _census_states[entity_id] = 'menu'
            await send_npc_chat(
                writer, builder, session, npc_name,
                "No problem! Choose another class number (1-13 or 15).", addr)
            return True
        else:
            await send_npc_chat(
                writer, builder, session, npc_name,
                f"Type 'yes' to become a {cls_name} or 'no' to pick again.", addr)
            return True

    return False


async def handle_npc_dialog(server, writer, builder, session, payload, addr):
    """Handle C->S 0x0044 NPC_DIALOG -- player selected a dialog option.

    C->S payload layout (after 4B counter+opcode):
      Bytes 4-7:  LE32 dialog_id (current dialog node ID) -- unconfirmed
      Byte  8:    B    option_index (0-based player choice) -- unconfirmed

    NOTE: Layout is inferred; verify against pcap once dialog packets are captured.
    """
    log.info(f"[{addr}] NPC_DIALOG ({len(payload)}B): {payload.hex(' ')}")

    state: DialogState | None = session.get('dialog_state')
    if state is None:
        log.warning(f"[{addr}] NPC_DIALOG received but no active dialog session")
        return

    # Parse option index -- best guess at byte 8 (after 4B counter+opcode + 4B unk)
    option_index = 0
    if len(payload) >= 9:
        option_index = payload[8]
    elif len(payload) >= 5:
        option_index = payload[4]

    dm = get_dialog_manager()
    next_state = dm.select_option(state, option_index)

    if next_state is None:
        # Dialog closed
        session['dialog_state'] = None
        log.info(f"[{addr}] Dialog {state.dialog_id} closed")
        # Send close acknowledgment
        # TODO: Verify close packet format
        close_pkt = build_dialog_close(state.npc_entity_id)
        pkt = builder.build_packet(pack_sub(close_pkt))
        writer.write(pkt)
        await writer.drain()
    else:
        # Check if this dialog node triggers an action (e.g. zone warp)
        if await process_dialog_actions(
                server, writer, builder, session, next_state, addr):
            return  # action executed, dialog consumed

        session['dialog_state'] = next_state
        # Send next dialog node
        resp = build_dialog_open(
            next_state.npc_entity_id, next_state.dialog_id,
            next_state.node.face)
        pkt = builder.build_packet(pack_sub(resp))
        writer.write(pkt)
        await writer.drain()


async def send_npc_chat(writer, builder, session, npc_name, text, addr):
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


def build_dialog_open(npc_entity_id, dialog_id, face_id):
    """Build S->C dialog open/advance packet (opcode 0x002B, placeholder layout).

    Placeholder layout (21 bytes -- TODO: confirm from pcap):
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


def build_dialog_close(npc_entity_id):
    """Build S->C dialog close packet (placeholder).

    TODO: Confirm opcode and format from pcap.
    """
    return struct.pack('<HI', 0x002B, npc_entity_id)
