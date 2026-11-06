#!/usr/bin/env python3
"""把 XNU 的内核 console 输出画到帧缓冲上（红魔 8 Pro+ / NX729J 专用补丁）。

为什么需要
----------
DarwinBoot 交接时留下了一个可绘制的帧缓冲，panic 屏已经用它画了好几轮。
但普通的内核日志一行都看不到：`vc_console_ready` 一直是 0，所以
`vcputc_options` 在开头就 return，所有 kprintf 静默丢弃。

（实测确认的两条事实，不是推测：
   __cnputs 入口先读 _disableConsoleOutput@0xfffffff007aae960，值为 0 → 允许输出，
   所以输出通道本身没被关；真正挡住的是 vc 状态机没初始化。
   _console_write 尾部 `b __cnputs`，_console_write_char → _console_write(&c,1)，
   所以 __cnputs 是所有文本输出的唯一汇聚点。）

做法
----
1. 把 scripts/xnu-console-screen.c 编成一段 freestanding 代码，
   放进 __TEXT_EXEC 里一处已验证为全零的填充区（代码洞）。
2. 把 __cnputs 的第一条指令换成 `b <代码洞>`，原函数体不再执行。
   原第一条是 `sub sp, sp, #0x80`，跳过它不破坏任何调用约定。

游标状态不放内核内存（__TEXT_EXEC 只读，__DATA 里找不到真正空闲的区域——
percpu 槽位占了那些"空洞"），而是存在帧缓冲最后一行，因此文字区域比屏幕矮一行。
这个补丁不写任何内核变量，只改两处：一条跳转 + 代码洞。

用法
----
    python3 patch-xnu-console-screen.py <kernel.macho> [--code FILE] [--dry-run]

在已打过 --panic-screen 的内核上运行（make-boot.sh 已按这个顺序调用）。
"""
import argparse
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_CODE = HERE / 'xnu-console-screen.c'

# __cnputs 的第一条指令：sub sp, sp, #0x80
CNPUTS_FIRST = 0xD10203FF

_FONT_SUFFIX = 'Silicon/Silicium/SiliciumPkg/Library/FrameBufferSerialPortLib'
FONT_DIR_CANDIDATES = [
    (Path(os.environ['UNIXNU_UEFI']) / _FONT_SUFFIX) if os.environ.get('UNIXNU_UEFI') else None,
    ROOT / 'uefi-sheng' / _FONT_SUFFIX,
    ROOT / 'work' / 'uefi-sheng-sheng' / _FONT_SUFFIX,
]


def tool(name):
    found = shutil.which(name)
    if not found:
        raise SystemExit('need %s on PATH (llvm/clang toolchain)' % name)
    return found


SEGMENTS = []   # (name, vmaddr, vmsize, fileoff, filesize, initprot)


def load_segments(data):
    magic, ncmds = struct.unpack_from('<Ii', data, 0)
    if magic != 0xFEEDFACF:
        raise SystemExit('not a 64-bit Mach-O: %#x' % magic)
    off = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', data, off)
        if cmd == 0x19:
            name = data[off + 8:off + 24].rstrip(b'\0').decode()
            vm, vmsz, fo, fs = struct.unpack_from('<QQQQ', data, off + 24)
            maxp, initp = struct.unpack_from('<ii', data, off + 56)
            SEGMENTS.append((name, vm, vmsz, fo, fs, initp))
        off += size


def file_offset(va):
    for name, vm, vmsz, fo, fs, _ in SEGMENTS:
        if vm <= va < vm + fs:
            return fo + (va - vm)
    raise SystemExit('%#x is not file-backed in this kernel' % va)


def read_word(data, va):
    return struct.unpack_from('<I', data, file_offset(va))[0]


def branch(from_va, to_va):
    delta = (to_va - from_va) // 4
    if (to_va - from_va) % 4 or not -(1 << 25) <= delta < (1 << 25):
        raise SystemExit('branch %#x -> %#x is out of range' % (from_va, to_va))
    return 0x14000000 | (delta & 0x3FFFFFF)


