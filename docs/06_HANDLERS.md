# Game Handlers

All handlers share this signature:
```python
async def handler(server, writer, builder, session, payload, addr)
```

## Movement (handlers/movement.py)

### handle_movement (Opcode 0x0004)

**C->S**: `[4B header][7B unk][LE16 dest_x at +11][LE16 dest_y at +13]`

**Process**:
1. Read destination from payload bytes 11-14
2. Calculate distance from current position
3. If distance > `MAX_MOVE_STEP` (200px), clamp to max step
4. Build movement response
5. Update session `pos_x`, `pos_y`
6. Persist to database
7. Broadcast to zone

**S->C Response** (30 bytes framed):
```
[LE16 len=2] [LE16 0x006D]                                 # Move flag
[LE16 len=24][LE16 0x0005][LE32 entity_id]                  # Position
             [LE32 cur_x][LE32 cur_y]
             [LE32 dst_x][LE32 dst_y]
             [LE16 speed=110]
```

### handle_zone_transfer

Called by NPC gates and dialog actions (action type 37).

**Process**:
1. Resolve spawn position from `config.MAP_SPAWN_POINTS[(map_id, point)]`
2. Load new map data from PAK files
3. Load NPC/monster databases
4. Merge map-local dialogs into DialogManager
5. Create fresh entity registry
6. Generate and send area packets (NPC/monster spawns)
7. Send movement response with new position
8. Update session state and database

### process_dialog_actions

Checks if a dialog node has executable actions (e.g., zone warp).

**Supported action types**:
- **37**: Zone warp — `params[0]` = dest_map_id, `params[1]` = spawn_point

---

## NPC Interaction (handlers/npc.py)

### handle_entity_action (Opcode 0x000D)

**C->S**: `[4B header][LE32 runtime_entity_id at +4]`

**NPC Resolution Flow**:
```
1. Store runtime_entity_id in session['last_npc_entity_id']
2. Look up NPC type: entity_registry[runtime_entity_id] -> npc_type_id
3. Look up NPC name: npc_db[npc_type_id] -> npc_info
4. Check NPC_BEHAVIORS dict for hardcoded behaviors
5. If no behavior, check map_data.npc_dialogs for dialog_id
6. Start dialog or send chat message
```

### NPC Behaviors

Hardcoded behaviors for specific NPC type IDs:

| NPC Type ID | Name | Behavior |
|-------------|------|----------|
| 2006 | Census Angel | Class selection dialog |
| 2429 | BattlefieldAngel | Shop (shop_id=1) |
| 8804 | Blessing Angel | Totem (message only) |
| 1553 | House Pickets | Gate -> map 3 |
| 1554 | Gaoler Angel | Gate -> map 3 |
| 1938 | Dark City Tot | Totem |
| 1940 | Breeze Totem | Totem |

**Behavior Types**:
- `'gate'`: Instant zone transfer to `dest_map` with `spawn_point`
- `'shop'`: Welcome message + shop open (TODO)
- `'totem'`: System message
- `'census_angel'`: Class selection state machine
- `'dialog'`: Start specific dialog_id
- `'quest_npc'`: Quest progression

### Census Angel (Class Selection)

State machine per player entity:

```
State 'menu':           Show class list, wait for number
State 'confirm_<N>':    Player picked class N, ask yes/no
```

**Class Options**:
| ID | Class | Category |
|----|-------|----------|
| 1 | Priest | Combat |
| 2 | Summoner | Combat |
| 3 | Wizard | Combat |
| 4 | Magician | Combat |
| 5 | Protector | Heavy |
| 6 | Warrior | Heavy |
| 7 | Swordsman | Heavy |
| 8 | Spearman | Heavy |
| 9 | Archer | Ranged |
| 10 | Weaponsmith | Crafting |
| 11 | Armorsmith | Crafting |
| 12 | Tailor | Crafting |
| 13 | Technician | Crafting |
| 15 | Chef | Crafting |

Selection is done via chat messages intercepted by `handle_census_chat()`.

### handle_npc_dialog (Opcode 0x0044)

**C->S**: `[4B header][4B unk][1B option_index at +8]`

