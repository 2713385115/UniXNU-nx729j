#!/usr/bin/env python3
"""Decode the SHLOG window that package/BootLog.c keeps at the tail of userdata.

The layout is the one in package/BootLog.c (48-52 and the struct at 104-118):

  block 0            header (magic "SHLOG001", written last in every round)
  block 1..32        the kernel message buffer, oldest byte first
  block 33..         text slots of 8 blocks in a ring, newest at slots_written % slot_count

Usage:
  python tools/decode-shlog.py work/peek/window.bin [--slots 3]

The dump must start at the first block of the window (absolute LBA first_lba), i.e.

  python tools/edl-read-range.py --port 8 --lun 0 --lba 0x741FFFB --blocks 8192 \
      --out work/peek/window.bin
"""
import argparse
import struct
import sys

BLOCK = 4096
HEADER = struct.Struct('<8sIIQQ8I6I32s5I')
FIELDS = ('magic', 'version', 'header_bytes', 'boot_id', 'first_lba',
          'window_blocks', 'msg_first', 'msg_blocks', 'msg_bytes',
          'slot_first', 'slot_blocks', 'slot_count', 'slots_written',
          'uptime_s', 'rounds', 'write_errors', 'gpt_open', 'gpt_last', 'state',
          'tag', 'export_first', 'export_blocks', 'export_bytes',
          'export_generation', 'export_below')
STATES = {0: 'off', 1: 'waiting for the window (no root layout yet)',
          2: 'writing', 3: 'stopped after write errors', 4: 'stopped (GPT changed)'}


def text(raw):
    """The message buffer and the slots are NUL-padded character data."""
    return raw.split(b'\0', 1)[0].decode('utf-8', 'replace')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dump')
    ap.add_argument('--slots', type=int, default=3, help='how many text slots to print, newest first')
    ap.add_argument('--msg', action='store_true', help='print the whole message buffer')
    args = ap.parse_args()

    data = open(args.dump, 'rb').read()
    if len(data) < BLOCK:
        sys.exit('dump is shorter than one block')
    head = dict(zip(FIELDS, HEADER.unpack_from(data, 0)))
    if head['magic'] != b'SHLOG001':
        print('no SHLOG001 header: magic =', head['magic'])
        nz = sum(1 for b in data[:BLOCK] if b)
        print('nonzero bytes in block 0:', nz, 'of', BLOCK)
        if len(data) > BLOCK and all(not b for b in data):
            print('the whole dump is zero: the writer never opened the window')
        print('(the window is only written once native_ufs_log_open succeeds: gStoreGeometry.valid && gHost.ready)')
        return 2
    for key in FIELDS:
        value = head[key]
        if key == 'magic':
            value = value.decode()
        elif key == 'tag':
            value = value.split(b'\0', 1)[0].decode('utf-8', 'replace')
        elif key == 'state':
            value = '%d (%s)' % (value, STATES.get(value, '?'))
        print('%-16s %s' % (key, value))

    base = head['msg_first'] * BLOCK
    msg = data[base:base + head['msg_blocks'] * BLOCK]
    print('\n--- message buffer (%d bytes, %d announced) ---' % (len(msg), head['msg_bytes']))
    body = text(msg)
    print(body.rstrip() if args.msg else '\n'.join(body.rstrip().splitlines()[-40:]))

    if head['slot_count']:
        newest = (head['slots_written'] - 1) % head['slot_count'] if head['slots_written'] else 0
        for back in range(min(args.slots, head['slot_count'])):
            index = (newest - back) % head['slot_count']
            start = (head['slot_first'] + index * head['slot_blocks']) * BLOCK
            chunk = data[start:start + head['slot_blocks'] * BLOCK]
            print('\n--- text slot %d (%d back) ---' % (index, back))
            print(text(chunk).rstrip())
    return 0


if __name__ == '__main__':
    sys.exit(main())
