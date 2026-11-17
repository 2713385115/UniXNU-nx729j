#!/usr/bin/env python3
"""Apply the NX729J Stage 7 watchdog diagnostic to NativeUfs."""
from pathlib import Path
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--source", type=Path, default=Path("drivers/NativeUfs/NativeUfs.cpp"))
parser.add_argument("--check", action="store_true", help="validate the transformation without writing")
args = parser.parse_args()
source = args.source
text = source.read_text(encoding="utf-8")

if "NX729J_WDT_PROBE" in text or "nx729j_wdt_keepalive" in text:
    raise SystemExit("watchdog diagnostic is already present; refusing a duplicate patch")

anchor = 'static uint64_t boot_log_ticks (void)'
anchor_start = text.index(anchor)
anchor_end = text.index('\n', anchor_start) + 1
insert = """
// Some NX729J power-on paths leave the Gunyah watchdog armed by the bootloader.
// Probe once, and only ping when the hypervisor reports that it is armed. An
// error or a non-zero status leaves the watchdog untouched.
enum { PON_GUNYAH_WDT_STATUS = 0x86000006u, PON_GUNYAH_WDT_PING = 0x86000007u };
static uint64_t nx729j_wdt_smc (uint64_t fid)
{
  register uint64_t x0 __asm__("x0") = fid, x1 __asm__("x1") = 0,
                    x2 __asm__("x2") = 0, x3 __asm__("x3") = 0;
  __asm__ volatile ("smc #0" : "+r" (x0), "+r" (x1), "+r" (x2), "+r" (x3)
                    :: "memory", "x4", "x5", "x6", "x7", "x8", "x9", "x10", "x11", "x12",
                       "x13", "x14", "x15", "x16", "x17");
  return x0;
}
static uint32_t nx729j_wdt_state;
static uint32_t nx729j_wdt_pings;
static void nx729j_wdt_keepalive (uint32_t round)
{
  if (!nx729j_wdt_state) {
    const int64_t status = (int64_t) nx729j_wdt_smc (PON_GUNYAH_WDT_STATUS);
    nx729j_wdt_state = status ? 2u : 1u;
    IOLog ("NativeUfs: NX729J_WDT_PROBE status=%lld armed=%d\\n", (long long) status, nx729j_wdt_state == 1u);
  }
  if (nx729j_wdt_state != 1u) return;
  (void) nx729j_wdt_smc (PON_GUNYAH_WDT_PING);
  ++nx729j_wdt_pings;
  if (!round || !(round % 30)) IOLog ("NativeUfs: NX729J_WDT_PING n=%u round=%u\\n", nx729j_wdt_pings, round);
}
"""
if anchor_start < 0:
    raise SystemExit("NativeUfs boot-log anchor not found")
text = text[:anchor_end] + insert + text[anchor_end:]

hook_anchor = """    if (!ok) { ++errors; if (errors >= 3) return; } else errors = 0;
    IOSleep (BootLogPeriodMs);
"""
hook = """    if (!ok) { ++errors; if (errors >= 3) return; } else errors = 0;
    // Keep an already-armed bootloader watchdog from resetting the diagnostic
    // session while leaving an unarmed or unsupported watchdog untouched.
    nx729j_wdt_keepalive (h.rounds);
    IOSleep (BootLogPeriodMs);
"""
if hook_anchor not in text:
    raise SystemExit("NativeUfs boot-log hook anchor not found")
text = text.replace(hook_anchor, hook, 1)
if args.check:
    print(f"PASS: transformation validated for {source}")
else:
    source.write_text(text, encoding="utf-8", newline="\n")
    print(f"PASS: patched {source}")
