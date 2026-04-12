# Handlers

Handlers live under [src/handlers/](../src/handlers/) and share the same signature:

```python
async def handler(server, writer, builder, session, payload, addr):
    ...
```

They're wired into the world server via the `OPCODE_HANDLERS` dict in [world_server.py:54](../src/world_server.py#L54). This doc says what each handler actually *does* — not just its opcode.

---

## movement.py

### `handle_movement` — C→S `0x0004` ✅

Payload: `[4B frame][7B unk][LE16 dst_x@+11][LE16 dst_y@+13]`. The client also fills in the "from" position at bytes 4 and 6 but we trust the *from* values the client reports.

Flow:
1. Read `from_x/y` and `dst_x/y` from the packet.
2. Snap current session position to `from_x/y` (the client's belief wins — our session state comes from replayed captures and doesn't always match).
3. Clamp total step distance to `MAX_MOVE_STEP` (200 px). Larger moves get scaled to the 200-px ray toward the requested destination.
4. Build a **30-byte move response** (the `0x006D + 0x0005` pair) and write it to the player's own socket.
5. Persist new `pos_x/y` to the DB.
6. Call `presence.broadcast_movement(...)` so every other player on the same `map_id` receives a `0x0005` update for this entity.

❌ **No collision check.** The server has no walkability data — a malicious client can walk through walls. Fix requires parsing `.MPC` collision layers, which is blocked on PAK extraction.

### `handle_zone_transfer`

Called by dialog-action warps (action_type `37`) and NPC gates. Not dispatched from an opcode — triggered internally.

1. Resolve spawn position from `config.MAP_SPAWN_POINTS[(dest_map_id, spawn_point)]` → fall back to `DEFAULT_SPAWN` → fall back to map center if we have a `MapData`.
2. Load `MapData(dest_map_id)` (from PAK if available, else empty).
3. Merge the destination map's local dialogs into the dialog manager.
4. Rebuild the runtime entity registry for the new zone.
5. Generate and send area entity packets (NPC/mob spawns).
6. Send a move response at the new position so the client relocates.
7. Persist `map_id / pos_x / pos_y`.

❓ Presence is not re-broadcast on zone transfer — other players on the destination map don't see the arrival until the next movement tick.

### `process_dialog_actions`

Checks a dialog node's `actions` list for executable actions:

- **Action 37 (zone warp)** ✅: `params = [dest_map_id, spawn_point, flag?]` — calls `handle_zone_transfer`.

Any other action type is ignored. Dialog-driven quest/item granting isn't implemented.

---

## npc.py

### `handle_entity_action` — C→S `0x000D` ✅ (+ `0x0005`, `0x0019`)

Payload: `[4B frame][LE32 runtime_entity_id@+4]`. `0x0005` and `0x0019` reuse the same handler because their payload shape is identical and the client uses them for "clicked" on an entity in different contexts.

Flow:
1. Resolve `runtime_entity_id → npc_type_id` via the session's `entity_registry`.
2. Look up `npc_type_id` in `npc_db` (from `npc.xml`).
3. If `npc_type_id < 1500`, it's a monster — route to `combat.handle_auto_attack` for a basic attack. **New routing — clicks on mice now actually damage them.**
4. Otherwise check the `NPC_BEHAVIORS` dict for a hardcoded behavior. Seven entries right now:

   | NPC type | Name | Behavior | Notes |
   |---|---|---|---|
   | 2006 | Census Angel | `census_angel` | starts class-selection state machine |
   | 2429 | BattlefieldAngel | `shop` | sends welcome, shop UI is TODO |
   | 8804 | Blessing Angel | `totem` | system message only |
   | 1553 | House Pickets | `gate` → map 3 | ❓ destination spawn point is guessed |
   | 1554 | Gaoler Angel | `gate` → map 3 | ❓ same |
   | 1938 | Dark City Tot | `totem` | |
   | 1940 | Breeze Totem | `totem` | |

5. If there's no hardcoded behavior, look up `map_data.npc_dialogs[runtime_entity_id]` to start a dialog from `msg.xml`.
6. Anything with no dialog and no behavior just gets a log line.

### Census Angel class-selection

Per-player state stored in `npc._census_states[entity_id]`:

```
state = 'menu'        → waiting for the player to type a class number in chat
state = 'confirm_<N>' → we asked "do you want class N? (yes/no)", waiting for yes/no
```

The chat handler intercepts player messages and routes them here via `handle_census_chat`. On confirmed selection:

1. Update `players.class_id` in the DB.
2. Re-read the player row into `session['player']`.
3. Send a confirmation chat message.

Only runs for Novices (class 0). Already-classed players get "you're already a `<class>`" and the state machine is not entered.

Available classes come from `setting/eng/class.xml`:

| ID | Name | Notes |
|---|---|---|
| 0 | Novice | default |
| 1 | Priest | |
| 2 | Summoner | |
| 3 | Wizard | |
| 4 | Magician | |
| 5 | Protector | |
| 6 | Warrior | |
| 7 | Swordsman | |
| 8 | Spearman | |
| 9 | Archer | |
| 10-15 | Weaponsmith / Armorsmith / Tailor / Technician / (skip 14) / Chef | crafting classes — Census Angel menu exposes these but [class_stats.py](../src/class_stats.py) has no stat table for them yet |

### `handle_npc_dialog` — C→S `0x0044` ✅

Payload: `[4B frame][4B unk][u8 option_idx@+8]`.

