#!/usr/bin/env python3
"""Host-side contract check for the NX729J watchdog diagnostic hook."""
from pathlib import Path
import os
import re

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("UNIXNU_NATIVE_UFS_SOURCE", ROOT / "drivers" / "NativeUfs" / "NativeUfs.cpp"))
if not SOURCE.exists():
    raise SystemExit(f"missing active NativeUfs source: {SOURCE}")
text = SOURCE.read_text(encoding="utf-8")

assert text.count("PON_GUNYAH_WDT_STATUS =") == 1
assert text.count("PON_GUNYAH_WDT_PING =") == 1
assert text.count("NX729J_WDT_PROBE") == 1
assert text.count("NX729J_WDT_PING") == 1
assert "WDT" in text
print("PASS: NX729J watchdog probe/ping contract")
