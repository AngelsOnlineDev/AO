# Reverse-engineering notes

This is the file where we keep track of **what we've learned from the client**, **what we've verified empirically**, and **what we still don't know**. The other docs describe the server we've built; this one describes the protocol/game we're reimplementing.

If a piece of knowledge in this repo feels load-bearing, it should be listed here with its evidence. If something here turns out to be wrong, fix it — this file drifts the most.

## Sources of truth (in order of reliability)

1. **IDA/Ghidra decompile of the client binary** — highest confidence. We have live IDA via MCP (see the memory file `reference_ida_mcp.md`).
2. **Captures of a live session** at `C:\Users\Gamer\Downloads\Angels Online\Angels Online\server\captures\` — a recording of a real login + gameplay against the original server. Bytes here are authoritative for "what the real server actually sent".
3. **Our own seed packets** at `tools/seed_data/*.hex` — subset of (2), the ones we replay. Captured with the character "Soualz" (Priest, level 18, faction Steel).
4. **Empirical testing** — patching bytes in the seed packets, observing client behavior. Works for confirming a hypothesis but not for discovering new structure.
5. **Guessing from patterns** — lowest confidence. Anything marked ❓ falls here until verified.

## Methodology

### Understanding a new opcode

1. Find it in an IDA dispatch table. For the world server, the table lives at `0x5E5350` (`sub_5E5350`) — 256 function pointers indexed by opcode. Decompile the handler function.
2. Map what the handler reads from `a2 + N`. `a2` is the sub-message body starting at the opcode bytes (so `a2+0..1 = opcode`, `a2+2..` = body).
3. Confirm offsets against a real capture. If the capture says "Soualz has R.Atk 4553" and the handler reads LE32 from `a2+128`, look at offset 128 in the captured packet — if it's `c9 11 00 00` that's 4553, confirmed.
4. Write it down with ✅ and the evidence.

### Patching experiments

For the init packets, we decompress the seed, patch specific bytes, recompress, send. If the client renders the change, the offset is correct. This is how we verified the HP/MP/stats offsets in `init_pkt1` `0x0002` profile.

**Warning**: some field changes only become visible when the *server* sends a fresh packet at runtime (e.g. remote player spawns via `0x0001`). The init packet is a one-shot; changes there only affect first render.

## Key constants ✅

| Constant | Value | Meaning | Where |
|---|---|---|---|
| `HEADER_XOR` | `0x1357` | XOR mask for payload length in header bytes 0-1 | `FUN_0081dbf0` |
| Checksum seed | `0xD31F` | Initial value for packet checksum | `FUN_0081dbf0` |
| Hello sequence | `0xFFFF` | Reserved sequence number for the handshake packet | observed universally |
| World dispatch | `sub_5E5350` | World server opcode-to-handler table setup | dispatch table base at `+7B0` |

## Binary analysis (one-time Ghidra pass)

Rough counts from a previous Ghidra dump (❓ not re-verified, may be stale):

| Metric | Count |
|---|---|
| Functions analyzed | ~7,090 |
| Dispatch tables identified | ~338 |
| VTable classes | ~659 |
| Total opcodes mapped | ~3,172 |

Most of these opcodes are never exercised in basic play. The main runtime dispatch goes through `FUN_0048ae30` (~331 entries); the world loop's opcode handler is a subset of that.

Strings worth remembering:
- `vc_gakkari`, `vc_ooiyo`, … — Japanese voice commands (client has these even in the English build).
- `sqlite3_extension_init` — the **client** embeds SQLite. Some client data is kept in local SQLite files.

## Confirmed findings (chronological)

### `CryptXORIV` key evolution ✅

The C→S cipher mutates after every decrypt by adding the **padded** payload length to each of the four LE32 DWORDs of the key. This is enforced by `CryptXORIV._update_key` in [crypto.py](../src/crypto.py). Discovered by walking a capture packet-by-packet and watching which key permutation decrypted each packet correctly.

### Phase-4 slot opcodes ✅

After the login response, the client loops sending slot operations until it commits. Opcodes (all confirmed from IDA decompile of the live client):

- `0x0003` `sub_4C0C50` — CREATE (body: slot, appearance, name)
- `0x0004` `sub_4C4990` — DELETE (body: slot, password MD5 hex)
- `0x0005` `sub_4C4AC0` — SELECT preview (body: slot) — UI ping, no server action
- `0x0006` `sub_4C4A20` — ENTER WORLD (body: slot, flag, password MD5 hex) — commit

This replaced an earlier wrong guess where we thought `0x0006` was create and `0x0024` was delete. Memory got updated.

### Init packet 0x0002 player profile ✅

Decompile of `sub_5E9C90` (the `0x0002` handler) gives us the authoritative profile layout. `a2` in the decompile maps to `data[base + (a2 - 2)]` in the seed packet (where `base = off + 4` skips `[sub_len:2][opcode:2]`).

Key findings from the decompile + Soualz capture verification:

```
a2 offset  data[base+N]  semantic                           capture (Soualz Priest L18)
───────    ────────────  ─────────────────                  ──────────────────────────
+2         +0           entity_id LE32                      0x3543018D
+6         +4           model+480 flags LE32                0x8D
+10        +8           pixel_x LE32                        4688
+14        +12          pixel_y LE32                        3952
+18..+33   +16..+31     name (16B NUL-padded)               "Soualz"
+35..      +33..        guild name NUL-term                 ""
+52        +50          facing byte                         0
+57..+61   +55..+59     5 appearance bytes -> model+576..580  (varies)
+62        +60          FACTION -> model+744                3 (Steel) ✅ not class_id!
+66        +64          -> model+1206  ❓                   0
+67        +65          level LE32 -> model+588             18
+71..+91   +69..+89     6×LE32 stat block 1 -> model+1208..1228  50087, 0, 49923, 0, 61582, 0
                        ❓ values look like XP/timers, not atk/dfs
+95        +93          class_id -> model+1232              1 (Priest) ✅
+96,+100   +94,+98      2×LE32 -> model+1236,1240
+104       +102         HP_max LE32 -> model+640            12435
+108       +106         HP     LE32 -> model+660            12435
+112       +110         MP_max LE32 -> model+644            10051
+116       +114         MP     LE32 -> model+664            10051
+120,+122  +118,+120    stamina WORDs -> model+648, 668
```

**Breakthrough**: `model+744` is **faction**, not class_id. We had mislabeled it for weeks. The real class_id is at `data[base+93]` → `model+1232`. The Soualz capture has `base+60 = 3` (Steel faction, confirmed against screenshots) and `base+93 = 1` (Priest, confirmed). Our code writes to the correct offsets; just the *labels* were wrong.

The mislabeling had cascading effects:
- `build_remote_player_spawn` (`0x0001`) has `buf[62]` labeled "class_id" but it also maps to `model+744` → faction. Our call sites pass `class_id` here, so remote players get their class value as their faction. Not visibly wrong in practice because we don't track faction separately yet.
- Docs said "data[60] is faction" in a comment, which was right as a *guess* but inconsistent with the `class_id → model+744` label elsewhere. Now reconciled.

**Still unknown** ❓:
- What `model+1206` (`a2+66`) means.
- What `model+1208..1228` carry. Capture values (50087, 49923, 61582) are too large to be attack/defense; likely total XP, kills, timers, or similar counters. Would need a capture with a different character to diff.
- The 5×LE32 combat stats (R.Atk/L.Atk/Dfs/Spl.Atk/Spl.Dfs) are at `data[base+126..154]` — verified empirically against Soualz's in-game stat sheet values (4553, 4536, 4540, 4065, 4036). These map to `a2+128..156` → `model+1248..` block. We don't know which exact model offset is which stat, but the offsets on the wire are correct.

### Dual-opcode player spawn ✅

- `0x0001` (`sub_5E97F0`) carries rich player info (name, appearance, equipment) but only runs if the receiver is in a fresh-login / char-select state.
- `0x000E` (`sub_5EF410`) is a bare spawn with no name/appearance that works mid-game.

Neither works on its own. Sending **both** during presence broadcasts lets each receiver process whichever one their current state accepts. Verified empirically by staring at two clients side-by-side.

### Movement is `0x0005`, not `0x0018` ✅

Decompile of `sub_5EC7B0` confirms `0x0005` is the entity-move opcode. `0x0018` is unrelated — it's an emote/animation handler (`sub_5F14D0`). Older docs had them swapped. Our current `build_entity_move` uses the correct `0x0005`.

### `0x001E` has two forms ✅

- **6-byte form**: `[op][LE32 entity_ref]` — seen only in init packets, as an area entity reference.
- **Long form**: full chat-message layout — used at runtime for chat and our NPC-dialog workaround.

Same opcode, different handlers on the client side. They're distinguished by sub-message length.

## Partially understood (❓)

- **`0x0042` character stats tail**. First 18 bytes (HP/MP) are confirmed. The remaining 89 bytes are replayed verbatim from the capture. Works perfectly — we've just never traced what each field means.
- **`0x006A` buff info**. Layout understood (entity + buff count + buff records), but we don't know the `buff_id → effect` mapping and there's no buff table in the XML we've found.
- **Inventory (`0x0155`)**. Opcode identified, packet size ~602B. No decoded layout. The client sends this during init (currently replayed) — a diff capture with different inventories would map slot positions.
- **Equipment (`0x0049`, `0x0063`)**. We know the opcodes exist. We've never sent an equip packet and the client has nothing to equip, so untested end-to-end.
- **NPC shop packets**. We can "open" a shop by sending a welcome message but the actual shop UI packet is unknown.

## Known unknowns (❌)

- **The real NPC dialog opcode (S→C)**. We fake dialog with `0x001E` chat. Our best guess was `0x002B` but the client hasn't reacted to packets we've sent with that opcode. A capture of `C→S 0x0044 (NPC_DIALOG)` immediately followed by the real response would solve this in minutes.
- **HP-bar update opcode**. `0x0019` renders damage numbers and the hit animation but does not update the target's HP bar. We've been wrong about this opcode twice already. Needs a targeted live combat capture.
- **The "Job" label in the character profile**. Even after patching `data[base+93] = class_id`, the in-game Job field doesn't match the value we set. Either the label is driven by a secondary field we haven't found, or it's cached from something outside the `0x0002` profile (possibly the login response's slot struct or the `0x0042` char stats tail).
- **Walkability**. Server has zero collision data. Movement is client-trusted. Fix requires PAK extraction → `.MPC` parsing → walkability bitmap.
- **Equipment slot IDs**. `0x0001` player spawn carries 8+3 equipment slot IDs. Our DB has no equipment table so we send zeros; remote players render as naked defaults. Fix is a `equipment` table + a lookup in `presence._spawn_subs`. Blocks "remote players wear the right clothes".
- **PAK archive format**. Not standard ZIP. The archives contain maps, textures, sounds — all blocked by this.
- **File server protocol**. Port 21238 stub. The client's avatar-upload / download handshake isn't decoded.
- **Respawn announcement**. We respawn mobs server-side (30s timer in `MobRegistry`) but never send a packet to clients. Unknown what the real server sent.
- **Password hash algorithm**. We store and compare raw bytes from the client. The hashing function itself hasn't been traced.
- **Mystery 29 bytes** in the Phase-2 auth packet between the opcode and the session token. Replayed from capture. Possibly a client build hash or timestamp.
- **Quest action types** 1, 2, 3, 4, 10. Dialog parser reads them; nothing executes them.
- **Dialog condition types** 7, 10, 38, 39. Parsed, never evaluated.

## Dead-end / refuted claims

Things earlier docs said that turned out to be wrong. Kept here so we don't re-learn them:

- ❌ "class_id at `data[base+60]` → model+744" — **wrong**, that's faction. Real class_id is at `+93` → model+1232.
- ❌ "`0x0034` is CANCEL_ACTION" — **wrong**, no such opcode in the dispatch table. Dialog cancel is `0x0009` STOP_ACTION.
- ❌ "`0x0018` is entity move" — **wrong**, it's emote/anim. Entity move is `0x0005`.
- ❌ "Phase 4 is a PIN confirmation (8 bytes, ignored)" — **wrong**, Phase 4 is a slot-op loop (CREATE/DELETE/SELECT/ENTER_WORLD).
- ❌ "CREATE opcode is 0x0006, DELETE is 0x0024" — **wrong** (earlier memory), actual: CREATE 0x0003, DELETE 0x0004.
- ❓ "Stats at `a2+71..91` are R.Atk/L.Atk/Dfs" — **unlikely**, values too large. The real combat stats are at `a2+128..156`, verified empirically against Soualz's visible stat sheet.

## Tips for further RE

1. **New opcodes**: grep the live capture JSON for the opcode, find a packet, look up the handler via the world dispatch table (`sub_5E5350`), decompile.
2. **Field mapping**: capture two sessions with known differences (levels, items, stats) and diff the same opcode across both.
3. **HP-update hunt**: open IDA MCP, grep for `+ 0x294` (= 660, which is `model+660` = current HP), find every function that **writes** to it, cross-reference which is called from a packet handler. The `tool_search:xrefs_to_field` tool does this.
4. **Shop packet**: click a shop NPC in a live capture, look for the S→C packet immediately after the C→S `0x000D`.
5. **Dialog packet**: same approach — click an NPC with dialogue, find S→C packets after `C→S 0x0044`.
