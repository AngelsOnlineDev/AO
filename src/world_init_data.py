"""World init data - loads initialization packets from hex files.

The .hex files in tools/seed_data/ contain the original captured init payloads
as text hex (LZO compressed for packets 1,2; raw for packet 3). These are
loaded at startup, cached, and sent to each connecting client.

Sub-message format: [LE16 sub_len][sub_data] repeated

Packet contents (verified by opcode analysis):
  init_pkt1.hex: 137 subs (0x018D, 0x001D x96, 0x0064, 0x0002, ...) — compressed
  init_pkt2.hex: 130 subs (0x001D x96, 0x0042, 0x0144, ...) — compressed
  init_pkt3.hex: 6 subs (0x0142 x2, 0x001D, 0x0027, 0x003F, 0x0040) — ACK response
  init_pkt4.hex: 34 subs (0x0158 x34) — skill data

Real server sequence (from sniffer analysis):
  1. S->C: Init data (pkt1, pkt2) — NO standalone 0x018E or 0x0191
  2. S->C: ACK response (pkt3 structure, built dynamically)
  3. ~1s later: S->C: Skill data (pkt4, 0x0158 x34)

NOTE: init_pkt3 is NOT sent during init — it's the ACK response, which is
built dynamically by world_server._build_ack_response() with the correct
entity_id. init_pkt4 is sent separately after the ACK exchange.

IMPORTANT: The real server does NOT send 0x018E (zone list) or 0x0191
(session config) as part of the game world init. 0x018D already contains
zone IDs. Sending extra opcodes the client doesn't expect can cause crashes.
"""

import os
import logging

log = logging.getLogger("world_init_data")

_SEED_DIR = os.path.join(os.path.dirname(__file__), '..', 'tools', 'seed_data')

# Init packets sent during the init sequence (before ACK response)
# NOTE: pkt3 (ACK response) and pkt4 (skills) are excluded — see module docstring
# NOTE: Real server does NOT send 0x018E or 0x0191 — don't add extra packets
_INIT_PACKETS = [
    ("init_pkt1.hex", True),
    ("init_pkt2.hex", True),
]

# Skill data sent ~1s after ACK response (separate from init sequence)
_SKILL_PACKET = ("init_pkt4.hex", True)

_cached_init = None
_cached_skills = None


def _load_hex(filepath: str) -> bytes:
    """Load a .hex file and decode to bytes."""
    with open(filepath, 'r') as f:
        return bytes.fromhex(f.read().strip())


def build_init_packets() -> list[tuple[bytes, bool]]:
    """Load init packets for the main init sequence.

    Sequence (matches real server sniffer capture):
      0. init_pkt1.hex — character, map, entity data (compressed)
      1. init_pkt2.hex — more entity data, stats (compressed)

    The real server sends ONLY these two compressed packets before the
    ACK response. No standalone 0x018E zone list or 0x0191 session config.
    0x018D (first sub-message in pkt1) already contains zone IDs.

    Returns:
        List of (payload_bytes, compressed_flag) tuples.
    """
    packets = []

    for filename, compressed in _INIT_PACKETS:
        filepath = os.path.join(_SEED_DIR, filename)
        if not os.path.exists(filepath):
            log.warning(f"Missing init data file: {filepath}")
            packets.append((b'', compressed))
            continue

        payload = _load_hex(filepath)
        log.info(f"Loaded {filename}: {len(payload)} bytes (compressed={compressed})")
        packets.append((payload, compressed))

    return packets


def build_skill_packet() -> tuple[bytes, bool] | None:
    """Load the skill data packet (0x0158 x34).

    In the real server, this is sent ~1 second after the ACK response,
    separate from the main init sequence.

    Returns:
        (payload_bytes, compressed_flag) or None if file missing.
    """
    filename, compressed = _SKILL_PACKET
    filepath = os.path.join(_SEED_DIR, filename)
    if not os.path.exists(filepath):
        log.warning(f"Missing skill data file: {filepath}")
        return None

    payload = _load_hex(filepath)
    log.info(f"Loaded {filename}: {len(payload)} bytes (compressed={compressed})")
    return (payload, compressed)


def get_init_packets():
    """Get cached init packets, loading from files on first call."""
    global _cached_init
    if _cached_init is None:
        _cached_init = build_init_packets()
    return _cached_init


def get_skill_packet():
    """Get cached skill packet, loading from file on first call."""
    global _cached_skills
    if _cached_skills is None:
        _cached_skills = build_skill_packet()
    return _cached_skills
