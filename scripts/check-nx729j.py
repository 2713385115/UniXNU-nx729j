#!/usr/bin/env python3
"""Preflight for the nx729j port.

Run by `unixnu build firmware` (see [firmware] preflight in board.toml) and safe
to run by hand at any time:

    python3 scripts/check-nx729j.py
    python3 scripts/check-nx729j.py --uefi ../uefi-sheng

Hard failures stop the build. Everything that only means "this part is not
brought up yet" is reported as a warning, because a port is expected to boot in
stages: firmware, menu, payload, kernel, platform expert, storage, root, userland.

Only the standard library is used, so it runs inside the build tree.
"""
from pathlib import Path
import argparse
import hashlib
import json
import re
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]

OK, WARN, FAIL = 'ok', 'warn', 'fail'
results = []


def add(level, item, message):
    results.append((level, item, message))


def covered_by_reservations(rows, base, size):
    """这块地址是否已被 Reserv 条目（可以多条拼起来）完整盖住。

    不能要求单条精确匹配：映射表里既有聚合条目（"PIL Reserved" 一条盖 400 MB），
    也有把一段拆成几块的（"XBL DT" + "XBL Ramdump" + "AOP" 合起来盖住
    xbl_dt_log_merged）。安全性只看这片地址有没有整块落在保留区里——
    只要覆盖没有空洞，UEFI 就不会分配它，拆分方式不影响结论。
    """
    target = base + size
    end = base
    for start, stop in sorted((a, a + s) for _, a, s, attrs in rows if 'Reserv' in attrs):
        if stop <= end:
            continue
        if start > end:
            break
        end = max(end, stop)
        if end >= target:
            return True
    return False


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def find_uefi(explicit):
    """Locate the Mu-Silicium checkout: --uefi, then the usual siblings."""
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    candidates += [ROOT.parent / 'uefi-sheng', ROOT / 'uefi-sheng', Path.cwd() / 'uefi-sheng',
                   Path.cwd().parent / 'uefi-sheng']
    for candidate in candidates:
        if (candidate / 'Platforms').is_dir():
            return candidate.resolve()
    return None


