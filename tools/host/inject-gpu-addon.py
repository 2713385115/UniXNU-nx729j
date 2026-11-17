#!/usr/bin/env python3
"""Inject an official NGPUADD2 sidecar into an nx729j boot image.

The loader (DarwinBoot.c) looks for <KernelPath>.gpuaddon in the payload FAT
volume and requires a matching 16-byte zeroed /chosen/memory-map/GpuAddon
property in the ADT. Both halves are mandatory: a sidecar without the DT
property (or vice versa) makes the loader return EFI_COMPROMISED_DATA.

    python tools/inject-gpu-addon.py IN.img ADDON.bin OUT.img

Steps:
  1. inflate the boot image's gzip(payload)+dtb
  2. create EFI/UniXNU/Payloads/kernel.macho.gpuaddon (FAT32 LFN + cluster chain)
  3. append the GpuAddon property to the memory-map node of kernel.macho.adt
  4. regzip, rebuild the boot image, and read everything back
"""
import argparse
import gzip
import hashlib
import importlib.util
import json
import struct
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_BOOT = 100663296
END_OF_CHAIN = 0x0FFFFFFF
FREE = 0
ORIGINAL_TRUST_SHA = '7416ca4f8377146626bb284e2133c76bee6cb4e3ee0af5d648b186235a7323a7'
SIDECAR = 'kernel.macho.gpuaddon'
SHORT_NAME = b'GPUADD~1GPU'
ADT_PATH = ('EFI', 'UniXNU', 'Payloads', 'kernel.macho.adt')
PAYLOAD_DIR = ('EFI', 'UniXNU', 'Payloads')


