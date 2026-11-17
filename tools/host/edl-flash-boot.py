"""Flash a boot image to boot_a over EDL, no fastboot and no fastbootD involved.

boot_a lives on LUN 4 at sector 114054 with a 24576-sector window. The UniXNU images are
shorter than the window, so only the image's own sectors are written; the boot image header
describes the payload, so the stale tail is never read.

Usage:
    python tools/edl-flash-boot.py boot-root-mrk24.img
    python tools/edl-flash-boot.py --stock
"""
import argparse
from datetime import datetime
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLBOX = Path(r'D:\刷机\中兴家族工具箱1.2.4-beta3\bin\tool\Win')
LOADER = Path(r'D:\刷机\10.0.11_A分区\firehose.elf')
STOCK_BOOT = Path(r'D:\刷机\10.0.11_A分区\boot.img')
SECTOR = 4096
BOOT_LUN = 4
BOOT_START = 114054
BOOT_WINDOW = 24576


def run(cmd, cwd=None, timeout=1800):
    print('$ ' + ' '.join(str(c) for c in cmd))
    p = subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None,
                       capture_output=True, text=True, errors='replace', timeout=timeout)
    blob = (p.stdout or '') + (p.stderr or '')
    for line in blob.strip().splitlines()[-8:]:
        print('  ' + line)
    return p, blob


def detect_port():
    """自动找当前 Qualcomm 9008 的 COM 口。

    以前这里硬编码 COM6，设备重新插拔后枚举成 COM8 就会
    "Failed to open com port" 直接刷失败。改成每次现查。
    """
    try:
        out = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             "Get-PnpDevice -PresentOnly | "
             "Where-Object { $_.FriendlyName -match '9008' } | "
             "ForEach-Object { $_.FriendlyName }"],
            capture_output=True, text=True, timeout=20,
        ).stdout
    except Exception:
        out = ''
    i = out.find('(COM')
    if i >= 0:
        j = out.find(')', i)
        if j > i:
            try:
                return int(out[i + 4:j])
            except ValueError:
                pass
    return 6


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('image', nargs='?', type=Path)
    ap.add_argument('--stock', action='store_true')
    ap.add_argument('--port', type=int, default=None,
                    help='EDL 串口号（默认自动检测 9008 的 COM 口）')
    ap.add_argument('--no-loader', action='store_true')
    ap.add_argument('--no-reset', action='store_true')
    ap.add_argument('--work-dir', type=Path,
                    help='本次刷写的独立事务目录（默认创建唯一目录）')
    args = ap.parse_args()

    if args.port is None:
        args.port = detect_port()
        print(f'  EDL 串口自动检测: COM{args.port}')

    image = STOCK_BOOT if args.stock else args.image
    if image is None:
        raise SystemExit('give an image, or --stock')
    if not image.is_absolute():
        image = ROOT / image
    if not image.exists():
        raise SystemExit(f'not found: {image}')

    size = image.stat().st_size
    if size % SECTOR:
        raise SystemExit(f'{image.name} is {size} bytes, not a multiple of {SECTOR}')
    sectors = size // SECTOR
    if sectors > BOOT_WINDOW:
        raise SystemExit(f'{image.name} needs {sectors} sectors, window is only {BOOT_WINDOW}')

    print(f'{image.name}: {size} bytes = {sectors} sectors -> LUN{BOOT_LUN} boot_a @ {BOOT_START}')
    print(f'  (partition window {BOOT_WINDOW} sectors, {BOOT_WINDOW - sectors} left untouched)')

    work = args.work_dir or ROOT / 'work' / 'edl-boot' / (
        f'{image.stem}-{datetime.now().strftime("%Y%m%d-%H%M%S-%f")}')
    work.mkdir(parents=True, exist_ok=True)

    if not args.no_loader:
        print('[1] QSaharaServer -> firehose.elf')
        p, blob = run([TOOLBOX / 'QSaharaServer.exe', '-u', str(args.port), '-s', f'13:{LOADER}'], cwd=TOOLBOX)
        if p.returncode and 'FIREHOSE MODE DETECTED' in blob:
            print('  Firehose is already active; continuing without Sahara upload')
        elif p.returncode:
            print('  !! Sahara loader upload failed')
            return p.returncode

    xml = work / f'program-{image.stem}-{datetime.now().strftime("%Y%m%d-%H%M%S-%f")}.xml'
    xml.write_text(
        '<?xml version="1.0" ?>\n<data>\n'
        f'  <program SECTOR_SIZE_IN_BYTES="{SECTOR}" file_sector_offset="0" filename="{image.name}" '
        f'label="boot_a" num_partition_sectors="{sectors}" partofsingleimage="false" '
        f'physical_partition_number="{BOOT_LUN}" readbackverify="false" '
        f'size_in_KB="{sectors * SECTOR / 1024:.1f}" sparse="false" '
        f'start_byte_hex="{BOOT_START * SECTOR:#x}" start_sector="{BOOT_START}"/>\n</data>\n',
        encoding='ascii')

    print('[2] write')
    p, blob = run([TOOLBOX / 'fh_loader.exe', f'--port=\\\\.\\COM{args.port}', f'--sendxml={xml}',
                   '--noprompt', '--zlpawarehost=1', '--memoryname=ufs',
                   f'--mainoutputdir={work}', f'--search_path={image.parent}\\'], cwd=work)
    if 'All Finished Successfully' not in blob:
        print('  !! fh_loader did not report success')
        return 1

    if args.no_reset:
        print('[3] reset skipped')
        return 0
    print('[3] reset')
    run([TOOLBOX / 'fh_loader.exe', f'--port=\\\\.\\COM{args.port}', '--power=reset,1',
         '--noprompt'], cwd=TOOLBOX)
    print('done: the device is booting the image just written')
    return 0


if __name__ == '__main__':
    sys.exit(main())
