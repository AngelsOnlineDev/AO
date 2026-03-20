"""
Angels Online PCAP Stream Analyzer
====================================
Proper TCP stream reassembly + full packet decoding.

Unlike pcap_decoder.py, this tool:
- Reassembles TCP streams (handles multi-segment game packets)
- Decodes the 6-byte obfuscated packet header
- Extracts real C->S opcodes (bytes 2-3, not the obfuscated counter)
- Decompresses LZO-compressed payloads
- Parses sub-messages within payloads

Usage:
  python pcap_analyzer.py <pcap_file> [--stream N] [--full] [--opcodes]
"""

import os
import subprocess
import struct
import sys
import json
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

try:
    from lzallright import LZOCompressor
    _lzo = LZOCompressor()
except ImportError:
    _lzo = None

TSHARK = r"C:\Program Files\Wireshark\tshark.exe"

GAME_SERVERS = {"192.243.45.237", "192.243.47.237", "192.243.47.238", "192.243.47.107",
                "192.243.47.239"}

# Known opcodes
KNOWN_OPCODES = {
    # Login
    0x004b: "LOGIN",
    0x0900: "LOGIN_RESPONSE",
    0x01f5: "PIN",
    0x0020: "REDIRECT",

    # World init (sub-messages inside compressed packets)
    0x0001: "PLAYER_SPAWN",
    0x0023: "AUTH",
    0x018D: "INIT_HEADER",
    0x018E: "ZONE_LIST",
    0x0191: "SESSION_CONFIG",
    0x001D: "ENTITY_SETTING",
    0x0131: "GUILD_LIST",
    0x0158: "SKILL_DATA",
    0x0142: "INIT_MISC",
    0x0014: "SETTING_BATCH",

    # Entity world (S->C primarily)
    0x0002: "MOVEMENT_RESP",
    0x0007: "ENTITY_POS_SHORT",
    0x0008: "NPC_SPAWN",
    0x000E: "ENTITY_SPAWN",
    0x000F: "MOB_SPAWN",
    0x0005: "POSITION",
    0x0018: "ENTITY_MOVE",
    0x0019: "COMBAT_ACTION",
    0x0041: "NPC_EXTENDED",
    0x002D: "ENTITY_DETAIL",
    0x000C: "ENTITY_BATCH",
    0x000D: "ENTITY_ACTION",
    0x0010: "ENTITY_STATS",
    0x000B: "ENTITY_STATUS",
    0x005C: "ITEM_INFO",
    0x006A: "BUFF_INFO",
    0x00ED: "ZONE_DATA",
    0x001B: "ENTITY_DESPAWN",

    # Game control
    0x000A: "KEEPALIVE",
    0x0003: "ACK",
    0x0006: "ENTITY_SELECT",
    0x001E: "CHAT_MSG",
    0x0024: "SYSTEM_MSG",
    0x002B: "QUEST_INFO",
    0x002C: "QUEST_UPDATE",
    0x003B: "PARTY_INFO",

    # Game C->S
    0x0004: "MOVEMENT_REQ",
    0x0016: "USE_SKILL",
    0x0012: "BUY_SELL",
    0x0009: "STOP_ACTION",
    0x002E: "CHAT_SEND",
    0x003E: "TOGGLE_ACTION",
    0x0044: "NPC_DIALOG",
    0x0048: "EQUIP_ITEM",
    0x0049: "EQUIP_ITEM2",
    0x0101: "PET_COMMAND",
    0x0122: "SKILL_SLOT",
    0x012B: "INVENTORY_ACTION",
    0x012D: "INVENTORY_CLOSE",
    0x0139: "SHOP_CLOSE",
    0x0143: "ZONE_READY",
    0x0150: "EMOTE",
    0x015E: "PING",
    0x0011: "ENTITY_QUERY",
}


