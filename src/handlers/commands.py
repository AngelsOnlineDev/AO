"""Slash-command dispatcher for in-game chat.

Chat messages starting with "/" are intercepted by handle_chat_send and
routed here. Commands are the fastest way to test server features without
implementing full NPC/UI wiring.

Supported commands:
    /help                   list commands
    /tp <x> <y>             teleport to map-local (x,y) pixel coordinates
    /heal                   restore HP/MP to class-level max and resync stats
    /level <N>              set character level (clamped 1..99), recomputes
                            stats and re-sends 0x0042
    /xp <N>                 grant N experience (can trigger level-ups)
    /setclass <N>           set class_id (0..9); recomputes stats, re-sends
    /g <message>            broadcast to global/world chat (0x0128, ch 0x0a)
    /where                  print your current map + position

All commands echo their result back to the sender as a system chat line.
"""

import logging

from packet_builders import (
    build_chat_msg,
    build_movement_resp,
    build_world_chat,
    pack_sub,
)
from class_stats import compute_stats, class_name
import database

log = logging.getLogger('handlers.commands')


async def handle_command_input(server, writer, builder, session, payload, addr):
    """Handle C->S opcode 0x0001 — the client's slash-command channel.

    The client strips the leading "/" before sending, so the payload is
    literally "cmdname args...\0" at bytes 4..end. Unlike 0x002E chat, this
    opcode has no channel byte — the whole thing after the opcode header
    is the command text.
    """
    if len(payload) < 4:
        return
    text_bytes = payload[4:]
    # Stop at first NUL if present
    nul = text_bytes.find(b'\x00')
    if nul >= 0:
        text_bytes = text_bytes[:nul]
    text = text_bytes.decode('utf-8', errors='replace').strip()
    if not text:
        return
    log.info(f"[{addr}] C->S command: /{text}")
    # handle_command expects the leading slash so it can be reused from
    # the chat dispatcher path too.
    await handle_command(server, writer, builder, session, '/' + text, addr)


async def handle_command(server, writer, builder, session, text: str, addr) -> bool:
    """Dispatch a slash command. Returns True if text was a command
    (whether it succeeded or not); False if not a command at all."""
    if not text.startswith('/'):
        return False

    parts = text[1:].split()
    if not parts:
        return True
    cmd = parts[0].lower()
    args = parts[1:]

    handler = _COMMANDS.get(cmd)
    if handler is None:
        await _reply(writer, builder, session,
                     f"Unknown command: /{cmd}. Try /help")
        return True

    try:
        await handler(server, writer, builder, session, args, addr)
    except Exception:
        log.exception(f"[{addr}] /{cmd} failed")
        await _reply(writer, builder, session,
                     f"/{cmd} errored — see server log")
    return True


async def _reply(writer, builder, session, message: str, channel: int = 0x0D):
    """Send a chat line back to the sender via the REAL chat opcode 0x0128.

    Channel 0x0D renders in the chat log as "Name: message" without the
    overhead speech bubble and without the scrolling top-banner (channel
    0x00 goes to the scrolling game-announcement ticker — wrong place for
    command output).

    The "Sarah:" prefix is inherent to channel 0x0D. We prepend "[System]"
    to the message content so users can distinguish command replies from
    real chat.
    """
    from packet_builders import build_world_chat
    sub = build_world_chat(
        sender_entity_id=session.get('entity_id', 0),
        sender_name=session.get('player_name', 'System'),
        message=f"[System] {message}",
        channel=channel,
    )
    log.debug(f"reply ch=0x{channel:02X}: sub={sub.hex(' ')}")
    writer.write(builder.build_packet(pack_sub(sub)))
    try:
        await writer.drain()
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass


def _refresh_player(session) -> None:
    """Re-read the DB row into session['player'] after a mutation."""
    eid = session.get('entity_id')
    if eid:
        session['player'] = database.get_player(eid)


async def _send_stats_resync(writer, builder, session) -> None:
    """Push a 0x0042 char-stats sub-message so the client updates HP/MP
    bars. Uses the captured tail bytes from init_pkt2 so we don't blank
    out the stat display (R.Atk/Def/etc) as a side effect."""
    try:
        from world_init_builder import get_char_stats_body
        player = session.get('player')
        if not player:
            return
        hp = player['hp'] if 'hp' in player.keys() else 294
        hp_max = player['hp_max'] if 'hp_max' in player.keys() else 294
        mp = player['mp'] if 'mp' in player.keys() else 280
        mp_max = player['mp_max'] if 'mp_max' in player.keys() else 280
        sub = get_char_stats_body(hp, hp_max, mp, mp_max)
        writer.write(builder.build_packet(pack_sub(sub)))
        await writer.drain()
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass


