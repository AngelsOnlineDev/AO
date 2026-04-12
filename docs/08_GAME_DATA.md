# Game Data & Content

## Data Sources Overview

| Source | Location | Format | Content |
|--------|----------|--------|---------|
| msg.xml | `data/game_xml/` | XML | 42,053 text strings (dialog text, UI labels) |
| spmsg.xml | `data/game_xml/` | XML | 24,450 global dialog nodes |
| EVENT.XML | `data/game_xml/setting/` | XML | 39 global event dialog nodes |
| npc.xml | `data/game_xml/` | XML | 827 NPC type definitions |
| monster.xml | `data/game_xml/` | XML | 1,682 monster stat definitions |
| quest.xml | `data/game_xml/` | XML | Quest definitions |
| *.pak files | AO install dir | ZIP-like | Maps, textures, sounds |
| *.hex files | `tools/seed_data/` | Hex text | Pre-captured init/area packets |

## Lazy Loading (game_data.py)

All game data is loaded as singletons on first access:

```python
get_dialog_manager()   # -> DialogManager (msg.xml + spmsg.xml + EVENT.XML)
get_npc_db()           # -> dict[int, dict] from npc.xml
get_monster_db()       # -> dict[int, dict] from monster.xml
get_map(map_id)        # -> MapData from PAK files
```

---

## XML Files

### msg.xml — Text Strings

42,053 entries mapping ID to display text.

```xml
<字串 編號="5001" 文字="/c$2%1/c*, welcome to the Guidance Class..." />
<字串 編號="5008" 文字="I'm ready." />
```

| Attribute | Meaning |
|-----------|---------|
| `編號` | String ID (integer) |
| `文字` | Display text (may contain formatting codes) |

**Text Formatting Codes**:
- `/c$N` — Start color N (e.g., `/c$2` = green, `/c$9` = item reference)
- `/c*` — End color
- `%N%` — Parameter placeholder (player name, item ID, etc.)
- `\n` — Newline

### spmsg.xml — Dialog Trees

24,450 dialog node definitions. Each node has text, portrait, options, triggers, and actions.

```xml
<對話 編號="5133" 訊息="5132" 臉譜="3" RefEvent="0" FirstMsg="5133">
    <選項 訊息="5133" 下一句="2013853"/>
    <選項 訊息="5131" 下一句="2013855"/>
    <觸發器 編號="0" 觸發="0" 條件="1">
        <條件 編號="38"><參數 數值="1601"/></條件>
    </觸發器>
    <成立 下一句="7809"/>
</對話>
```

| Element | Attribute | Meaning |
|---------|-----------|---------|
| `對話` (Dialog) | `編號` | Dialog node ID |
| | `訊息` | Text msg_id (lookup in msg.xml) |
| | `臉譜` | Portrait sprite ID |
| `選項` (Option) | `訊息` | Option text msg_id |
| | `下一句` | Next dialog ID (0 = close) |
| `觸發器` (Trigger) | `條件` | Condition type |
| `條件` (Condition) | `編號` | Condition check type |
| `參數` (Param) | `數值` | Condition parameter |
| `動作` (Action) | `編號` | Action type to execute |
| `成立` (Branch) | `下一句` | Unconditional next dialog |

**Condition Types**: 38 (quest/flag check), 39 (item possession), 7 (quantity), 10 (level)

**Action Types**:
| Type | Meaning | Parameters |
|------|---------|------------|
| 25 | Start dialog | `[dialog_id]` |
| 37 | Zone warp | `[map_id, spawn_point, flag]` |
| 3, 4, 10 | Script-based | varies |

### EVENT.XML — Global Events

Same format as spmsg.xml. Contains 39 dialog nodes for global events. IDs don't overlap with spmsg.xml.

### npc.xml — NPC Definitions

827 NPC type definitions (type_id >= 1500).

```xml
<npc 編號="1501" 圖號="40001" 名稱="Michael" 等級="99" 陣營="中立"/>
```

| Attribute | Meaning |
|-----------|---------|
| `編號` | NPC type ID (1500+) |
| `圖號` | Sprite/graphics ID |
| `名稱` | Display name |
| `等級` | Level (decorative for NPCs) |
| `陣營` | Faction (中立=neutral) |
| `問安語音` | Greeting voice sound ID |
| `道別語音` | Farewell voice sound ID |

**Loaded as**: `dict[npc_type_id] -> {'name': str, 'sprite_id': int}`

### monster.xml — Monster Definitions

1,682 monster definitions (type_id < 1500).

```xml
<npc 編號="1" 圖號="42055" 名稱="Wind Elf" 等級="4" 系別="能量"
     陣營="怪物陣營" HP="162" 平均攻擊="41" 防禦="41" 魔攻="33"
     魔防="21" 精準="32" 靈敏="24" 移動速度="50" 攻擊速度="70"
     重擊機率="5" 經驗價值="41" />
```

