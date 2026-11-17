"""Read the UniXNU diagnostic log (sheng-log=1) back over EDL -- read only, writes nothing.

NativeUfs opens the last kLogReserveBlocks (8192 x 4096 B = 32 MiB) of userdata as the log
window and, every two seconds, writes:

    block 0        boot_log_header, magic "SHLOG001"
    blocks 1..32   the kernel message buffer (the same bytes the frame buffer shows), oldest first
    blocks 33..40  one text slot

so a run that dies before mountroot can be read back verbatim instead of photographed.

Usage:
    python tools/edl-read-log.py                 # default: compute the window from the GPT size
    python tools/edl-read-log.py --first-lba N   # override when the geometry is in doubt
    python tools/edl-read-log.py --scan          # pull the whole 32 MiB window and locate SHLOG001
"""
import argparse
import subprocess
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLBOX = Path(r'D:\刷机\中兴家族工具箱1.2.4-beta3\bin\tool\Win')
LOADER = Path(r'D:\刷机\10.0.11_A分区\firehose.elf')
SECTOR = 4096
USERDATA_LUN = 0

# userdata geometry (fastboot getvar partition-size:userdata = 0x718BCD3000 bytes)
USERDATA_FIRST = 2712360
USERDATA_BYTES = 0x718BCD3000
KLOG_BLOCKS = 8192          # NativeUfs kLogReserveBlocks

HEADER = struct.Struct('<8sIIQQ8I6I32s4II')
FIELDS = ('magic version header_bytes boot_id first_lba window_blocks msg_first msg_blocks '
          'msg_bytes slot_first slot_blocks slot_count slots_written uptime_s rounds '
          'write_errors gpt_open gpt_last state tag export_first export_blocks export_bytes '
          'export_generation export_below').split()


def run(cmd, cwd=None, timeout=1800):
    print('$ ' + ' '.join(str(c) for c in cmd))
    p = subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None,
                       capture_output=True, text=True, errors='replace', timeout=timeout)
    blob = (p.stdout or '') + (p.stderr or '')
    for line in blob.strip().splitlines()[-6:]:
        print('  ' + line)
    return p, blob


def detect_port():
    """Return the COM number of the 9008 device that is present *now*.

    The 9008 interface is re-enumerated on every power cycle and does not keep
    its COM number, and `Get-PnpDevice` happily reports ports left over from a
    previous session (that is how a read ended up aimed at a dead COM8 while the
    device sat on COM3).  Ask the serial stack instead: it lists only live ports.
    """
    try:
        from serial.tools import list_ports
        for info in list_ports.comports():
            if '9008' in (info.description or '') or '05C6:9008' in (info.hwid or '').upper():
                return int(info.device[3:])
    except Exception:
        pass
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
    return None


def load_loader(port):
    """QSaharaServer, retried once: the first attempt often loses to a stale port owner."""
    for attempt in (1, 2):
        print(f'[1] QSaharaServer (attempt {attempt})')
        p, blob = run([TOOLBOX / 'QSaharaServer.exe', '-u', str(port), '-s', f'13:{LOADER}'], cwd=TOOLBOX)
        if 'Failed' not in blob and p.returncode == 0:
            return True
        print('  !! loader upload failed, retrying')
    return False


def read_blocks(port, work, first_lba, blocks, name='log.bin'):
    xml = work / f'read-{name}.xml'
    xml.write_text(
        '<?xml version="1.0" ?>\n<data>\n'
        f'  <read SECTOR_SIZE_IN_BYTES="{SECTOR}" filename="{name}" label="userdata" '
        f'num_partition_sectors="{blocks}" physical_partition_number="{USERDATA_LUN}" '
        f'start_sector="{first_lba}"/>\n</data>\n',
        encoding='ascii')
    print(f'[2] read {blocks} blocks @ LBA {first_lba} ({blocks * SECTOR / 1024:.0f} KiB)')
    out = work / name
    # A stale pull is worse than no data: this file is left behind by the previous read, so a
    # failed fh_loader would silently hand the decoder an older boot's window and every later
    # conclusion would be built on it. Remove it first and treat any failure as fatal.
    out.unlink(missing_ok=True)
    p, blob = run([TOOLBOX / 'fh_loader.exe', f'--port=\\\\.\\COM{port}', f'--sendxml={xml}',
                   '--noprompt', '--zlpawarehost=1', '--memoryname=ufs',
                   f'--mainoutputdir={work}', f'--search_path={work}\\"'], cwd=work)
    ok = 'All Finished Successfully' in blob
    if not ok:
        print('  !! fh_loader did not report success')
    if not ok or not out.exists():
        print(f'  !! no window pulled ({out.name}); refusing to decode a previous boot')
        return None
    print(f'  -> {out} ({out.stat().st_size} bytes)')
    return out


