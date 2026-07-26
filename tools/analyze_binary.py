#!/usr/bin/env python3
"""Deep binary analysis of PENKITINFENG format."""
import struct, zipfile
from pathlib import Path

ARCHIVE = Path(__file__).parent.parent / "sample" / "无边.hinote"

def u32(data, offset):
    return struct.unpack_from(">I", data, offset)[0]

def f32(data, offset):
    return struct.unpack_from(">f", data, offset)[0]

def dump_hex(data, label, start=0, length=None):
    length = length or min(len(data)-start, 256)
    print(f"\n  {label} (offset {start}, {length} bytes):")
    for i in range(start, min(start+length, len(data)), 16):
        hex_str = " ".join(f"{b:02x}" for b in data[i:i+16])
        ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in data[i:i+16])
        print(f"    {i:04x}: {hex_str:48s}  {ascii_str}")

# Scan entire file for table patterns [count, stride, 0]
def scan_tables(data, start_offset, max_stride=100):
    tables = []
    for off in range(start_offset, len(data) - 12, 1):
        cnt = u32(data, off)
        stride = u32(data, off + 4)
        zero = u32(data, off + 8)
        if 2 <= cnt <= 16384 and 0 < stride <= max_stride and zero == 0:
            pts_end = off + 12 + cnt * stride
            if pts_end <= len(data):
                tables.append((off, cnt, stride))
    return tables

with zipfile.ZipFile(ARCHIVE) as z:
    for bin_name in sorted(z.namelist()):
        if not bin_name.endswith('.bin'):
            continue
        data = z.read(bin_name)
        is_penkit = data[:12] == b'PENKITINFENG'
        if not is_penkit:
            continue
            
        print(f"\n{'='*70}")
        print(f"FILE: {bin_name} ({len(data)} bytes)")
        print(f"{'='*70}")
        
        # === PHASE 1: Find actual header size by looking for the first table ===
        # Scan from offset 0 for [count, stride, 0] patterns
        tables = scan_tables(data, 0)
        print(f"\nPhase 1: Scanning for [count, stride, 0] table headers")
        print(f"  Total potential tables found: {len(tables)}")
        
        if tables:
            first_table = tables[0]
            print(f"  First table at offset {first_table[0]}: count={first_table[1]}, stride={first_table[2]}")
            print(f"  Header size = {first_table[0]} bytes")
            
            # Dump the header area
            dump_hex(data, "Header area", 0, first_table[0] + 32)
            
            # Dump first 4 points
            pts_start = first_table[0] + 12
            stride = first_table[2]
            count = first_table[1]
            print(f"\n  First table: count={count}, stride={stride}")
            print(f"  Point data at offset {pts_start}, {count}x{stride}={count*stride} bytes")
            
            print(f"\n  First 5 points as BIG-ENDIAN floats:")
            for i in range(min(5, count)):
                po = pts_start + i * stride
                vals = []
                for field in range(stride // 4):
                    fv = f32(data, po + field * 4)
                    uv = u32(data, po + field * 4)
                    vals.append(f"f{field}={fv:+.2f}")
                print(f"    Point[{i}] @ {po}: {' | '.join(vals)}")
            
            # Try to interpret fields like PENCILENGINE: x,y at offset+4,+8
            print(f"\n  Trying PENCILENGINE-like layout (x=off+4, y=off+8):")
            for i in range(min(3, count)):
                po = pts_start + i * stride
                x = f32(data, po+4)
                y = f32(data, po+8)
                r0 = u32(data, po)
                r12 = u32(data, po+12)
                p = f32(data, po+16) if stride >= 20 else 0
                print(f"    Pt[{i}]  offset={po}:  reserved={r0}  x={x:.2f}  y={y:.2f}  reserved12={r12}  pressure={p:.4f}")
            
            # Also try x=off, y=off+4
            print(f"\n  Trying x=off+0, y=off+4:")
            for i in range(min(3, count)):
                po = pts_start + i * stride
                x = f32(data, po)
                y = f32(data, po+4)
                f8 = f32(data, po+8) if stride >= 12 else 0
                f12 = f32(data, po+12) if stride >= 16 else 0
                print(f"    Pt[{i}]  x={x:.2f}  y={y:.2f}  f8={f8:.4f}  f12={f12:.4f}")
        
        # === PHASE 2: List all found tables ===
        if tables:
            print(f"\nPhase 2: All valid tables in file:")
            for off, cnt, stride in tables:
                pts_start = off + 12
                pts_end = pts_start + cnt * stride
                # Verify first and last coordinate make sense
                if cnt >= 2:
                    x0 = f32(data, pts_start)
                    y0 = f32(data, pts_start+4)
                    x1 = f32(data, pts_start + (cnt-1)*stride)
                    y1 = f32(data, pts_start + (cnt-1)*stride + 4)
                    print(f"  Table[{off:5d}] count={cnt:4d} stride={stride:2d}  "
                          f"data=[{pts_start:5d}:{pts_end:5d}]  "
                          f"first=({x0:.1f},{y0:.1f})  last=({x1:.1f},{y1:.1f})")

print(f"\n{'='*70}")
print("DONE")
print(f"{'='*70}")
