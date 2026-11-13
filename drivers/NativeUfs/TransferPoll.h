// SPDX-License-Identifier: BSD-2-Clause-Patent
// Single-slot completion policy. Port supplies ordered MMIO, invalidate-only
// descriptor reads, a monotonic deadline and bounded pacing. No resubmission.
enum class UfsTransferEnd { Completed, Fatal, Timeout };
struct UfsTransferPoll {
  uint32_t polls = 0, doorbellSeen = 0, interruptSeen = 0, ocs = 15;
};
static constexpr uint32_t kUfsFatalStatus = 0x71880U;

// The production port can report progress to the console; the host test doubles do not implement that, so the call is
// resolved away when the method is absent instead of forcing every mock to grow one.
template <class Port>
static auto ufsObserve (Port &port, uint32_t polls, uint32_t doorbell, uint32_t interrupt, uint32_t ocs, int)
  -> decltype (port.observe (polls, doorbell, interrupt, ocs), void ())
{
  port.observe (polls, doorbell, interrupt, ocs);
}
template <class Port>
static void ufsObserve (Port &, uint32_t, uint32_t, uint32_t, uint32_t, ...) {}

template <class Port>
static UfsTransferEnd pollUfsTransfer(Port &port, UfsTransferPoll &state)
{
  for (;;) {
    ++state.polls;
    uint32_t doorbell = port.doorbell();
    state.doorbellSeen |= doorbell;
    state.interruptSeen |= port.interrupt();
    if (state.interruptSeen & kUfsFatalStatus) return UfsTransferEnd::Fatal;
    // Sparse heartbeat: a transfer that never returns leaves no failure record on the medium (the header write that would
    // carry it needs this same command path), so the only surviving evidence is the frame buffer console line it prints
    // here. 512 polls is about a millisecond at the 2 us pacing, and the milestone sequence is powers of two.
    if (!(state.polls & (state.polls - 1)) && state.polls >= 512) {
      ufsObserve (port, state.polls, doorbell, state.interruptSeen, state.ocs, 0);
    }
    if (!(doorbell & 1)) {
      state.ocs = port.completedOcs();
      // A zero doorbell alone does not prove that this command was accepted.
      // Also allow a completion so fast that software never observes DB=1.
      if (state.ocs != 15) return UfsTransferEnd::Completed;
    }
    if (port.expired()) return UfsTransferEnd::Timeout;
    port.delay();
  }
}
