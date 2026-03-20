# Angels Online Private Server

A Python-based server emulator for Angels Online, built for **educational and game preservation purposes only**. This project reverse-engineers the game's network protocol to allow the client to connect to a locally hosted server after the official servers shut down on February 27, 2026.

> **Disclaimer:** This project is provided as-is for educational and preservation purposes only. It comes with no warranty or guarantee of any kind. Use at your own risk.

## Features

- **Login Server** — Authentication handshake with XOR key exchange and LZO-compressed responses
- **World Server** — Game world with movement, NPC interaction, dialog trees, zone warping, combat stubs, and chat
- **File Server** — Stub implementation for asset serving
- **NPC Dialog System** — XML-based dialog trees with 703 global dialogs and per-map local dialogs
- **Zone Warp System** — Travel between 26+ maps via gate NPCs and dialog-triggered warps
- **Quest Framework** — Quest loading with step tracking and objective parsing
- **Map Loader** — Parses .MPC map files from game PAK archives for dynamic NPC/entity spawning
- **Multi-player Foundation** — Session token auth, player tracker, and zone-based broadcasting

## Requirements

- **Python 3.10+**
- **Angels Online client** (installed, with PAK files for map loading)

### Dependencies

```
pip install lzallright
```

Optional (for packet sniffing tools):
```
pip install scapy
```

## Quick Start

### Windows

```batch
run_server.bat
```

Or manually:

```batch
pip install lzallright
python src/server.py
```

### Linux

```bash
pip install lzallright
python src/server.py
```

### Client Setup

The client's `SERVER.XML` must point to your server. Replace the `ip` and `fip` attributes with your server's address:

```xml
<伺服器 名稱="Hestia" 編號="16" 選擇="100" ip="127.0.0.1" port="16768" 分流="2" fip="127.0.0.1" fport="21238"/>
```

A pre-configured `SERVER_PROXY.XML` is included in `data/` — copy it to your game directory as `SERVER.XML`.

## Configuration

All settings can be overridden with environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `AO_HOST` | `127.0.0.1` | Server listen address |
| `AO_REDIRECT_HOST` | same as `AO_HOST` | Address the client connects to for the world server |
| `AO_LOGIN_PORT` | `16768` | Login server port |
| `AO_WORLD_PORT` | `27901` | World server port |
| `AO_FILE_PORT` | `21238` | File server port |
| `AO_GAME_DIR` | `C:\Program Files (x86)\Angels Online` | Game install directory (for PAK/map loading) |

Example with custom settings:

```bash
AO_HOST=0.0.0.0 AO_REDIRECT_HOST=192.168.1.100 python src/server.py
```

## Project Structure

```
├── src/
│   ├── server.py              # Entry point — starts all three servers
│   ├── config.py              # Configuration and environment variables
│   ├── game_server.py         # Login server
│   ├── world_server.py        # World server (packet dispatch)
│   ├── file_server.py         # File server (stub)
│   ├── handlers/              # Packet handler modules
│   │   ├── movement.py        #   Movement and zone transfers
│   │   ├── npc.py             #   NPC interaction and dialogs
│   │   ├── combat.py          #   Targeting and skills
│   │   ├── social.py          #   Chat and emotes
│   │   └── misc.py            #   Shop, toggles, zone ready
│   ├── game_data.py           # Shared game data singletons
│   ├── database.py            # SQLite database layer
│   ├── crypto.py              # XOR packet encryption
│   ├── packet.py              # Packet framing and parsing
│   ├── packet_builders.py     # 44+ opcode packet builders
│   ├── dialog_manager.py      # NPC dialog tree engine
│   ├── quest_manager.py       # Quest state tracking
│   ├── map_loader.py          # MPC map file parser
│   ├── area_entity_data.py    # NPC/entity spawn packet generation
│   ├── world_init_builder.py  # Dynamic init packet assembly
│   ├── player_tracker.py      # Zone-based player tracking
│   └── game_finder.py         # Game install directory detection
├── data/
│   ├── angels.db              # SQLite database (players, entities)
│   ├── game_xml/              # Extracted game XML files
│   └── SERVER_PROXY.XML       # Pre-configured server config for client
├── tools/
│   ├── seed_data/             # Captured init/area packet hex files
│   ├── game_sniffer.py        # Live packet sniffer (requires scapy + Npcap)
│   ├── pcap_analyzer.py       # PCAP analysis tools
│   └── verify_builders.py     # Packet builder verification
└── logs/                      # Server log output
```

## Protocol

The server implements Angels Online's custom TCP protocol:

- **6-byte obfuscated header** with XOR encoding
- **16-byte repeating XOR encryption** (static for server-to-client, evolving for client-to-server)
- **Sub-message framing**: `[LE16 length][data]` repeated within payloads
- **LZO compression** for init and login response packets
- **Checksum validation** using 0xD31F seed with bit rotation

## License

This project is for educational and game preservation purposes only. Angels Online is the property of its respective owners.