def symbols_of(kernel):
    nm = tool('llvm-nm')
    table = {}
    out = subprocess.run([nm, '-n', str(kernel)], capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3:
            try:
                table.setdefault(parts[2], int(parts[0], 16))
            except ValueError:
                pass
    return table


def free_holes(data, need):
    """全零且足够大的 __TEXT_EXEC 填充区，按到 need 无关的大小降序返回。"""
    holes = []
    for name, vm, vmsz, fo, fs, initp in SEGMENTS:
        if name != '__TEXT_EXEC':
            continue
        blob = data[fo:fo + fs]
        i, n = 0, len(blob)
        while i < n:
            if blob[i] == 0:
                j = i
                while j < n and blob[j] == 0:
                    j += 1
                if j - i >= need:
                    holes.append((j - i, vm + i))
                i = j
            else:
                i += 1
    holes.sort(reverse=True)
    return holes


def font_dir():
    for candidate in FONT_DIR_CANDIDATES:
        if candidate is not None and (candidate / 'Font.h').is_file():
            return candidate
    raise SystemExit('cannot find FrameBufferSerialPortLib/Font.h (set it in the script)')


def compile_c(source, va, workdir, vinfo_va):
    clang, lld = tool('clang'), tool('ld.lld')
    objcopy, nm, size = tool('llvm-objcopy'), tool('llvm-nm'), tool('llvm-size')
    obj, elf, binary = (workdir / n for n in ('console.o', 'console.elf', 'console.bin'))
    subprocess.run([clang, '--target=aarch64-none-elf', '-ffreestanding', '-fno-builtin',
                    '-fno-stack-protector', '-mgeneral-regs-only', '-fno-pic', '-mcmodel=tiny',
                    '-Oz', '-fno-asynchronous-unwind-tables', '-Wall', '-Wextra',
                    '-DVINFO_ADDR=%#xUL' % vinfo_va,
                    '-I%s' % font_dir(), '-c', str(source), '-o', str(obj)], check=True)
    nums = subprocess.run([size, '--format=berkeley', str(obj)],
                          capture_output=True, text=True, check=True).stdout.splitlines()[-1].split()
    if int(nums[1]) or int(nums[2]):
        # 代码跑在内核只读 text 里，全局变量第一次 store 就会页错误。
        raise SystemExit('console screen has writable data (data %s, bss %s); state must be external'
                         % (nums[1], nums[2]))
    script = workdir / 'console.ld'
    script.write_text(
        'SECTIONS { . = %#x; .text : { *(.text.a_entry) *(.text*) *(.rodata*) } '
        '/DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) *(.ARM.*) } }\n' % va)
    subprocess.run([lld, '-T', str(script), '-e', 'console_screen', str(obj), '-o', str(elf)], check=True)
    subprocess.run([objcopy, '-O', 'binary', '-j', '.text', str(elf), str(binary)], check=True)

    load = entry = None
    readelf = tool('llvm-readelf')
    for line in subprocess.run([readelf, '-S', str(elf)], capture_output=True, text=True, check=True).stdout.splitlines():
        parts = line.replace('[', ' ').replace(']', ' ').split()
        if len(parts) >= 4 and parts[1] == '.text':
            load = int(parts[3], 16)
    for line in subprocess.run([nm, str(elf)], capture_output=True, text=True, check=True).stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] == 'console_screen':
            entry = int(parts[0], 16)
    if load is None or entry != load:
        raise SystemExit('console_screen is not the first byte of .text (load %s, entry %s)'
                         % (load, entry))
    return binary.read_bytes(), load


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('kernel', type=Path, help='the COPY of the kernel to patch in place')
    parser.add_argument('--code', type=Path, default=DEFAULT_CODE, help='console screen C source')
    parser.add_argument('--dry-run', action='store_true', help='report the plan, write nothing')
    args = parser.parse_args()

    data = bytearray(args.kernel.read_bytes())
    load_segments(data)
    syms = symbols_of(args.kernel)

    for name in ('__cnputs', '_vinfo'):
        if name not in syms:
            raise SystemExit('symbol %s is missing; this is not the kernel these patches are for'
                             % name)
    cnputs = syms['__cnputs']
    vinfo = syms['_vinfo']

    original = read_word(data, cnputs)
    if original != CNPUTS_FIRST:
        raise SystemExit('__cnputs at %#x starts with %#010x, expected %#010x (sub sp, sp, #0x80). '
                         'Already patched, or a different kernel build.'
                         % (cnputs, original, CNPUTS_FIRST))

    # 先用一个占位地址量出代码大小（-fno-pic 下地址不影响指令长度），再选洞。
    with tempfile.TemporaryDirectory() as tmp:
        probe, _ = compile_c(args.code, 0x400000, Path(tmp), vinfo)
    need = len(probe)
    holes = free_holes(data, need + 8)
    if not holes:
        raise SystemExit('no zero-filled run in __TEXT_EXEC is %#x bytes long; the kernel image '
                         'has no room for the console screen' % need)
    # 最近的洞优先，减少跳转距离并让同一份镜像里的补丁集中。
    holes.sort(key=lambda item: (abs(item[1] - cnputs), -item[0]))
    for size, start in holes:
        # lld 把 .text 对齐到 8（字体表要求），所以从 8 对齐处开始放。
        cave = (start + 7) & ~7
        if cave + need > start + size:
            continue
        if any(data[file_offset(cave):file_offset(cave) + need]):
            continue
        break
    else:
        raise SystemExit('no clean hole of %#x bytes after 8-byte alignment' % need)

    with tempfile.TemporaryDirectory() as tmp:
        code, load = compile_c(args.code, cave, Path(tmp), vinfo)
    if load != cave:
        raise SystemExit('linker put .text at %#x, not %#x' % (load, cave))
    if len(code) > size - (cave - start):
        raise SystemExit('code is %#x bytes at %#x but the hole ends at %#x'
                         % (len(code), cave, start + size))

    print('__cnputs      %#x' % cnputs)
    print('vinfo         %#x' % vinfo)
    print('console code  %#x bytes -> %#x (hole %#x bytes, %d candidates)'
          % (len(code), cave, size, len(holes)))
    print('patch         %#x: %#010x -> %#010x  (b %#x)'
          % (cnputs, original, branch(cnputs, cave), cave))

    if args.dry_run:
        print('\n--dry-run: nothing written')
        return

    data[file_offset(cave):file_offset(cave) + len(code)] = code
    struct.pack_into('<I', data, file_offset(cnputs), branch(cnputs, cave))
    args.kernel.write_bytes(data)
    print('\nwrote %s' % args.kernel)


if __name__ == '__main__':
    main()