| Attribute | Chinese | Meaning |
|-----------|---------|---------|
| `編號` | - | Monster ID (1-1499) |
| `名稱` | - | Name |
| `等級` | - | Level |
| `系別` | Element | 能量=Energy, 植物=Plant, 動物=Animal, 人型=Humanoid, 惡魔=Demon, 不死=Undead |
| `HP` | - | Health points |
| `平均攻擊` | Avg Attack | Average attack damage |
| `攻擊變數` | Atk Variance | Damage range |
| `防禦` | Defense | Physical defense |
| `魔攻` | Magic Atk | Magic attack |
| `魔防` | Magic Def | Magic defense |
| `精準` | Accuracy | Hit accuracy |
| `靈敏` | Agility | Evasion/speed |
| `火焰攻擊/防禦` | Fire Atk/Def | Fire element stats |
| `寒冰攻擊/防禦` | Ice Atk/Def | Ice element stats |
| `雷電攻擊/防禦` | Lightning | Lightning element stats |
| `腐蝕攻擊/防禦` | Corruption | Corruption element stats |
| `移動速度` | Move Speed | Movement animation speed |
| `攻擊速度` | Atk Speed | Attack animation speed |
| `重擊機率` | Crit Chance | Critical strike % |
| `經驗價值` | EXP Value | Experience reward |
| `攻擊法術1/2` | Atk Spells | Attack spell IDs |
| `補血法術1` | Heal Spell | Healing spell ID |
| `王` | Boss | 是=yes (boss flag) |
| `免疫媚惑/定身/冰凍` | Immunities | Status effect immunities |

### quest.xml — Quest Definitions

```xml
<任務 編號="100" 任務名稱="Registration at Angels' Tutor"
     任務類型="Angel Lyceum" 任務前言="[description]"
     可否重接="可重接"
     地點01="Angel Lyceum" 承接01="Angels' Tutor"
     步驟01="1. Talk with Angels' Tutor"/>
```

| Attribute | Meaning |
|-----------|---------|
| `編號` | Quest ID |
| `任務名稱` | Quest name |
| `任務類型` | Category (Angel Lyceum, Aurora City, etc.) |
| `任務前言` | Description |
| `可否重接` | "可重接" = repeatable |
| `地點NN` | Step N location |
| `承接NN` | Step N NPC name |
| `步驟NN` | Step N description |
| `刪除道具01` | Item consumed on completion |

**Objective parsing** in step descriptions: `/c$9%ID%/c* /COUNT` -> kill/collect ID x COUNT

---

## Map Loading System

### PAK Archives

Maps are stored in `.mpc` files within PAK archives. Search order (newest first):

```
UPDATE10.PAK, UPDATE9.PAK, ..., UPDATE2.PAK, update.pak, data1.pak
```

PAK files are zlib-compressed archives in the AO install directory.

### MPC File Format

```
Header:
  [4B]  magic = "MAP\0"
  [4B]  map_width (tiles)
  [4B]  map_height (tiles)
  [4B]  flags
  [4B]  tile_w (pixels, usually 32)
  [4B]  tile_h (pixels, usually 32)
  [4B]  entity_section_offset
  [4B]  entity_section_size
  [4B]  event_xml_offset
  [4B]  event_xml_size
  ...
  [4B]  dialog_xml_offset

Tile Data:
  [tile_w * tile_h * record_size]

Entity Section:
  [19B] entity list header
  [N * 74B] entity records

Event XML Section:
  [event_xml_size] XML data

Dialog XML Section:
  [remaining] XML data (per-map dialogs)
```

### Entity Record (74 bytes per entity)

```
Offset  Size  Field
0       4B    x_pixel    (tile_x = x_pixel / 32)
4       4B    y_pixel    (tile_y = y_pixel / 32)
8       4B    entity_id  (NPC type >= 1500, monster type < 1500)
12      4B    flags      (event link for NPCs)
16      4B    direction  (0=none, 2=facing direction)
20      54B   padding/reserved
```

### Map Event XML

```xml
<事件 編號="1" 游標="3">
    <觸發器 觸發="2">
        <動作 編號="25"><參數 數值="21901"/></動作>
    </觸發器>
</事件>
```

Trigger type 2 = player click/talk. Action type 25 = start dialog. Action type 37 = zone warp.

### Map Dialog XML

Same format as spmsg.xml. Contains map-local dialog nodes (IDs like 21901, 23101). Merged into the DialogManager when a player enters the zone.

### MapData Object

```python
class MapData:
    map_id: int
    width: int                              # Tiles
    height: int                             # Tiles
    tile_w: int = 32                        # Pixels per tile
    tile_h: int = 32
    entities: list[MapEntity]               # All spawned entities
    events: dict[int, MapEvent]             # event_id -> event
    npc_dialogs: dict[int, int]             # npc_type_id -> dialog_id
    local_dialogs: dict[int, dict]          # Per-map dialog nodes
    warp_events: dict[int, tuple[int,int,int]]  # event -> (map, spawn, flag)

    @property
    def npcs(self) -> list[MapEntity]:      # entity_id >= 1500
    @property
    def monsters(self) -> list[MapEntity]:  # entity_id < 1500
```

