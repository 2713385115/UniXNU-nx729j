#!/usr/bin/env python3
"""Supply the VerifiedRoot policy symbols so NativeUfs links before any
original image has been verified.

NativeUfs is compiled exactly like the qualified configuration, with
-DNATIVE_VERIFIED_BLOCK=1. That pulls in four data symbols and two entry
points from the VerifiedRoot kit. The kit itself only produces a real archive
once a full C-signature and block-verification pass over the original OS image
has run (build/verified-root-os/verification.json), which does not exist for a
new board yet.

This archive stands in for that link only:
  - verified_root_policy_count is 0, so NativeUfsBlock::configureVerification()
    refuses at its first check and no block device is ever published through
    the verified path;
  - vr_open() and vr_verify_block() return a failure code;
  - the crypto table points at refusing functions instead of NULL.

The raw transport (hostBringUp, the command queue, hostRead4k) is not touched
by any of this, so `native-ufs-probe=10` and `native-ufs-probe=6/7/8` run the
ordinary UFS code. Nothing here can authenticate a read; a build that needs to
authenticate reads has to come back with the real kit.
"""
import json
import struct
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build/verified-root-stub'
OUT.mkdir(parents=True, exist_ok=True)

record = json.loads((ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/'
                     'IOPlatformExpert.cpo.json').read_text().strip().rstrip(','))

SOURCE_TEXT = r'''// SPDX-License-Identifier: BSD-2-Clause-Patent
// Link-time placeholder for the VerifiedRoot kit. Every entry refuses.
#include <stddef.h>
#include <stdint.h>
#include "verified_root.h"
#include "VerifiedBlockPolicy.h"

namespace {

int refuseHash (const void *, size_t, uint8_t[32])
{
  return VR_KEY;
}

int refuseSignature (const uint8_t[VR_KEY_BYTES], const uint8_t[32], const uint8_t[VR_SIGNATURE_BYTES])
{
  return VR_KEY;
}

}  // namespace

extern "C" {

// Every one of these is reached only through NativeUfs, which lives in another
// object of the same link, so each definition is marked used.
//
// The declarations below are not decoration: a namespace-scope const object in
// C++ has internal linkage by default, and extern "C" only changes the name
// mangling, not the linkage. Without an extern declaration first, these four
// objects would be local symbols that the linker cannot see, and the kernel link
// would fail with "_verified_root_key referenced from configureVerification()"
// while this archive sat in the link with the symbol present but invisible.
extern const NativeVerifiedBlockPolicy verified_root_policies[];
extern const uint64_t verified_root_policy_count;
extern const uint8_t verified_root_key[];
extern const struct vr_crypto vr_xnu_crypto;

// Zero policies: configureVerification() refuses before it reads any manifest.
__attribute__((used)) const NativeVerifiedBlockPolicy verified_root_policies[1] = { { nullptr, 0 } };
__attribute__((used)) const uint64_t verified_root_policy_count = 0;
__attribute__((used)) const uint8_t verified_root_key[VR_KEY_BYTES] = {};

__attribute__((used)) const struct vr_crypto vr_xnu_crypto = { refuseHash, refuseSignature };

int vr_open (const uint8_t *, size_t, const uint8_t[VR_KEY_BYTES], uint64_t,
             const struct vr_crypto *, struct vr_view *)
{
  return VR_FORMAT;
}

int vr_verify_block (const struct vr_view *, uint64_t, const void *, size_t,
                     const struct vr_crypto *)
{
  return VR_BLOCK;
}

}  // extern "C"
'''

source = OUT / 'VerifiedRootStub.cpp'
source.write_text(SOURCE_TEXT)
destination = OUT / 'VerifiedRootStub.o'

arguments = record['arguments']
command = []
index = 0
while index < len(arguments):
    value = arguments[index]
    if value == record['file']:
        command.append(str(source))
    elif value == '-o':
        command.extend(['-o', str(destination)])
        index += 1
    elif value == '-Xclang' and arguments[index + 1].startswith('-W'):
        index += 1
    elif not value.startswith('-W'):
        command.append(value)
    index += 1

command.extend(['-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unused-const-variable',
                '-I', str(ROOT / 'kits/VerifiedRoot/include'),
                '-I', str(ROOT / 'drivers/NativeVirtioBlock')])

