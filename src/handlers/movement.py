"""Movement and zone transfer handlers."""

import struct
import logging

from packet_builders import build_movement_resp, pack_sub
from area_entity_data import get_area_packets
from game_data import get_map, get_npc_db, get_dialog_manager
from dialog_manager import DialogState
import config
import database

log = logging.getLogger('handlers.movement')


async def handle_movement(server, writer, builder, session, payload, addr):
    """Handle C->S 0x0004 movement request."""
    if len(payload) < 15:
        return

    log.info(f"[{addr}] Movement raw ({len(payload)}b): {payload.hex(' ')}")

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

    session['pos_x'] = dest_x
    session['pos_y'] = dest_y
    database.update_player_position(entity_id, dest_x, dest_y)


async def handle_zone_transfer(server, writer, builder, session,
                                dest_map_id, spawn_point, addr):
    """Transfer the player to a different map/zone."""
    old_map_id = session.get('map_id', config.START_MAP_ID)
    entity_id = session['entity_id']

    # Resolve spawn position
    spawn_pos = config.MAP_SPAWN_POINTS.get((dest_map_id, spawn_point))
    if spawn_pos is None:
        spawn_pos = config.MAP_SPAWN_POINTS.get((dest_map_id, 0))
    if spawn_pos is None:
        spawn_pos = config.DEFAULT_SPAWN
    dest_x, dest_y = spawn_pos

    log.info(f"[{addr}] Zone transfer: map {old_map_id} → {dest_map_id} "
             f"(spawn_point={spawn_point}, pos=({dest_x},{dest_y}))")

    # Load new map
    new_map_data = get_map(dest_map_id)
    npc_db = get_npc_db()

    # Merge new map's local dialogs
    dm = get_dialog_manager()
    if new_map_data is not None and new_map_data.local_dialogs:
        dm.merge_local_dialogs(new_map_data.local_dialogs)

    # Use map center as fallback if no spawn point defined
    if new_map_data is not None and spawn_pos == config.DEFAULT_SPAWN:
        center_x = (new_map_data.width * new_map_data.tile_w) // 2
        center_y = (new_map_data.height * new_map_data.tile_h) // 2
        if center_x > 0 and center_y > 0:
            dest_x, dest_y = center_x, center_y
            log.info(f"[{addr}] Using map center as spawn: ({dest_x},{dest_y})")

    # Reset entity registry and generate new area packets
    new_entity_registry: dict[int, int] = {}
    area_pkts = get_area_packets(
        map_data=new_map_data,
        npc_db=npc_db,
        monster_db=session.get('monster_db'),
        entity_registry=new_entity_registry,
    )

    # Send area packets for the new zone
    for payload_bytes, compressed in area_pkts:
        pkt = builder.build_packet(payload_bytes, compressed=compressed)
        writer.write(pkt)
    if area_pkts:
        await writer.drain()
        log.info(f"[{addr}] Sent {len(area_pkts)} area packets for map {dest_map_id}")

    # Send updated position to client
    resp_payload = build_movement_resp(
        entity_id, dest_x, dest_y, dest_x, dest_y, config.MOVE_SPEED)
    pkt = builder.build_packet(resp_payload)
    writer.write(pkt)
    await writer.drain()

    # Update session
    session['map_id'] = dest_map_id
    session['pos_x'] = dest_x
    session['pos_y'] = dest_y
    session['map_data'] = new_map_data
    session['entity_registry'] = new_entity_registry
    session['dialog_state'] = None

    # Persist to database
    database.update_player_map(entity_id, dest_map_id, dest_x, dest_y)

    log.info(f"[{addr}] Zone transfer complete: now on map {dest_map_id} "
             f"at ({dest_x},{dest_y})")


async def process_dialog_actions(server, writer, builder, session,
                                  state: DialogState, addr) -> bool:
    """Check a dialog state for executable actions (e.g. warp).

    Returns True if an action was executed (caller should stop dialog flow).
    """
    if not state or not state.node.actions:
        return False

    for action in state.node.actions:
        if action.action_type == 37 and action.params:
            dest_map = action.params[0]
            spawn_point = action.params[1] if len(action.params) > 1 else 0
            session['dialog_state'] = None
            await handle_zone_transfer(
                server, writer, builder, session, dest_map, spawn_point, addr)
            return True

    return False
