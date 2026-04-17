"""Miscellaneous handlers — entity select, shop, toggle, zone ready."""

import struct
import logging

from packet_builders import build_setting_16, build_keepalive_tick, pack_sub

log = logging.getLogger('handlers.misc')


async def handle_heartbeat(server, writer, builder, session, payload, addr):
    """Handle C->S 0x000F — anti-AFK / session heartbeat tick.

    Client sends one every ~5s with a monotonic millisecond counter at
    bytes 4-7. We previously misidentified this as TARGET_MOB. Swallow
    silently except at debug level.
    """
    if len(payload) >= 8:
        tick = struct.unpack_from('<I', payload, 4)[0]
        log.debug(f"[{addr}] heartbeat tick={tick}")


async def handle_entity_select(server, writer, builder, session, payload, addr):
    """Handle C->S 0x0006 ENTITY_SELECT."""
    if len(payload) >= 8:
        target_id = struct.unpack_from('<I', payload, 4)[0]
        log.info(f"[{addr}] C->S ENTITY_SELECT target=0x{target_id:08X}")
    else:
        log.debug(f"[{addr}] C->S ENTITY_SELECT ({len(payload)}B)")


async def handle_buy_sell(server, writer, builder, session, payload, addr):
    """Handle C->S 0x0012 BUY_SELL."""
    if len(payload) >= 8:
        item_id = struct.unpack_from('<H', payload, 4)[0]
        quantity = struct.unpack_from('<H', payload, 6)[0]
        log.info(f"[{addr}] C->S BUY_SELL item={item_id} qty={quantity}")
    else:
        log.debug(f"[{addr}] C->S BUY_SELL ({len(payload)}B)")


async def handle_toggle_action(server, writer, builder, session, payload, addr):
    """Handle C->S 0x003E TOGGLE_ACTION — sit/stand/meditate."""
    action_id = payload[4] if len(payload) >= 5 else 0
    entity_id = session['entity_id']
    log.info(f"[{addr}] C->S TOGGLE_ACTION action={action_id}")

    setting = build_setting_16(
        entity_id=entity_id,
        marker=0x3501,
        setting_id=0x074E,
        value_lo=0,
        value=action_id,
        value_hi=0,
    )
    pkt = builder.build_packet(pack_sub(setting))
    writer.write(pkt)
    await writer.drain()


async def handle_zone_ready(server, writer, builder, session, payload, addr):
    """Handle C->S 0x0143 ZONE_READY."""
    log.info(f"[{addr}] C->S ZONE_READY — client zone load complete")

    tick_sub = build_keepalive_tick()
    pkt = builder.build_packet(pack_sub(tick_sub))
    writer.write(pkt)
    await writer.drain()