1. Get `session['dialog_state']`.
2. Call `dm.select_option(state, option_idx)` → next `DialogState` or `None`.
3. If `None`, send a dialog-close message and clear state.
4. Otherwise run `process_dialog_actions` on the new state (for warps); if the state survives, send the NPC speech and store it.

### `send_npc_chat` — workaround ❌

NPC speech is currently sent as a `0x001E` chat message with `chat_type=0x0001, channel=0x01` so it renders in the chat bubble. The **real** NPC-dialog opcode hasn't been identified — this is a hack that works well enough to advance dialogs, but the client doesn't render the proper dialog box with portrait and options. See [09_REVERSE_ENGINEERING.md](09_REVERSE_ENGINEERING.md).

---

## combat.py

### `handle_stop_action` — C→S `0x0009` ✅

Clears `session['dialog_state']`. No packet sent back. Also used as "cancel dialog" since there's no dedicated cancel opcode.

### `handle_target_mob` — C→S `0x000F` ✅

Payload: `[4B frame][LE32 mob_id@+4]`.

Stores `session['target_mob_id']` and sends back an entity status (`0x000B`) with the mob "alive, targeted" bytes. The client uses this to highlight the target.

### `handle_use_skill` — C→S `0x0016` ✅ (partial)

Payload: `[4B frame][u8 skill_id@+4][LE32 target@+5]`.

Shared damage logic lives in `_resolve_and_hit`, used by both skill casts and auto-attack:

1. If `target_id` is a mob in the seed registry → lazy-register it with `server.mobs` using the monster.xml HP pool.
2. Compute damage via `_compute_damage(session)` — uses `class_stats` `ratk + sp_atk` scaled by a ±20% random roll.
3. Apply damage to `server.mobs`.
4. Send a `0x0019` combat action to the attacker (and broadcast to the zone, ❓ may double-animate).
5. If the mob's HP hits 0 → death animation + mark for respawn (30s via `MobRegistry.RESPAWN_DELAY_SEC`).

❌ **HP bar doesn't update**. `0x0019` renders the hit animation and damage number but doesn't move the target's health bar — the real HP update opcode is still unidentified. We've been wrong about which opcode it is at least twice.

### `handle_auto_attack`

Entry point for `npc.handle_entity_action` when a monster is clicked. Same internal path as `_resolve_and_hit`, just without a skill_id.

---

## social.py

### `handle_chat_send` — C→S `0x002E` ✅

Payload: `[4B frame][u8 channel][NUL-term text]`.

1. Parse channel byte and text (stop at first NUL).
2. First check: if the player has an active Census Angel state, route the text to `npc.handle_census_chat` and return.
3. Otherwise build a `0x001E` chat sub-message.
4. Echo to the sender (so their own message appears in their chat box).
5. Broadcast to everyone else on the same map via `server.broadcast_to_zone`.

### `handle_emote` — C→S `0x0150`

Just logs the emote ID. ❓ We haven't figured out the S→C packet that plays an emote animation, so nothing visual happens.

### `handle_request_player_details` — C→S `0x001A`

Log only. ❓ The real server's response layout is unknown — we've seen the request but never captured the response.

---

## misc.py

### `handle_entity_select` — C→S `0x0006`

Log only. Fired when the client "soft-selects" an entity (hover/click without action). No response required.

### `handle_buy_sell` — C→S `0x0012`

Log only. Would normally be a shop transaction. No item/inventory system yet.

### `handle_toggle_action` — C→S `0x003E` ✅

Payload: `[4B frame][u8 action_id@+4]`. Sit / stand / meditate / mount toggle.

Sends a `0x001D` entity-setting sub-message back with `entity_id + marker=0x3501 + setting_id=0x074E + value=action_id`. Broadcasts to the zone so other players see the animation.

### `handle_zone_ready` — C→S `0x0143` ✅

The client sends this once its zone has finished loading. We reply with a `0x018A` keepalive tick (the smallest valid response) so the client knows the server is still there.

---

## Dialog manager ([dialog_manager.py](../src/dialog_manager.py))

Not a handler per se, but every NPC interaction goes through it.

### Data shapes

```python
DialogNode:
    dialog_id: int
    msg_id: int                  # index into msg.xml
    text: str                    # resolved, with color codes
    face: int                    # portrait sprite
    options: list[DialogOption]
    triggers: list[dict]         # ❓ unused
    unconditional_next: int
    actions: list[DialogAction]

DialogOption:
    msg_id: int
    text: str
    next_id: int                 # 0 = close dialog

DialogAction:
    action_type: int             # 25 = start dialog, 37 = zone warp
    params: list[int]

DialogState:                     # lives in session['dialog_state']
    dialog_id: int
    node: DialogNode
    npc_entity_id: int
```

### Text formatting

Text strings in `msg.xml` use Angels Online color codes:

- `/c$N` — start color N (`2` = green, etc.)
- `/c*` — end color
- `%N%` — parameter placeholder
- `\n` — line break

Example: `"/c$2%1/c*, welcome to Eden!"` renders with the player's name in green.

---

## What's not implemented

- ❌ **Item / inventory system** — the client sends `0x0012 BUY_SELL` but we have no items table, so the handler just logs.
- ❌ **Equipment** — no storage, no equip packet, no wear-it-on-the-model wiring.
- ❌ **Party/guild opcodes** — `0x014A` party name is in the init packet but we don't handle party operations.
- ❌ **Trade** — never touched.
- ❌ **Quest progression** — the dialog manager parses `EVENT.XML` quest nodes but `handle_npc_dialog` only processes warps (action 37), not quest grants (actions 1, 2, 3, 25).
- ❌ **Combat HP-bar update** — renders damage but target HP stays full.
