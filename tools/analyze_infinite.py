#!/usr/bin/env python3
"""Analyze the PENKITINFENG infinite canvas binary format."""
import zipfile, struct, json, gzip
from pathlib import Path

ARCHIVE = Path(__file__).parent.parent / "sample" / "无边.hinote"

def u32(data, offset):
    return struct.unpack_from(">I", data, offset)[0]

def f32(data, offset):
    return struct.unpack_from(">f", data, offset)[0]

with zipfile.ZipFile(ARCHIVE) as z:
    for n in sorted(z.namelist()):
        if n.endswith(".bin"):
            data = z.read(n)
            magic = data[:12]
            is_penkit = magic == b"PENKITINFENG"
            print(f"{'='*60}")
            print(f"File: {n}  ({len(data)} bytes)")
            print(f"{'='*60}")
            
            if is_penkit:
                print(f"  Magic:        {data[:12].decode('ascii')}")
                print(f"  [12:16] BlockLen:   {u32(data,12)} (0x{u32(data,12):08x})")
                print(f"  [16:20] penType:    {u32(data,16)} (0x{u32(data,16):08x})")
                print(f"  [20:24] field20:    {u32(data,20)} (0x{u32(data,20):08x})")
                print(f"  [24:28] colorRaw:   {u32(data,24)} (0x{u32(data,24):08x})")
                print(f"  [28:32] baseWidth:  {f32(data,28):.4f}")
                print(f"  [32:36] unknown32:  {f32(data,32):.4f}")
                print(f"  [36:40] opacity:    {f32(data,36):.4f}")
                print(f"  [40:44] stride:     {u32(data,40)}")
                print(f"  [44:52] raw44_52:   {data[44:52].hex()}")
                
                ah = data[52:]
                print(f"\n  Data after 52-byte header: {len(ah)} bytes")
                
                # Heuristic scan for point tables [count, stride, 0]
                stride = u32(data, 40)
                if 0 < stride < 100:
                    tables_found = []
                    for off in range(0, len(ah) - 12, 2):
                        cnt = u32(ah, off)
                        st = u32(ah, off + 4)
                        zr = u32(ah, off + 8)
                        if 2 <= cnt <= 16384 and st == stride and zr == 0:
                            # Validate: coordinates should be finite floats
                            pts_data = ah[off+12:off+12+cnt*stride]
                            if len(pts_data) >= cnt * stride:
                                # Check first and last point for validity
                                x0 = struct.unpack_from(">f", pts_data, 0)[0]
                                y0 = struct.unpack_from(">f", pts_data, 4)[0]
                                xn = struct.unpack_from(">f", pts_data, (cnt-1)*stride)[0]
                                yn = struct.unpack_from(">f", pts_data, (cnt-1)*stride+4)[0]
                                if (abs(x0) < 1e6 and abs(y0) < 1e6 and 
                                    abs(xn) < 1e6 and abs(yn) < 1e6):
                                    tables_found.append((off, cnt))
                    
                    print(f"  Valid point tables found ({len(tables_found)}):")
                    for off, cnt in tables_found:
                        # Read first 3 points to show coordinates
                        pts_start = 52 + off + 12
                        coords = []
                        for i in range(min(3, cnt)):
                            x = struct.unpack_from(">f", data, pts_start + i*stride)[0]
                            y = struct.unpack_from(">f", data, pts_start + i*stride + 4)[0]
                            coords.append(f"({x:.1f},{y:.1f})")
                        pad = "..." if cnt > 3 else ""
                        print(f"    [{off:5d}] count={cnt:4d}  points: {', '.join(coords)}{pad}")
                
                # Hex dump of first 128 bytes
                print(f"\n  Hex dump (first 128 bytes):")
                for i in range(0, min(128, len(data)), 16):
                    hex_str = " ".join(f"{b:02x}" for b in data[i:i+16])
                    ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in data[i:i+16])
                    print(f"    {i:04x}: {hex_str:48s}  {ascii_str}")
                print()
            
            elif data[:3] == b"gsd" or data[:3] == b"ged":
                ftype = "GSD (Global State Data)" if data[:3] == b"gsd" else "GED (Global Element Data?)"
                print(f"  Type: {ftype}")
                print(f"  Raw hex: {data.hex()}")
                print(f"  BE parse:")
                for i in range(0, len(data), 4):
                    if i + 4 <= len(data):
                        v = u32(data, i)
                        fv = f32(data, i)
                        print(f"    [{i:3d}:{i+3:3d}] uint={v:12d} (0x{v:08x})  float={fv:.6f}")
                print()

# Also analyze the PNG thumbnail
with zipfile.ZipFile(ARCHIVE) as z:
    for n in z.namelist():
        if n.endswith(".png"):
            png_data = z.read(n)
            w, h = struct.unpack_from(">II", png_data, 16)
            print(f"PNG Thumbnail: {n}  {w}x{h}  ({len(png_data)} bytes)")