def load_tool():
    spec = importlib.util.spec_from_file_location('pt', ROOT / 'tools' / 'patch-unixnu-toml.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fat_entry(vol, copy, c):
    off = vol.fat_off + copy * vol.fatsz * vol.bps + c * 4
    return struct.unpack_from('<I', vol.d, off)[0] & 0x0FFFFFFF


def set_fat_entry(vol, c, value):
    for copy in range(vol.nfat):
        off = vol.fat_off + copy * vol.fatsz * vol.bps + c * 4
        old = struct.unpack_from('<I', vol.d, off)[0]
        struct.pack_into('<I', vol.d, off, (old & 0xF0000000) | (value & 0x0FFFFFFF))


def cluster_count(vol):
    base = vol.base
    tot16 = struct.unpack_from('<H', vol.d, base + 0x13)[0]
    tot32 = struct.unpack_from('<I', vol.d, base + 0x20)[0]
    total = tot32 if tot32 else tot16
    return (total - (vol.rsvd + vol.nfat * vol.fatsz)) // vol.spc


def chain_full(vol, start):
    return vol.chain(start, limit=cluster_count(vol) + 2)


def read_full(vol, ent):
    return b''.join(vol.read_clus(c) for c in chain_full(vol, ent['clus']))[:ent['size']]


def find_free(vol, need, skip):
    out = []
    c = 2
    last = cluster_count(vol) + 1
    while c <= last and len(out) < need:
        if c not in skip and fat_entry(vol, 0, c) == FREE:
            out.append(c)
        c += 1
    if len(out) < need:
        raise SystemExit('FAT free clusters: need %d, found %d' % (need, len(out)))
    return out


def write_clusters(vol, old_chain, data):
    """Assign a cluster chain (grow/shrink) and write data. Returns the new chain."""
    need = max(1, (len(data) + vol.clus_bytes - 1) // vol.clus_bytes)
    if not old_chain:
        chain = find_free(vol, need, set())
        for a, b in zip(chain, chain[1:]):
            set_fat_entry(vol, a, b)
        set_fat_entry(vol, chain[-1], END_OF_CHAIN)
    elif need <= len(old_chain):
        chain = old_chain[:need]
        set_fat_entry(vol, chain[-1], END_OF_CHAIN)
        for c in old_chain[need:]:
            set_fat_entry(vol, c, FREE)
    else:
        chain = list(old_chain)
        extra = find_free(vol, need - len(old_chain), set(old_chain))
        for a, b in zip([chain[-1]] + extra, extra):
            set_fat_entry(vol, a, b)
        set_fat_entry(vol, extra[-1], END_OF_CHAIN)
        chain.extend(extra)
    for i, c in enumerate(chain):
        chunk = data[i * vol.clus_bytes:(i + 1) * vol.clus_bytes]
        chunk += b'\x00' * (vol.clus_bytes - len(chunk))
        off = vol.clus_abs(c)
        vol.d[off:off + vol.clus_bytes] = chunk
    return chain


def lfn_checksum(short11):
    s = 0
    for c in short11:
        s = (((s & 1) << 7) + (s >> 1) + c) & 0xFF
    return s


def lfn_entries(name, checksum):
    chars = list(name)
    count = (len(chars) + 12) // 13
    chunks = [chars[i * 13:(i + 1) * 13] for i in range(count)]
    out = []
    for idx in range(count - 1, -1, -1):
        ordinal = idx + 1
        if idx == count - 1:
            ordinal |= 0x40
        part = list(chunks[idx])
        if idx == count - 1:
            # FAT LFN readers stop at a NUL; without it the 0xFFFF padding
            # becomes part of the name. A full 13-char tail needs its own
            # extra ordinal entry (not needed for kernel.macho.gpuaddon).
            if len(part) >= 13:
                raise SystemExit('LFN tail needs an extra ordinal entry')
            part.append('\x00')
        part += ['\uffff'] * (13 - len(part))
        raw = b''.join(ch.encode('utf-16-le') for ch in part)
        e = bytearray(32)
        e[0] = ordinal
        e[1:11] = raw[:10]
        e[11] = 0x0F
        e[12] = 0
        e[13] = checksum
        e[14:26] = raw[10:22]
        e[28:32] = raw[22:26]
        out.append(bytes(e))
    return out


def dir_slots(vol, dir_ent, count):
    """Return `count` consecutive free 32-byte directory slots, growing the dir if needed."""
    chain = chain_full(vol, dir_ent['clus'])
    buf = b''.join(vol.read_clus(c) for c in chain)
    first = len(buf)
    for i in range(0, len(buf), 32):
        if buf[i] == 0:
            first = i
            break
    while (len(buf) - first) // 32 < count:
        extra = find_free(vol, 1, set(chain))
        c = extra[0]
        set_fat_entry(vol, chain[-1], c)
        set_fat_entry(vol, c, END_OF_CHAIN)
        off = vol.clus_abs(c)
        vol.d[off:off + vol.clus_bytes] = b'\x00' * vol.clus_bytes
        chain.append(c)
        buf = b''.join(vol.read_clus(x) for x in chain)
        # Keep `first`: directory entries may span the old/new cluster boundary.
    slots = []
    for j in range(count):
        pos = first + j * 32
        ci, off = divmod(pos, vol.clus_bytes)
        slots.append(vol.clus_abs(chain[ci]) + off)
    return slots


def add_sidecar(vol, dir_ent, data):
    for e in vol.listdir(dir_ent['clus']):
        if e['name'].lower() == SIDECAR.lower() or e['short'] == SHORT_NAME.decode('ascii'):
            raise SystemExit('sidecar already present in FAT: %r' % e['name'])
    entries = lfn_entries(SIDECAR, lfn_checksum(SHORT_NAME)) + [None]
    slots = dir_slots(vol, dir_ent, len(entries))
    chain = write_clusters(vol, [], data)
    short = bytearray(32)
    short[0:11] = SHORT_NAME
    short[11] = 0x20
    struct.pack_into('<H', short, 20, (chain[0] >> 16) & 0xFFFF)
    struct.pack_into('<H', short, 26, chain[0] & 0xFFFF)
    struct.pack_into('<I', short, 28, len(data))
    entries[-1] = bytes(short)
    for off, e in zip(slots, entries):
        vol.d[off:off + 32] = e
    print('  sidecar    : %s %d bytes, %d clusters, %d dir entries'
          % (SIDECAR, len(data), len(chain), len(entries)))
    return chain[0]


def parse_node(buf, off):
    nprops, nchild = struct.unpack_from('<II', buf, off)
    p = off + 8
    props = []
    for _ in range(nprops):
        key = buf[p:p + 32].rstrip(b'\x00')
        ln = struct.unpack_from('<I', buf, p + 32)[0]
        props.append((key, ln, buf[p + 36:p + 36 + ln]))
        p += 36 + ln + ((-ln) % 4)
    props_end = p
    for _ in range(nchild):
        p, _, _, _, _, _ = parse_node(buf, p)
    return p, nprops, nchild, props, props_end, off


def walk_nodes(buf, off=0):
    end, nprops, nchild, props, props_end, start = parse_node(buf, off)
    yield start, nprops, props, props_end
    p = props_end
    for _ in range(nchild):
        cend, _, _, _, _, _ = parse_node(buf, p)
        yield from walk_nodes(buf, p)
        p = cend


def patch_adt(adt):
    if not isinstance(adt, bytearray):
        adt = bytearray(adt)
    target = None
    for start, nprops, props, props_end in walk_nodes(adt):
        if props and props[0][0] == b'name' and props[0][2].rstrip(b'\x00') == b'memory-map':
            target = (start, nprops, props, props_end)
            break
    if target is None:
        raise SystemExit('ADT has no /chosen/memory-map node')
    start, nprops, props, props_end = target
    if any(k == b'GpuAddon' for k, _, _ in props):
        raise SystemExit('ADT already has a GpuAddon property')
    prop = struct.pack('<32sI', b'GpuAddon', 16) + bytes(16)
    out = adt[:props_end] + bytearray(prop) + adt[props_end:]
    struct.pack_into('<I', out, start, nprops + 1)
    # verify the tree still parses and the property is visible
    found = False
    for _s, _n, p2, _e in walk_nodes(out):
        if any(k == b'GpuAddon' and ln == 16 and v == bytes(16) for k, ln, v in p2):
            found = True
    if not found:
        raise SystemExit('ADT patch did not survive a reparse')
    print('  adt        : +GpuAddon (16 zero bytes), %d -> %d bytes' % (len(adt), len(out)))
    return bytes(out)


def build_owner_trust(original, trust):
    """Official scripts/make-owner-trust-bundle.py logic, offline inputs only."""
    if hashlib.sha256(original).hexdigest() != ORIGINAL_TRUST_SHA:
        raise SystemExit('original trust cache does not match the pinned Apple modules')
    count = struct.unpack_from('<I', original)[0]
    if count != 2:
        raise SystemExit('expected exactly two original trust modules, got %d' % count)
    offsets = struct.unpack_from('<2I', original, 4)
    originals = [original[offsets[0]:offsets[1]], original[offsets[1]:]]
    hashes = sorted(bytes.fromhex(trust[k])
                    for k in ('cdhash', 'daemon_cdhash', 'turnip_cdhash'))
    if len(set(hashes)) != len(hashes):
        raise SystemExit('duplicate CDHash in trust.json')
    ident = uuid.uuid5(uuid.NAMESPACE_URL,
                       'org.blu-sharky.adreno-metal:' + ''.join(h.hex() for h in hashes))
    own = struct.pack('<I16sI', 2, ident.bytes, len(hashes)) + \
        b''.join(h + bytes([2, 0, 0, 0]) for h in hashes)
    modules = [own, *originals]
    cursor, starts = 16, []
    for module in modules:
        starts.append(cursor)
        cursor += len(module)
    bundle = struct.pack('<4I', 3, *starts) + b''.join(modules)
    n = struct.unpack_from('<I', bundle)[0]
    if not 0 < n <= 128:
        raise SystemExit('bad owner trust module count')
    for index in range(n):
        start = struct.unpack_from('<I', bundle, 4 + 4 * index)[0]
        end = struct.unpack_from('<I', bundle, 8 + 4 * index)[0] if index + 1 < n else len(bundle)
        entries = struct.unpack_from('<I', bundle, start + 20)[0]
        if start < 4 + 4 * n or start & 3 or end > len(bundle) or end - start < 24 or \
                struct.unpack_from('<I', bundle, start)[0] != 2 or end - start != 24 + entries * 24:
            raise SystemExit('owner trust bundle would be refused by the loader')
        for entry in range(entries):
            at = start + 24 + entry * 24
            if not 1 <= bundle[at + 20] <= 4 or \
                    (entry and bundle[at - 24:at - 4] >= bundle[at:at + 20]):
                raise SystemExit('owner trust entry order/flags would be refused by the loader')
    print('  trust       : owner module + 2 original Apple modules, %d -> %d bytes'
          % (len(original), len(bundle)))
    return bundle


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('addon')
    ap.add_argument('dst')
    ap.add_argument('--trust-json', help='trust.json from build-adreno-metal-plugin.py')
    a = ap.parse_args()

    addon = Path(a.addon).read_bytes()
    if addon[:8] != b'NGPUADD2':
        raise SystemExit('addon is not NGPUADD2: %r' % addon[:8])
    count, _zero, total = struct.unpack_from('<IIQ', addon, 8)
    if total != len(addon):
        raise SystemExit('addon header size mismatch: %d vs %d' % (total, len(addon)))
    print('addon        : NGPUADD2, %d files, %d bytes' % (count, len(addon)))

    t = load_tool()
    info = t.load_img(a.src)
    payload, dtb = t.inflate(info['blob'])
    payload = bytearray(payload)
    vol0 = t.find_fat_volume(payload)[0]
    vol = t.Fat(payload, vol0.base)

    dir_parts = list(PAYLOAD_DIR)
    dir_ent = vol.resolve(dir_parts)
    add_sidecar(vol, dir_ent, addon)

    adt_ent = vol.resolve(list(ADT_PATH))
    adt = patch_adt(read_full(vol, adt_ent))
    write_clusters(vol, chain_full(vol, adt_ent['clus']), adt)
    struct.pack_into('<I', payload, adt_ent['ent_off'] + 28, len(adt))

    expected_tc = None
    if a.trust_json:
        trust = json.loads(Path(a.trust_json).read_text())
        tc_ent = vol.resolve(list(PAYLOAD_DIR) + ['kernel.macho.trustcache'])
        expected_tc = build_owner_trust(read_full(vol, tc_ent), trust)
        write_clusters(vol, chain_full(vol, tc_ent['clus']), expected_tc)
        struct.pack_into('<I', payload, tc_ent['ent_off'] + 28, len(expected_tc))

    blob = gzip.compress(bytes(payload), 9, mtime=0) + dtb
    out = t.build(info, blob)
    if len(out) > MAX_BOOT:
        raise SystemExit('boot image exceeds partition limit: %d' % len(out))
    Path(a.dst).write_bytes(out)
    print('image        : %s %d bytes (was %d)' % (a.dst, len(out), len(info['raw'])))

    info2 = t.load_img(a.dst)
    payload2, dtb2 = t.inflate(info2['blob'])
    v2 = t.Fat(payload2, t.find_fat_volume(payload2)[0].base)
    e2 = v2.resolve(list(PAYLOAD_DIR) + [SIDECAR])
    got = read_full(v2, e2)
    adt2 = read_full(v2, v2.resolve(list(ADT_PATH)))
    has = any(k == b'GpuAddon' and v == bytes(16)
              for _s, _n, props, _e in walk_nodes(adt2) for k, ln, v in props)
    tc_ok = True
    if expected_tc is not None:
        tc2 = read_full(v2, v2.resolve(list(PAYLOAD_DIR) + ['kernel.macho.trustcache']))
        tc_ok = tc2 == expected_tc
    ok = got == addon and has and dtb2 == dtb and tc_ok
    print('verify       : sidecar=%s (%d bytes) GpuAddon=%s dtb=%s trust=%s'
          % (got == addon, len(got), has, dtb2 == dtb, tc_ok))
    if not ok:
        raise SystemExit('verification failed')
    print('OK')


if __name__ == '__main__':
    main()