def _apply_level(entity_id: int, session, new_level: int) -> dict:
    """Set level (and by implication the stat pool) and update HP/MP max.
    Returns the recomputed stats dict so callers can log them."""
    new_level = max(1, min(99, int(new_level)))
    player = database.get_player(entity_id)
    class_id = player['class_id'] if player and 'class_id' in player.keys() else 0
    stats = compute_stats(class_id, new_level)
    database.update_player_full(
        entity_id,
        level=new_level,
        hp_max=stats['hp_max'],
        mp_max=stats['mp_max'],
        # Full refill on level change — same as the original game.
        hp=stats['hp_max'],
        mp=stats['mp_max'],
    )
    _refresh_player(session)
    return stats


# ----- individual commands -----

async def _cmd_help(server, writer, builder, session, args, addr):
    lines = [
        "/tp <x> <y>     teleport to coords",
        "/heal           full HP/MP refill",
        "/level <N>      set your level (1-99)",
        "/xp <N>         grant experience",
        "/setclass <N>   change class (0-9)",
        "/g <msg>        global chat",
        "/where          show position",
    ]
    for ln in lines:
        await _reply(writer, builder, session, ln)


async def _cmd_tp(server, writer, builder, session, args, addr):
    if len(args) != 2:
        await _reply(writer, builder, session, "Usage: /tp <x> <y>")
        return
    try:
        x = int(args[0])
        y = int(args[1])
    except ValueError:
        await _reply(writer, builder, session, "/tp: coords must be integers")
        return
    import config
    entity_id = session['entity_id']
    cur_x = session.get('pos_x', x)
    cur_y = session.get('pos_y', y)
    # Send a movement response that walks the character to the target.
    # speed=0 plays the walk animation but doesn't actually translate
    # the position, so we use a fast (but non-zero) speed. Short distance
    # means the client arrives in <1s.
    resp = build_movement_resp(
        entity_id, cur_x, cur_y, x, y, speed=config.MOVE_SPEED * 4)
    writer.write(builder.build_packet(resp))
    await writer.drain()
    session['pos_x'] = x
    session['pos_y'] = y
    database.update_player_position(entity_id, x, y)
    # Broadcast so other players see us move, too.
    try:
        import presence
        await presence.broadcast_movement(
            session, server.tracker, cur_x, cur_y, x, y,
            speed=config.MOVE_SPEED * 4)
    except Exception:
        log.debug("/tp broadcast failed", exc_info=True)
    await _reply(writer, builder, session, f"Teleported to ({x},{y})")


async def _cmd_heal(server, writer, builder, session, args, addr):
    entity_id = session['entity_id']
    player = session.get('player') or database.get_player(entity_id)
    class_id = player['class_id'] if player and 'class_id' in player.keys() else 0
    level = player['level'] if player and 'level' in player.keys() else 1
    stats = compute_stats(class_id, level)
    database.update_player_full(
        entity_id, hp=stats['hp_max'], mp=stats['mp_max'])
    _refresh_player(session)
    await _send_stats_resync(writer, builder, session)
    await _reply(writer, builder, session,
                 f"Healed to {stats['hp_max']}/{stats['mp_max']}")


async def _cmd_level(server, writer, builder, session, args, addr):
    if len(args) != 1:
        await _reply(writer, builder, session, "Usage: /level <1-99>")
        return
    try:
        new_level = int(args[0])
    except ValueError:
        await _reply(writer, builder, session, "/level: N must be an integer")
        return
    entity_id = session['entity_id']
    # Also snap total experience to match the level's threshold so
    # future /xp grants feel consistent with /level.
    database.update_player_full(
        entity_id, experience=database.xp_for_level(new_level))
    stats = _apply_level(entity_id, session, new_level)
    await _send_stats_resync(writer, builder, session)
    await _reply(writer, builder, session,
                 f"Level {new_level} — HP {stats['hp_max']}, MP {stats['mp_max']}, "
                 f"R.Atk {stats['ratk']}")


async def _cmd_xp(server, writer, builder, session, args, addr):
    if len(args) != 1:
        await _reply(writer, builder, session, "Usage: /xp <N>")
        return
    try:
        amount = int(args[0])
    except ValueError:
        await _reply(writer, builder, session, "/xp: N must be an integer")
        return
    entity_id = session['entity_id']
    player = database.get_player(entity_id)
    old_xp = player['experience'] if 'experience' in player.keys() else 0
    old_level = player['level'] if 'level' in player.keys() else 1
    new_xp = max(0, old_xp + amount)
    database.update_player_full(entity_id, experience=new_xp)
    new_level = database.level_for_xp(new_xp)
    if new_level != old_level:
        stats = _apply_level(entity_id, session, new_level)
        await _send_stats_resync(writer, builder, session)
        await _reply(writer, builder, session,
                     f"+{amount} XP (total {new_xp}) — LEVEL UP! "
                     f"{old_level} -> {new_level}")
    else:
        _refresh_player(session)
        to_next = database.xp_for_level(old_level + 1) - new_xp
        await _reply(writer, builder, session,
                     f"+{amount} XP (total {new_xp}, {to_next} to level {old_level + 1})")


