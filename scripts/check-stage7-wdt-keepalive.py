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
assert len(re.findall(r"nx729j_wdt_keepalive\s*\(\s*h\.rounds\s*\)", text)) == 1
body = text[text.index("static void nx729j_wdt_keepalive"):text.index("// Copy the kernel message buffer", text.index("static void nx729j_wdt_keepalive"))]
assert "status ? 2u : 1u" in body
assert "nx729j_wdt_state != 1u" in body
assert re.search(r"nx729j_wdt_smc \(PON_GUNYAH_WDT_PING\)", body)
print("PASS: NX729J watchdog probe/ping contract")
