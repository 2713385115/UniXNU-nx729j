#!/usr/bin/env python3
"""Bind a statically linked block driver to the prelinked IOStorageFamily ABI.

Why this exists
---------------
`NativeUfs`, `NativeRamBlock` and `NativeVirtioBlock` derive from
`IOBlockStorageDevice`. That class is not part of XNU: it ships in
`/System/Library/Extensions/IOStorageFamily.kext`, which the kernel image carries
as a prelinked kext (its segments live in `__PRELINK_TEXT`, `__PLK_TEXT_EXEC`,
`__PLK_DATA_CONST` and `__PRELINK_DATA`).

The first attempt compiled Apple's published sources (refer/IOStorageFamily) into
the kernel. That links, but the machine then panics while the kext starts:

    OSMetaClass: Kext com.apple.iokit.IOStorageFamily class IOBlockStorageDevice
    is a duplicate; kext __kernel__ already has a class by that name.

Two classes with one name cannot coexist, and the kext one is the one IOKit needs
because `IOBlockStorageDriver` matches `IOBlockStorageDevice` personalities.

What the tree already does for the other two transports (and what this does for
`NativeUfs`) is what `scripts/build-native-virtio-bindings.py` does: keep the
driver's own subclass, but resolve every inherited entry to the *original*
prelinked code with absolute symbols,

    .globl __ZN20IOBlockStorageDevice7doUnmapE...
    .set   __ZN20IOBlockStorageDevice7doUnmapE..., 0xfffffff00496f074

The addresses come from scripts/prelink-storage-bindings.json, measured once out
of a build by scripts/scan-prelink-storage.py (the prelink closure is copied
verbatim from the original kernelcache, so they do not move between builds).
The slot-to-name direction comes from compiling the same class here: the
reference object's vtable is 190 relocated slots in declaration order, which is
exactly how the original kext's dispatch table is laid out (the scan asserts the
same count and the same abstract-destructor traps).

Divergence from the upstream build (deliberate)
-----------------------------------------------
* Only `IOBlockStorageDevice.cpp` is compiled, and only to read its vtable. The
  object is *not* linked into the kernel any more; linking it is the duplicate.
* The compiled object is still built with the exact command line XNU used for its
  own IOKit sources (`build/xnu/ios-obj/.../IOPlatformExpert.cpo.json`, input file
  swapped), so the vtable order matches what the kernel tools expect.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REFER = ROOT / 'refer/IOStorageFamily'
OUT = ROOT / 'build/io-storage-family'
RECIPE = ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json'
PIN = Path(__file__).with_name('prelink-storage-bindings.json')

CLASS = 'IOBlockStorageDevice'
MANGLED = str(len(CLASS)) + CLASS
VTABLE = '__ZTV' + MANGLED
METACLASS = '__ZN' + MANGLED + '10gMetaClassE'
METACLASS_POINTER = '__ZN' + MANGLED + '9metaClassE'
CONSTRUCTOR = '__ZN' + MANGLED + 'C'          # C1/C2 plus the parameter mangling
DESTRUCTOR = '__ZN' + MANGLED + 'D2Ev'
SLOTS = 190
PURE_VIRTUAL = '___cxa_pure_virtual'

sys.path.insert(0, str(ROOT / 'kits/NativePrelink'))
import native_prelink as np  # noqa: E402


def fail(message):
    raise SystemExit('build-storage-bindings: ' + message)


def compile_reference(destination: Path) -> Path:
    """Compile IOBlockStorageDevice.cpp with the kernel's own flags."""
    if not REFER.is_dir():
        fail(f'missing {REFER}; the build fetches it with "unixnu fetch IOStorageFamily"')
    if not RECIPE.is_file():
        fail(f'missing {RECIPE}; the compile database has not been produced yet')
    record = json.loads(RECIPE.read_text().strip().rstrip(','))
    source = REFER / 'IOBlockStorageDevice.cpp'
    if not source.is_file():
        fail(f'missing {source}')
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
    command.extend(['-Wno-error', '-I', str(ROOT / 'build/storage-abi/include'), '-I', str(REFER)])
    (OUT / 'reference.compile-command.json').write_text(json.dumps(command, indent=2) + '\n')
    result = subprocess.run(command, cwd=record['directory'], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    (OUT / 'reference.compile.log').write_text(result.stdout or '')
    if result.returncode:
        fail('the reference class did not compile:\n' + (result.stdout or '')[-3000:])
    return destination


def vtable_names(object_path: Path) -> list[str]:
    """The name each vtable slot dispatches to, in slot order."""
    obj = object_path.read_bytes()
    image = np.macho(obj)
    sycmd = next(at for command, at, size in image['commands'] if command == 2)
    symoff, count, stroff = struct.unpack_from('<4I', obj, sycmd + 8)[:3]
    symbols = []
    for index in range(count):
        name_at, kind, _, _, value = struct.unpack_from('<IBBHQ', obj, symoff + index * 16)
        symbols.append((obj[stroff + name_at:obj.index(b'\0', stroff + name_at)].decode(),
                        value, kind))
    vtable = next((value for name, value, _ in symbols if name == VTABLE), None)
    if vtable is None:
        fail(f'{object_path} does not define {VTABLE}')
    relocations = {}
    for segment in image['segments']:
        for section in segment['sections']:
            if not section['va'] <= vtable < section['va'] + section['size']:
                continue
            reloff, relcount = struct.unpack_from('<II', obj, section['command_offset'] + 56)
            for i in range(relcount):
                address, word = struct.unpack_from('<II', obj, reloff + i * 8)
                if word >> 27 & 1 and word >> 28 == 0 and word >> 25 & 3 == 3:
                    relocations[section['va'] + address] = symbols[word & 0xFFFFFF][0]
    names = [relocations.get(vtable + 16 + i * 8) for i in range(SLOTS)]
    if not all(names) or vtable + 16 + SLOTS * 8 in relocations:
        fail(f'the reference {CLASS} vtable is not {SLOTS} fully relocated slots')
    if not (names[0].endswith('D1Ev') and names[1].endswith('D0Ev')):
        fail('the reference vtable does not start with the complete/deleting destructors')
    return names


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('sources', nargs='*', help='ignored; kept so older board.toml entries still run')
    args = ap.parse_args()
    del args

    if not PIN.is_file():
        fail(f'missing {PIN}; regenerate it with scripts/scan-prelink-storage.py')
    pin = json.loads(PIN.read_text())
    if pin.get('slot_count') != SLOTS:
        fail(f'the pin records {pin.get("slot_count")} slots, expected {SLOTS}')
    slots = [int(value, 16) for value in pin['slots']]
    text_va, text_size = int(pin['kext_text']['va'], 16), pin['kext_text']['size']
    owned = [index for index, value in enumerate(slots)
             if text_va <= value < text_va + text_size]
    if owned != pin['kext_owned_slots']:
        fail('the pin disagrees with itself about which slots live in the kext')

    OUT.mkdir(parents=True, exist_ok=True)
    reference = compile_reference(OUT / (CLASS + '.reference.o'))
    names = vtable_names(reference)

    bindings = {
        VTABLE: int(pin['vtable'], 16),
        METACLASS: int(pin['metaclass'], 16),
        METACLASS_POINTER: int(pin['metaclass_pointer'], 16),
        DESTRUCTOR: int(pin['base_destructor'], 16),
    }
    # The class is abstract, so the two complete-deleting destructors are traps;
    # a concrete subclass supplies both, and binding them would be a lie.
    for index in owned:
        if index in pin['abstract_destructor_slots']:
            continue
        name = names[index]
        if name == PURE_VIRTUAL:
            continue
        bindings.setdefault(name, slots[index])
    # Both C1 and C2 are bound: which one the driver's constructor calls is the
    # compiler's choice, and in the kext they are the same function.
    for symbol in (CONSTRUCTOR + '1EPK11OSMetaClass', CONSTRUCTOR + '2EPK11OSMetaClass'):
        bindings.setdefault(symbol, int(pin['protected_constructor'], 16))

    assembly = OUT / 'original-bindings.S'
    assembly.write_text(''.join(f'.globl {name}\n.set {name}, {value:#x}\n'
                               for name, value in sorted(bindings.items())))
    obj = OUT / 'original-bindings.o'
    result = subprocess.run(['xcrun', 'clang', '-target', 'arm64-apple-ios17.0',
                             '-Werror', '-Wno-incompatible-sysroot', '-c', str(assembly),
                             '-o', str(obj)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    if result.returncode:
        fail('the binding assembly did not assemble:\n' + (result.stdout or '')[-2000:])

    archive = OUT / 'libStorageBindings.a'
    archive.unlink(missing_ok=True)
    result = subprocess.run(['xcrun', 'ar', 'rcs', str(archive), str(obj)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode:
        fail('xcrun ar failed: ' + (result.stdout or ''))

    listing = subprocess.run(['xcrun', 'nm', '-g', '--defined-only', str(archive)],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    absolute = set()
    for line in listing.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] in ('a', 'A'):
            absolute.add(parts[2])
    missing = sorted(set(bindings) - absolute)
    if missing:
        fail('these aliases are not absolute in the archive: ' + ', '.join(missing[:5]))

    report = dict(class_name=CLASS, pin=pin['kext'], pin_vtable=pin['vtable'],
                  pin_metaclass=pin['metaclass'], pin_kext_text_sha256=pin['kext_text']['sha256'],
                  reference_object_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
                  vtable_slots=SLOTS, kext_owned_slots=owned,
                  bindings={name: hex(value) for name, value in sorted(bindings.items())},
                  archive=str(archive), archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                  original_code_modified=False)
    (OUT / 'bindings.json').write_text(json.dumps(report, indent=2) + '\n')
    print('PASS: %s -> %d absolute bindings over %d kext-owned slots (%s)'
          % (archive.name, len(bindings), len(owned), pin['kext']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
