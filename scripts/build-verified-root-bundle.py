#!/usr/bin/env python3
"""Link the real VerifiedRoot kit into the kernel, with owner-signed policies.

`NativeUfs` refuses to publish any root window unless each one authenticates
against an owner-signed envelope (`NativeUfsBlock::configureVerification`), so a
kernel built with `build-verified-root-stub.py` - which supplies
`verified_root_policy_count = 0` - can talk to the UFS but can never hand a block
device to APFS. This archive is the real thing:

    kits/VerifiedRoot/src/verified_root.c        vr_open, vr_verify_block
    kits/VerifiedRoot/adapters/xnu_crypto.c      SHA-256 + RSA-3072 from libkern
    verified-root-policies.S                     the two envelopes + anchor + table

Both envelopes are produced from the exact window bytes that go into the
`userdata` image (kits/VerifiedRoot/tools/manifest.py signs the ordered SHA-256 of
every 4096-byte block and the APFS volume UUID), so a read that verifies here is a
read of the original image.

Divergence from the upstream build (deliberate)
-----------------------------------------------
* The envelopes are built ahead of time, on a machine with the window images,
  instead of by `scripts/build-owner-os-policy.py`. That script and
  `scripts/build-verified-root-probe.py` gate on `build/verified-root-{os,
  systemos}/verification.json`, whose producers (the macOS host dylib and the
  SystemOS policy step) are not part of this fork, so they cannot run here. The
  envelope format and the signing are identical because both go through
  kits/VerifiedRoot/tools/manifest.py.
* The objects are compiled with the command line XNU used for its own bsd_init.o,
  so the kit sees exactly the headers and flags the rest of the kernel does.
"""
import hashlib
import json
import os
import struct
from pathlib import Path
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
OUT = ROOT / 'build/verified-root-bundle'
RECIPE = ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT/bsd_init.o.json'
KIT = ROOT / 'kits/VerifiedRoot'

# Both envelopes and the anchor sit in one directory. make-owner-policy.py writes it
# next to the image it signed, gh-release-upload.py publishes it as a release, and
# this script fetches that release when the directory is absent - which is the CI
# case, where the source tree (and therefore this path) only exists by the time the
# archives are built. Order fixes the policy table order; NativeUfs picks a policy by
# matching its signed image size against the window size.
INPUTS = ROOT / 'build/verified-root-inputs'
POLICIES = [('OS', INPUTS / 'OS' / 'manifest.bin'), ('SystemOS', INPUTS / 'SystemOS' / 'manifest.bin')]
ANCHOR = INPUTS / 'trusted-key.bin'
WINDOWS = {'OS': 6077580800, 'SystemOS': 3303014400}
# "owner/repository@tag"; set by VERIFIED_ROOT_POLICY_RELEASE, which the CI pre-build
# step writes into $GITHUB_ENV. Only used to fill a missing INPUTS directory.
DEFAULT_RELEASE = '2713385115/UniXNU-nx729j@owner-policy-1'
ASSETS = {'os-manifest.bin': 'OS/manifest.bin',
          'systemos-manifest.bin': 'SystemOS/manifest.bin',
          'trusted-key.bin': 'trusted-key.bin'}


def fail(message):
    raise SystemExit('build-verified-root-bundle: ' + message)


def fetch_policy():
    """Download the released envelopes into INPUTS, when they are not already there."""
    spec = os.environ.get('VERIFIED_ROOT_POLICY_RELEASE', DEFAULT_RELEASE)
    repo, _, tag = spec.partition('@')
    token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    if not token:
        fail('build/verified-root-inputs is empty and GITHUB_TOKEN is unset, so the owner policy '
             f'at {spec} cannot be downloaded')

    def api(url, accept='application/vnd.github+json'):
        request = urllib.request.Request(url)
        request.add_header('Authorization', f'Bearer {token}')
        request.add_header('Accept', accept)
        request.add_header('User-Agent', 'unixnu-ci')
        return urllib.request.urlopen(request, timeout=120)

    release = json.loads(api(f'https://api.github.com/repos/{repo}/releases/tags/{tag}').read())
    assets = {a['name']: a['id'] for a in release['assets']}
    for name, relative in ASSETS.items():
        if name not in assets:
            fail(f'{spec} has no asset {name}')
        destination = INPUTS / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with api(f'https://api.github.com/repos/{repo}/releases/assets/{assets[name]}',
                 'application/octet-stream') as response, destination.open('wb') as out:
            while chunk := response.read(1 << 20):
                out.write(chunk)
        print(f'VerifiedRootBundle: {name} -> {relative} ({destination.stat().st_size} B)')



