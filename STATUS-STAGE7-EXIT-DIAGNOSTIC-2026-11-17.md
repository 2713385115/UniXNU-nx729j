# NX729J Stage 7 exit diagnostics

Date: 2026-11-17

## State

- Stage 6 storage and Stage 7 userspace are reached.
- The no-GPU image runs for several minutes, but SpringBoard and related services restart.
- GPU/Metal adaptation remains disabled until a stable userspace boot is proven.

## Fix in this revision

- `package/BootProgress.c` exports the process-exit ring through `boot_progress_exit_rows()`.
- `package/BootLog.c` writes the exported rows to SHLOG under `[process exits]`.
- `scripts/build-boot-progress.py` compiles and archives `BootLog.o` together with the Stage 7 diagnostic objects. This is required when legacy display/GPU archives are disabled.

The exported rows contain total exits, nonzero exits, crash rows with signal/status, EL0 PC/LR/ESR/FAR details when captured, and uncaught-exception reason text when available.

## Verification

Host source checks passed on Windows. ARM64 compilation requires the macOS `xcrun` environment used by the official build workflow. No device partition was written for this revision.

After the diagnostic image is built, flash only `boot_a`, wait for userspace to run, then read SHLOG over 9008. The first `[process exits]` crash row is the next repair input.

## 2026-11-17 watchdog diagnostic branch

- Latest read-only 9008 capture on COM11 reports boot `0x150eb656`, uptime 362s, 58 rounds, 0 write errors, and regular `NativeUfs: log round` entries. It records launchd high-wake activity and repeated SpringBoard/process corpse failures; the image contains no `NX729J_WDT_PROBE` or `NX729J_WDT_PING` markers.
- A single-variable candidate was prepared from `D:\刷机\IOS\work\NativeUfs-ufslog-fixed.cpp`: probe the Gunyah watchdog once and ping only when the hypervisor reports status zero (armed). Nonzero/error status is left untouched.
- Candidate and rollback evidence: `C:\Users\Administrator\Documents\Codex\2026-11-16\read-https-unixnu-com-zh-porting-2\work\stage7-wdt-keepalive-20261117`.
- Candidate SHA-256: `79FFA7BDE59AC6A436571AF50FE4F9C0C8FE42B25DD990C7F21893B3AB8DD176`; baseline SHA-256: `578C58A21AD1DFC5BE64D0A994D654AC9A7FF36C9DE590221E9D52238ADE67FC`.
- Static marker/count and rollback checks passed. No image build or device write was performed because this Windows host lacks the macOS `xcrun` ARM64 build artifacts.
- Added `scripts/prepare-stage7-wdt.py`, an idempotence-checked pre-build transform for the official macOS workflow. Its `--check` mode passes on the baseline and rejects a duplicate patch on the candidate.

- Firehose read-only capture artifacts are in `C:\Users\Administrator\Documents\Codex\2026-11-16\read-https-unixnu-com-zh-porting-2\work\stage7-wdt-keepalive-20261117`: `current-9008-log-window.bin`, `current-9008-panic.bin`, `current-9008-phase.bin`, `current-9008-failure.bin`, and `port_trace.txt`. The log SHA-256 is `F88200DDD9768E28769465F1A790127486D331CE966979A8124AD7EF4D87335A`.

The port remains at Stage 7. Next action is to build this candidate in the official macOS/xcrun workflow, then flash/read back only `boot_a` and compare the new SHLOG watchdog markers and process-exit records. GPU/Metal remains deferred.
