"""Social handlers — chat, emotes, player details."""

import struct
import logging

from packet_builders import build_chat_msg, pack_sub
from handlers.npc import handle_census_chat

log = logging.getLogger('handlers.social')


async def handle_chat_send(server, writer, builder, session, payload, addr):
    """Handle C->S 0x002E CHAT_SEND."""
    if len(payload) < 5:
        log.debug(f"[{addr}] C->S CHAT_SEND (too short)")
        return

    msg_data = payload[4:]
    log.info(f"[{addr}] C->S CHAT_SEND ({len(msg_data)}B): "
             f"{msg_data.hex(' ')}")

    channel = msg_data[0] if len(msg_data) > 0 else 0

    text_start = 1
    text_end = msg_data.find(0x00, text_start)
    if text_end < 0:
        text_end = len(msg_data)
    message_text = msg_data[text_start:text_end].decode(
        'utf-8', errors='replace')

    if message_text:
        log.info(f"[{addr}] Chat (ch={channel}): {message_text}")

        # Check if player is in Census Angel class selection flow
        if await handle_census_chat(
                server, writer, builder, session, message_text, addr):
            return

        entity_id = session['entity_id']
        player_name = session.get('player_name', 'Player')
        chat_sub = build_chat_msg(
            sender_entity_id=entity_id,
            sender_name=player_name,
            message=message_text,
            pos_x=session.get('pos_x', 0),
            pos_y=session.get('pos_y', 0),
            chat_type=0x0001,
            channel=0x00,
        )
        # Echo to sender, then broadcast to everyone else on the map.
        pkt_sender = builder.build_packet(pack_sub(chat_sub))
        writer.write(pkt_sender)
        await writer.drain()
        await server.broadcast_to_zone(
            session.get('map_id', 0),
            pack_sub(chat_sub),
            exclude_entity=entity_id,
        )


async def handle_emote(server, writer, builder, session, payload, addr):
    """Handle C->S 0x0150 EMOTE."""
    emote_id = 0
    if len(payload) >= 6:
        emote_id = struct.unpack_from('<H', payload, 4)[0]
    log.info(f"[{addr}] C->S EMOTE id={emote_id}")


async def handle_request_player_details(server, writer, builder, session, payload, addr):
    """Handle C->S 0x001A REQUEST_PLAYER_DETAILS."""
    if len(payload) >= 8:
        target_eid = struct.unpack_from('<I', payload, 4)[0]
        log.info(f"[{addr}] C->S REQUEST_PLAYER_DETAILS "
                 f"0x{target_eid:08X}")
    else:
        log.debug(f"[{addr}] C->S REQUEST_PLAYER_DETAILS "
                  f"({len(payload)}B)")
