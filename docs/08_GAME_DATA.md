# Game data & content

The server reads two categories of external data:

1. **Extracted XML** — text strings, NPC/monster/quest definitions, dialog trees. Lives in `data/game_xml/`, committed to the repo. Parsed at startup by `game_data.py` lazy singletons.
2. **Captured seed packets** — pre-recorded init and area packets from a live AO session, stored as hex text in `tools/seed_data/`. Replayed and patched per-player.

Nothing about this data is "ours" — it all came from the original game client and a captured live session.

## Sources at a glance

| Source | Location | Size | Content |
|---|---|---|---|
| `msg.xml` | `data/game_xml/` | 42,053 strings | all dialog text and UI labels |
| `spmsg.xml` | `data/game_xml/` | 24,450 nodes | global dialog trees |
| `setting/EVENT.XML` | `data/game_xml/setting/` | 39 nodes | global event dialogs |
| `npc.xml` | `data/game_xml/` | 827 NPCs | NPC type definitions |
| `monster.xml` | `data/game_xml/` | 1,682 monsters | monster stats |
| `quest.xml` | `data/game_xml/` | varies | quest definitions |
| `setting/eng/class.xml` | `data/game_xml/setting/eng/` | 16 classes | class name order (trust this, not our guesses) |
| `*.pak` | AO install dir | varies | maps, textures, sounds — ❌ not parsed yet |
| `*.hex` | `tools/seed_data/` | ~50KB total | captured init + area packets |

## Lazy singletons ([game_data.py](../src/game_data.py))

```python
get_dialog_manager()    # msg.xml + spmsg.xml + EVENT.XML
get_npc_db()            # npc.xml  -> dict[type_id] -> {...}
get_monster_db()        # monster.xml -> dict[type_id] -> {...}
get_map(map_id)         # MapData from PAK (returns None if PAK not parsed)
```

All are cached after first call. Startup cost is ~6-7s for the dialog manager (42k strings + 24k dialog nodes); everything else is cheap.

❓ **Known perf issue**: these are loaded lazily *per connection* because of an early architecture mistake. A client connecting to world adds ~7s of load time. They should be loaded once at `server.py` startup. Non-blocking fix; just hasn't been prioritized.

---

## XML files — formats

All XML files are in-game Traditional Chinese attribute names. We map them to English keys on parse.

### msg.xml

```xml
<字串 編號="5001" 文字="/c$2%1/c*, welcome to the Guidance Class..." />
```

| XML attr | Key | Meaning |
|---|---|---|
| `編號` | `id` | string ID |
| `文字` | `text` | display text |

Text formatting codes (interpreted client-side):
- `/c$N` — start color `N` (`/c$2` = green, `/c$9` = item reference, etc.)
- `/c*` — end color
- `%N%` — parameter placeholder, substituted with runtime values (player name, item ID…)
- `\n` — linebreak

### spmsg.xml

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

| XML element | Our parse |
|---|---|
| `對話` (dialog) | `DialogNode(id=編號, msg_id=訊息, face=臉譜, …)` |
| `選項` (option) | `DialogOption(msg_id=訊息, next_id=下一句)` — 0 = close |
| `觸發器` (trigger) | condition block — ❓ only action-37 warps and action-25 goto are implemented |
| `條件` (condition) | condition type — ❓ most types unimplemented |
| `參數` (param) | condition parameter |
| `動作` (action) | `DialogAction(type=編號, params=…)` |
| `成立` (unconditional) | `unconditional_next=下一句` |

Condition types we've observed but don't implement:
- `7` — quantity check
- `10` — level check
- `38` — quest/flag state
- `39` — item possession

Action types:

| Type | Meaning | Status |
|---|---|---|
| 25 | start dialog | ✅ |
| 37 | zone warp `(dest_map, spawn_point, flag)` | ✅ |
| 1, 2, 3, 4, 10 | script-based (give quest, grant item, etc.) | ❌ |

### npc.xml

```xml
<npc 編號="1501" 圖號="40001" 名稱="Michael" 等級="99" 陣營="中立"/>
```

