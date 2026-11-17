#!/usr/bin/env python3
"""Verify over 9008/EDL that boot_a really contains the image we flashed.

boot_a lives on LUN 4 at sector 114054 (see tools/edl-flash-boot.py).  fh_loader
writes with readbackverify="false", so before mrk294 nobody had ever confirmed
that the device boots the partition we think we patched.  This tool reads the
whole partition back and compares it block-by-block with the local image.

Usage:
    python tools/edl-verify-boot.py --port 8 --image boot-mrk294.img
"""
import argparse
import hashlib
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SECTOR = 4096
BOOT_LUN = 4
BOOT_START = 114054


def load_reader():
    spec = importlib.util.spec_from_file_location(
        'edl_read_log', ROOT / 'tools' / 'edl-read-log.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8)
    ap.add_argument('--image', required=True)
    ap.add_argument('--no-loader', action='store_true')
    a = ap.parse_args()

    image = ROOT / a.image
    if not image.is_file():
        sys.exit('image not found: %s' % image)
    src = image.read_bytes()
    blocks = (len(src) + SECTOR - 1) // SECTOR

    m = load_reader()
    work = (ROOT / 'work' / 'edl-log').resolve()
    work.mkdir(parents=True, exist_ok=True)
    name = 'boot-readback.bin'
    (work / name).unlink(missing_ok=True)

    if not a.no_loader:
        ok = m.load_loader(a.port)
        print('[1] firehose loader: %s' % ('OK' if ok else 'FAILED or already loaded'))

    m.USERDATA_LUN = BOOT_LUN
    print('[2] read %d blocks @ LUN%d LBA %d (%.1f MiB)'
          % (blocks, BOOT_LUN, BOOT_START, blocks * SECTOR / 1048576))
    out = m.read_blocks(a.port, work, BOOT_START, blocks, name=name)
    if out is None:
        sys.exit('READ FAILED - check that the device is in firehose mode')

    b = out.read_bytes()
    n = min(len(b), len(src))
    bad = [i for i in range(0, n, SECTOR) if b[i:i + SECTOR] != src[i:i + SECTOR]]
    print('[3] readback %d B, image %d B' % (len(b), len(src)))
    print('    mismatched 4096-byte blocks: %d %s'
          % (len(bad), [hex(i) for i in bad[:8]] if bad else ''))
    print('    sha256 readback %s' % hashlib.sha256(b[:n]).hexdigest()[:24])
    print('    sha256 image    %s' % hashlib.sha256(src[:n]).hexdigest()[:24])
    if len(b) < len(src) or bad:
        print('    VERDICT: MISMATCH - the flashed boot_a is NOT this image')
        return 1
    print('    VERDICT: IDENTICAL - boot_a really holds this image')
    return 0


if __name__ == '__main__':
    sys.exit(main())
