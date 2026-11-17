#!/usr/bin/env python3
"""Build the stage-7 diagnostic image per boot-arguments#apply-arguments.

One diagnostic edit, official 1000-byte command line kept:
  - drop two legacy input probes that stage 7 does not judge on
  - add sheng-build-tag=<tag>        (BootProgress.c:526)
  - add boot-ui-focus=launchd        (BootProcessProbe.c:101, rows go to the
    persisted log through BootLog.c:207 boot_ui_trace_role_rows)

    python tools/build-stage7-trace.py IN.img OUT.img [--tag s7p] [--focus launchd]
"""
import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DROP = ("offline-key-events", "offline-hid-home")


def load_tool():
    spec = importlib.util.spec_from_file_location("g", HERE / "grow-toml-args.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_args(img):
    g = load_tool()
    info = g.toml_tool.load_img(str(img))
    payload, _dtb = g.toml_tool.inflate(info["blob"])
    vol = g.pick_volume(bytearray(payload))
    ent = vol.resolve(g.TOML_PATH)
    cur = g.read_file_fast(vol, ent)
    return g.toml_tool.ARGS_RE.search(cur).group(1).decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--tag", default="s7p")
    ap.add_argument("--focus", default="launchd")
    a = ap.parse_args()

    old = read_args(a.src)
    tokens = [t for t in old.split() if t.split("=")[0] not in DROP]
    tokens.append("sheng-build-tag=" + a.tag)
    tokens.append("boot-ui-focus=" + a.focus)
    new = " ".join(tokens)
    if len(new) > 1000:
        raise SystemExit("command line %d > 1000 bytes" % len(new))
    print("args %d -> %d bytes (limit 1000)" % (len(old), len(new)))
    print("dropped: %s" % ", ".join(DROP))
    print("added  : sheng-build-tag=%s boot-ui-focus=%s" % (a.tag, a.focus))
    r = subprocess.run([sys.executable, str(HERE / "grow-toml-args.py"), a.src, a.dst, "--args", new])
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
