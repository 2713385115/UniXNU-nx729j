// SPDX-License-Identifier: MIT
//
// NX729J port: libBootProgress.a compiles BootProgress.c alone, while upstream also builds the
// board probes that define these symbols (BootLog.c, BootUiTraceProbe.c, BootProcessProbe.c,
// LegacyDisplayProbe.cpp, HidInputProbe.cpp, the Adreno audits, the cpufreq sampler). This board
// has none of those transports yet, and `weak_import` is only a weak *reference* under a dynamic
// linker -- the kernel links statically, so all of them came back as undefined symbols.
//
// Every definition below is weak on purpose: a real probe, once ported, overrides it without
// this file changing, and until then the diagnostic page reports zeros, which is the truth.

#include <stdint.h>

#define NX729J_WEAK __attribute__((weak))

// Callable, observes nothing.
//
// boot_process_* and boot_ui_trace_* are deliberately absent: the board now compiles the real
// BootProcessProbe.c and BootUiTraceProbe.c (official stage-7 instruments, see
// guide/debugging.md "UI trace"), and a weak definition here would collide with them at link.
NX729J_WEAK void boot_log_tick(unsigned lap) { (void)lap; }
NX729J_WEAK void sheng_cpufreq_poll(void) {}

// Character tables the page copies verbatim.
NX729J_WEAK char native_touch_rows[4][120] = {{0}};

// Counters and status words. Wide enough for any read site in BootProgress.c; zero means "the
// owning probe never ran on this board".
NX729J_WEAK volatile uint64_t native_adreno_audit_attr = 0;
NX729J_WEAK volatile uint64_t native_adreno_audit_ticks = 0;
NX729J_WEAK volatile uint64_t native_adreno_cache_stats = 0;
NX729J_WEAK volatile uint64_t native_adreno_coherence = 0;
NX729J_WEAK volatile uint64_t native_adreno_reassert_ticks = 0;
NX729J_WEAK volatile uint64_t native_adreno_wc_audit = 0;
NX729J_WEAK volatile uint64_t native_adreno_wc_distrusted = 0;
NX729J_WEAK volatile uint64_t native_cpufreq_row = 0;
NX729J_WEAK volatile uint64_t native_cpufreq_stage = 0;
NX729J_WEAK volatile uint64_t native_gop_gpu_abandoned = 0;
NX729J_WEAK volatile uint64_t native_gop_gpu_completions = 0;
NX729J_WEAK volatile uint64_t native_gop_gpu_failures = 0;
NX729J_WEAK volatile uint64_t native_gop_gpu_offers = 0;
NX729J_WEAK volatile uint64_t native_hid_input_events = 0;
NX729J_WEAK volatile uint64_t native_hid_input_results = 0;
NX729J_WEAK volatile uint64_t native_legacy_client_calls = 0;
NX729J_WEAK volatile uint64_t native_legacy_client_class = 0;
NX729J_WEAK volatile uint64_t native_legacy_client_pid = 0;
NX729J_WEAK volatile uint64_t native_legacy_client_status = 0;
NX729J_WEAK volatile uint64_t native_legacy_client_type = 0;
NX729J_WEAK volatile uint64_t native_legacy_copy_bytes_last = 0;
NX729J_WEAK volatile uint64_t native_legacy_copy_bytes_sum = 0;
NX729J_WEAK volatile uint64_t native_legacy_copy_last = 0;
NX729J_WEAK volatile uint64_t native_legacy_copy_max = 0;
NX729J_WEAK volatile uint64_t native_legacy_copy_n = 0;
NX729J_WEAK volatile uint64_t native_legacy_copy_sum = 0;
NX729J_WEAK volatile uint64_t native_legacy_frame_color_counts = 0;
NX729J_WEAK volatile uint64_t native_legacy_frame_color_other = 0;
NX729J_WEAK volatile uint64_t native_legacy_frame_colors = 0;
NX729J_WEAK volatile uint64_t native_legacy_frame_count = 0;
NX729J_WEAK volatile uint64_t native_legacy_frame_kind = 0;
NX729J_WEAK volatile uint64_t native_legacy_frame_stats = 0;
NX729J_WEAK volatile uint64_t native_legacy_frame_time = 0;
NX729J_WEAK volatile uint64_t native_legacy_gop_bytes = 0;
NX729J_WEAK volatile uint64_t native_legacy_gop_physical = 0;
NX729J_WEAK volatile uint64_t native_legacy_gop_virtual = 0;
NX729J_WEAK volatile uint64_t native_legacy_gpu_fallbacks = 0;
NX729J_WEAK volatile uint64_t native_legacy_gpu_last_status = 0;
NX729J_WEAK volatile uint64_t native_legacy_guard_slot = 0;
NX729J_WEAK volatile uint64_t native_legacy_pm_callbacks = 0;
NX729J_WEAK volatile uint64_t native_legacy_pm_calls = 0;
NX729J_WEAK volatile uint64_t native_legacy_pm_stage = 0;
NX729J_WEAK volatile uint64_t native_legacy_prepare_attempts = 0;
NX729J_WEAK volatile uint64_t native_legacy_prepare_calls = 0;
NX729J_WEAK volatile uint64_t native_legacy_prepare_reason = 0;
NX729J_WEAK volatile uint64_t native_legacy_rej_class = 0;
NX729J_WEAK volatile uint64_t native_legacy_rej_reason = 0;
NX729J_WEAK volatile uint64_t native_legacy_rej_size = 0;
NX729J_WEAK volatile uint64_t native_legacy_rej_state = 0;
NX729J_WEAK volatile uint64_t native_legacy_rej_surface = 0;
NX729J_WEAK volatile uint64_t native_legacy_rej_surfaces = 0;
NX729J_WEAK volatile uint64_t native_legacy_retirement_calls = 0;
NX729J_WEAK volatile uint64_t native_legacy_stage = 0;
NX729J_WEAK volatile uint64_t native_legacy_swap_accepted_count = 0;
NX729J_WEAK volatile uint64_t native_legacy_swap_rejected_count = 0;
NX729J_WEAK volatile uint64_t native_legacy_swap_validation_calls = 0;
NX729J_WEAK volatile uint64_t native_legacy_swap_validation_reason = 0;
NX729J_WEAK volatile uint64_t native_legacy_swap_validation_result = 0;
NX729J_WEAK volatile uint64_t native_legacy_transfer_bytes = 0;
NX729J_WEAK volatile uint64_t native_legacy_transfer_calls = 0;
NX729J_WEAK volatile uint64_t native_legacy_transfer_status = 0;
NX729J_WEAK volatile uint64_t native_touch_count = 0;
NX729J_WEAK volatile uint64_t native_touch_seq = 0;
NX729J_WEAK volatile uint64_t native_touch_stage_req = 0;
NX729J_WEAK volatile uint64_t native_touch_xs = 0;
NX729J_WEAK volatile uint64_t native_touch_ys = 0;
NX729J_WEAK volatile uint64_t native_virtio_read_failures = 0;
NX729J_WEAK volatile uint64_t native_virtio_read_requests = 0;
NX729J_WEAK volatile uint64_t native_virtio_ready = 0;
NX729J_WEAK volatile uint64_t native_virtio_verified_blocks = 0;