**Process**:
1. Get active dialog state from session
2. Call `dm.select_option(state, option_index)` to advance
3. If next state is None -> send dialog close, clear state
4. If next state exists -> check for actions (warp, etc.), send dialog text

### send_npc_chat

Sends NPC speech as a chat message (workaround while real dialog opcode is unknown).

```
[LE16 0x001E][LE16 chat_type=0x0001][LE32 npc_entity_id]
[8B npc_name][LE32 pos_x][LE32 pos_y][1B channel=0x01]
[var message + null]
```

---

## Combat (handlers/combat.py)

### handle_stop_action (Opcode 0x0009)
Clears dialog state. No response sent.

### handle_target_mob (Opcode 0x000F)

**C->S**: `[4B header][LE32 mob_id at +4]`

**Process**:
1. Store `session['target_mob_id'] = mob_id`
2. Send entity status response

**S->C**: Entity Status (0x000B, 13 bytes) with status_a=1, status_b=1

### handle_use_skill (Opcode 0x0016)

**C->S**: `[4B header][1B skill_id at +4][LE32 target_id at +5]`

**Process**:
1. If target_id != 0, send combat action response
2. Currently uses hardcoded damage=100

**S->C**: Combat Action (0x0019, 27 bytes)
```
[source_entity][target_entity][action_type=2][skill_id][damage=100][flags=0]
```

---

## Social (handlers/social.py)

### handle_chat_send (Opcode 0x002E)

**C->S**: `[4B header][1B channel][var message (null-terminated)]`

**Process**:
1. Parse channel byte and message text
2. Check Census Angel interception (if player in class selection)
3. Build chat message response
4. Broadcast to zone

**S->C**: Chat Message (0x001E, variable)

### handle_emote (Opcode 0x0150)

**C->S**: `[4B header][LE16 emote_id at +4]`

Currently logging only, no response.

### handle_request_player_details (Opcode 0x001A)

**C->S**: `[4B header][LE32 target_entity_id at +4]`

Currently logging only, no response.

---

## Miscellaneous (handlers/misc.py)

### handle_entity_select (Opcode 0x0006)

**C->S**: `[4B header][LE32 target_entity_id at +4]`

Currently logging only.

### handle_buy_sell (Opcode 0x0012)

**C->S**: `[4B header][LE16 item_id at +4][LE16 quantity at +6]`

Currently logging only.

### handle_toggle_action (Opcode 0x003E)

**C->S**: `[4B header][1B action_id at +4]`

**S->C**: Entity Setting (0x001D, 16 bytes)
```
entity_id + marker=0x3501 + setting_id=0x074E + value=action_id
```

Broadcasts the sit/stand/meditate state to the zone.

### handle_zone_ready (Opcode 0x0143)

**C->S**: `[4B header]` (no payload)

Client signals zone load complete. Server responds with a keepalive tick.

**S->C**: Keepalive Tick (0x018A, 10 bytes)

---

## Dialog System

### Data Structures

```python
DialogNode:
    dialog_id: int
    msg_id: int          # Text ID from msg.xml
    text: str            # Resolved display text
    face: int            # Portrait sprite ID
    options: list[DialogOption]
    triggers: list[dict]
    unconditional_next: int
    actions: list[DialogAction]

DialogOption:
    msg_id: int
    text: str
    next_id: int         # 0 = close dialog

DialogAction:
    action_type: int     # 25=start dialog, 37=zone warp
    params: list[int]

DialogState:
    dialog_id: int
    node: DialogNode
    npc_entity_id: int
```

### Dialog Flow

```
Player clicks NPC (0x000D)
    -> Look up npc_type_id from entity_registry
    -> Look up dialog_id from map_data.npc_dialogs
    -> dm.start_dialog(dialog_id) -> DialogState
    -> Check for immediate actions (warp)
    -> Send NPC speech via chat message
    -> Store state in session['dialog_state']

Player selects option (0x0044)
    -> dm.select_option(state, option_index) -> next DialogState
    -> If closed: clear state
    -> If new state: check actions, send speech, store state
```

### Text Formatting

From msg.xml, text contains color codes:
- `/c$N` — start color N (e.g., `/c$2` = green)
- `/c*` — end color
- `%N%` — parameter placeholder N
- `\n` — newline

Example: `"/c$2%1/c*, welcome to Eden!"` (player name in green)