| XML attr | Key | Meaning |
|---|---|---|
| `編號` | `id` | NPC type ID (≥ 1500 means NPC) |
| `圖號` | `sprite_id` | model/sprite ID |
| `名稱` | `name` | display name |
| `等級` | `level` | decorative |
| `陣營` | `faction` | 中立 = neutral, 正義 = justice, etc. ❓ |
| `問安語音` / `道別語音` | `greet_sound` / `farewell_sound` | voice IDs |

### monster.xml

Monsters use the same root tag `<npc>` but with IDs `< 1500`. They're differentiated from NPCs by `npc_type_id < 1500`, not by tag name.

```xml
<npc 編號="1" 圖號="42055" 名稱="Wind Elf" 等級="4" 系別="能量"
     陣營="怪物陣營" HP="162" 平均攻擊="41" 防禦="41" 魔攻="33"
     魔防="21" 精準="32" 靈敏="24" 移動速度="50" 攻擊速度="70"
     重擊機率="5" 經驗價值="41" />
```

Common attributes:

| Attr | Chinese | Meaning |
|---|---|---|
| `HP` | - | max HP — used by `MobRegistry` ✅ |
| `平均攻擊` | avg attack | base damage |
| `攻擊變數` | attack variance | damage range |
| `防禦 / 魔攻 / 魔防` | phys def / mag atk / mag def | |
| `精準 / 靈敏` | accuracy / agility | hit / dodge |
| `火焰/寒冰/雷電/腐蝕 攻擊/防禦` | elemental atk/def | 4 elements × atk+def |
| `移動速度 / 攻擊速度` | move / attack speed | |
| `重擊機率` | crit chance | % |
| `經驗價值` | XP value | reward |
| `攻擊法術1/2 / 補血法術1` | atk / heal spells | spell IDs |
| `王` | boss flag | 是 = yes |
| `免疫媚惑/定身/冰凍` | immunities | charm / root / freeze |

❓ We use `HP`, `平均攻擊`, and `防禦` in combat. Everything else is parsed and ignored.

### quest.xml ❌

Parsed into a `QuestManager` that nothing currently reads from. The quest step parsing (`/c$9%ID%/c* /COUNT` → kill/collect) works but quest state isn't tracked anywhere.

### class.xml ✅

**Authoritative class ordering** — trust this over any guessed class list. Current mapping:

```
0=Novice  1=Priest     2=Summoner  3=Wizard   4=Magician
5=Protector 6=Warrior  7=Swordsman 8=Spearman 9=Archer
10=Weaponsmith 11=Armorsmith 12=Tailor 13=Technician 15=Chef
```

Used by [class_stats.py](../src/class_stats.py) for per-class stat scaling and by the Census Angel dialog.

---

## Map loading ❌ (not implemented)

Maps live inside `.pak` archives in the AO install directory. The archives use a custom obfuscated format that isn't standard ZIP — we haven't cracked extraction yet. Without it, `get_map(map_id)` returns `None` and the server falls back to replaying captured seed packets for the one map we have.

What we *do* know about `.mpc` files (from other RE efforts — ❓ unverified against our build):

```
Header:
  4B  magic = "MAP\0"
  4B  map_width (tiles)
  4B  map_height (tiles)
  4B  flags
  4B  tile_w (32)
  4B  tile_h (32)
  4B  entity_section_offset
  4B  entity_section_size
  4B  event_xml_offset
  4B  event_xml_size
  …
  4B  dialog_xml_offset

Tile data:
  width * height * record_size

Entity section:
  19B header
  N × 74B entity records

Event + dialog XML sections follow.
```

Each 74-byte entity record (❓ verified for area seeds, not for live maps):

```
00-03  LE32  x_pixel          (tile_x = /32)
04-07  LE32  y_pixel
08-11  LE32  entity_id        (type; NPC ≥1500, monster <1500)
12-15  LE32  flags            (event link for NPCs)
16-19  LE32  direction        (0=none, 2=facing)
20-73  54B   padding          (contains dialog references, not yet mapped)
```

