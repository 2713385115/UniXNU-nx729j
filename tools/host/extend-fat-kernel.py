"""Extend the cluster chain of Payloads/kernel.macho so a slightly larger kernel fits.

The nx729j boot image lays out a fixed FAT32 whose kernel.macho chain was sized for
one exact CI build.  A rebuild that adds a few kilobytes (the stage-7 probes add
about 4 KiB) overflows that chain and build-boot-swap-kernel.py refuses to write.
Free clusters are appended to the tail of the chain and every FAT copy is updated;
the file's own directory entry keeps holding the real length.

    python extend-fat-kernel.py IN.img OUT.img --need 125349888
"""
import argparse
import importlib.util
import struct
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('put', HERE / 'patch-unixnu-toml.py')
tt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tt)

LOADER_LEN = 0x301000
END = 0x0FFFFFFF


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('image')
    ap.add_argument('out')
    ap.add_argument('--need', type=int, required=True, help='required byte length')
    ap.add_argument('--path', default='Payloads/kernel.macho')
    args = ap.parse_args()

    info = tt.load_img(args.image)
    payload, dtb = tt.inflate(info['blob'])
    fat = bytearray(payload[LOADER_LEN:])
    vol = None
    for v in tt.find_fat_volume(fat):
        try:
            v.resolve(('EFI', 'UniXNU', 'unixnu.toml'))
            vol = v
            break
        except SystemExit:
            continue
    if vol is None:
        raise SystemExit('no FAT volume with unixnu.toml')

    ent = vol.resolve(('EFI', 'UniXNU') + tuple(args.path.split('/')))
    chain = vol.chain(ent['clus'], limit=4_000_000)
    cap = len(chain) * vol.clus_bytes
    print(f'[0] {args.path}: entry size {ent["size"]}, {len(chain)} clusters, capacity {cap} B, need {args.need} B')
    if cap >= args.need:
        print('[1] capacity already sufficient, nothing to do')
        Path(args.out).write_bytes(Path(args.image).read_bytes())
        return 0

    slack = -(-(args.need - cap) // vol.clus_bytes)
    # Free clusters: FAT entry zero, index at least 2, inside the data region.
    total_clus = (len(fat) - vol.data_off) // vol.clus_bytes + 2
    used = set(chain)
    free = []
    for c in range(2, total_clus):
        if c in used:
            continue
        if struct.unpack_from('<I', fat, vol.fat_off + c * 4)[0] != 0:
            continue
        free.append(c)
        if len(free) >= slack:
            break
    if len(free) < slack:
        raise SystemExit(f'only {len(free)} free clusters, need {slack}')

    print(f'[1] linking {slack} free clusters: {free[0]}..{free[-1]}')
    links = [chain[-1]] + free
    for a, b in zip(links, links[1:]):
        for k in range(vol.nfat):
            off = vol.fat_off + k * vol.fatsz * vol.bps + a * 4
            struct.pack_into('<I', fat, off, b)
    for k in range(vol.nfat):
        off = vol.fat_off + k * vol.fatsz * vol.bps + free[-1] * 4
        struct.pack_into('<I', fat, off, END)

    # Verify the chain now covers the request.
    vol2 = None
    for v in tt.find_fat_volume(fat):
        try:
            v.resolve(('EFI', 'UniXNU', 'unixnu.toml'))
            vol2 = v
            break
        except SystemExit:
            continue
    new_chain = vol2.chain(ent['clus'], limit=4_000_000)
    new_cap = len(new_chain) * vol2.clus_bytes
    print(f'[2] chain now {len(new_chain)} clusters, capacity {new_cap} B')
    if new_cap < args.need:
        raise SystemExit('chain still too small')

    payload = payload[:LOADER_LEN] + bytes(fat)
    co = zlib.compressobj(9, zlib.DEFLATED, 31)
    blob = co.compress(payload) + co.flush() + dtb
    img = tt.build(info, blob)
    Path(args.out).write_bytes(img)
    print(f'[3] wrote {args.out}  {len(img)} B')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
