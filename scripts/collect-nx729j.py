#!/usr/bin/env python3
"""Measure the values of the nx729j port that no public source can supply.

The one hard requirement for `unixnu build userdata` is the geometry of the
phone's own userdata partition: its first and last LBA in the GPT of UFS LUN 0.
Everything else in board.toml is already known, so this script collects the
geometry plus a few sanity values and writes device-info.json next to the
checks it feeds.

Two ways in:

  1. Straight from the phone, over adb, with root
         python3 scripts/collect-nx729j.py
     Reads the first 34 sectors of every /dev/block/sd* LUN with dd and parses
     the GPT in Python. Nothing is written to the device.

  2. From a GPT dump you already have
         python3 scripts/collect-nx729j.py --gpt userdata-gpt.bin
     The file only needs to hold the GPT header and the partition array, i.e.
     the first 34 sectors (17 KiB) of the LUN.

The script never writes to the phone. `adb shell` calls are read only.
"""
from pathlib import Path
import argparse
import json
import struct
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
SECTOR = 512
GPT_HEADER_SECTOR = 1
MAX_GPT_BYTES = 34 * SECTOR


def adb(*args, binary=False):
    command = ['adb', *args]
    try:
        result = subprocess.run(command, capture_output=True, timeout=60)
    except FileNotFoundError:
        sys.exit('adb not found: install platform-tools and put it on PATH')
    except subprocess.TimeoutExpired:
        sys.exit(f'timed out: {" ".join(command)}')
    if result.returncode != 0:
        return None, result.stderr.decode('utf-8', 'replace').strip()
    return (result.stdout if binary else result.stdout.decode('utf-8', 'replace')), None


def sh(script, binary=False):
    """Run a shell command as root if possible, else as the shell user."""
    for prefix in (['su', '-c'], ['sh', '-c']):
        out, err = adb('exec-out' if binary else 'shell',
                       *([] if binary else []),
                       ' '.join(prefix[:2] + [f"'{script}'"]), binary=binary)
        if out is not None and out:
            return out, None
    return None, err


def read_props():
    props = {}
    for key in ('ro.product.model', 'ro.product.device', 'ro.product.name',
                'ro.board.platform', 'ro.build.version.release',
                'ro.build.version.incremental', 'ro.boot.hardware',
                'ro.boot.slot_suffix'):
        value, _ = adb('shell', 'getprop', key)
        props[key] = (value or '').strip()
    return props


def read_memtotal():
    value, _ = sh('cat /proc/meminfo')
    if not value:
        return None
    for line in value.splitlines():
        if line.startswith('MemTotal:'):
            kib = int(line.split()[1])
            return {'memtotal_kib': kib, 'memtotal_bytes': kib * 1024}
    return None


def parse_gpt(blob, source):
    """Return (lun, partitions) from a raw GPT blob, or raise ValueError."""
    if len(blob) < 2 * SECTOR:
        raise ValueError(f'{source}: shorter than two sectors')
    header = blob[SECTOR:SECTOR + SECTOR]
    if header[:8] != b'EFI PART':
        raise ValueError(f'{source}: no EFI PART signature at LBA 1')
    header_size = struct.unpack_from('<I', header, 0x0C)[0]
    entry_lba = struct.unpack_from('<Q', header, 0x48)[0]
    entry_count = struct.unpack_from('<I', header, 0x50)[0]
    entry_size = struct.unpack_from('<I', header, 0x54)[0]
    if not 0x5C <= header_size <= SECTOR or not 128 <= entry_size <= 1024 or not entry_count:
        raise ValueError(f'{source}: implausible GPT header '
                         f'(header_size {header_size}, entry_size {entry_size}, count {entry_count})')
    base = entry_lba * SECTOR
    if base + entry_count * entry_size > len(blob):
        raise ValueError(f'{source}: partition array is past the dump; '
                         'read at least 34 sectors')
    partitions = []
    for index in range(entry_count):
        offset = base + index * entry_size
        entry = blob[offset:offset + entry_size]
        if entry[:16] == bytes(16):
            continue
        first, last = struct.unpack_from('<QQ', entry, 0x20)
        attrs = struct.unpack_from('<Q', entry, 0x30)[0]
        name = entry[0x38:0x38 + 72].decode('utf-16-le', 'replace').split('\0')[0]
        partitions.append({'index': index + 1, 'name': name,
                           'first_lba': first, 'last_lba': last,
                           'attributes': f'{attrs:#018x}',
                           'sectors': last - first + 1,
                           'bytes': (last - first + 1) * SECTOR})
    return partitions