---

## Area Entity Data

### Seed Hex Files

Pre-captured packets from the real server, stored as hex text in `tools/seed_data/`:

| File | Format | Content |
|------|--------|---------|
| `init_pkt1.hex` | LZO compressed | Character/map/entity init data (~36KB) |
| `init_pkt2.hex` | LZO compressed | Stats/currency data (~5KB) |
| `init_pkt3.hex` | Raw | ACK response structure (not sent during init) |
| `init_pkt4.hex` | LZO compressed | Skill data (34 x 0x0158 slots) |
| `area_pkt01.hex` - `area_pkt03.hex` | LZO compressed | Area entity packets |
| `area_pkt04.hex` - `area_pkt17.hex` | Raw | Area entity packets |

### Entity Registry

Maps runtime entity IDs to NPC type IDs. Built by scanning seed hex files for 0x0008 NPC spawn sub-messages.

```python
entity_registry: dict[int, int]  # runtime_entity_id -> npc_type_id

# Example entries from seed data:
# 0x53A6093A -> NPC 19 ('Slarm')
# 0x53E50979 -> NPC 7 ('Lily')
# 0x12A80D7D -> NPC 1554 ('Gaoler Angel')
# 0x13B00E85 -> NPC 2006 ('Census Angel')
```

### Runtime Entity ID Allocation

For map-based spawning (when PAK data is available):

```python
_next_entity_id = 0x12341000  # hi=0x1234, lo=0x1000

def _alloc_entity_id() -> int:
    global _next_entity_id
    eid = _next_entity_id
    _next_entity_id += 1
    return eid

# Produces: 0x12341000, 0x12341001, 0x12341002, ...
```

Real server uses different hi-word ranges (0x13B0, 0x127F, etc.) per entity group.

### Area Packet Generation

```python
def get_area_packets(map_data, npc_db, monster_db, entity_registry):
    if map_data:
        # Dynamic: generate from map entities
        packets = build_area_packets_from_map(map_data, npc_db, monster_db, entity_registry)
    else:
        # Fallback: use seed hex files
        packets = load_seed_area_packets()
    return packets  # list of (payload_bytes, compressed_flag)
```

Each area packet contains sub-messages:
- `0x0008` (65B) — NPC spawn
- `0x0007` (7B) — Entity position marker
- `0x000E` (45B) — Static entity
- `0x000F` (46B) — Monster spawn

---

## Dialog Manager

### Loading

```python
dm = DialogManager()
dm.load_texts('data/game_xml/msg.xml')           # 42,053 strings
dm.load_dialogs('data/game_xml/spmsg.xml')        # 24,450 dialog nodes
dm.load_dialogs('data/game_xml/setting/EVENT.XML') # 39 event nodes
dm.merge_local_dialogs(map_data.local_dialogs)     # Per-map dialogs
```

### Dialog Traversal

```python
# Start dialog
state = dm.start_dialog(dialog_id=5133, npc_entity_id=0x12341000)
# state.node.text = "Welcome, traveler..."
# state.node.options = [DialogOption(...), DialogOption(...)]

# Player selects option 0
next_state = dm.select_option(state, option_index=0)
# next_state.node.text = "Next dialog text..."

# Auto-advance (no options, follows unconditional_next)
next_state = dm.advance(state)
```

---

## Player Tracker

Tracks connected players by zone for efficient broadcasting.

```python
class PlayerTracker:
    register(entity_id, map_id, session)
    unregister(entity_id)
    change_map(entity_id, new_map_id)
    get_zone_sessions(map_id, exclude_entity=0) -> list[session]
    get_session(entity_id) -> session | None
    player_count -> int
```

---

## Game Finder

Locates the Angels Online install directory at startup.

**Search order**:
1. `AO_GAME_DIR` environment variable
2. Saved `ao_config.ini` file
3. Windows Registry keys:
   - `HKLM/HKCU\SOFTWARE\WOW6432Node\Angels Online\InstallPath`
   - `HKLM/HKCU\SOFTWARE\Angels Online\Path`
   - Uninstall registry entries
4. Common paths: `C:\Program Files (x86)\Angels Online`, etc.
5. GUI folder dialog (tkinter, if display available)

**Marker files** (any one identifies valid install): `data1.pak`, `ANGEL.DAT`, `ANGLE.DAT`, `update.pak`, `ao.ico`

---

## File Server (Stub)

Port 21238. Accepts connections and logs received data but sends no responses.

Known client requests include file timestamps and filenames (e.g., `16_Player.png`). Protocol is not yet reverse-engineered.