async def _cmd_setclass(server, writer, builder, session, args, addr):
    if len(args) != 1:
        await _reply(writer, builder, session, "Usage: /setclass <0-9>")
        return
    try:
        new_cls = int(args[0])
    except ValueError:
        await _reply(writer, builder, session, "/setclass: N must be an integer")
        return
    if not 0 <= new_cls <= 9:
        await _reply(writer, builder, session,
                     "/setclass: class_id must be 0..9")
        return
    entity_id = session['entity_id']
    database.update_player_class(entity_id, new_cls)
    database.seed_equipment(entity_id, new_cls)
    _refresh_player(session)
    # Level-apply triggers stat recalc from the new class table.
    player = session.get('player') or database.get_player(entity_id)
    level = player['level'] if player and 'level' in player.keys() else 1
    _apply_level(entity_id, session, level)
    await _send_stats_resync(writer, builder, session)
    await _reply(writer, builder, session,
                 f"Class changed to {class_name(new_cls)} "
                 f"(restart login to see outfit change for other players)")


async def _cmd_global(server, writer, builder, session, args, addr):
    if not args:
        await _reply(writer, builder, session, "Usage: /g <message>")
        return
    message = ' '.join(args)
    entity_id = session['entity_id']
    name = session.get('player_name', 'Player')
    sub = build_world_chat(
        sender_entity_id=entity_id,
        sender_name=name,
        message=message,
        # Channel 0x0D renders as "[Name]: message" in the chat tab
        # (sub_6344A0 mode 1 per decompile of sub_5F3B40). Channel 0x0A
        # that we used before was silently dropped.
        channel=0x0D,
    )
    payload = pack_sub(sub)
    # Echo to sender so they see their own line, then fan out to all
    # connected world sessions regardless of map.
    writer.write(builder.build_packet(payload))
    await writer.drain()
    await _broadcast_all(server, payload, exclude_entity=entity_id)


async def _cmd_where(server, writer, builder, session, args, addr):
    x = session.get('pos_x', 0)
    y = session.get('pos_y', 0)
    map_id = session.get('map_id', 0)
    await _reply(writer, builder, session,
                 f"map={map_id}  pos=({x},{y})  entity=0x{session['entity_id']:08X}")


async def _broadcast_all(server, payload: bytes, exclude_entity: int = 0):
    """Send to every active world session (any map)."""
    sessions = list(server.sessions.values())
    for s in sessions:
        if s.get('entity_id') == exclude_entity:
            continue
        try:
            s['writer'].write(s['builder'].build_packet(payload))
        except (ConnectionResetError, BrokenPipeError, OSError, KeyError):
            pass
    # Fire-and-forget drain on each.
    import asyncio
    await asyncio.gather(
        *(_drain(s) for s in sessions),
        return_exceptions=True,
    )


async def _drain(session):
    try:
        await session['writer'].drain()
    except (ConnectionResetError, BrokenPipeError, OSError, KeyError):
        pass


async def _cmd_chattest(server, writer, builder, session, args, addr):
    """Send 9 chat packets on the REAL chat opcode (0x0128) with every
    channel the client dispatcher handles. Report which labels render
    and where they appear.
    """
    from packet_builders import build_world_chat
    entity_id = session.get('entity_id', 0)
    player_name = session.get('player_name', 'Player')
    # Every channel sub_5F3B40 handles non-default.
    channels = [0x00, 0x01, 0x02, 0x0A, 0x0B, 0x0C, 0x0D, 0x0F, 0x10]
    for ch in channels:
        sub = build_world_chat(
            sender_entity_id=entity_id,
            sender_name=player_name,
            message=f"chattest ch=0x{ch:02X}",
            channel=ch,
        )
        log.info(f"chattest sending via 0x0128 channel=0x{ch:02X}")
        writer.write(builder.build_packet(pack_sub(sub)))
    try:
        await writer.drain()
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass


_COMMANDS = {
    'help':     _cmd_help,
    'tp':       _cmd_tp,
    'heal':     _cmd_heal,
    'level':    _cmd_level,
    'xp':       _cmd_xp,
    'setclass': _cmd_setclass,
    'g':        _cmd_global,
    'where':    _cmd_where,
    'chattest': _cmd_chattest,
}