def xor_crypt(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def decode_header(raw: bytes) -> dict:
    """Decode the 6-byte obfuscated packet header."""
    if len(raw) < 6:
        return None
    b = struct.unpack_from('<BBBBBB', raw, 0)
    raw_len_word = b[0] | (b[1] << 8)
    payload_length = raw_len_word ^ 0x1357
    seq = b[2] ^ (raw_len_word & 0xFF)
    flags = b[3] ^ (raw_len_word & 0xFF)
    checksum_lo = b[4]
    checksum_hi = b[5]

    compressed = bool(flags & 0x80)
    encrypted = bool(flags & 0x01)

    # Padded to 16-byte boundary
    padded_length = (payload_length + 15) & ~15

    return {
        'payload_length': payload_length,
        'padded_length': padded_length,
        'sequence': seq,
        'flags': flags,
        'compressed': compressed,
        'encrypted': encrypted,
        'total_size': 6 + padded_length,
    }


def extract_tcp_segments(pcap_file):
    """Extract all TCP data segments using tshark."""
    ip_filter = " or ".join(f"ip.addr == {ip}" for ip in GAME_SERVERS)
    filter_str = f"({ip_filter}) && tcp.len > 0"

    cmd = [
        TSHARK, "-r", pcap_file,
        "-Y", filter_str,
        "-T", "fields",
        "-e", "frame.number",
        "-e", "frame.time_relative",
        "-e", "ip.src",
        "-e", "ip.dst",
        "-e", "tcp.srcport",
        "-e", "tcp.dstport",
        "-e", "tcp.stream",
        "-e", "data",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    segments = []
    for line in result.stdout.strip().split('\n'):
        if not line.strip():
            continue
        fields = line.split('\t')
        if len(fields) < 8:
            continue
        segments.append({
            "frame": int(fields[0]),
            "time": float(fields[1]),
            "src_ip": fields[2],
            "dst_ip": fields[3],
            "src_port": int(fields[4]),
            "dst_port": int(fields[5]),
            "stream": int(fields[6]),
            "data": bytes.fromhex(fields[7]) if fields[7] else b"",
        })
    return segments


def reassemble_stream(segments, stream_id):
    """Reassemble a TCP stream into S->C and C->S byte buffers."""
    stream_segs = [s for s in segments if s["stream"] == stream_id]
    if not stream_segs:
        return None

    first = stream_segs[0]
    if first["src_ip"] in GAME_SERVERS:
        server_ip = first["src_ip"]
        server_port = first["src_port"]
    else:
        server_ip = first["dst_ip"]
        server_port = first["dst_port"]

    s2c_data = bytearray()
    c2s_data = bytearray()
    s2c_times = []
    c2s_times = []

    for seg in stream_segs:
        is_s2c = seg["src_ip"] in GAME_SERVERS
        if is_s2c:
            s2c_times.append((len(s2c_data), seg["time"], seg["frame"]))
            s2c_data.extend(seg["data"])
        else:
            c2s_times.append((len(c2s_data), seg["time"], seg["frame"]))
            c2s_data.extend(seg["data"])

    return {
        "server": f"{server_ip}:{server_port}",
        "stream": stream_id,
        "s2c_data": bytes(s2c_data),
        "c2s_data": bytes(c2s_data),
        "s2c_times": s2c_times,
        "c2s_times": c2s_times,
        "start_time": stream_segs[0]["time"],
        "end_time": stream_segs[-1]["time"],
    }


def find_time_for_offset(times_list, offset):
    """Find the approximate timestamp for a byte offset in the stream."""
    last_time = 0.0
    last_frame = 0
    for off, t, f in times_list:
        if off > offset:
            break
        last_time = t
        last_frame = f
    return last_time, last_frame


def _extract_sub_opcodes(data: bytes) -> list:
    """Try to parse sub-message framing and extract opcodes."""
    opcodes = []
    pos = 0
    while pos + 2 <= len(data):
        sub_len = struct.unpack_from('<H', data, pos)[0]
        pos += 2
        if sub_len == 0:
            break
        if pos + sub_len > len(data):
            break
        if sub_len >= 2:
            op = struct.unpack_from('<H', data, pos)[0]
            opcodes.append((op, sub_len))
        pos += sub_len
    # Only treat as valid sub-messages if we found at least 2 and consumed most data
    if len(opcodes) >= 2 and pos >= len(data) * 0.8:
        return opcodes
    return []


def parse_packets_from_buffer(buf, xor_key, direction, times_list, is_login=False):
    """Parse game packets from a reassembled byte buffer.

    Returns list of decoded packet dicts.
    """
    packets = []
    pos = 0

    while pos + 6 <= len(buf):
        hdr = decode_header(buf[pos:pos+6])
        if hdr is None:
            break

        total = hdr['total_size']
        if pos + total > len(buf):
            # Incomplete packet at end of buffer
            break

        payload_raw = buf[pos+6:pos+6+hdr['padded_length']]

        # Decrypt — ALL game packets after Hello are XOR-encrypted regardless
        # of the flags 'encrypted' bit (bit 0 is unreliable)
        if xor_key:
            payload = xor_crypt(payload_raw, xor_key)
        else:
            payload = payload_raw

        payload = payload[:hdr['payload_length']]

        time_approx, frame_approx = find_time_for_offset(times_list, pos)

        pkt = {
            'offset': pos,
            'time': time_approx,
            'frame': frame_approx,
            'direction': direction,
            'payload_len': hdr['payload_length'],
            'sequence': hdr['sequence'],
        }

        # Try LZO decompression on all packets — the header 'compressed'
        # flag (bit 7) is unreliable, just like the 'encrypted' flag.
        # If decompression succeeds and yields valid sub-message framing,
        # treat as a compressed multi-sub-message container.
        # If it decompresses but has no sub-messages, use decompressed data
        # for opcode extraction (compressed single packet).
        decrypted_payload = payload
        is_multi = False
        if _lzo and len(payload) > 20:
            try:
                decompressed = _lzo.decompress(payload)
                sub_opcodes = _extract_sub_opcodes(decompressed)
                if sub_opcodes:
                    pkt['compressed'] = True
                    pkt['decompressed_len'] = len(decompressed)
                    pkt['type'] = 'multi'
                    pkt['sub_opcodes'] = sub_opcodes
                    pkt['sub_count'] = len(sub_opcodes)
                    pkt['raw_payload'] = decrypted_payload
                    is_multi = True
                elif len(decompressed) > 4:
                    # Compressed single packet — try to unwrap sub-message
                    # framing: [LE16 sub_len][sub_data]
                    sub_len = struct.unpack_from('<H', decompressed, 0)[0]
                    if 2 + sub_len <= len(decompressed) and sub_len >= 2:
                        inner = decompressed[2:2+sub_len]
                        op = struct.unpack_from('<H', inner, 0)[0]
                        pkt['compressed'] = True
                        pkt['decompressed_len'] = len(decompressed)
                        pkt['type'] = 'multi'
                        pkt['sub_opcodes'] = [(op, sub_len)]
                        pkt['sub_count'] = 1
                        pkt['raw_payload'] = decrypted_payload
                        is_multi = True
                    else:
                        pkt['compressed'] = True
                        pkt['decompressed_len'] = len(decompressed)
                        payload = decompressed
            except Exception:
                pass

        if is_multi:
            packets.append(pkt)
            pos += total
            continue

        if len(payload) >= 2:
            raw_opcode = struct.unpack_from('<H', payload, 0)[0]

            if direction == "C->S" and len(payload) >= 4:
                # C->S: bytes 0-1 = obfuscated counter, bytes 2-3 = real opcode
                pkt['counter'] = raw_opcode
                pkt['opcode'] = struct.unpack_from('<H', payload, 2)[0]
                pkt['payload_data'] = payload[4:]
            else:
                # S->C: bytes 0-1 = opcode directly
                pkt['opcode'] = raw_opcode
                pkt['payload_data'] = payload[2:]

            pkt['opcode_name'] = KNOWN_OPCODES.get(pkt['opcode'], "")

        pkt['raw_payload'] = payload
        packets.append(pkt)
        pos += total

    return packets


def parse_login_stream(stream_data):
    """Parse a login server stream (simpler protocol)."""
    s2c = stream_data['s2c_data']
    c2s = stream_data['c2s_data']

    # S->C starts with Hello
    if len(s2c) < 28 or s2c[0] != 0x41 or s2c[1] != 0x13:
        return None, None, []

    xor_key = s2c[12:28]

    # Parse S->C after hello
    s2c_packets = parse_packets_from_buffer(
        s2c[28:], xor_key, "S->C", stream_data['s2c_times'], is_login=True)

    # Parse C->S
    c2s_packets = parse_packets_from_buffer(
        c2s, xor_key, "C->S", stream_data['c2s_times'], is_login=True)

    # Merge and sort by time
    all_packets = s2c_packets + c2s_packets
    all_packets.sort(key=lambda p: (p['time'], p['offset']))

    return xor_key, {"type": "HELLO", "key": xor_key.hex(' ')}, all_packets


def parse_game_stream(stream_data):
    """Parse a game world stream."""
    s2c = stream_data['s2c_data']
    c2s = stream_data['c2s_data']

    # S->C starts with Hello
    if len(s2c) < 28 or s2c[0] != 0x41 or s2c[1] != 0x13:
        return None, None, []

    xor_key = s2c[12:28]

    # Parse S->C after hello
    s2c_packets = parse_packets_from_buffer(
        s2c[28:], xor_key, "S->C", stream_data['s2c_times'])

    # Parse C->S
    c2s_packets = parse_packets_from_buffer(
        c2s, xor_key, "C->S", stream_data['c2s_times'])

    # Merge and sort by time
    all_packets = s2c_packets + c2s_packets
    all_packets.sort(key=lambda p: (p['time'], p['offset']))

    return xor_key, {"type": "HELLO", "key": xor_key.hex(' ')}, all_packets


def analyze_stream(stream_data):
    """Full analysis of one TCP stream."""
    server = stream_data['server']
    is_login = ":16768" in server

    if is_login:
        xor_key, hello, packets = parse_login_stream(stream_data)
    else:
        xor_key, hello, packets = parse_game_stream(stream_data)

    if not packets:
        return None

    # Count opcodes
    opcode_counts = defaultdict(lambda: {"count": 0, "sizes": [], "directions": set()})
    for pkt in packets:
        if 'opcode' in pkt:
            op = pkt['opcode']
            opcode_counts[op]["count"] += 1
            opcode_counts[op]["sizes"].append(pkt['payload_len'])
            opcode_counts[op]["directions"].add(pkt['direction'])

    # Build opcode summary
    opcode_summary = {}
    for op, info in sorted(opcode_counts.items()):
        dirs = "/".join(sorted(info["directions"]))
        sizes = info["sizes"]
        min_s, max_s = min(sizes), max(sizes)
        size_str = f"{min_s}" if min_s == max_s else f"{min_s}-{max_s}"
        name = KNOWN_OPCODES.get(op, "")
        opcode_summary[f"0x{op:04X}"] = {
            "count": info["count"],
            "direction": dirs,
            "size": size_str,
            "name": name,
        }

    return {
        "stream": stream_data["stream"],
        "server": server,
        "is_login": is_login,
        "xor_key": xor_key.hex(' ') if xor_key else None,
        "duration": stream_data["end_time"] - stream_data["start_time"],
        "total_packets": len(packets),
        "s2c_bytes": len(stream_data["s2c_data"]),
        "c2s_bytes": len(stream_data["c2s_data"]),
        "opcode_summary": opcode_summary,
        "packets": packets,
    }


def print_summary(sessions):
    """Print overview of all sessions."""
    print("=" * 70)
    print("SESSION OVERVIEW")
    print("=" * 70)

    for s in sessions:
        stype = "LOGIN" if s["is_login"] else "GAME"
        print(f"\nStream {s['stream']}: {s['server']} [{stype}]")
        print(f"  Duration: {s['duration']:.1f}s | "
              f"Packets: {s['total_packets']} | "
              f"Data: {s['s2c_bytes']}B S->C, {s['c2s_bytes']}B C->S")

        if s["opcode_summary"]:
            print(f"  Opcodes ({len(s['opcode_summary'])}):")
            for op, info in sorted(s["opcode_summary"].items(),
                                   key=lambda x: -x[1]["count"]):
                name = f" {info['name']}" if info['name'] else ""
                print(f"    {info['direction']:5s} {op}{name}: "
                      f"{info['count']}x, size={info['size']}")


def print_opcodes(sessions):
    """Print consolidated opcode reference across all sessions."""
    # Track per-direction counts separately
    s2c_opcodes = defaultdict(lambda: {"count": 0, "sizes": set(), "name": ""})
    c2s_opcodes = defaultdict(lambda: {"count": 0, "sizes": set(), "name": ""})

    for s in sessions:
        if s["is_login"]:
            continue
        for pkt in s["packets"]:
            if 'opcode' not in pkt:
                continue
            op = pkt['opcode']
            d = s2c_opcodes if pkt['direction'] == "S->C" else c2s_opcodes
            d[op]["count"] += 1
            d[op]["sizes"].add(pkt['payload_len'])
            name = KNOWN_OPCODES.get(op, "")
            if name:
                d[op]["name"] = name

    print("\n" + "=" * 70)
    print("GAME OPCODE REFERENCE (all sessions combined)")
    print("=" * 70)

    def _print_opcodes(title, opcodes):
        print(f"\n--- {title} ---")
        for op in sorted(opcodes.keys()):
            info = opcodes[op]
            sizes = sorted(info["sizes"])
            size_str = f"{sizes[0]}" if len(sizes) == 1 else f"{sizes[0]}-{sizes[-1]}"
            name = f" {info['name']}" if info['name'] else ""
            print(f"  0x{op:04X}{name}: {info['count']}x, size={size_str}")

    _print_opcodes("S->C Opcodes", s2c_opcodes)
    _print_opcodes("C->S Opcodes", c2s_opcodes)


def print_stream_detail(session, max_packets=None):
    """Print detailed packet listing for one session."""
    stype = "LOGIN" if session["is_login"] else "GAME"
    print(f"\n{'='*70}")
    print(f"Stream {session['stream']}: {session['server']} [{stype}]")
    print(f"{'='*70}")

    packets = session["packets"]
    if max_packets:
        packets = packets[:max_packets]

    for pkt in packets:
        compressed = " [LZO]" if pkt.get('compressed') else ""
        decomp = ""
        if 'decompressed_len' in pkt:
            decomp = f" -> {pkt['decompressed_len']}b"

        if pkt.get('type') == 'multi':
            # Multi-sub-message packet (init/area data)
            sub_ops = pkt.get('sub_opcodes', [])
            op_summary = ", ".join(f"0x{op:04X}x{sum(1 for o,_ in sub_ops if o==op)}"
                                   for op in dict.fromkeys(o for o,_ in sub_ops))
            print(f"  [{pkt['time']:8.3f}s] {pkt['direction']} "
                  f"MULTI ({pkt['payload_len']}b{decomp}{compressed}) "
                  f"{pkt.get('sub_count',0)} sub-msgs: {op_summary}")
            continue

        if 'opcode' not in pkt:
            continue
        op = pkt['opcode']
        name = KNOWN_OPCODES.get(op, "")
        name_str = f" {name}" if name else ""

        counter_str = ""
        if pkt['direction'] == "C->S" and 'counter' in pkt:
            counter_str = f" ctr=0x{pkt['counter']:04X}"

        # Show payload preview
        raw = pkt.get('raw_payload', b'')
        preview = raw[:48].hex(' ')
        if len(raw) > 48:
            preview += "..."

        print(f"  [{pkt['time']:8.3f}s] {pkt['direction']} "
              f"0x{op:04X}{name_str} "
              f"({pkt['payload_len']}b{decomp}){compressed}{counter_str}")
        print(f"             {preview}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python pcap_analyzer.py <pcap_file> [options]")
        print("  --stream N   Show detail for stream N only")
        print("  --full       Show all packets (not just first 100)")
        print("  --opcodes    Show consolidated opcode reference")
        print("  --summary    Show only session overview")
        sys.exit(1)

    pcap_file = sys.argv[1]
    target_stream = None
    full_output = "--full" in sys.argv
    show_opcodes = "--opcodes" in sys.argv
    summary_only = "--summary" in sys.argv

    for i, arg in enumerate(sys.argv):
        if arg == "--stream" and i + 1 < len(sys.argv):
            target_stream = int(sys.argv[i + 1])

    print(f"Extracting TCP segments from {pcap_file}...")
    segments = extract_tcp_segments(pcap_file)
    print(f"Found {len(segments)} TCP data segments")

    streams = sorted(set(s["stream"] for s in segments))
    print(f"Found {len(streams)} TCP streams")

    if target_stream is not None:
        streams = [s for s in streams if s == target_stream]

    sessions = []
    for stream_id in streams:
        print(f"  Reassembling stream {stream_id}...")
        stream_data = reassemble_stream(segments, stream_id)
        if stream_data:
            session = analyze_stream(stream_data)
            if session:
                sessions.append(session)

    # Print summary
    print_summary(sessions)

    if show_opcodes:
        print_opcodes(sessions)

    if not summary_only:
        for s in sessions:
            max_pkts = None if full_output else 100
            print_stream_detail(s, max_packets=max_pkts)
            if not full_output and len(s["packets"]) > 100:
                print(f"  ... ({len(s['packets']) - 100} more packets, use --full)")

    # Save to JSON (without raw bytes)
    out_file = pcap_file.rsplit('.', 1)[0] + "_analysis.json"
    json_sessions = []
    for s in sessions:
        js = {k: v for k, v in s.items() if k != 'packets'}
        js['packets'] = []
        for p in s['packets']:
            jp = {k: v for k, v in p.items()
                  if k not in ('raw_payload', 'payload_data')}
            if 'raw_payload' in p:
                jp['payload_hex'] = p['raw_payload'].hex(' ')
            js['packets'].append(jp)
        json_sessions.append(js)

    with open(out_file, 'w') as f:
        json.dump(json_sessions, f, indent=2, default=str)
    print(f"\nFull analysis saved to: {out_file}")


if __name__ == "__main__":
    main()