def parse_header(block):
    h = HEADER.unpack_from(block, 0)
    d = dict(zip(FIELDS, h))
    d['magic'] = d['magic'].split(b'\0')[0].decode('latin1')
    d['tag'] = d['tag'].split(b'\0')[0].decode('latin1')
    # Official version 4/5 words (read-sheng-log.py: DIAG4 at 140, MSG5 at 148).
    if d['version'] >= 5:
        d['msg_mode'], d['msg_resyncs'], lo, hi = struct.unpack_from('<4I', block, 148)
        d['msg_head'] = lo | (hi << 32)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--first-lba', type=int, default=None)
    ap.add_argument('--scan', action='store_true', help='pull the whole 32 MiB window and search for SHLOG001')
    ap.add_argument('--slots', action='store_true',
                    help='also pull the text slots (blocks 33+): they carry the persist-store counters')
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--no-loader', action='store_true')
    args = ap.parse_args()

    port = args.port or detect_port()
    if port is None:
        raise SystemExit('no 9008 device found; enter EDL first')
    print(f'  EDL port: COM{port}')

    last = USERDATA_FIRST + USERDATA_BYTES // SECTOR - 1
    default_first = last + 1 - KLOG_BLOCKS
    first = args.first_lba if args.first_lba is not None else default_first
    print(f'  userdata last LBA {last}; log window first LBA {first}')

    work = ROOT / 'work' / 'edl-log'
    work.mkdir(parents=True, exist_ok=True)

    if not args.no_loader:
        if not load_loader(port):
            return 1

    blocks = KLOG_BLOCKS if args.scan else 33
    blob_path = read_blocks(port, work, first, blocks, name='log.bin' if not args.scan else 'log-window.bin')
    if blob_path is None:
        return 1
    data = blob_path.read_bytes()
    if len(data) < SECTOR:
        print('  !! short read')
        return 1

    if args.scan:
        at = data.find(b'SHLOG001')
        print(f'[3] SHLOG001 at window offset {at if at >= 0 else "NOT FOUND"}')
        if at < 0:
            return 1
        base = at // SECTOR
        data = data[base * SECTOR:]
        print(f'    header is window block {base}; decoding from there')

    h = parse_header(data[:SECTOR])
    print('\n[3] header')
    for k in ('magic', 'version', 'header_bytes', 'boot_id', 'first_lba', 'window_blocks',
              'msg_first', 'msg_blocks', 'msg_bytes', 'slot_count', 'slots_written',
              'uptime_s', 'rounds', 'write_errors', 'gpt_open', 'gpt_last', 'state', 'tag'):
        v = h[k]
        print(f'    {k:16} {hex(v) if isinstance(v, int) and k in ("boot_id", "first_lba") else v}')
    if h.get('msg_mode') is not None:
        print(f'    msg_mode         {h["msg_mode"]}  ({"stream ring" if h["msg_mode"] == 1 else "linear"})')
        print(f'    msg_resyncs      {h["msg_resyncs"]}')
        print(f'    msg_head         {h["msg_head"]}  (stream bytes published)')
    if h['magic'] != 'SHLOG001':
        print('  !! magic mismatch: the log window was never written (or the LBA is wrong)')
        print('     first 64 bytes:', data[:64].hex())
        return 2

    # Fields appended after the original 144-byte header. Absent in older images, where they read as zero.
    try:
        # version 5 inserted DIAG4 (8 B at 140) and MSG5 (16 B at 148) before the nx729j phase
        # extension, so it now starts at 164. phase_mat needs 8-byte alignment and lands at 176.
        # A version 3 header (phase at 140) is decoded as zeros by the official reader instead;
        # use scripts/read-sheng-log.py --decode for old windows.
        if h['version'] >= 5:
            phase, phase_round = struct.unpack_from('<II', data, 164)
            phase_mat, = struct.unpack_from('<Q', data, 176)
            max_round_ms, cmds = struct.unpack_from('<II', data, 184)
            msg_lap, = struct.unpack_from('<Q', data, 192)
            store_writes, = struct.unpack_from('<I', data, 200)
        else:
            phase, phase_round = struct.unpack_from('<II', data, 140)
            phase_mat, = struct.unpack_from('<Q', data, 152)
            max_round_ms, cmds = struct.unpack_from('<II', data, 160)
            msg_lap, = struct.unpack_from('<Q', data, 168)
            store_writes, = struct.unpack_from('<I', data, 176)
    except struct.error:
        phase = phase_round = phase_mat = max_round_ms = cmds = msg_lap = store_writes = 0
    if phase or phase_round or phase_mat or max_round_ms:
        names = {1: 'round start', 2: 'slot written', 3: 'msgbuf copied', 4: 'messages written',
                 5: 'published (round complete)'}
        print(f'    phase            {phase} ({names.get(phase, "?")})')
        print(f'    phase_round      {phase_round}')
        print(f'    phase_mat        {phase_mat}')
        print(f'    max_round_ms     {max_round_ms}')
        print(f'    cmds             {cmds}   (UFS commands the kernel completed by that step)')
        print(f'    msg_lap          {msg_lap}   (msgbuf cursor: moves on any print from any thread)')
        print(f'    store_writes     {store_writes}')
        if phase and phase != 5:
            print(f'  !! the last round ({phase_round}) stopped at phase {phase}: it never published. '
                  f'The writer died inside that step, with mach time {phase_mat}.')

    msg_off = h['msg_first'] * SECTOR
    msg = data[msg_off:msg_off + h['msg_blocks'] * SECTOR]
    text = msg.split(b'\0')[0].decode('latin1', errors='replace')
    if not text.strip():
        text = msg.decode('latin1', errors='replace').replace('\0', '')
    out = work / 'msgbuf.txt'
    out.write_text(text, encoding='utf-8', errors='replace')
    print(f'\n[4] kernel message buffer: {len(text)} chars -> {out}')
    print('-' * 70)
    print(text)
    print('-' * 70)

    # The panic channel (NativeUfs native_ufs_panic_dump): ten blocks ending two blocks below the window's end, each prefixed
    # with "PANICTXT i/n len=L". The panic screen calls it before it parks the core, so this is where a panic message lands
    # even though the round that would have published it never finished.
    path = read_blocks(port, work, first + h['window_blocks'] - 12, 10, name='panic.bin')
    if path is not None:
        raw = path.read_bytes()
        pieces, total = {}, 0
        for i in range(10):
            blk = raw[i * SECTOR:(i + 1) * SECTOR]
            if blk[:8] != b'PANICTXT':
                continue
            nl = blk.find(b'\n')
            head = blk[:nl if nl >= 0 else 64].decode('latin1', errors='replace')
            if 'len=' in head:
                try:
                    total = int(head.split('len=')[1].split()[0])
                except ValueError:
                    total = 0
            pieces[i] = blk[nl + 1:].rstrip(b'\0') if nl >= 0 else b''
        if pieces:
            joined = b''.join(pieces[k] for k in sorted(pieces))
            if total:
                joined = joined[:total]
            txt = joined.decode('latin1', errors='replace')
            print(f'\n[8] PANIC TEXT (NativeUfs native_ufs_panic_dump, {len(pieces)} block(s), len={total})')
            print('-' * 70)
            print(txt)
            print('-' * 70)
            (work / 'panic.txt').write_text(txt, encoding='utf-8', errors='replace')
        else:
            print('\n[8] no panic text (the panic screen never reached the storage channel)')

    if args.slots:
        used = min(h['slots_written'] + 1, h['slot_count'])
        if used <= 0:
            print('[5] no text slot was ever written')
            return 0
        end_block = h['slot_first'] + used * h['slot_blocks']
        path = read_blocks(port, work, first, end_block, name='slots.bin')
        if path is None:
            return 1
        data2 = path.read_bytes()
        for k in range(max(0, used - 4), used):
            base = (h['slot_first'] + k * h['slot_blocks']) * SECTOR
            raw = data2[base:base + h['slot_blocks'] * SECTOR]
            txt = raw.split(b'\0')[0].decode('latin1', errors='replace')
            print(f'\n[5] text slot {k} of {used}')
            print('-' * 70)
            print(txt)
            print('-' * 70)

    # Block first + window_blocks - 1 is where a command that had to be quarantined leaves its own record, written on the
    # failing path itself: the periodic header write it would otherwise use is refused from that moment on. It is read
    # unconditionally, because its absence is also the answer (a stuck doorbell cannot write at all).
    path = read_blocks(port, work, first + h['window_blocks'] - 1, 1, name='failure.bin')
    if path is None:
        return 0
    raw = path.read_bytes()[:SECTOR]
    if raw[:8] == b'SHLOGERR':
        txt = raw.split(b'\0')[0].decode('latin1', errors='replace')
        print('\n[6] QUARANTINE RECORD (last block of the window)')
        print('-' * 70)
        print(txt)
        print('-' * 70)
        (work / 'failure.txt').write_text(txt, encoding='utf-8', errors='replace')
    else:
        print(f'\n[6] no quarantine record (first bytes {raw[:8]!r})')

    # The block before it carries the phase the writer's current round reached, rewritten six times per round, so it names
    # the exact step a stalled round never came back from.
    path = read_blocks(port, work, first + h['window_blocks'] - 2, 1, name='phase.bin')
    if path is None:
        return 0
    raw = path.read_bytes()[:SECTOR]
    if raw[:5] == b'PHASE':
        txt = raw.split(b'\0')[0].decode('latin1', errors='replace')
        print('\n[7] WRITER PHASE (last step of the round that stalled)')
        print('-' * 70)
        print(txt)
        print('-' * 70)
        (work / 'phase.txt').write_text(txt, encoding='utf-8', errors='replace')
    else:
        print(f'\n[7] no phase record (first bytes {raw[:8]!r})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
