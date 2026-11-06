#!/usr/bin/env python3
"""Build the opt-in board diagnostic against this source kernel's actual ABI."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build/boot-progress'
OUT.mkdir(parents=True, exist_ok=True)


def kit_file(name):
    """Locate a board package source by name.

    In the assembled build tree the device package appears as kits/<board.kit>/
    (sheng -> kits/sheng, nx729j -> kits/nx729j), so a hard-coded kit name breaks
    every board that is not sheng.  In a bare device-repository checkout the same
    sources sit directly in package/.  Search both, kit directory first.
    """
    kits = ROOT / 'kits'
    if kits.is_dir():
        for hit in sorted(kits.glob(f'*/{name}')):
            if hit.is_file():
                return hit
    direct = ROOT / 'package' / name
    if direct.is_file():
        return direct
    raise SystemExit(f'{name}: not found under kits/*/ or package/')


record = json.loads((ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/osfmk/DEVELOPMENT/startup.o.json').read_text().strip().rstrip(','))
source = kit_file('BootProgress.c')
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
            '-I', str(ROOT / 'uefi-sheng/Silicon/Silicium/SiliciumPkg/Library/FrameBufferSerialPortLib')]
subprocess.run(command, cwd=record['directory'], check=True, timeout=90)
(OUT / 'compile-command.json').write_text(json.dumps(command,indent=2)+'\n')
fault_source = kit_file('BootFaultProbe.c')
fault_obj = OUT / 'BootFaultProbe.o'
fault_command = [str(fault_source) if arg == str(source) else str(fault_obj) if arg == str(obj) else arg for arg in command]
subprocess.run(fault_command, cwd=record['directory'], check=True, timeout=90)
policy_source = kit_file('BootWatchdogPolicy.c')
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

# The persistent diagnostic writer (package/BootLog.c, boot argument sheng-log=1). The board progress
# monitor above starts it with boot_log_tick() once a second, and it is what keeps the 32 MiB SHLOG001
# window at the tail of userdata current - the only way a run on this board is read back without a photo
# (unixnu.com/zh/porting/log-capture, step 4: the kernel writer and its bounded storage calls).
# build-native-legacy-display.py compiles the same file for sheng, but this board withholds that archive
# until an original display candidate exists, so the writer has to be built here. Without it the
# boot_log_tick that BootProgress.c calls stays an unresolved weak import: the kernel then has no writer,
# sheng-log=1 writes nothing, and the log window stays zero no matter what the rest of the boot does.
log_source = kit_file('BootLog.c')
log_obj = OUT / 'BootLog.o'
log_command = [str(log_source) if arg == str(source) else str(log_obj) if arg == str(obj) else arg for arg in command]
# The kit directory carries the board's own headers next to the sources, the same way the display
# archive builder adds it; in a bare device checkout there is nothing there and -I is harmless.
log_command += ['-I', str(log_source.parent)]
subprocess.run(log_command, cwd=record['directory'], check=True, timeout=90)

archive = OUT / 'libBootProgress.a'
archive.unlink(missing_ok=True)
subprocess.run(['xcrun', 'ar', 'rcs', str(archive), str(obj), str(fault_obj), str(policy_obj), str(log_obj)], check=True, timeout=30)
(OUT / 'build-report.json').write_text(json.dumps({
    'watchdog_policy_sha256': hashlib.sha256(policy_source.read_bytes()).hexdigest(),
    'fault_sha256': hashlib.sha256(fault_source.read_bytes()).hexdigest(),
    'log_sha256': hashlib.sha256(log_source.read_bytes()).hexdigest(),
    'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
    'lifecycle_sha256': hashlib.sha256((source.parent / 'BootProgressLifecycle.h').read_bytes()).hexdigest(),
    'core_sha256': hashlib.sha256((source.parent / 'BootProgressCore.h').read_bytes()).hexdigest(),
    'display_sha256': hashlib.sha256((source.parent / 'BootProgressDisplay.h').read_bytes()).hexdigest(),
    'pulse_sha256': hashlib.sha256((source.parent / 'BootProgressPulse.h').read_bytes()).hexdigest(),
    'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
    'hardware_execution': False,
}, indent=2) + '\n')
print('PASS: strict source-kernel ARM64 boot diagnostic build')
