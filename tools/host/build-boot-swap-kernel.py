#!/usr/bin/env python3
"""Swap the kernel inside a prepared boot image, keeping loader stub and FAT layout.

Why: boot-u082.img carries the Data seed and the ADT with PackedRAMDisk, but its
kernel.macho is u053, which was built with XNU_OWNER_DATA_MOUNT=0 -- it contains
no native_owner_data_* symbols at all, so the kernel never mounts the owner Data
volume even when the seed is present.

kernel-u055.macho has the full set (native_owner_data_status / _stage / _readonly /
_device / _seed_base / _seed_size / _seed_sha256 / _device_path), i.e. it was built
with XNU_OWNER_DATA_MOUNT=1.  Its size, 125,251,584 B, is exactly the cluster-chain
capacity of Payloads/kernel.macho in that FAT, which confirms the FAT was laid out
for it.

Usage:
    python build-boot-swap-kernel.py --base boot-u082.img --kernel work/ci-u055/kernel-u055.macho --out boot-u083.img
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


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tt = _load("put", "patch-unixnu-toml.py")


def chain_full(vol, start):
    out, seen, c = [], set(), start
    while 2 <= c < 0x0FFFFFF8:
        if c in seen:
            raise SystemExit("FAT chain loop")
        seen.add(c)
        out.append(c)
        c = vol.next_clus(c)
    return out


def write_file_var(vol, ent, data):
    chain = chain_full(vol, ent["clus"])
    cap = len(chain) * vol.clus_bytes
    if len(data) > cap:
        raise SystemExit("kernel %d B exceeds cluster capacity %d B" % (len(data), cap))
    pos = 0
    for c in chain:
        o = vol.clus_abs(c)
        n = min(vol.clus_bytes, len(data) - pos)
        vol.d[o:o + n] = data[pos:pos + n]
        if n < vol.clus_bytes:
            vol.d[o + n:o + vol.clus_bytes] = b"\x00" * (vol.clus_bytes - n)
        pos += n
    struct.pack_into("<I", vol.d, ent["ent_off"] + 28, len(data))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--kernel", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    info = tt.load_img(a.base)
    p, dtb = tt.inflate(info["blob"])
    if len(p) != PAYLOAD_LEN:
        raise SystemExit("base payload is %d B, expected %d" % (len(p), PAYLOAD_LEN))
    loader = p[:LOADER_LEN]
    fat = bytearray(p[LOADER_LEN:])
    print("[0] base %s  loader=%d  fat=%d  dtb=%d" % (a.base, len(loader), len(fat), len(dtb)))

    vol = None
    for v in tt.find_fat_volume(fat):
        try:
            v.resolve(("EFI", "UniXNU", "unixnu.toml"))
            vol = v
            break
        except SystemExit:
            continue
    if vol is None:
        raise SystemExit("no FAT volume with unixnu.toml")

    # Sanity: the seed and the ADT must already be there (base is u082).
    if vol.resolve(("EFI", "UniXNU", "Payloads", "scratch-data.img")) is None:
        raise SystemExit("base has no Data seed; run build-boot-seed-v2.py first")
    adt = vol.read_file(vol.resolve(("EFI", "UniXNU", "Payloads", "kernel.macho.adt")))
    if b"PackedRAMDisk" not in adt:
        raise SystemExit("base ADT does not advertise PackedRAMDisk")

    new_kernel = Path(a.kernel).read_bytes()
    ent = vol.resolve(("EFI", "UniXNU", "Payloads", "kernel.macho"))
    old_size = ent["size"]
    write_file_var(vol, ent, new_kernel)
    print("[1] kernel %d B -> %d B (dir entry was %d B)" % (old_size, len(new_kernel), old_size))

    payload = loader + bytes(fat)
    co = zlib.compressobj(9, zlib.DEFLATED, 31)
    blob = co.compress(payload) + co.flush() + dtb
    img = tt.build(info, blob)
    Path(a.out).write_bytes(img)
    print("[2] wrote %s  %d B (limit %d, %s)"
          % (a.out, len(img), MAX_BOOT, "fits" if len(img) <= MAX_BOOT else "TOO BIG"))
    if len(img) > MAX_BOOT:
        raise SystemExit("image exceeds boot_a")

    # Read everything back.
    chk = tt.load_img(a.out)
    p2, d2 = tt.inflate(chk["blob"])
    v2 = None
    for v in tt.find_fat_volume(bytearray(p2[LOADER_LEN:])):
        try:
            v.resolve(("EFI", "UniXNU", "unixnu.toml"))
            v2 = v
            break
        except SystemExit:
            continue
    got = v2.read_file(v2.resolve(("EFI", "UniXNU", "Payloads", "kernel.macho")))
    seed_ent = v2.resolve(("EFI", "UniXNU", "Payloads", "scratch-data.img"))
    ok = [
        ("payload_len", len(p2) == PAYLOAD_LEN),
        ("loader", p2[:LOADER_LEN] == loader),
        ("dtb", d2 == dtb),
        ("kernel_bytes", got == new_kernel),
        ("seed", seed_ent is not None),
        ("adt_packed", b"PackedRAMDisk" in v2.read_file(v2.resolve(("EFI", "UniXNU", "Payloads", "kernel.macho.adt")))),
        ("initrd", b'initrd = "Payloads/scratch-data.img"' in v2.read_file(v2.resolve(("EFI", "UniXNU", "unixnu.toml")))),
    ]
    print("[3] verify: " + "  ".join("%s=%s" % (k, "OK" if v else "FAIL") for k, v in ok))
    if not all(v for _k, v in ok):
        raise SystemExit("verification failed")


if __name__ == "__main__":
    main()
