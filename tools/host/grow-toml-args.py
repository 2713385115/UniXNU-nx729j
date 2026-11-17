#!/usr/bin/env python3
"""扩充 unixnu.toml 的 FAT 簇链，让启动参数能放超过 512 B。

为什么需要
----------
TOML 只占 1 个 512 B 簇，args 273 字符 + 201 B 骨架 = 474 B，只剩 38 B 余量。
sheng 官方 board.toml 有 45 条 kernel_args，我们的镜像只塞了 16 条 ——
缺的 7 条 `offline-legacy-*` 正是原生显示（GopFramebuffer）初始化开关。

做法
----
在同一个 FAT 卷里找空闲簇（FAT 项 == 0），接到 TOML 簇链尾部，镜像所有 FAT 副本
同步写入，再更新目录项 size。payload 总大小不变，所以 boot 镜像尺寸不变。

用法:
    grow-toml-args.py IN.img OUT.img --args "<新的 args 字符串>" [--kernel K.macho]
    grow-toml-args.py IN.img --show
"""
import argparse
import importlib.util
import struct
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAX_BOOT = 100663296
TOML_PATH = ("EFI", "UniXNU", "unixnu.toml")
KERNEL_PATH = ("EFI", "UniXNU", "Payloads", "kernel.macho")
EOC = 0x0FFFFFFF


def _load(name, fn):
    spec = importlib.util.spec_from_file_location(name, HERE / fn)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


toml_tool = _load("put", "patch-unixnu-toml.py")


def chain_full(vol, start):
    out, seen, c = [], set(), start
    while 2 <= c < 0x0FFFFFF8:
        if c in seen:
            raise SystemExit("FAT 簇链成环 @%d" % c)
        seen.add(c)
        out.append(c)
        c = vol.next_clus(c)
    return out


def read_file_fast(vol, ent):
    buf = bytearray(ent["size"])
    pos = 0
    for c in chain_full(vol, ent["clus"]):
        if pos >= ent["size"]:
            break
        o = vol.clus_abs(c)
        n = min(vol.clus_bytes, ent["size"] - pos)
        buf[pos:pos + n] = vol.d[o:o + n]
        pos += n
    return bytes(buf)


def set_fat(vol, clus, value):
    """写入所有 FAT 副本。"""
    for k in range(vol.nfat):
        base = vol.fat_off + k * vol.fatsz * vol.bps
        struct.pack_into("<I", vol.d, base + clus * 4, value & 0x0FFFFFFF)


def grow_chain(vol, chain, need):
    """把簇链扩到至少 need 个簇，返回新链。"""
    if len(chain) >= need:
        return chain
    used = set(chain)
    total = vol.fatsz * vol.bps // 4
    extra = []
    c = 2
    while len(extra) < need - len(chain):
        if c >= total:
            raise SystemExit("FAT 里空闲簇不够：需要 %d，只找到 %d"
                             % (need - len(chain), len(extra)))
        if c not in used and vol.next_clus(c) == 0:
            extra.append(c)
            used.add(c)
        c += 1
    chain = list(chain) + extra
    for i in range(len(chain) - 1):
        set_fat(vol, chain[i], chain[i + 1])
    set_fat(vol, chain[-1], EOC)
    # 清空新簇内容
    for c in extra:
        o = vol.clus_abs(c)
        vol.d[o:o + vol.clus_bytes] = b"\x00" * vol.clus_bytes
    return chain


