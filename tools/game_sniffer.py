"""
Angels Online Live Packet Sniffer
==================================
Captures and decodes Angels Online TCP traffic in real-time using
scapy + Npcap. Must be run as Administrator.

Features:
- Captures all game server IPs (login + world servers)
- TCP stream reassembly with per-stream byte buffers
- Automatic Hello detection and XOR key extraction
- Real-time packet decryption and opcode display
- LZO decompression for compressed init/area packets
- Logs decoded output to server/logs/

Usage (run as Admin):
  python game_sniffer.py [--raw] [--quiet] [--iface <name>]

Options:
  --raw          Also show raw hex dumps
  --quiet        Only show unknown opcodes (suppress keepalives, movement, etc.)
  --iface <name> Force a specific network interface (e.g. "Ethernet 1", "Wi-Fi")

Requires:
  pip install scapy lzallright
  Npcap (comes with Wireshark)
"""

import struct
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

try:
    from scapy.all import sniff, TCP, IP, conf
    conf.verb = 0  # Suppress scapy startup noise
except ImportError:
    print("ERROR: scapy is required. Install it with:")
    print("  pip install scapy")
    print("\nAlso ensure Npcap is installed (comes with Wireshark).")
    sys.exit(1)

try:
    from lzallright import LZOCompressor
    _lzo = LZOCompressor()
except ImportError:
    _lzo = None

GAME_SERVERS = {
    "192.243.45.237",   # Login server
    "192.243.47.237",   # Game world (load balanced)
    "192.243.47.238",   # Game world (load balanced)
    "192.243.47.107",   # Game world (load balanced)
}

LOG_DIR = os.path.join(os.path.dirname(__file__), '..', 'logs')
LOG_FILE = os.path.join(LOG_DIR, "game_sniffer.log")

# Quiet mode suppresses these high-frequency opcodes
QUIET_OPCODES = {0x000A, 0x0002, 0x0004, 0x0018, 0x0007, 0x0005}

KNOWN_OPCODES = {
    # Login
    0x004b: "LOGIN", 0x0900: "LOGIN_RESP", 0x01f5: "PIN", 0x0020: "REDIRECT",
    # Entity world
    0x0002: "MOVE_RESP", 0x0007: "ENT_POS", 0x0008: "NPC_SPAWN",
    0x000E: "ENT_SPAWN", 0x000F: "MOB_SPAWN", 0x0005: "POSITION",
    0x0018: "ENT_MOVE", 0x0019: "COMBAT", 0x0041: "NPC_EXT",
    0x002D: "ENT_DETAIL", 0x000C: "ENT_BATCH", 0x000D: "ENT_ACTION",
    0x0010: "ENT_STATS", 0x000B: "ENT_STATUS", 0x005C: "ITEM_INFO",
    0x006A: "BUFF_INFO", 0x00ED: "ZONE_DATA", 0x001B: "ENT_DESPAWN",
    0x001D: "ENT_SETTING", 0x001E: "CHAT_MSG", 0x0024: "SYS_MSG",
    # Game control
    0x000A: "KEEPALIVE", 0x0003: "ACK", 0x0006: "SELECT",
    # C->S
    0x0004: "MOVE_REQ", 0x0016: "SKILL", 0x0012: "BUY_SELL",
    0x0009: "STOP", 0x002E: "CHAT_SEND", 0x003E: "TOGGLE",
    0x0044: "NPC_DIALOG", 0x0048: "EQUIP", 0x0049: "EQUIP2",
    0x0101: "PET_CMD", 0x0143: "ZONE_READY", 0x015E: "PING",
}


