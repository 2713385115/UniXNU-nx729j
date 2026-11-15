#!/usr/bin/env python3
"""Build the opt-in board diagnostic against this source kernel's actual ABI.

NX729J port note: upstream keeps this source in ``kits/<kit>/``, and this board's
``board.toml`` declares ``kit = "nx729j"`` so the repository's ``package/`` shows up
as ``kits/nx729j`` in the build tree.  The published UniXNU tree ships no
``kits/sheng``, so resolve the kit directory instead of hard-coding one name, and
carry ``Font.h`` inside it rather than reaching into ``uefi-sheng``.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build/boot-progress'
OUT.mkdir(parents=True, exist_ok=True)


def kit_dir(root: Path) -> Path:
    candidates = [os.environ.get('UNIXNU_KIT_DIR'), root / 'kits/nx729j', root / 'package']
    for candidate in candidates:
        if not candidate:
            continue
        directory = Path(candidate)
        if (directory / 'BootProgress.c').is_file():
            return directory
    raise SystemExit('BootProgress.c not found: looked in $UNIXNU_KIT_DIR, kits/nx729j, package/')


KIT = kit_dir(ROOT)
record = json.loads((ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/osfmk/DEVELOPMENT/startup.o.json').read_text().strip().rstrip(','))
source = KIT / 'BootProgress.c'
obj = OUT / 'BootProgress.o'
command = []
args = record['arguments']
i = 0
while i < len(args):
    arg = args[i]
    if arg == record['file']:
        command.append(str(source))
    elif arg in ('-o', '-MF', '-MT', '-MJ'):
        if arg == '-o':
            command += ['-o', str(obj)]
        i += 1
    elif arg == '-Xclang' and args[i+1].startswith('-W'):
        i += 1
    elif arg == '-D' and args[i+1].startswith('XNU_BOOT_PROGRESS='):
        i += 1
    elif not arg.startswith(('-W', '-DXNU_BOOT_PROGRESS=')):
        command.append(arg)
    i += 1
command += ['-DXNU_BOOT_PROGRESS=1', '-mgeneral-regs-only', '-Wall', '-Wextra', '-Werror',
            '-I', str(ROOT / '.cache/xnu-work/osfmk'),
            '-I', str(KIT)]
subprocess.run(command, cwd=record['directory'], check=True, timeout=90)
(OUT / 'compile-command.json').write_text(json.dumps(command,indent=2)+'\n')
fault_source = KIT / 'BootFaultProbe.c'
fault_obj = OUT / 'BootFaultProbe.o'
fault_command = [str(fault_source) if arg == str(source) else str(fault_obj) if arg == str(obj) else arg for arg in command]
subprocess.run(fault_command, cwd=record['directory'], check=True, timeout=90)
policy_source = KIT / 'BootWatchdogPolicy.c'
policy_obj = OUT / 'BootWatchdogPolicy.o'
policy_record = json.loads((ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT/kern_exec.o.json').read_text().strip().rstrip(','))
policy_command = []
policy_args = policy_record['arguments']
j = 0
while j < len(policy_args):
    arg = policy_args[j]
    if arg == policy_record['file']:
        policy_command.append(str(policy_source))
    elif arg in ('-o', '-MF', '-MT', '-MJ'):
        if arg == '-o': policy_command += ['-o', str(policy_obj)]
        j += 1
    elif arg == '-Xclang' and policy_args[j+1].startswith('-W'):
        j += 1
    elif not arg.startswith('-W'):
        policy_command.append(arg)
    j += 1
policy_command += ['-mgeneral-regs-only', '-Wall', '-Wextra', '-Werror']
subprocess.run(policy_command, cwd=policy_record['directory'], check=True, timeout=90)

archive = OUT / 'libBootProgress.a'
archive.unlink(missing_ok=True)
subprocess.run(['xcrun', 'ar', 'rcs', str(archive), str(obj), str(fault_obj), str(policy_obj)], check=True, timeout=30)
(OUT / 'build-report.json').write_text(json.dumps({
    'kit_dir': str(KIT.relative_to(ROOT)) if KIT.is_relative_to(ROOT) else str(KIT),
    'watchdog_policy_sha256': hashlib.sha256(policy_source.read_bytes()).hexdigest(),
    'fault_sha256': hashlib.sha256(fault_source.read_bytes()).hexdigest(),
    'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
    'lifecycle_sha256': hashlib.sha256((source.parent / 'BootProgressLifecycle.h').read_bytes()).hexdigest(),
    'core_sha256': hashlib.sha256((source.parent / 'BootProgressCore.h').read_bytes()).hexdigest(),
    'display_sha256': hashlib.sha256((source.parent / 'BootProgressDisplay.h').read_bytes()).hexdigest(),
    'pulse_sha256': hashlib.sha256((source.parent / 'BootProgressPulse.h').read_bytes()).hexdigest(),
    'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
    'hardware_execution': False,
}, indent=2) + '\n')
print('PASS: strict source-kernel ARM64 boot diagnostic build (kit=%s)' % KIT)