def dump_lun(device):
    """Read the GPT of one block device over adb, as root if available."""
    script = f'dd if={device} bs=512 count=34 2>/dev/null'
    blob, err = sh(script, binary=True)
    if not blob:
        return None, err or 'no data (needs root)'
    return blob, None


def from_device():
    props = read_props()
    if not props.get('ro.product.device') and not props.get('ro.product.model'):
        sys.exit('no device on adb: `adb devices` must list it, with USB debugging on')
    print(f"device: {props.get('ro.product.model')} "
          f"/ {props.get('ro.product.device')} "
          f"/ {props.get('ro.board.platform')} "
          f"/ {props.get('ro.build.version.incremental')}")

    luns, err = sh('ls /dev/block/sd* 2>/dev/null')
    if not luns:
        print(f'cannot list LUNs: {err}')
        return None
    found = {}
    for device in sorted(set(luns.split())):
        blob, error = dump_lun(device)
        if not blob:
            print(f'{device}: {error}')
            continue
        try:
            partitions = parse_gpt(blob, device)
        except ValueError as exc:
            print(f'{device}: {exc}')
            continue
        found[device] = partitions
        print(f'{device}: {len(partitions)} partitions')
    return {'props': props, 'memtotal': read_memtotal(), 'luns': found}


def summarize(info, out_path, name='userdata'):
    luns = info['luns']
    chosen = None
    for device, partitions in luns.items():
        names = {p['name'] for p in partitions}
        if name in names:
            chosen = device
            break
    if chosen is None:
        print(f'\nno LUN carries a partition named "{name}".')
        for device, partitions in luns.items():
            print(f'{device}: {", ".join(p["name"] for p in partitions[:12])} …')
        print('If the naming differs, pass the right partition name with --name.')
        return 1

    partitions = luns[chosen]
    userdata = next(p for p in partitions if p['name'] == name)
    print(f'\nuserdata on {chosen}: index {userdata["index"]}')
    for partition in partitions:
        print(f'  {partition["index"]:>3} {partition["name"]:<24} '
              f'{partition["first_lba"]:#012x}..{partition["last_lba"]:#012x} '
              f'{partition["bytes"] / 2**30:9.2f} GiB')

    result = {
        'generated_by': 'scripts/collect-nx729j.py',
        'device': info['props'],
        'memtotal': info['memtotal'],
        'gpt_device': chosen,
        'userdata': {
            'first_lba': userdata['first_lba'],
            'last_lba': userdata['last_lba'],
            'lba_bytes': SECTOR,
            'bytes': userdata['bytes'],
            'gib': round(userdata['bytes'] / 2**30, 2),
        },
        'partitions': partitions,
    }
    out_path.write_text(json.dumps(result, indent=2) + '\n')
    print(f'\nwrote {out_path}')
    print('\nput these into board.toml:')
    print('[userdata]')
    print(f'first_lba = {userdata["first_lba"]:#x}')
    print(f'last_lba = {userdata["last_lba"]:#x}')
    print('lba_bytes = 4096')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--gpt', type=Path, default=None,
                        help='raw dump of the first 34 sectors of UFS LUN 0')
    parser.add_argument('--out', type=Path, default=REPO / 'device-info.json')
    parser.add_argument('--name', default='userdata',
                        help='partition name to measure (default userdata)')
    args = parser.parse_args()

    if args.gpt:
        blob = args.gpt.read_bytes()
        partitions = parse_gpt(blob, str(args.gpt))
        info = {'props': {'source': str(args.gpt)}, 'memtotal': None,
                'luns': {str(args.gpt): partitions}}
    else:
        info = from_device()
        if info is None:
            return 1
    return summarize(info, args.out, args.name)


if __name__ == '__main__':
    sys.exit(main())
