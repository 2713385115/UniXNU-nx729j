#!/usr/bin/env python3
"""Dispatch the macOS kernel build and keep retrying while GitHub has no runner.

Hosted macOS arm64 runners are capacity constrained. A job that cannot be
acquired is retried internally by GitHub a few times and then cancelled after
about fifteen minutes with the annotation

    The job was not acquired by Runner of type hosted even after multiple attempts

That is a platform capacity problem, not a build failure, so the useful move is
to give up on that attempt early and dispatch again -- optionally on another
runner label.

Exit code 0 means a run actually reached the runner (whether it then succeeded
or failed); its id is printed. Exit code 2 means every attempt was dropped for
lack of capacity.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

OWNER = "2713385115"
REPO = "UniXNU"
WORKFLOW = "macos.yml"
DEVICE_REPO = "2713385115/UniXNU-nx729j"
DEVICE_REF = "main"
SOURCE_REF = "main"
PRE_BUILD = ""
DATA_SEED_URL = ""
GROW_GIB = ""
UPLOAD_USERDATA = False
SKIP_SETUP = False
DEFCONFIG = "nx729j_bringup_defconfig"
UNIQUE_CHIP_ID = "4e583732394a0001"

API = "https://api.github.com"


def token() -> str:
    path = pathlib.Path(__file__).with_name(".gh-token")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise SystemExit("empty token")
    return value


TOKEN = token()


def api(method: str, path: str, body: dict | None = None) -> dict:
    payload = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path,
        data=payload,
        method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError:
            raise
        except Exception as exc:  # transient TLS/connection faults
            if attempt == 3:
                raise
            print(f"    (api retry {attempt + 1}: {exc})", flush=True)
            time.sleep(3 * (attempt + 1))
    return {}


def stamp() -> str:
    return time.strftime("%H:%M:%S")


def log(msg: str) -> None:
    print(f"[{stamp()}] {msg}", flush=True)


def dispatch(runner: str, steps: str) -> None:
    inputs = {
        "device_repository": DEVICE_REPO,
        "device_ref": DEVICE_REF,
        "defconfig": DEFCONFIG,
        "steps": steps,
        "runner": runner,
        "unique_chip_id": UNIQUE_CHIP_ID,
    }
    if PRE_BUILD:
        inputs["pre_build"] = PRE_BUILD
    if DATA_SEED_URL:
        inputs["data_seed_url"] = DATA_SEED_URL
    if GROW_GIB:
        inputs["grow_gib"] = GROW_GIB
    if UPLOAD_USERDATA:
        inputs["upload_userdata"] = True
    if SKIP_SETUP:
        inputs["skip_setup"] = True
    api(
        "POST",
        f"/repos/{OWNER}/{REPO}/actions/workflows/{WORKFLOW}/dispatches",
        {"ref": SOURCE_REF, "inputs": inputs},
    )


def latest_run() -> dict | None:
    data = api("GET", f"/repos/{OWNER}/{REPO}/actions/runs?per_page=1")
    runs = data.get("workflow_runs") or []
    return runs[0] if runs else None


def job_of(run_id: int) -> dict | None:
    data = api("GET", f"/repos/{OWNER}/{REPO}/actions/runs/{run_id}/jobs")
    jobs = data.get("jobs") or []
    return jobs[0] if jobs else None


def cancel(run_id: int) -> None:
    try:
        api("POST", f"/repos/{OWNER}/{REPO}/actions/runs/{run_id}/cancel")
    except urllib.error.HTTPError:
        pass


def wait_for_run(previous_id: int | None, queue_timeout: int) -> dict | None:
    """Return the freshly created run, or None if the dispatch never showed up."""
    deadline = time.time() + 120
    while time.time() < deadline:
        run = latest_run()
        if run and run["id"] != previous_id:
            return run
        time.sleep(5)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", default="kernel")
    ap.add_argument(
        "--runners",
        default="macos-latest,macos-latest,macos-14,macos-14,macos-latest,macos-15",
        help="runner labels to try in order, comma separated",
    )
    ap.add_argument("--queue-timeout", type=int, default=420,
                    help="seconds to wait for a runner before giving up on an attempt")
    ap.add_argument("--poll", type=int, default=45)
    ap.add_argument("--defconfig", default="nx729j_bringup_defconfig",
                    help="defconfig FILE name under configs/ in the device repository")
    ap.add_argument("--unique-chip-id", default="4e583732394a0001",
                    help="16 hex digits for /chosen/unique-chip-id; a software identity seed, "
                         "not a hardware UID, so any nonzero value works (default keeps IOPlatformUUID stable)")
    ap.add_argument("--pre-build", default="",
                    help="shell commands run in the UniXNU checkout before the kernel build")
    ap.add_argument("--data-seed-url", default="",
                    help="URL the runner downloads as the Data seed ($RUNNER_TEMP/data.dbr)")
    ap.add_argument("--grow-gib", default="",
                    help="grow the downloaded Data seed to this many GiB on the macOS runner")
    ap.add_argument("--device-repo", default="",
                    help="override owner/name of the device repository")
    ap.add_argument("--device-ref", default="main")
    ap.add_argument("--source-ref", default="main",
                    help="branch of the UniXNU fork the workflow runs on (default main)")
    ap.add_argument("--upload-userdata", action="store_true")
    ap.add_argument("--skip-setup", action="store_true",
                    help="set workflow input skip_setup: run seed-data-skip-setup.py on the Data seed")
    args = ap.parse_args()
    global DEFCONFIG, UNIQUE_CHIP_ID, PRE_BUILD, UPLOAD_USERDATA, DEVICE_REF, DEVICE_REPO
    global DATA_SEED_URL, GROW_GIB, SOURCE_REF, SKIP_SETUP
    SOURCE_REF = args.source_ref
    PRE_BUILD = args.pre_build
    DATA_SEED_URL = args.data_seed_url
    GROW_GIB = args.grow_gib
    UPLOAD_USERDATA = args.upload_userdata
    SKIP_SETUP = args.skip_setup
    DEVICE_REF = args.device_ref
    if args.device_repo:
        DEVICE_REPO = args.device_repo
    # `unixnu defconfig NAME` takes the file name verbatim; accept the short form too.
    DEFCONFIG = args.defconfig if args.defconfig.endswith("_defconfig") else args.defconfig + "_defconfig"
    UNIQUE_CHIP_ID = args.unique_chip_id

    labels = [s.strip() for s in args.runners.split(",") if s.strip()]

    previous = latest_run()
    previous_id = previous["id"] if previous else None

    for index, label in enumerate(labels, start=1):
        log(f"attempt {index}/{len(labels)}: dispatching on {label}")
        dispatch(label, args.steps)

        run = wait_for_run(previous_id, args.queue_timeout)
        if run is None:
            log("  dispatch accepted but no run appeared; retrying")
            time.sleep(10)
            continue

        run_id = run["id"]
        previous_id = run_id
        log(f"  run {run_id} created, waiting for a runner")

        started = time.time()
        while True:
            time.sleep(args.poll)
            try:
                run = api("GET", f"/repos/{OWNER}/{REPO}/actions/runs/{run_id}")
            except Exception as exc:
                log(f"  (poll failed: {exc})")
                continue

            status = run["status"]
            conclusion = run.get("conclusion")
            job = job_of(run_id) or {}
            runner = job.get("runner_name") or ""
            steps = job.get("steps") or []
            elapsed = int(time.time() - started)

            if status == "completed":
                if conclusion == "cancelled" and not steps:
                    log(f"  dropped after {elapsed}s: no runner was ever assigned")
                    break  # next attempt
                log(f"  finished: {conclusion} after {elapsed}s")
                print(f"RUN_ID={run_id}", flush=True)
                print(f"CONCLUSION={conclusion}", flush=True)
                return 0

            if not steps and not runner and elapsed > args.queue_timeout:
                log(f"  still queued after {elapsed}s, cancelling and retrying")
                cancel(run_id)
                time.sleep(10)
                break

            detail = ""
            if steps:
                done = [s for s in steps if s.get("conclusion")]
                detail = f" step {len(done)+1}/{len(steps)}"
            log(f"  {status}{detail} ({elapsed}s)")

    log("every attempt was dropped for lack of runner capacity")
    return 2


if __name__ == "__main__":
    sys.exit(main())
