#!/usr/bin/env python3
"""Swap Payloads/scratch-data.img inside a prepared boot image.

The official skip-setup step rewrites the Data seed (DBRDISK1 packed APFS) with
five preference files and four container directories.  Only that payload entry
is allowed to change here: loader stub, kernel.macho, ADT, trust cache, DTB and
unixnu.toml must stay byte-identical so a regression can be attributed to the
seed alone.

    python tools/build-boot-swap-seed.py \
        --base out/boot-s7tz.img \
        --seed work/ci-<run>/data-skip-setup-<hash>.dbr \
        --out out/boot-s7skip.img

If the new seed is larger than the existing FAT chain, first run
    python tools/extend-fat-kernel.py BASE EXT --need SIZE \
        --path Payloads/scratch-data.img
and use EXT as --base.
"""
import argparse
import importlib.util
import struct
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOADER_LEN = 0x301000
FAT_LEN = 201326592
PAYLOAD_LEN = LOADER_LEN + FAT_LEN
MAX_BOOT = 100663296
ROOT = ('EFI', 'UniXNU')
SEED_PATH = ROOT + ('Payloads', 'scratch-data.img')
KEEP = ('kernel.macho', 'kernel.macho.adt', 'kernel.macho.trustcache')


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tt = _load('put', 'patch-unixnu-toml.py')


def find_vol(fat):
    for v in tt.find_fat_volume(fat):
        try:
            v.resolve(ROOT + ('unixnu.toml',))
            return v
        except SystemExit:
            continue
    raise SystemExit('no FAT volume with unixnu.toml')


def chain_full(vol, start):
    out, seen, c = [], set(), start
    while 2 <= c < 0x0FFFFFF8:
        if c in seen:
            raise SystemExit('FAT chain loop')
        seen.add(c)
        out.append(c)
        c = vol.next_clus(c)
    return out


def write_file_var(vol, ent, data, label):
    chain = chain_full(vol, ent['clus'])
    cap = len(chain) * vol.clus_bytes
    if len(data) > cap:
        raise SystemExit('%s %d B exceeds cluster capacity %d B (%d clusters); '
                         'run extend-fat-kernel.py --path Payloads/scratch-data.img first'
                         % (label, len(data), cap, len(chain)))
    pos = 0
    for c in chain:
        o = vol.clus_abs(c)
        n = min(vol.clus_bytes, len(data) - pos)
        vol.d[o:o + n] = data[pos:pos + n]
        if n < vol.clus_bytes:
            vol.d[o + n:o + vol.clus_bytes] = b'\x00' * (vol.clus_bytes - n)
        pos += n
    struct.pack_into('<I', vol.d, ent['ent_off'] + 28, len(data))


def snapshot(vol):
    out = {'toml': vol.read_file(vol.resolve(ROOT + ('unixnu.toml',)))}
    for name in KEEP:
        try:
            out[name] = vol.read_file(vol.resolve(ROOT + ('Payloads', name)))
        except SystemExit:
            out[name] = None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True)
    ap.add_argument('--seed', required=True, help='new DBRDISK1 packed Data seed')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()

    info = tt.load_img(a.base)
    p, dtb = tt.inflate(info['blob'])
    if len(p) != PAYLOAD_LEN:
        raise SystemExit('base payload is %d B, expected %d' % (len(p), PAYLOAD_LEN))
    loader = p[:LOADER_LEN]
    fat = bytearray(p[LOADER_LEN:])
    vol = find_vol(fat)

    ent = vol.resolve(SEED_PATH)
    old = vol.read_file(ent)
    if old[:8] != b'DBRDISK1':
        raise SystemExit('base seed is not DBRDISK1: %r' % old[:16])
    before = snapshot(vol)

    new = Path(a.seed).read_bytes()
    if new[:8] != b'DBRDISK1':
        raise SystemExit('new seed is not DBRDISK1: %r' % new[:16])
    print('[0] base %s  seed %d B -> %d B' % (a.base, len(old), len(new)))

    write_file_var(vol, ent, new, 'seed')
    payload = loader + bytes(fat)
    co = zlib.compressobj(9, zlib.DEFLATED, 31)
    blob = co.compress(payload) + co.flush() + dtb
    img = tt.build(info, blob)
    Path(a.out).write_bytes(img)
    print('[1] wrote %s  %d B (limit %d, %s)'
          % (a.out, len(img), MAX_BOOT, 'fits' if len(img) <= MAX_BOOT else 'TOO BIG'))
    if len(img) > MAX_BOOT:
        raise SystemExit('image exceeds boot_a')

    chk = tt.load_img(a.out)
    p2, d2 = tt.inflate(chk['blob'])
    v2 = find_vol(bytearray(p2[LOADER_LEN:]))
    after = snapshot(v2)
    checks = [
        ('payload_len', len(p2) == PAYLOAD_LEN),
        ('loader', p2[:LOADER_LEN] == loader),
        ('dtb', d2 == dtb),
        ('seed_bytes', v2.read_file(v2.resolve(SEED_PATH)) == new),
        ('toml', after['toml'] == before['toml']),
        ('kernel_bytes', after['kernel.macho'] == before['kernel.macho']),
        ('adt', after['kernel.macho.adt'] == before['kernel.macho.adt']),
        ('trustcache', after['kernel.macho.trustcache'] == before['kernel.macho.trustcache']),
        ('initrd', b'initrd = "Payloads/scratch-data.img"' in after['toml']),
    ]
    print('[2] verify: ' + '  '.join('%s=%s' % (k, 'OK' if v else 'FAIL') for k, v in checks))
    if not all(v for _k, v in checks):
        raise SystemExit('verification failed')


if __name__ == '__main__':
    main()
