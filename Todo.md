# Angels Online Private Server - Todo

## Bugs

### [FIXED] Login connection closes before world server connect
- **File**: `src/game_server.py:371`
- **Cause**: `asyncio.sleep(1.0)` after redirect was too short. Client needs ~2s to process the redirect and connect to the world server. Premature login socket closure caused the client to abort the entire flow.
- **Fix**: Replaced fixed sleep with `await asyncio.wait_for(reader.read(1), timeout=10.0)` so the login server waits for the client to disconnect naturally.

## Performance

### Game data loaded per-connection instead of at startup
- **Files**: `src/world_server.py:182-194`
- **Issue**: Every client connection triggers loading of map data, NPC definitions (827 entries), monster definitions (1682 entries), area entity data, and the dialog manager (42k strings, 24k dialog nodes). This takes ~7 seconds and blocks the client from receiving init packets.
- **Impact**: Client sits idle for ~7s after connecting to the world server before receiving any game data.
- **Suggested fix**: Load NPC DB, monster DB, area entity registry, and dialog manager once at server startup (or lazily with caching), and pass references into `_world_flow`. Only per-player map data needs to be loaded per-connection.

## Missing Features

### File server protocol not implemented
- **File**: `src/file_server.py`
- **Issue**: File server accepts connections and logs received data but sends no responses. Client requests (e.g. `16_Player.png`) go unanswered.
- **Impact**: Client may hang or timeout waiting for file responses.

### Map loading requires pak_extractor
- **Log**: `pak_extractor not found in tools/; cannot load maps from PAK`
- **Issue**: Map data can't be loaded without the PAK extractor tool, falling back to no map data. This means map-specific NPCs, spawn points, and collision data are missing.