def pick_volume(payload):
    for vol in toml_tool.find_fat_volume(payload):
        try:
            vol.resolve(TOML_PATH)
            vol.resolve(KERNEL_PATH)
        except SystemExit:
            continue
        return vol
    raise SystemExit("没有同时含 unixnu.toml 与 Payloads/kernel.macho 的 FAT 卷")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst", nargs="?")
    ap.add_argument("--args")
    ap.add_argument("--kernel")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()

    info = toml_tool.load_img(a.src)
    payload, dtb = toml_tool.inflate(info["blob"])
    payload = bytearray(payload)
    vol = pick_volume(payload)
    tent = vol.resolve(TOML_PATH)
    kent = vol.resolve(KERNEL_PATH)

    cur = read_file_fast(vol, tent)
    m = toml_tool.ARGS_RE.search(cur)
    print("卷 '%s' clus=%d  TOML %d B  TOML 簇链 %d 簇（容量 %d B）"
          % (vol.label, vol.clus_bytes, tent["size"],
             len(chain_full(vol, tent["clus"])), len(chain_full(vol, tent["clus"])) * vol.clus_bytes))
    print("当前 args (%d 字符) = %r" % (len(m.group(1)), m.group(1).decode()))

    if a.show or not (a.args or a.kernel):
        return

    if not a.dst:
        raise SystemExit("需要输出路径")

    if a.kernel:
        newk = Path(a.kernel).read_bytes()
        kchain = chain_full(vol, kent["clus"])
        kneed = (len(newk) + vol.clus_bytes - 1) // vol.clus_bytes
        if kneed > len(kchain):
            print("[1] 内核簇链扩容 %d -> %d 簇（内核 %d B）"
                  % (len(kchain), kneed, len(newk)))
            kchain = grow_chain(vol, kchain, kneed)
            struct.pack_into("<I", vol.d, kent["ent_off"] + 28, len(newk))
        elif len(newk) != kent["size"]:
            print("[1] 内核等长复用簇链：%d B" % len(newk))
            struct.pack_into("<I", vol.d, kent["ent_off"] + 28, len(newk))
        pos = 0
        for c in kchain:
            o = vol.clus_abs(c)
            n = min(vol.clus_bytes, len(newk) - pos)
            if n <= 0:
                vol.d[o:o + vol.clus_bytes] = b"\x00" * vol.clus_bytes
                continue
            vol.d[o:o + n] = newk[pos:pos + n]
            pos += n
        print("[1] 内核已换入 %d B" % len(newk))

    if a.args:
        newargs = a.args.strip().encode()
        content = cur[:m.start(1)] + newargs + cur[m.end(1):]
        chain = chain_full(vol, tent["clus"])
        need = (len(content) + vol.clus_bytes - 1) // vol.clus_bytes
        if need > len(chain):
            print("[2] 簇链扩容 %d -> %d 簇" % (len(chain), need))
            chain = grow_chain(vol, chain, need)
        pos = 0
        for c in chain:
            o = vol.clus_abs(c)
            n = min(vol.clus_bytes, len(content) - pos)
            if n <= 0:
                vol.d[o:o + vol.clus_bytes] = b"\x00" * vol.clus_bytes
                continue
            vol.d[o:o + n] = content[pos:pos + n]
            if n < vol.clus_bytes:
                vol.d[o + n:o + vol.clus_bytes] = b"\x00" * (vol.clus_bytes - n)
            pos += n
        struct.pack_into("<I", vol.d, tent["ent_off"] + 28, len(content))
        print("[3] args 已改 (%d 字符)，TOML %d B / 容量 %d B"
              % (len(newargs), len(content), len(chain) * vol.clus_bytes))

    co = zlib.compressobj(9, zlib.DEFLATED, 31)
    img = toml_tool.build(info, co.compress(bytes(payload)) + co.flush() + dtb)
    Path(a.dst).write_bytes(img)
    print("[4] 输出 %s  %d B（上限 %d，%s）"
          % (a.dst, len(img), MAX_BOOT, "放得下" if len(img) <= MAX_BOOT else "放不下"))

    # 回读
    chk = toml_tool.load_img(a.dst)
    p2, d2 = toml_tool.inflate(chk["blob"])
    v2 = pick_volume(p2)
    t2 = v2.resolve(TOML_PATH)
    back = read_file_fast(v2, t2)
    got = toml_tool.ARGS_RE.search(back).group(1).decode()
    ok = (a.args is None) or (got == a.args.strip())
    print("[5] 回读 payload一致=%s dtb一致=%s args一致=%s"
          % (p2 == bytes(payload), d2 == dtb, ok))
    print("    读回 args = %r" % got)
    if not ok or p2 != bytes(payload):
        raise SystemExit("回读校验失败")


if __name__ == "__main__":
    main()
