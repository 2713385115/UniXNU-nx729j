"""Parallel range downloader for a large CI artifact.

A single connection through this link is throttled to roughly 40 KB/s, so the
serial chunked fetcher needs half an hour for an 80 MiB artifact.  The transfer
is split into many small ranges and driven by a thread pool; each worker owns
its own range and its own connection, and a failed range is retried on its own.

    python ci-fetch-parallel.py --run 43668362493 --name unixnu-nx729j_stage7_defconfig \
        --into work/ci-43668362493 --block 4 --workers 12
"""
import argparse
import http.client
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

TOKEN = (Path(__file__).with_name('.gh-token')).read_text().strip()
API = 'https://api.github.com'


def api(url):
    request = urllib.request.Request(url)
    request.add_header('Authorization', f'Bearer {TOKEN}')
    request.add_header('Accept', 'application/vnd.github+json')
    request.add_header('User-Agent', 'nx729j-ci')
    return json.loads(urllib.request.urlopen(request, timeout=120).read())


class Redirect(urllib.request.HTTPRedirectHandler):
    """The blob host must not receive the API token."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        follow = urllib.request.HTTPRedirectHandler.redirect_request(self, req, fp, code, msg, headers, newurl)
        if follow is not None:
            for name in ('Authorization', 'User-agent', 'Accept'):
                follow.remove_header(name)
        return follow


OPENER = urllib.request.build_opener(Redirect)
LOCK = threading.Lock()
DONE = [0]


def fetch(url, start, count, attempts=6):
    end = start + count - 1
    last = None
    for attempt in range(attempts):
        request = urllib.request.Request(url)
        request.add_header('Authorization', f'Bearer {TOKEN}')
        request.add_header('User-Agent', 'nx729j-ci')
        request.add_header('Range', f'bytes={start}-{end}')
        try:
            with OPENER.open(request, timeout=120) as response:
                data = response.read()
            if len(data) != count:
                raise OSError(f'short read: {len(data)} of {count}')
            return data
        except (OSError, urllib.error.URLError, http.client.HTTPException) as error:
            last = error
            time.sleep(2.0 * (attempt + 1))
    raise SystemExit(f'bytes {start}-{end}: {last}')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', required=True)
    ap.add_argument('--repo', default='2713385115/UniXNU')
    ap.add_argument('--name', required=True)
    ap.add_argument('--into', required=True)
    ap.add_argument('--block', type=int, default=4, help='MiB per range request')
    ap.add_argument('--workers', type=int, default=12)
    args = ap.parse_args()

    artifacts = api(f'{API}/repos/{args.repo}/actions/runs/{args.run}/artifacts')['artifacts']
    match = [a for a in artifacts if a['name'] == args.name]
    if len(match) != 1:
        raise SystemExit(f'{args.name}: {len(match)} matches')
    artifact = match[0]
    total = artifact['size_in_bytes']
    url = artifact['archive_download_url']
    into = Path(args.into).resolve()
    into.mkdir(parents=True, exist_ok=True)
    target = into / (args.name + '.zip')
    block = args.block << 20

    ranges = [(start, min(block, total - start)) for start in range(0, total, block)]
    print(f'{args.name}: {total / 2**20:.1f} MiB, {len(ranges)} ranges of {args.block} MiB, '
          f'{args.workers} workers', file=sys.stderr, flush=True)

    started = time.time()
    # Workers only return bytes.  A shared buffered file object cannot be used
    # here: concurrent seek/write pairs interleave at the Python buffer layer
    # and silently corrupt the archive even when guarded by a lock.  The main
    # thread consumes pool.map() in order and is the only writer.
    with target.open('wb') as out:
        out.truncate(total)

        def work(item):
            index, (start, count) = item
            data = fetch(url, start, count)
            with LOCK:
                DONE[0] += len(data)
                if index % 4 == 0 or DONE[0] == total:
                    rate = DONE[0] / max(1e-9, time.time() - started) / 2**20
                    print(f'  {DONE[0] / 2**20:8.1f} / {total / 2**20:.1f} MiB  {rate:6.2f} MiB/s',
                          file=sys.stderr, flush=True)
            return index, start, data

        written = 0
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for index, start, data in pool.map(work, enumerate(ranges)):
                if start != written:
                    raise SystemExit(f'out-of-order range at {start}, expected {written}')
                out.write(data)
                written += len(data)
        if written != total:
            raise SystemExit(f'incomplete download: {written} of {total} bytes')
        out.flush()

    if target.stat().st_size != total:
        raise SystemExit(f'file size mismatch: {target.stat().st_size} != {total}')
    print(f'  {target} {target.stat().st_size} bytes in {time.time() - started:.0f}s',
          file=sys.stderr, flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
