# Angels Online Private Server — Documentation

Technical reference for the server. Each section maps one subsystem; cross-references use the `NN_NAME.md` filenames below.

## Status legend

Every claim in these docs carries one of:

- ✅ **Confirmed** — matched against an IDA decompile of the original client *and* verified against a capture or a running server.
- ❓ **Guessed** — plausible, consistent with observed behavior, but not proven. Treat as a working hypothesis; don't rely on it for anything load-bearing.
- ❌ **Unknown** — open question. Listed so we stop forgetting what we don't know.

If a section has no marker, it describes our own server code (which is the source of truth for itself) and the code's own behavior — not a protocol claim.

## Index

| # | Document | Covers |
|---|----------|--------|
| 1 | [01_ARCHITECTURE.md](01_ARCHITECTURE.md) | Process layout, async servers, module map |
| 2 | [02_PROTOCOL.md](02_PROTOCOL.md) | Packet framing, headers, XOR crypto, checksum, sub-messages |
| 3 | [03_LOGIN_FLOW.md](03_LOGIN_FLOW.md) | Login handshake, auth, account/character creation, redirect |
| 4 | [04_WORLD_SERVER.md](04_WORLD_SERVER.md) | World handshake, init packets, dispatch table, presence, mob tick |
| 5 | [05_OPCODES.md](05_OPCODES.md) | Every opcode we've seen, by direction, with field layouts |
| 6 | [06_HANDLERS.md](06_HANDLERS.md) | What each `src/handlers/*.py` does (movement, NPC, combat, social) |
| 7 | [07_DATABASE.md](07_DATABASE.md) | SQLite schema, what each column means, what's missing |
| 8 | [08_GAME_DATA.md](08_GAME_DATA.md) | XML loaders, map/NPC/monster/dialog registries |
| 9 | [09_REVERSE_ENGINEERING.md](09_REVERSE_ENGINEERING.md) | RE findings, captures, the "known unknowns" list |

Not in the numbered set:
- [contributing.md](contributing.md) — how to add a handler, decode an opcode, capture packets
- [changelog.md](changelog.md) — release history

## Quick start

Three async TCP servers run from `src/server.py`:

| Server | Port | File | Purpose |
|---|---|---|---|
| Login | 16768 | `game_server.py` | Auth + redirect to world |
| World | 27901 | `world_server.py` | Gameplay, movement, NPCs |
| File | 21238 | `file_server.py` | Avatar portraits (stub) |

Plus support services for launcher compatibility: `patch_server.py` (HTTP :80) and `ftp_server.py` (FTP :21). These let `Start.exe` finish its update check.

Config: `src/config.py` and `ao_config.ini` (local machine paths).
Database: `data/angels.db` (SQLite, WAL mode, auto-created).
Captured init packets: `tools/seed_data/init_pkt{1,2}.hex` (LZO-compressed client-recorded sessions we replay and patch per-player).

## Connection lifecycle

```
LOGIN phase (port 16768)
  C → Connect
  S → Hello (16B XOR key)
  C → Login request (creds)
  S → Login response (compressed, MOTD + slot list)
  C → Phase4: SELECT/CREATE/DELETE (slot op)
  S → Redirect (session token + world host:port)
  C → Disconnect

WORLD phase (port 27901)
  C → Connect
  S → Hello (new 16B XOR key)
  C → Auth (session token)
  S → Init pkt 1 (compressed: entity, profile, map, NPCs)
  S → Init pkt 2 (compressed: char stats, currency, skills)
  S → S→C ACK (0x0003)
  C → C→S ACK (0x0003)
  C ⇄ S Game loop (dispatch by opcode — see 04_WORLD_SERVER.md)
  S → Keepalive (0x018A, ~1s)
```
