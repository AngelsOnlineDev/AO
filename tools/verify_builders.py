"""Verify that packet builder functions produce byte-identical output.

Decompresses each hex file, parses sub-messages, extracts parameters
from the raw bytes, calls the corresponding builder function, and
compares the result byte-for-byte with the original.

Usage:
    python verify_builders.py [--verbose]
"""

import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from lzallright import LZOCompressor
import packet_builders as pb

DATA_DIR = os.path.join(os.path.dirname(__file__), 'seed_data')

# Which packets are LZO compressed
INIT_COMPRESSED = {1, 2, 4}       # init_pkt1, init_pkt2, init_pkt4
AREA_COMPRESSED = {1, 2, 3, 10, 11, 12}

_lzo = LZOCompressor()


def parse_sub_messages(data: bytes) -> list[bytes]:
    """Parse [LE16 length][data] sub-message framing."""
    msgs = []
    pos = 0
    while pos + 2 <= len(data):
        sub_len = struct.unpack_from('<H', data, pos)[0]
        pos += 2
        if sub_len == 0:
            break
        if pos + sub_len > len(data):
            break
        msgs.append(data[pos:pos + sub_len])
        pos += sub_len
    return msgs


def load_hex_file(name: str, compressed: bool) -> bytes:
    """Load a hex file and optionally decompress."""
    path = os.path.join(DATA_DIR, name)
    with open(path, 'r') as f:
        raw = bytes.fromhex(f.read().strip())
    if compressed:
        return _lzo.decompress(raw)
    return raw


def extract_and_rebuild(msg: bytes) -> bytes | None:
    """Extract parameters from raw sub-message bytes and rebuild using builder.

    Returns the rebuilt bytes, or None if no builder exists for this opcode.
    """
    if len(msg) < 2:
        return None
    opcode = struct.unpack_from('<H', msg, 0)[0]

    # --- Fully decoded builders ---

    if opcode == 0x0007 and len(msg) == 7:
        lo = struct.unpack_from('<H', msg, 2)[0]
        hi = struct.unpack_from('<H', msg, 4)[0]
        flag = msg[6]
        return pb.build_entity_pos(lo, hi, flag)

    if opcode in (0x0027, 0x003F, 0x0040) and len(msg) == 10:
        area_id = struct.unpack_from('<I', msg, 2)[0]
        return pb.build_area_ref(opcode, area_id)

    if opcode == 0x005B and len(msg) == 218:
        entries = []
        for i in range(24):
            off = 2 + i * 9
            flag = msg[off]
            index = msg[off + 1]
            if flag != 0 or index != 0:
                entries.append((flag, index))
            else:
                entries.append((0, 0))
        return pb.build_slot_table(entries)

    if opcode == 0x005C and len(msg) == 39:
        return pb.build_empty_005C()

    if opcode == 0x005D and len(msg) == 6:
        ts = struct.unpack_from('<I', msg, 2)[0]
        return pb.build_timestamp_005D(ts)

    if opcode == 0x012A and len(msg) == 23:
        index = struct.unpack_from('<I', msg, 2)[0]
        return pb.build_indexed_slot_012A(index)

    if opcode == 0x0142 and len(msg) == 3:
        flag = msg[2]
        return pb.build_toggle_0142(flag)

    if opcode == 0x0144 and len(msg) == 8:
        return pb.build_empty_0144()

    if opcode == 0x0145 and len(msg) == 4:
        return pb.build_empty_0145()

    if opcode == 0x0162 and len(msg) == 10:
        return pb.build_empty_0162()

    if opcode == 0x0164 and len(msg) == 19:
        return pb.build_empty_0164()

    if opcode == 0x0178 and len(msg) == 10:
        val = struct.unpack_from('<I', msg, 2)[0]
        trail = struct.unpack_from('<I', msg, 6)[0]
        return pb.build_pair_0178(val, trail)

    if opcode == 0x017D and len(msg) == 4:
        return pb.build_empty_017D()

    if opcode == 0x0185 and len(msg) == 14:
        eid = struct.unpack_from('<I', msg, 2)[0]
        return pb.build_entity_ref_0185(eid)

    if opcode == 0x018F and len(msg) == 14:
        a = struct.unpack_from('<I', msg, 2)[0]
        b = struct.unpack_from('<I', msg, 6)[0]
        c = struct.unpack_from('<I', msg, 10)[0]
        return pb.build_triple_018F(a, b, c)

    # --- Partially decoded builders ---

    if opcode == 0x0042 and len(msg) == 107:
        hp = struct.unpack_from('<I', msg, 2)[0]
        hp_max = struct.unpack_from('<I', msg, 6)[0]
        mp = struct.unpack_from('<I', msg, 10)[0]
        mp_max = struct.unpack_from('<I', msg, 14)[0]
        stats_tail = msg[18:]
        return pb.build_char_stats(hp, hp_max, mp, mp_max, stats_tail)

    if opcode == 0x014A and len(msg) == 19:
        name_bytes = msg[2:14]   # 12 bytes name field
        tail = msg[14:]          # 5 bytes tail
        return pb.build_party_name(name_bytes, tail)

    # --- Raw data builders ---

    if opcode == 0x0014 and len(msg) == 9:
        return pb.build_raw_0014(msg[2:])

    if opcode == 0x001E and len(msg) == 6:
        return pb.build_raw_001E(msg[2:])

    if opcode == 0x0021 and len(msg) == 24:
        return pb.build_raw_0021(msg[2:])

    if opcode == 0x0149 and len(msg) == 38:
        return pb.build_raw_0149(msg[2:])

    if opcode == 0x0158 and len(msg) == 74:
        return pb.build_skill_slot(msg[2:])

    if opcode == 0x0160 and len(msg) == 27:
        return pb.build_raw_0160(msg[2:])

    if opcode == 0x016F and len(msg) == 20:
        return pb.build_raw_016F(msg[2:])

    if opcode == 0x017A and len(msg) == 19:
        return pb.build_raw_017A(msg[2:])

    if opcode == 0x018A and len(msg) == 23:
        return pb.build_raw_018A(msg[2:])

    return None