def check_memory_map(uefi, firmware):
    """Compare the UEFI memory descriptors with the DTB reserved regions.

    This is the failure mode that costs the most time on a new board: the
    firmware maps or clobbers a region the stock ABL reserved, and the phone
    dies before anything is printed. sheng's preflight does the same check.
    """
    memory_map = uefi / 'Platforms' / 'ZTE' / 'nx729jPkg' / 'Library' / 'MemoryMapLib' / 'MemoryMapLib.c'
    if not memory_map.exists():
        add(FAIL, 'uefi.memory_map', f'missing {memory_map.name}')
        return
    source = memory_map.read_text(errors='replace')
    rows = [(m.group(1), int(m.group(2), 16), int(m.group(3), 16), m.group(4))
            for m in re.finditer(r'\{"([^"]+)"\s*,\s*(0x[0-9a-fA-F]+)\s*,\s*(0x[0-9a-fA-F]+)\s*,([^}]+)\}', source)]
    if not rows:
        add(WARN, 'uefi.memory_map', 'no descriptors parsed (table layout changed?)')
        return
    add(OK, 'uefi.memory_map', f'{len(rows)} descriptors')

    problems = []
    for index, (name, base, size, _) in enumerate(rows):
        if size % 4096 or base % 4096 or not size:
            problems.append(f'{name}: not page aligned')
        for other, other_base, other_size, _ in rows[index + 1:]:
            if max(base, other_base) < min(base + size, other_base + other_size):
                problems.append(f'{name} overlaps {other}')
    add(FAIL if problems else OK, 'uefi.memory_map.alignment',
        '; '.join(problems[:4]) if problems else 'aligned, no overlap')

    # The firmware volume and the UEFI stack must be described here, or the first
    # memory write lands in a reserved range.
    config = uefi / 'Resources' / 'Configs' / f'{firmware.get("config", "")}.toml'
    if config.exists():
        with open(config, 'rb') as handle:
            fd = tomllib.load(handle).get('uefi_fd', {})
        base, size = fd.get('base'), fd.get('size')
        covered = any(a == base and s == size for _, a, s, _ in rows)
        add(OK if covered else FAIL, 'uefi.fd_window',
            f'{base:#x}/{size:#x} {"described" if covered else "not described"} by the memory map')

    dsc = uefi / str(firmware.get('dsc', ''))
    if dsc.exists():
        stack = re.search(r'PcdCPUCoresStackBase\|(0x[0-9a-fA-F]+)', dsc.read_text(errors='replace'))
        if stack:
            address = int(stack.group(1), 16)
            add(OK if any(a == address for _, a, _, _ in rows) else FAIL, 'uefi.stack',
                f'{address:#x} {"described" if any(a == address for _, a, _, _ in rows) else "not described"}')

    # Optional cross-check against the real device tree, when fdtget is around.
    dtb = uefi / 'Resources' / 'DTBs' / 'nx729j.dtb'
    if dtb.exists():
        try:
            listing = subprocess.run(['fdtget', '-l', str(dtb), '/reserved-memory'],
                                     capture_output=True, text=True, timeout=30)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            add(WARN, 'uefi.dtb.reservations', 'fdtget not available; reservation cross-check skipped')
            return
        if listing.returncode:
            add(WARN, 'uefi.dtb.reservations', 'no /reserved-memory node in the DTB')
            return
        missing = []
        for node in listing.stdout.split():
            values = subprocess.run(['fdtget', '-t', 'x', str(dtb), f'/reserved-memory/{node}', 'reg'],
                                    capture_output=True, text=True)
            if values.returncode:
                continue
            cells = [int(word, 16) for word in values.stdout.split()]
            for index in range(0, len(cells) - 3, 4):
                base = (cells[index] << 32) | cells[index + 1]
                size = (cells[index + 2] << 32) | cells[index + 3]
                if not size:
                    # 大小为 0 的节点（demura_region_0/1 就是这样）不占任何地址空间：
                    # ABL 启动时才把实际区间填进去（见 docs 里关于 demura 的逆向结论），
                    # 静态 DTB 里是 0。这种没有"未映射"可言，跳过。
                    continue
                # 判"已保护"：这片地址被 Reserv 条目完整盖住（允许多条拼接），
                # 细节见 covered_by_reservations 的说明。
                if not covered_by_reservations(rows, base, size):
                    missing.append(f'{node} {base:#x}/{size:#x}')
        add(FAIL if missing else OK, 'uefi.dtb.reservations',
            f'{len(missing)} reserved regions unmapped: ' + ', '.join(missing[:4])
            if missing else 'every DTB reservation has a matching descriptor')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uefi', type=Path, default=None,
                        help='Mu-Silicium checkout, normally ../uefi-sheng')
    parser.add_argument('--board', type=Path, default=ROOT / 'board.toml')
    parser.add_argument('--require-userdata-geometry', action='store_true',
                        help='treat an unmeasured userdata partition as fatal; '
                             'use before `unixnu build userdata` or any flash')
    args = parser.parse_args()

    if not args.board.exists() and (Path.cwd() / 'board.toml').exists():
        args.board = Path.cwd() / 'board.toml'
    if not args.board.exists():
        add(FAIL, 'board.toml', f'missing: {args.board}')
        return report()

    with open(args.board, 'rb') as handle:
        board = tomllib.load(handle)

    # ---------------------------------------------------------------- identity
    name = board.get('board', {}).get('name')
    add(OK if name == 'nx729j' else FAIL, 'board.name',
        f'{name!r} (expected "nx729j")')
    soc = board.get('board', {}).get('soc')
    add(OK if soc == 'SM8550' else WARN, 'board.soc', f'{soc!r}')

    # ------------------------------------------------------------------- IPSW
    ipsw = board.get('ipsw', {})
    if ipsw.get('product') == 'iPad7,1' and ipsw.get('build') == '21H461':
        add(OK, 'ipsw', 'iPad7,1 17.7.11 21H461, same target image as sheng')
    else:
        add(WARN, 'ipsw',
            f'{ipsw.get("product")} {ipsw.get("version")} {ipsw.get("build")}: '
            'a different image needs fresh text_fixups offsets and a new prelink pin')

    # --------------------------------------------------------------- userdata
    userdata = board.get('userdata', {})
    first = userdata.get('first_lba', 0)
    last = userdata.get('last_lba', 0)
    lba_bytes = userdata.get('lba_bytes', 0)
    if not first or not last or first >= last:
        # WARN by default: the firmware, package and empty-ramdisk smoke-test
        # boot image do not touch userdata, so an unmeasured partition must not
        # block milestone A. It does block `unixnu build userdata` and every
        # flash, so ask for a hard failure with --require-userdata-geometry
        # (tools/flash-nx729j.py passes it).
        add(FAIL if args.require_userdata_geometry else WARN, 'userdata',
            'first_lba/last_lba are not measured yet: this only blocks '
            '`unixnu build userdata` and flashing. Run '
            '`python nx729j.py measure` (tools/) or '
            '`python3 scripts/collect-nx729j.py` and paste the two values '
            'into board.toml')
    elif lba_bytes != 4096:
        add(FAIL, 'userdata', f'lba_bytes {lba_bytes}: UFS logical block is 4096')
    else:
        size = (last - first + 1) * lba_bytes
        add(OK, 'userdata', f'{first:#x}..{last:#x} = {size / 2**30:.1f} GiB')

    # ----------------------------------------------------------------- kernel
    defconfig = board.get('kernel', {}).get('defconfig')
    path = ROOT / 'configs' / str(defconfig)
    add(OK if path.exists() else FAIL, 'kernel.defconfig',
        f'configs/{defconfig} {"" if path.exists() else "missing"}'.strip())

    prelink = board.get('kernel', {}).get('prelink', {})
    add(OK if prelink.get('slide') else FAIL, 'kernel.prelink.slide',
        f'{prelink.get("slide")!r}')
    add(OK if prelink.get('kernelcache_sha256') else FAIL, 'kernel.prelink.kernelcache_sha256',
        'pinned' if prelink.get('kernelcache_sha256') else 'missing')

    # --------------------------------------------------------------- firmware
    firmware = board.get('firmware', {})
    packages = firmware.get('packages', [])
    if f'{{uefi}}/Platforms/ZTE' in packages:
        add(OK, 'firmware.packages', 'Platforms/ZTE present')
    else:
        add(WARN, 'firmware.packages', 'Platforms/ZTE missing: the ZTE DSC will not resolve')
    add(OK if firmware.get('fd', '').endswith('SILICIUM_UEFI.fd') else WARN,
        'firmware.fd', firmware.get('fd', 'missing'))

    # ----------------------------------------------------------------- payload
    payload = board.get('payload', {})
    volume = payload.get('volume', '')
    add(OK if 0 < len(volume) <= 11 else FAIL, 'payload.volume',
        f'{volume!r} ({len(volume)} chars, FAT label limit 11)')
    dram = int(payload.get('dram_size', '0x0'), 0)
    if dram == 0x400000000:
        add(OK, 'payload.dram_size', '0x400000000 (16 GiB)')
    else:
        add(WARN, 'payload.dram_size',
            f'{payload.get("dram_size")}: 16 GiB SKUs report 0x400000000 '
            '(8 GiB = 0x200000000, 12 GiB = 0x300000000)')

    kernel_args = payload.get('kernel_args', [])
    for required in ('native-ufs-root=1', 'sheng-pmgr=2'):
        add(OK if required in kernel_args else FAIL, 'payload.kernel_args', required)
    add(OK, 'payload.text_fixups',
        f'{len(board.get("payload", {}).get("text_fixups", []))} entries')

    adt = payload.get('adt', {})
    tool = ROOT / str(adt.get('tool', ''))
    add(OK if tool.exists() else FAIL, 'payload.adt.tool', str(adt.get('tool')))
    add(OK if adt.get('chip_id') == '207' else WARN, 'payload.adt.chip_id',
        f'{adt.get("chip_id")!r} (SM8550-AB is 0x207)')

    # ------------------------------------------------------------ UEFI inputs
    uefi = find_uefi(args.uefi)
    if uefi is None:
        add(WARN, 'uefi', 'no Mu-Silicium checkout found; firmware inputs not verified '
                          '(clone UniXNU/uefi-sheng next to this repository, or pass --uefi)')
    else:
        add(OK, 'uefi', str(uefi))
        dsc = uefi / str(firmware.get('dsc', ''))
        add(OK if dsc.exists() else FAIL, 'uefi.dsc',
            str(firmware.get('dsc')) if dsc.exists() else f'missing {dsc}')
        config = uefi / 'Resources' / 'Configs' / f'{firmware.get("config", "")}.toml'
        add(OK if config.exists() else FAIL, 'uefi.config',
            str(config.relative_to(uefi)) if config.exists() else f'missing {config}')
        dtb = uefi / 'Resources' / 'DTBs' / 'nx729j.dtb'
        if dtb.exists():
            digest = sha256(dtb)
            add(OK, 'uefi.dtb', f'{dtb.name} {dtb.stat().st_size} bytes sha256 {digest[:16]}…')
            blob = dtb.read_bytes()
            if b'cont_splash_region' in blob:
                add(OK, 'uefi.dtb.splash',
                    'cont_splash_region present: do not add a [[bootimg.dtb_node]] '
                    'for it, the stock ABL finds the region in the device tree')
            else:
                add(FAIL, 'uefi.dtb.splash',
                    'no cont_splash_region: the ABL will power the panel off, add the '
                    'reserved-memory node from the sheng board file with the region '
                    'measured on this device')
        else:
            add(FAIL, 'uefi.dtb', f'missing {dtb}')
        check_memory_map(uefi, firmware)

    # ------------------------------------------------------- bring-up staging
    panel = ROOT / 'docs' / 'reference' / 'panel'
    panel_json = panel / 'nx729j-panel.json'
    if panel_json.exists():
        try:
            with open(panel_json, encoding='utf-8') as handle:
                described = json.load(handle)
            info = described.get('panel', {})
            timings = described.get('timings', [])
            init = next((t['on_command']['bytes'] for t in timings
                         if t.get('on_command')), 0)
            add(OK, 'panel',
                f"{info.get('panel_name')} {info.get('width')}x{info.get('height')} "
                f"{info.get('refresh_rates')} Hz, init {init} bytes "
                f"(regenerate: scripts/extract-panel-timings.py --active)")
            if not init:
                add(WARN, 'panel.init',
                    'no init sequence captured: the panel stays dark after the '
                    'bootloader hands over')
        except Exception as error:  # noqa: BLE001
            add(WARN, 'panel', f'{panel_json.name} unreadable: {error}')
    elif panel.is_dir() and any(panel.iterdir()):
        add(OK, 'panel', 'display inputs present')
    else:
        add(WARN, 'panel',
            'no display inputs yet: run scripts/extract-panel-timings.py --active '
            'against the nx729j DTS to capture timings and the init sequence')

    if (ROOT / 'drivers' / 'NativeGoodixTouch').exists():
        add(WARN, 'touch',
            'Goodix GT9916 driver scaffold present but not implemented yet: '
            'native touch needs it, the bootloader HID path does not')
    return report()


def report():
    width = max(len(item) for _, item, _ in results)
    hard = 0
    for level, item, message in results:
        if level == FAIL:
            hard += 1
        print(f'[{level:4}] {item:<{width}}  {message}')
    print()
    print(f'{len(results)} checks, {hard} blocking')
    if hard:
        print('nx729j: preflight failed')
        return 1
    print('nx729j: preflight passed (warnings are expected before bring-up)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