def check(destination: Path) -> tuple[bytes, int]:
    """Reject an envelope that does not describe the window it claims to."""
    data = destination.read_bytes()
    if len(data) < 544 or data[0:8] != b'VROOT01\0':
        fail(f'{destination.name}: not a VROOT01 envelope')
    version, header_bytes, block_bytes, flags = struct.unpack_from('<4I', data, 8)
    image_bytes, count = struct.unpack_from('<2Q', data, 24)
    generation, = struct.unpack_from('<Q', data, 152)
    if (version, header_bytes, block_bytes, flags) != (1, 160, 4096, 0) or generation < 1:
        fail(f'{destination.name}: unexpected header {version}/{header_bytes}/{block_bytes}/{flags}/{generation}')
    if count != (image_bytes + 4095) // 4096 or len(data) != 544 + count * 32:
        fail(f'{destination.name}: {len(data)} bytes do not match {count} block hashes')
    return data, image_bytes


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    if not ANCHOR.is_file() or not all(path.is_file() for _, path in POLICIES):
        fetch_policy()
    if not RECIPE.is_file():
        fail(f'missing {RECIPE}; the compile database has not been produced yet')
    record = json.loads(RECIPE.read_text().strip().rstrip(','))

    anchor = ANCHOR.read_bytes()
    if len(anchor) != 395 or anchor[0:8] != b'VROOTKEY':
        fail(f'{ANCHOR.name}: expected a 395-byte VROOTKEY anchor')
    anchor_digest = hashlib.sha256(anchor).digest()

    envelopes, records = [], []
    for name, path in POLICIES:
        if not path.is_file():
            fail(f'missing {path}; the envelope has to be generated from the window image first')
        data, image_bytes = check(path)
        if WINDOWS[name] != image_bytes:
            fail(f'{name}: envelope signs {image_bytes} bytes, the window is {WINDOWS[name]}')
        if data[120:152] != anchor_digest:
            fail(f'{name}: envelope was signed under a different anchor than {ANCHOR.name}')
        envelopes.append((name, path, data))
        records.append((name, image_bytes))

    objects = []
    for source in (KIT / 'src/verified_root.c', KIT / 'adapters/xnu_crypto.c'):
        if not source.is_file():
            fail(f'missing {source}')
        destination = OUT / (source.stem + '.o')
        command, index = [], 0
        while index < len(record['arguments']):
            value = record['arguments'][index]
            if value == record['file']:
                command.append(str(source))
            elif value == '-o':
                command += ['-o', str(destination)]
                index += 1
            elif value == '-Xclang' and index + 1 < len(record['arguments']) \
                    and record['arguments'][index + 1].startswith('-W'):
                index += 1
            elif not value.startswith('-W'):
                command.append(value)
            index += 1
        command.extend(['-Wno-error', '-I', str(KIT / 'include')])
        (OUT / (source.stem + '.compile-command.json')).write_text(json.dumps(command, indent=2) + '\n')
        result = subprocess.run(command, cwd=record['directory'], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        (OUT / (source.stem + '.compile.log')).write_text(result.stdout or '')
        if result.returncode:
            fail(f'{source.name} did not compile:\n' + (result.stdout or '')[-3000:])
        objects.append(destination)

    # The envelopes ARE embedded. Two layouts have been tried on this board and only
    # this one boots:
    #
    #   embedded (this one)   125,165,568 B kernel, __DATA_CONST 0x46c0000 (74 MiB)
    #                         -> prints from entry, reaches kext loading / phase 16
    #   offsets into userdata  51,879,936 B kernel, __DATA_CONST 0xdc000 (0.9 MiB)
    #                         -> silent from entry, never printed a single line
    #
    # The offset layout was an attempt to keep the boot image small. It has failed on
    # every kernel it was applied to (mrk25, mrk25p, mrk44), so it is not used. The
    # matching reader is the pointer form of `NativeUfsBlock::configureVerification`,
    # which dereferences `policy.manifest` directly - the table below therefore has to
    # carry real kernel VAs, not byte offsets, or the driver reads address 0x22f400000.
    #
    # The trade-off is real: at 125 MiB the kernel leaves the loader with less room for
    # the payload, and one earlier build stalled right after entry for that reason. That
    # is still strictly better than a kernel that never speaks at all, and the previous
    # stalls were fixed by changing the payload, not by shrinking the kernel.
    assembly = OUT / 'verified-root-policies.S'
    text = ['// Generated inputs: the two owner-signed envelopes and the anchor key are',
            '// linked into the kernel; the policy table records their addresses and',
            '// lengths. Never edited by hand.',
            '.section __DATA_CONST,__const']
    text += ['.balign 8', '.globl _verified_root_key', '_verified_root_key:',
             f'.incbin "{ANCHOR.as_posix()}"']
    for name, path, data in envelopes:
        stem = name.lower()
        text += ['.balign 4096',
                 f'.globl _verified_root_{stem}_manifest',
                 f'_verified_root_{stem}_manifest:',
                 f'.incbin "{path.as_posix()}"',
                 '.balign 8',
                 f'.globl _verified_root_{stem}_manifest_bytes',
                 f'_verified_root_{stem}_manifest_bytes:',
                 f'.quad {len(data)}']
    text += ['.balign 8', '.globl _verified_root_policies, _verified_root_policy_count',
             '_verified_root_policies:']
    for name, _, data in envelopes:
        text.append(f'.quad _verified_root_{name.lower()}_manifest, {len(data)}')
    text += ['_verified_root_policy_count:', f'.quad {len(envelopes)}']
    assembly.write_text('\n'.join(text) + '\n')

    destination = OUT / 'verified-root-policies.o'
    # The assembly has to carry the same platform as the C objects, or ld refuses it with
    # "building for 'iOS', but linking in object file built for 'macOS'". Only -target does
    # that: the record also carries "-arch arm64", and passing that as well resets the
    # target back to the host, which is macOS.
    assemble = [record['arguments'][0]]
    if '-target' in record['arguments']:
        at = record['arguments'].index('-target')
        assemble += ['-target', record['arguments'][at + 1]]
    else:
        assemble += ['-target', 'arm64-apple-ios17.0']
    if '-isysroot' in record['arguments']:
        at = record['arguments'].index('-isysroot')
        assemble += ['-isysroot', record['arguments'][at + 1]]
    assemble += ['-c', str(assembly), '-o', str(destination)]
    result = subprocess.run(assemble, cwd=record['directory'], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    (OUT / 'assemble.log').write_text(result.stdout or '')
    if result.returncode:
        fail('the policy assembly did not assemble:\n' + (result.stdout or '')[-3000:])
    objects.append(destination)

    archive = OUT / 'libVerifiedRootBundle.a'
    archive.unlink(missing_ok=True)
    subprocess.run(['xcrun', 'ar', 'rcs', str(archive)] + [str(o) for o in objects], check=True)

    needed = {'_vr_open', '_vr_verify_block', '_vr_xnu_crypto', '_verified_root_key',
              '_verified_root_policies', '_verified_root_policy_count'}
    symbols = subprocess.run(['xcrun', 'nm', '-g', str(archive)], capture_output=True, text=True, check=True).stdout
    defined = {line.split()[-1] for line in symbols.splitlines() if len(line.split()) >= 3 and line.split()[-2] != 'U'}
    missing = needed - defined
    if missing:
        fail('the archive lacks ' + ', '.join(sorted(missing)))

    # The definitions belong in the archive NativeUfs.o already lives in: an archive
    # member is pulled in to satisfy a reference from a sibling member, whereas a
    # separate archive on the kernel link line was tried twice and produced undefined
    # symbols both times (see build-verified-root-stub.py, which does the same merge).
    ufs = ROOT / 'build/native-ufs/libNativeUfs.a'
    if not ufs.is_file():
        fail('build/native-ufs/libNativeUfs.a is not built yet; this archive entry has to come after '
             '"build-arm-driver.py NativeUfs" in the board list')
    subprocess.run(['xcrun', 'ar', 'rs', str(ufs)] + [str(o) for o in objects], check=True)
    merged_symbols = subprocess.run(['xcrun', 'nm', '-g', str(ufs)], capture_output=True, text=True, check=True).stdout
    merged = {line.split()[-1] for line in merged_symbols.splitlines()
              if len(line.split()) >= 3 and line.split()[-2] != 'U'}
    absent = needed - merged
    if absent:
        fail('libNativeUfs.a still lacks ' + ', '.join(sorted(absent)))

    print('VerifiedRootBundle: %d policies (%s) under anchor %s' %
          (len(envelopes), ', '.join('%s %d B' % (n, b) for n, b in records),
           hashlib.sha256(anchor).hexdigest()[:16]))
    print('VerifiedRootBundle: %s (%d bytes), merged into %s' %
          (archive.name, archive.stat().st_size, ufs.relative_to(ROOT)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