def verify_all(verbose: bool = False):
    """Verify all builder functions against original hex data."""
    total = 0
    passed = 0
    failed = 0
    skipped = 0
    no_builder = 0

    # Opcode statistics
    opcode_pass = {}   # opcode -> count of passed
    opcode_fail = {}   # opcode -> list of (source, msg_index)

    # Process init packets
    for pkt_num in range(1, 5):
        name = f"init_pkt{pkt_num}.hex"
        compressed = pkt_num in INIT_COMPRESSED
        path = os.path.join(DATA_DIR, name)
        if not os.path.exists(path):
            print(f"  SKIP {name} (not found)")
            continue

        data = load_hex_file(name, compressed)
        msgs = parse_sub_messages(data)

        for i, msg in enumerate(msgs):
            total += 1
            if len(msg) < 2:
                skipped += 1
                continue

            opcode = struct.unpack_from('<H', msg, 0)[0]
            rebuilt = extract_and_rebuild(msg)

            if rebuilt is None:
                no_builder += 1
                if verbose:
                    print(f"  -- {name}#{i}: opcode=0x{opcode:04X} "
                          f"({len(msg)}B) no builder")
                continue

            if rebuilt == msg:
                passed += 1
                opcode_pass[opcode] = opcode_pass.get(opcode, 0) + 1
                if verbose:
                    print(f"  OK {name}#{i}: opcode=0x{opcode:04X} "
                          f"({len(msg)}B)")
            else:
                failed += 1
                if opcode not in opcode_fail:
                    opcode_fail[opcode] = []
                opcode_fail[opcode].append((name, i))
                print(f"  FAIL {name}#{i}: opcode=0x{opcode:04X} "
                      f"({len(msg)}B)")
                # Show first difference
                for j in range(min(len(msg), len(rebuilt))):
                    if msg[j] != rebuilt[j]:
                        print(f"       First diff at byte {j}: "
                              f"expected 0x{msg[j]:02X}, got 0x{rebuilt[j]:02X}")
                        break
                if len(msg) != len(rebuilt):
                    print(f"       Size mismatch: expected {len(msg)}, "
                          f"got {len(rebuilt)}")

    # Process area packets
    pkt_num = 1
    while True:
        name = f"area_pkt{pkt_num:02d}.hex"
        path = os.path.join(DATA_DIR, name)
        if not os.path.exists(path):
            break

        compressed = pkt_num in AREA_COMPRESSED
        data = load_hex_file(name, compressed)
        msgs = parse_sub_messages(data)

        for i, msg in enumerate(msgs):
            total += 1
            if len(msg) < 2:
                skipped += 1
                continue

            opcode = struct.unpack_from('<H', msg, 0)[0]
            rebuilt = extract_and_rebuild(msg)

            if rebuilt is None:
                no_builder += 1
                if verbose:
                    print(f"  -- {name}#{i}: opcode=0x{opcode:04X} "
                          f"({len(msg)}B) no builder")
                continue

            if rebuilt == msg:
                passed += 1
                opcode_pass[opcode] = opcode_pass.get(opcode, 0) + 1
                if verbose:
                    print(f"  OK {name}#{i}: opcode=0x{opcode:04X} "
                          f"({len(msg)}B)")
            else:
                failed += 1
                if opcode not in opcode_fail:
                    opcode_fail[opcode] = []
                opcode_fail[opcode].append((name, i))
                print(f"  FAIL {name}#{i}: opcode=0x{opcode:04X} "
                      f"({len(msg)}B)")
                for j in range(min(len(msg), len(rebuilt))):
                    if msg[j] != rebuilt[j]:
                        print(f"       First diff at byte {j}: "
                              f"expected 0x{msg[j]:02X}, got 0x{rebuilt[j]:02X}")
                        break
                if len(msg) != len(rebuilt):
                    print(f"       Size mismatch: expected {len(msg)}, "
                          f"got {len(rebuilt)}")

        pkt_num += 1

    # Print summary
    print()
    print("=" * 60)
    print("VERIFICATION SUMMARY")
    print("=" * 60)
    print(f"  Total sub-messages:  {total}")
    print(f"  Passed:              {passed}")
    print(f"  Failed:              {failed}")
    print(f"  No builder:          {no_builder}")
    print(f"  Skipped (too small): {skipped}")
    print()

    if opcode_pass:
        print("  Passed by opcode:")
        for op in sorted(opcode_pass):
            print(f"    0x{op:04X}: {opcode_pass[op]} instances")
        print()

    if opcode_fail:
        print("  FAILURES:")
        for op in sorted(opcode_fail):
            locs = opcode_fail[op]
            print(f"    0x{op:04X}: {len(locs)} failures")
            for src, idx in locs[:3]:
                print(f"      - {src}#{idx}")
        print()

    if failed == 0 and passed > 0:
        print("  ALL BUILDERS PASS!")
    elif failed > 0:
        print(f"  {failed} FAILURES - check output above")

    return failed == 0


if __name__ == "__main__":
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    print(f"Verifying builders against hex files in {DATA_DIR}")
    print()
    success = verify_all(verbose)
    sys.exit(0 if success else 1)
