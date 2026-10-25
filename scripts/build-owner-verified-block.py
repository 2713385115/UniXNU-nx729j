#!/usr/bin/env python3
"""Link the native_verified_block_* queries for the UFS verified block device.

Why this exists
---------------
`patches/xnu/offline-owner-*.patch` calls `native_verified_block_root_authorized`
and `native_verified_root_probe`. Upstream those live in NativeVirtioBlock, so a
kernel built without that driver ends the link with

    Undefined symbols for architecture arm64:
      "_native_verified_block_root_authorized", referenced from:
          _bsd_init in bsd_init.o

and the owner-root chain can never be enabled on a board whose verified block
device is NativeUfsBlock instead of virtio. That is exactly the nx729j case:
build-arm-driver.py NativeUfs publishes the authenticated window, and
build-verified-root-bundle.py supplies the owner-signed envelopes it
authenticates against.

This script compiles drivers/NativeUfs/OwnerVerifiedBlock.c and merges the
object into build/native-ufs/libNativeUfs.a, the archive the kernel link already
force-loads. Definitions placed there are found by construction; a separate
archive on LD_KERNEL_ARCHIVES was tried twice upstream and produced an
undefined-symbol link both times.

Ordering
--------
This entry must come after "build-arm-driver.py NativeUfs" (it merges into that
archive) and after "build-verified-root-bundle.py" (it reads the policy count
that script links in). board.toml keeps that order.

Scope
-----
Only the two symbols the stage-6 configuration references are provided. The
volume lookup and manifest digest belong to OWNER_CACHE_MOUNT and
OWNER_BOOT_MANIFEST, which stay off for stage 6; supplying guesses for them
would let mountroot act on a device that was never resolved.
"""

import json
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build/owner-verified-block'
RECIPE = ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT/bsd_init.o.json'
SOURCE = ROOT / 'drivers/NativeUfs/OwnerVerifiedBlock.c'
UFS = ROOT / 'build/native-ufs/libNativeUfs.a'
NEEDED = {'_native_verified_root_probe', '_native_verified_block_root_authorized'}


def fail(message):
    raise SystemExit('build-owner-verified-block: ' + message)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    if not SOURCE.is_file():
        fail(f'missing {SOURCE}')
    if not RECIPE.is_file():
        fail(f'missing {RECIPE}; the compile database has not been produced yet')
    if not UFS.is_file():
        fail(f'missing {UFS}; this entry has to come after "build-arm-driver.py NativeUfs"')
    record = json.loads(RECIPE.read_text().strip().rstrip(','))

    destination = OUT / 'OwnerVerifiedBlock.o'
    # Reuse the kernel's own command line for bsd_init.o with only the input file
    # swapped, so the object has the same target triple, deployment target and
    # include order as the code that calls into it.
    command = []
    index = 0
    while index < len(record['arguments']):
        value = record['arguments'][index]
        if value == record['file']:
            command.append(str(SOURCE))
        elif value == '-o':
            command += ['-o', str(destination)]
            index += 1
        elif value == '-Xclang' and index + 1 < len(record['arguments']) \
                and record['arguments'][index + 1].startswith('-W'):
            index += 1
        elif not value.startswith('-W'):
            command.append(value)
        index += 1
    command.append('-Wno-error')

    (OUT / 'compile-command.json').write_text(json.dumps(command, indent=2) + '\n')
    result = subprocess.run(command, cwd=record['directory'],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    (OUT / 'compile.log').write_text(result.stdout or '')
    if result.returncode:
        fail('OwnerVerifiedBlock.c did not compile:\n' + (result.stdout or '')[-3000:])

    data = destination.read_bytes()
    magic, cpu, subtype, kind, count = struct.unpack_from('<5I', data, 0)
    if (magic, cpu, subtype, kind) != (0xfeedfacf, 0x100000c, 0, 1):
        fail('the object is not ordinary ARM64 output; the target triple came out wrong')

    defined = subprocess.run(['xcrun', 'nm', '-g', str(destination)],
                             capture_output=True, text=True, check=True).stdout
    symbols = {line.split()[-1] for line in defined.splitlines()
               if len(line.split()) >= 3 and line.split()[-2] != 'U'}
    missing = NEEDED - symbols
    if missing:
        fail('the object does not export ' + ', '.join(sorted(missing)))

    subprocess.run(['xcrun', 'ar', 'rs', str(UFS), str(destination)], check=True)
    merged = subprocess.run(['xcrun', 'nm', '-g', str(UFS)],
                            capture_output=True, text=True, check=True).stdout
    present = {line.split()[-1] for line in merged.splitlines()
               if len(line.split()) >= 3 and line.split()[-2] != 'U'}
    absent = NEEDED - present
    if absent:
        fail('libNativeUfs.a still lacks ' + ', '.join(sorted(absent)))

    print('OwnerVerifiedBlock: %s merged into %s' %
          (', '.join(sorted(NEEDED)), UFS.relative_to(ROOT)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
