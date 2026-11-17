#!/usr/bin/env python3
"""Upload an explicit list of release assets, one at a time, with retry.

`gh-release-upload.py` handles the owner-policy envelope set and is keyed to a
fixed directory layout. The stage-7 handoff ships arbitrary files (images and
33 MiB log windows), so this takes local=asset-name pairs directly and deletes a
same-named asset first, which keeps re-runs idempotent.

    python gh-release-assets.py --release 448263885 out/a.img=a.img ...
"""
import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

API = 'https://api.github.com'
REPO = '2713385115/UniXNU-nx729j'
TOKEN = Path(__file__).with_name('.gh-token').read_text().strip()


def call(method, url, body=None, content_type='application/json', timeout=900):
    payload = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    request = urllib.request.Request(url, data=payload, method=method)
    request.add_header('Authorization', f'Bearer {TOKEN}')
    request.add_header('Accept', 'application/vnd.github+json')
    request.add_header('User-Agent', 'nx729j-ci')
    if payload is not None:
        request.add_header('Content-Type', content_type)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        return {'error': error.code, 'detail': error.read().decode('utf-8', 'replace')[:300]}


def drop_existing(release, name):
    current = call('GET', f'{API}/repos/{REPO}/releases/{release}/assets')
    for asset in (current if isinstance(current, list) else []):
        if asset['name'] == name:
            call('DELETE', f'{API}/repos/{REPO}/releases/assets/{asset["id"]}')
            print(f'  removed stale {name}', flush=True)


def upload(release, path, name, tries=4, timeout=240):
    drop_existing(release, name)
    body = path.read_bytes()
    url = f'https://uploads.github.com/repos/{REPO}/releases/{release}/assets?name={name}'
    last = None
    for attempt in range(1, tries + 1):
        started = time.time()
        result = call('POST', url, body, 'application/octet-stream', timeout=timeout)
        if 'error' not in result:
            dt = time.time() - started
            print(f'  ok {name} {len(body)} B {dt:.0f}s '
                  f'({len(body) / dt / 2**20:.2f} MiB/s) state={result.get("state")}', flush=True)
            return result
        last = f'{result["error"]} {result.get("detail", "")}'
        print(f'  retry {attempt}/{tries} {name}: {last[:160]}', flush=True)
        time.sleep(5 * attempt)
    raise SystemExit(f'failed {name}: {last}')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--release', required=True, help='release id, not tag')
    ap.add_argument('--timeout', type=int, default=240,
                    help='per-attempt socket timeout; a stalled POST never returns on its own')
    ap.add_argument('pairs', nargs='+', help='local=asset-name')
    args = ap.parse_args()
    for pair in args.pairs:
        local, name = pair.split('=', 1)
        print(f'upload {local} -> {name}', flush=True)
        upload(args.release, Path(local), name, timeout=args.timeout)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
