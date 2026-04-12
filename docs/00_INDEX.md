# Angels Online Private Server - Master Documentation

A complete technical reference for understanding, extending, or reimplementing the Angels Online private server.

## Document Index

| # | Document | Description |
|---|----------|-------------|
| 1 | [Architecture Overview](01_ARCHITECTURE.md) | Server components, startup sequence, and data flow |
| 2 | [Protocol & Packet Format](02_PROTOCOL.md) | Wire format, header encoding, encryption, framing |
| 3 | [Login Flow](03_LOGIN_FLOW.md) | Login server handshake, authentication, redirect |
| 4 | [World Server Flow](04_WORLD_SERVER.md) | World init, game loop, keepalives, session management |
| 5 | [Opcode Reference](05_OPCODES.md) | All known opcodes with packet layouts and field offsets |
| 6 | [Game Handlers](06_HANDLERS.md) | Movement, NPC, combat, social, and misc handler details |
| 7 | [Database Schema](07_DATABASE.md) | SQLite tables, account/character management, queries |
| 8 | [Game Data & Content](08_GAME_DATA.md) | XML files, map loading, NPC/monster/quest systems |
| 9 | [Reverse Engineering Notes](09_REVERSE_ENGINEERING.md) | Protocol research, client binary analysis, pcap findings |

## Quick Start

1. The server runs three async services: **Login** (port 16768), **World** (port 27901), **File** (port 21238)
2. Entry point: `src/server.py` which starts all three via `asyncio.gather()`
3. Configuration: `src/config.py` (override via environment variables)
4. Database: `data/angels.db` (SQLite, auto-created on first run)
5. Game data: `data/game_xml/` (extracted XML) + Angels Online install dir (PAK files for maps)

## Connection Lifecycle

```
Client          Login Server         World Server         File Server
  |                 |                     |                    |
  |--- TCP connect -->                    |                    |
  |<-- Hello (XOR key) --|                |                    |
  |--- Login (creds) ---->                |                    |
  |<-- Response (MOTD) ---|               |                    |
  |--- PIN/Session ------>                |                    |
  |<-- Redirect (token) --|               |                    |
  |                 |                     |                    |
  |--- TCP connect ---------------------->|                    |
  |<-- Hello (new key) ------------------|                    |
  |--- Auth (token) --------------------->|                    |
  |--- ACK (0x0003) --------------------->|                    |
  |<-- Init packets (compressed) --------|                    |
  |<-- ACK response --------------------|                    |
  |<-- Skill data ----------------------|                    |
  |<-- Area entity packets --------------|                    |
  |<-- Keepalives (every 1s) ------------|                    |
  |<-> Game packets (bidirectional) -----|                    |
  |                                       |                    |
  |--- TCP connect ------------------------------------------>|
  |--- File requests ---------------------------------------->|
  |<-- File responses (TODO) --------------------------------|
```