result = subprocess.run(command, cwd=record['directory'], capture_output=True, text=True)
(OUT / 'compile.log').write_text(result.stdout + result.stderr)
if result.returncode:
    raise SystemExit((result.stdout + result.stderr)[-3500:])

data = destination.read_bytes()
magic, cpu, subtype, kind, count = struct.unpack_from('<5I', data, 0)
if (magic, cpu, subtype, kind) != (0xfeedfacf, 0x100000c, 0, 1):
    raise SystemExit('Stub requires ordinary ARM64 output')

offset, deployment = 32, None
for _ in range(count):
    cmd, length = struct.unpack_from('<II', data, offset)
    if cmd == 0x32:
        deployment = struct.unpack_from('<II', data, offset + 8)
    offset += length
if deployment != (2, 0x110000):
    raise SystemExit('Stub requires the iOS 17 deployment target')

# Every symbol NativeUfs.cpp asks for must be present *and externally visible*.
# N_TYPE == N_SECT (0x0e) is not enough: a local definition satisfies that, and a
# local definition cannot resolve the kernel's reference. N_EXT (0x01) is what
# makes it a symbol the link can use, so the mask below demands 0x0f exactly.
needed = {'_vr_open', '_vr_verify_block', '_vr_xnu_crypto', '_verified_root_key',
          '_verified_root_policies', '_verified_root_policy_count'}
offset, found = 32, set()
for _ in range(count):
    cmd, length = struct.unpack_from('<II', data, offset)
    if cmd == 2:
        symoff, nsyms, stroff, _ = struct.unpack_from('<4I', data, offset + 8)
        for i in range(nsyms):
            ix, kind, _, _, _ = struct.unpack_from('<IBBHQ', data, symoff + i * 16)
            if kind & 0xff != 0x0f:
                continue
            found.add(data[stroff + ix:data.index(b'\0', stroff + ix)].decode())
    offset += length
missing = needed - found
if missing:
    raise SystemExit('Stub is missing externally visible %s' % ', '.join(sorted(missing)))

archive = OUT / 'libVerifiedRootStub.a'
archive.unlink(missing_ok=True)
subprocess.run(['xcrun', 'ar', 'rcs', archive, destination], check=True)

print('VerifiedRootStub: refusing policy provider linked as a placeholder '
      '(policy_count=0, no block device can publish)')
print('VerifiedRootStub: %s exports %d external symbols; %s' %
      (destination, len(found), archive.name))

# Both symbols the kernel link reported missing are referenced by NativeUfs.o, so the surest place for their
# definitions is the archive that object already lives in: an archive member is pulled to satisfy a reference
# from a sibling member. Two earlier attempts failed for reasons outside this file -- -Wl,-force_load of the
# standalone archive, and the bare object path on LD_KERNEL_ARCHIVES -- and the compile record, the object and
# its symbol table were all verified correct each time, so this stops depending on how the build script's
# extra link inputs reach the linker at all.
UFS_ARCHIVE = ROOT / 'build/native-ufs/libNativeUfs.a'
if not UFS_ARCHIVE.is_file():
    raise SystemExit('build/native-ufs/libNativeUfs.a not built yet; build-verified-root-stub.py must run '
                     'after "build-arm-driver.py NativeUfs" in the board archives list')
subprocess.run(['xcrun', 'ar', 'rs', str(UFS_ARCHIVE), str(destination)], check=True)

# Verify the merged archive, not just the object: that is what the kernel link reads.
# nm prints a defined symbol as "0000000000000000 D _name" (three fields) and an
# undefined one as "                 U _name" (two fields), so a two-field-only
# parser reads every definition as absent.
global_symbols = subprocess.run(['xcrun', 'nm', '-g', str(UFS_ARCHIVE)],
                                capture_output=True, text=True, check=True).stdout
merged = {}
for line in global_symbols.splitlines():
    parts = line.split()
    if len(parts) >= 3 and parts[-2] != 'U':
        merged[parts[-1]] = parts[-2]
absent = needed - set(merged)
if absent:
    raise SystemExit('libNativeUfs.a still lacks %s' % ', '.join(sorted(absent)))
print('VerifiedRootStub: merged into libNativeUfs.a; read back as defined: %s' %
      ' '.join('%s(%s)' % (name.lstrip('_'), kind) for name, kind in sorted(merged.items())
               if name in needed))