**MapData object** ([map_loader.py:99](../src/map_loader.py#L99)):

```python
class MapData:
    map_id: int
    width: int                              # tiles
    height: int
    tile_w = 32                             # px per tile
    tile_h = 32
    entities: list[MapEntity]
    events: dict[int, MapEvent]
    npc_dialogs: dict[int, int]             # entity_id -> dialog_id
    local_dialogs: dict[int, dict]          # per-map dialog nodes
    warp_events: dict[int, tuple]           # ❓ declared but never populated
```

❌ `MapData` has **no walkability / collision layer**. The server can't validate movement destinations; client-reported positions are trusted with only a max-step clamp.

---

## Seed packets ([tools/seed_data/](../tools/seed_data/))

| File | Format | Content |
|---|---|---|
| `init_pkt1.hex` | LZO compressed | map/entity/profile init (~36 KB decompressed) |
| `init_pkt2.hex` | LZO compressed | char stats + currency (~5 KB) |
| `init_pkt3.hex` | raw | ACK response (used inline, not sent as init) |
| `init_pkt4.hex` | LZO compressed | skill slots (~2.5 KB, 34 × `0x0158`) |
| `area_pkt01.hex` – `area_pkt17.hex` | mixed | area NPC/monster spawn packets per zone |

These were captured from a live AO session where the character was "Soualz" (Priest, level 18, faction Steel). Per-player patching in [world_init_builder.py](../src/world_init_builder.py) rewrites the name, appearance, class, stats, and entity_id. Every other byte is replayed as-is.

### Runtime entity registry

Maps observed at load time from the seed packets:

```python
entity_registry: dict[int, int]   # runtime_entity_id -> npc_type_id

# examples:
0x53A6093A -> 19   ('Slarm')
0x53E50979 -> 7    ('Lily')
0x12A80D7D -> 1554 ('Gaoler Angel')
0x13B00E85 -> 2006 ('Census Angel')
```

The runtime IDs aren't stable across seed files — each capture used its own allocation. For map-based spawning (when we eventually load maps live), there's an allocator:

```python
_next_entity_id = 0x12341000
def _alloc_entity_id() -> int:
    global _next_entity_id
    _next_entity_id += 1
    return _next_entity_id - 1
```

❓ The real server uses multiple hi-word ranges (`0x13B0`, `0x127F`, …) per entity category. Our single-bucket allocator works for now because nothing cares about ID range semantics, but it may break quest triggers if anything keys on hi-word.

---

## Dialog manager

```python
dm = DialogManager()
dm.load_texts('data/game_xml/msg.xml')              # 42,053 strings
dm.load_dialogs('data/game_xml/spmsg.xml')          # 24,450 nodes
dm.load_dialogs('data/game_xml/setting/EVENT.XML')  # 39 event nodes
dm.merge_local_dialogs(map_data.local_dialogs)      # per-zone, on map load
```

Traversal:

```python
state = dm.start_dialog(dialog_id=5133, npc_entity_id=0x12341000)
next_state = dm.select_option(state, option_index=0)   # player picks option 0
next_state = dm.advance(state)                          # follow unconditional_next
```

---

## Game finder ([game_finder.py](../src/game_finder.py))

Locates the AO install directory at startup:

1. `AO_GAME_DIR` env var.
2. `ao_config.ini` in repo root.
3. Windows registry keys: `HKLM/HKCU\SOFTWARE\WOW6432Node\Angels Online\InstallPath`, `HKLM/HKCU\SOFTWARE\Angels Online\Path`, Uninstall entries.
4. Common path `C:\Program Files (x86)\Angels Online`.
5. GUI folder-picker (tkinter) if a display is available.

Marker files used to confirm a valid install: `data1.pak`, `ANGEL.DAT`, `ANGLE.DAT`, `update.pak`, `ao.ico`.

---

## File server (stub)

Port `21238`. Accepts the client's avatar-upload handshake and logs what it receives. Never responds. The client uploads per-character portrait PNGs (`16_<name>.png`) and re-downloads them for the character select screen — we swallow these requests and the client caches fallbacks.

❌ **Protocol not reverse-engineered**. Low priority because the character select still renders defaults.

---

## Known unknowns

- ❌ **PAK extraction** — blocks everything map-related (collision, dynamic spawns, per-map init packets).
- ❌ **File server protocol** — avatar upload/download.
- ❓ **The tail bytes of 74-byte entity records** (`[20..73]`). They clearly carry dialog references but we haven't mapped them.
- ❌ **Quest action types** (1, 2, 3, 4, 10). Dialog manager parses them but `handle_npc_dialog` ignores all but action-37.
- ❓ **Condition types** (7, 10, 38, 39). Parsed and logged, never evaluated.