def xor_crypt(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def decode_header(raw: bytes) -> dict | None:
    """Decode the 6-byte obfuscated packet header."""
    if len(raw) < 6:
        return None
    b = struct.unpack_from('<BBBBBB', raw, 0)
    raw_len_word = b[0] | (b[1] << 8)
    payload_length = raw_len_word ^ 0x1357
    if payload_length < 0 or payload_length > 65535:
        return None
    padded_length = (payload_length + 15) & ~15
    return {
        'payload_length': payload_length,
        'padded_length': padded_length,
        'total_size': 6 + padded_length,
    }


def extract_sub_opcodes(data: bytes) -> list:
    """Parse sub-message framing and extract opcodes."""
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
    if len(opcodes) >= 2 and pos >= len(data) * 0.8:
        return opcodes
    return []


class StreamState:
    """Per-TCP-stream state for reassembly and decoding."""

    def __init__(self, stream_key: str, server_ip: str, server_port: int):
        self.key = stream_key
        self.server_ip = server_ip
        self.server_port = server_port
        self.is_login = server_port == 16768
        self.xor_key = None
        self.s2c_buf = bytearray()
        self.c2s_buf = bytearray()
        self.s2c_hello_done = False
        self.pkt_count = 0

    def feed_s2c(self, data: bytes) -> list[str]:
        """Feed server->client data, return decoded lines."""
        self.s2c_buf.extend(data)

        # Check for Hello (first S->C data)
        if not self.s2c_hello_done and len(self.s2c_buf) >= 28:
            if self.s2c_buf[0] == 0x41 and self.s2c_buf[1] == 0x13:
                self.xor_key = bytes(self.s2c_buf[12:28])
                self.s2c_hello_done = True
                self.s2c_buf = self.s2c_buf[28:]
                return [f"  HELLO key={self.xor_key.hex(' ')}"]
            else:
                self.s2c_hello_done = True  # Not a hello, skip

        return self._parse_packets(self.s2c_buf, "S->C")

    def feed_c2s(self, data: bytes) -> list[str]:
        """Feed client->server data, return decoded lines."""
        self.c2s_buf.extend(data)
        return self._parse_packets(self.c2s_buf, "C->S")

    def _parse_packets(self, buf: bytearray, direction: str) -> list[str]:
        """Parse complete game packets from buffer, return decoded lines."""
        lines = []
        while len(buf) >= 6:
            hdr = decode_header(buf[:6])
            if hdr is None:
                buf.clear()
                break

            total = hdr['total_size']
            if len(buf) < total:
                break  # Wait for more data

            payload_raw = bytes(buf[6:6 + hdr['padded_length']])
            del buf[:total]

            # Decrypt (all game packets are XOR encrypted)
            if self.xor_key:
                payload = xor_crypt(payload_raw, self.xor_key)
            else:
                payload = payload_raw
            payload = payload[:hdr['payload_length']]

            self.pkt_count += 1
            line = self._decode_packet(payload, direction, hdr['payload_length'])
            if line:
                lines.append(line)

        return lines

    def _decode_packet(self, payload: bytes, direction: str,
                       raw_len: int) -> str | None:
        """Decode a single packet payload into a display line."""
        if len(payload) < 2:
            return None

        # Try LZO decompression (compressed flag is unreliable)
        if _lzo and len(payload) > 20:
            try:
                decompressed = _lzo.decompress(payload)
                sub_ops = extract_sub_opcodes(decompressed)
                if sub_ops:
                    op_counts = {}
                    for op, _ in sub_ops:
                        op_counts[op] = op_counts.get(op, 0) + 1
                    summary = ", ".join(
                        f"0x{op:04X}x{cnt}" for op, cnt in op_counts.items())
                    return (f"  {direction} MULTI ({raw_len}b -> "
                            f"{len(decompressed)}b) {len(sub_ops)} subs: "
                            f"{summary}")
                # Single sub-message compressed packet
                if len(decompressed) > 4:
                    sub_len = struct.unpack_from('<H', decompressed, 0)[0]
                    if 2 + sub_len <= len(decompressed) and sub_len >= 2:
                        op = struct.unpack_from('<H', decompressed, 2)[0]
                        name = KNOWN_OPCODES.get(op, "")
                        return (f"  {direction} COMPRESSED 0x{op:04X} {name} "
                                f"({raw_len}b -> {len(decompressed)}b)")
            except Exception:
                pass

        # Normal packet
        if direction == "C->S" and len(payload) >= 4:
            opcode = struct.unpack_from('<H', payload, 2)[0]
            data_part = payload[4:]
        else:
            opcode = struct.unpack_from('<H', payload, 0)[0]
            data_part = payload[2:]

        name = KNOWN_OPCODES.get(opcode, "")
        preview = data_part[:24].hex(' ') if data_part else ""
        if len(data_part) > 24:
            preview += "..."

        return (f"  {direction} 0x{opcode:04X} {name:<12s} "
                f"({raw_len}b) {preview}")


def find_game_iface():
    """Auto-detect the network interface that routes to game servers."""
    from scapy.all import conf as sc_conf, get_working_ifaces
    # Use scapy's routing table to find which interface routes to a game server
    test_ip = next(iter(GAME_SERVERS))
    route = sc_conf.route.route(test_ip)
    route_iface = route[0]  # interface name or object
    # Find the matching interface object
    for iface in get_working_ifaces():
        if iface == route_iface or iface.name == route_iface or \
           getattr(iface, 'guid', None) == route_iface or \
           str(iface) == str(route_iface):
            return iface
    # Fallback: return whatever scapy resolved
    return route_iface


def main():
    show_raw = "--raw" in sys.argv
    quiet = "--quiet" in sys.argv

    # Parse --iface option
    iface = None
    for i, arg in enumerate(sys.argv):
        if arg == "--iface" and i + 1 < len(sys.argv):
            iface_name = sys.argv[i + 1]
            from scapy.all import get_working_ifaces
            for candidate in get_working_ifaces():
                if iface_name.lower() in candidate.name.lower() or \
                   iface_name.lower() in candidate.description.lower():
                    iface = candidate
                    break
            if iface is None:
                print(f"ERROR: Interface '{iface_name}' not found. Available:")
                for candidate in get_working_ifaces():
                    print(f"  {candidate.name}: {candidate.description} (IP: {candidate.ip})")
                sys.exit(1)

    if iface is None:
        iface = find_game_iface()

    # Build BPF filter for all game server IPs
    host_filters = " or ".join(f"host {ip}" for ip in sorted(GAME_SERVERS))
    bpf_filter = f"tcp and ({host_filters})"

    iface_name = getattr(iface, 'name', str(iface))
    iface_desc = getattr(iface, 'description', '')
    iface_ip = getattr(iface, 'ip', '')
    print(f"Interface: {iface_name} ({iface_desc}) [{iface_ip}]")
    print(f"Servers:  {', '.join(sorted(GAME_SERVERS))}")
    print(f"Filter:   {bpf_filter}")
    print(f"Log:      {LOG_FILE}")
    if quiet:
        print("Mode:     Quiet (suppressing keepalives, movement)")
    if not _lzo:
        print("Warning:  lzallright not installed, compressed packets won't decode")
    print("=" * 70)

    os.makedirs(LOG_DIR, exist_ok=True)

    # Stream tracking: (server_ip, server_port, client_ip, client_port) -> StreamState
    streams: dict[tuple, StreamState] = {}
    segment_count = 0

    logf = open(LOG_FILE, "a", encoding="utf-8")
    logf.write(f"\n{'='*70}\nCapture started: {datetime.now()}\n{'='*70}\n")
    logf.flush()

    def output(line: str):
        print(line)
        logf.write(f"{line}\n")
        logf.flush()

    def process_packet(pkt):
        nonlocal segment_count

        if IP not in pkt or TCP not in pkt:
            return

        ip_layer = pkt[IP]
        tcp_layer = pkt[TCP]

        src_ip = ip_layer.src
        dst_ip = ip_layer.dst
        src_port = tcp_layer.sport
        dst_port = tcp_layer.dport
        tcp_flags = tcp_layer.flags

        is_s2c = src_ip in GAME_SERVERS
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]

        # Handle SYN/FIN/RST
        if tcp_flags & 0x07:  # SYN, FIN, RST
            flag_names = []
            if tcp_flags & 0x02:
                flag_names.append("SYN")
            if tcp_flags & 0x01:
                flag_names.append("FIN")
            if tcp_flags & 0x04:
                flag_names.append("RST")
            flag_str = "+".join(flag_names)
            srv = f"{src_ip}:{src_port}" if is_s2c else f"{dst_ip}:{dst_port}"
            output(f"[{timestamp}] {srv} {flag_str}")

            # SYN = new connection (use client SYN, not server SYN-ACK)
            if (tcp_flags & 0x02) and not (tcp_flags & 0x10):
                if is_s2c:
                    server_ip, server_port = src_ip, src_port
                else:
                    server_ip, server_port = dst_ip, dst_port
                stream_key = (server_ip, server_port,
                              dst_ip if is_s2c else src_ip,
                              dst_port if is_s2c else src_port)
                streams[stream_key] = StreamState(
                    f"{server_ip}:{server_port}",
                    server_ip, server_port)
                stype = "LOGIN" if server_port == 16768 else "GAME"
                output(f"[{timestamp}] NEW {stype} stream -> {server_ip}:{server_port}")

        # Extract TCP payload
        payload = bytes(tcp_layer.payload)
        if len(payload) == 0:
            return

        segment_count += 1

        # Determine stream key (always server_ip, server_port, client_ip, client_port)
        if is_s2c:
            stream_key = (src_ip, src_port, dst_ip, dst_port)
        else:
            stream_key = (dst_ip, dst_port, src_ip, src_port)

        stream = streams.get(stream_key)
        if stream is None:
            # Stream started before capture — create it now
            if is_s2c:
                server_ip, server_port = src_ip, src_port
            else:
                server_ip, server_port = dst_ip, dst_port
            stream = StreamState(
                f"{server_ip}:{server_port}",
                server_ip, server_port)
            streams[stream_key] = stream

        # Feed data to stream reassembler
        if is_s2c:
            decoded_lines = stream.feed_s2c(payload)
        else:
            decoded_lines = stream.feed_c2s(payload)

        # Display decoded packets
        for line in decoded_lines:
            # Quiet mode filtering
            if quiet and any(f"0x{op:04X}" in line for op in QUIET_OPCODES):
                continue
            output(f"[{timestamp}] {stream.key} {line}")

        # Raw hex dump (optional)
        if show_raw and payload:
            direction = "S->C" if is_s2c else "C->S"
            raw_line = (f"  {direction} raw ({len(payload)}b): "
                        f"{payload[:48].hex(' ')}"
                        f"{'...' if len(payload) > 48 else ''}")
            logf.write(f"{raw_line}\n")

    print(f"Sniffing on {iface_name}... Press Ctrl+C to stop.\n")

    try:
        sniff(iface=iface, filter=bpf_filter, prn=process_packet, store=False)
    except KeyboardInterrupt:
        pass
    finally:
        total_pkts = sum(s.pkt_count for s in streams.values())
        summary = (f"\nStopped. {segment_count} TCP segments, "
                   f"{total_pkts} game packets decoded, "
                   f"{len(streams)} streams.")
        print(summary)
        logf.write(f"\nStopped: {datetime.now()}. {segment_count} segments, "
                   f"{total_pkts} game packets, {len(streams)} streams.\n")
        logf.close()


if __name__ == "__main__":
    main()
