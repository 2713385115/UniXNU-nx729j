#!/usr/bin/env python3
"""Compare the payload FAT of two nx729j boot images file by file.

Used to prove a single-argument change (boot-arguments#apply-arguments) did not
disturb the kernel, ADT, trust cache, GPU sidecar or Data seed.

    python tools/verify-p7-image.py OLD.img NEW.img
"""
import hashlib
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("g", HERE / "grow-toml-args.py")
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


def walk(img):
    info = g.toml_tool.load_img(str(img))
    payload, _dtb = g.toml_tool.inflate(info["blob"])
    vol = g.pick_volume(bytearray(payload))
    files, visited = {}, set()

    def rec(clus, prefix):
        if clus in visited:
            return
        visited.add(clus)
        for e in vol.listdir(clus):
            name = e["name"]
            if name in (".", "..", ""):
                continue
            path = prefix + [name]
            if e["attr"] & 0x10:
                rec(e["clus"], path)
            else:
                data = vol.read_file(e)
                files["/".join(path)] = (len(data), hashlib.sha256(data).hexdigest())

    rec(vol.rootclus, [])
    return files, hashlib.sha256(payload).hexdigest(), len(payload)


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    old, new = Path(sys.argv[1]), Path(sys.argv[2])
    a, pa, la = walk(old)
    b, pb, lb = walk(new)
    print("payload: %d -> %d bytes, sha equal=%s" % (la, lb, pa == pb))
    print("%-48s %10s %10s %s" % ("file", "old", "new", "state"))
    changed = []
    for name in sorted(set(a) | set(b)):
        if name not in a:
            print("%-48s %10s %10d NEW" % (name, "-", b[name][0]))
            changed.append(name)
        elif name not in b:
            print("%-48s %10d %10s GONE" % (name, a[name][0], "-"))
            changed.append(name)
        else:
            same = a[name][1] == b[name][1]
            print("%-48s %10d %10d %s" % (name, a[name][0], b[name][0], "same" if same else "CHANGED"))
            if not same:
                changed.append(name)
    print("changed: %s" % (", ".join(changed) if changed else "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
