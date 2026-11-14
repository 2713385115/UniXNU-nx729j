// SPDX-License-Identifier: BSD-2-Clause-Patent
// Shared production command path. Included inside the root transport namespace.
// The host harness replaces only MMIO, DMA synchronization, clock and lock primitives.
struct HardwareTransferPort {
  uint64_t deadline;
  uint32_t doorbell () { return read32 (gHost.hc, 0x58); }
  uint32_t interrupt () { return read32 (gHost.hc, 0x20); }
  uint32_t completedOcs () {
    cacheInvalidate (gHost.virt, 64);
    return *reinterpret_cast<volatile uint32_t *> (gHost.virt + 8) & 0xff;
  }
  bool expired () { return mach_absolute_time () >= deadline; }
  uint32_t polls_done = 0;
  void delay () {
    // A command that never completes used to be indistinguishable from a dead machine: the poll loop said nothing and the
    // storage-backed log writer had already stopped, so the window froze at the round before. Pace the wait onto the frame
    // buffer console instead. That path never touches the command slot, the doorbell or the UFS clock, so it keeps
    // reporting even while the controller is wedged. Roughly every 262 ms, which is far below the shortest transfer
    // timeout, so the last screenful always describes the command that is stuck.
    if (!(++polls_done & 0x1ffffU)) {
      observe (polls_done, doorbell (), interrupt (), 0);
    }
    IODelay (2);
  }
  // Printed from inside the poll loop so a transfer that is still waiting is visible while it waits. Without it a command
  // that never completes looks exactly like a boot that stopped logging: both leave the window frozen at the last header.
  void observe (uint32_t polls, uint32_t doorbell, uint32_t interrupt, uint32_t ocs) {
    IOLog ("NativeUfs: transfer waiting %u polls: db %x is %x ocs %x lba %llx op %02x cmd %llu\n",
           polls, doorbell, interrupt, ocs,
           (unsigned long long) gRootDiagnostic.lba, gRootDiagnostic.opcode,
           (unsigned long long) gRootDiagnostic.command);
  }
};

static void putBe32 (uint8_t *at, uint32_t value)
{
  at[0] = static_cast<uint8_t> (value >> 24);
  at[1] = static_cast<uint8_t> (value >> 16);
  at[2] = static_cast<uint8_t> (value >> 8);
  at[3] = static_cast<uint8_t> (value);
}

static uint32_t loadBe32 (const uint8_t *at)
{
  return (uint32_t (at[0]) << 24) | (uint32_t (at[1]) << 16) | (uint32_t (at[2]) << 8) | at[3];
}

// Declared here, defined with the write path further down: the quarantine that needs it happens inside hostSubmit, while
// the window globals it writes through are declared with the other window state.
static bool hostRecordFailure (UfsTransferEnd end, uint32_t dataBytes, uint32_t timeoutUs,
                               const UfsTransferPoll &state, uint32_t doorbell, uint32_t interrupt);
// Same reason: the submit path has to be able to drop a request the previous command left behind before it decides the
// controller is gone. Defined with the write path; idempotent, and answers true when the doorbell is already clear.
static bool hostClearStuckDoorbell (void);

// Issue the UPIU already placed at gHost.virt + kUcdOffset through request slot 0. `direction` is
// the UTRD data direction (0 none, 1 from host, 2 to host). Direction 1 is reachable only through hostWrite4k, which
// confines it to the persistent store window. Returns the overall command status
// (0 = completed) in *ocs. Timeout/fatal status quarantines the allocation.
// Where the last submission got to, in the only units that survive a bus that stops answering: a plain memory write.
// 0 idle, 1 the command was counted, 2 the request descriptor is built, 3 the cache flush returned, 4 the completion bit
// was acknowledged, 5 the doorbell was rung, 6 the poll loop is running, 7 done. A thread that never moves off 4 died
// inside a register write; one parked at 6 is waiting for a completion. Reading a register to decide which would hang
// on the same bus, so the liveness thread prints this number before it touches any MMIO.
static volatile uint32_t gSubmitStage;
static volatile uint64_t gSubmitCommand;
static volatile uint64_t gSubmitMat;
static volatile uint32_t gSubmitBytes;
static volatile uint32_t gSubmitDirection;

static IOReturn hostSubmit (uint32_t direction, uint32_t dataBytes, uint32_t timeoutUs, uint32_t *ocs)
{
  *ocs = 15;
  if (!timeoutUs || dataBytes > kDataMax || (dataBytes & 3) ||
      (dataBytes ? (direction != 1 && direction != 2) : direction != 0)) return kIOReturnBadArgument;
  if (gHost.poisoned) return kIOReturnNotReady;
  if (read32 (gHost.hc, 0x58) != 0) {
    // A request from an earlier command is still owned by the controller. Disabling and re-enabling the controller drops
    // it without touching the LUN configuration the device already accepted, so this is the difference between one lost
    // command and a boot that never moves again: the quarantine this used to take unconditionally turned every single
    // stuck request into a storage stack that answered NotReady for the rest of the boot. Only a doorbell that survives
    // the reset means the controller itself is gone.
    if (hostClearStuckDoorbell ()) {
      IOLog ("NativeUfs: stale request dropped before submit (lba %llx op %02x); controller reset\n",
             (unsigned long long) gRootDiagnostic.lba, gRootDiagnostic.opcode);
    } else {
      // Nothing can be written after this, so the reason goes to kprintf (the frame buffer console) as well, where it
      // survives a dead UFS.
      IOLog ("NativeUfs: poisoned: doorbell still %08x (lba %llx op %02x); no further storage I/O\n",
             read32 (gHost.hc, 0x58), (unsigned long long) gRootDiagnostic.lba, gRootDiagnostic.opcode);
      gHost.poisoned = true;
      gHost.ready = false;
      return recordTransport (kIOReturnBusy, *ocs);
    }
  }
  if (read32 (gHost.hc, 0x20) & kUfsFatalStatus) {
    IOLog ("NativeUfs: poisoned: interrupt %08x has a fatal bit (lba %llx op %02x)\n",
           read32 (gHost.hc, 0x20), (unsigned long long) gRootDiagnostic.lba, gRootDiagnostic.opcode);
    UfsTransferPoll none;
    hostRecordFailure (UfsTransferEnd::Fatal, 0, 0, none, 0, read32 (gHost.hc, 0x20));
    gHost.poisoned = true;
    gHost.ready = false;
    return recordTransport (kIOReturnIOError, *ocs);
  }
  gRootDiagnostic.command++;
  gSubmitStage = 1;
  gSubmitCommand = gRootDiagnostic.command;
  gSubmitMat = mach_absolute_time ();
  gSubmitBytes = dataBytes;
  gSubmitDirection = direction;
  gRootDiagnostic.type = gHost.virt[kUcdOffset];
  gRootDiagnostic.lun = gHost.virt[kUcdOffset + 2];
  gRootDiagnostic.opcode = gHost.virt[kUcdOffset + (gRootDiagnostic.type == 1 ? 16 : 12)];
  gRootDiagnostic.scsi = 0xffff;
  gRootDiagnostic.response = 0;
  gRootDiagnostic.protocol = 0;
  gRootDiagnostic.residual = 0;
  gRootDiagnostic.sense = 0;
  // A doorbell write that never returns leaves nothing behind: no completion, no timeout, no log line. Rolling the
  // submission itself onto the frame buffer console costs one print per 256 commands and turns that silent hang into a
  // legible last line - the command that was being submitted when the bus stopped answering.
  if (!(gRootDiagnostic.command & 0xffU)) {
    IOLog ("NativeUfs: submit cmd %llu lba %llx op %02x dir %u bytes %u db %08x\n",
           (unsigned long long) gRootDiagnostic.command, (unsigned long long) gRootDiagnostic.lba,
           gRootDiagnostic.opcode, direction, dataBytes, read32 (gHost.hc, 0x58));
  }
  uint8_t *utrd = gHost.virt;
  uint64_t ucdPhys = gHost.phys + kUcdOffset;
  uint32_t *words = reinterpret_cast<uint32_t *> (utrd);
  for (unsigned index = 0; index < 8; index++) {
    words[index] = 0;
  }
  words[0] = 0x10000000U | (direction << 25);
  words[2] = 0x0fU;
  words[4] = static_cast<uint32_t> (ucdPhys);
  words[5] = static_cast<uint32_t> (ucdPhys >> 32);
  words[6] = (512U >> 2) | ((kRspOffset >> 2) << 16);
  words[7] = (dataBytes ? 1U : 0U) | ((kPrdtOffset >> 2) << 16);
  if (dataBytes) {
    uint64_t dataPhys = gHost.phys + kDataOffset;
    uint32_t *prd = reinterpret_cast<uint32_t *> (gHost.virt + kUcdOffset + kPrdtOffset);
    prd[0] = static_cast<uint32_t> (dataPhys);
    prd[1] = static_cast<uint32_t> (dataPhys >> 32);
    prd[2] = 0;
    prd[3] = dataBytes - 1;
  }
  gSubmitStage = 2;
  cacheCivac (gHost.virt, kDataOffset + dataBytes);
  gSubmitStage = 3;
  // Interrupt status is write-one-to-clear, and clearing only bit 0 leaves every other raised bit set for the rest of the
  // boot. A UTMRCS or UTE left standing keeps the controller from accepting the next request: the doorbell write below
  // then never returns, which is exactly the shape every stalled boot has - the last line on the medium is the submit
  // print, and then no completion, no timeout and no failure record, because the thread that would have written them is
  // parked inside a register write. The fatal bits were already tested a few lines above, so everything still set here is
  // stale rather than news: acknowledge all of it.
  {
    uint32_t pending = read32 (gHost.hc, 0x20);
    if (pending) {
      hostWrite (0x20, pending);
    }
  }
  gSubmitStage = 4;
  HardwareTransferPort port;
  clock_interval_to_deadline (timeoutUs, kMicrosecondScale, &port.deadline);
  hostWrite (0x58, 1);
  gSubmitStage = 5;
  UfsTransferPoll state;
  gSubmitStage = 6;
  UfsTransferEnd end = pollUfsTransfer (port, state);
  gSubmitStage = 7;
  gRootDiagnostic.polls = state.polls;
  gRootDiagnostic.dbSeen = state.doorbellSeen;
  gRootDiagnostic.isSeen = state.interruptSeen;
  *ocs = state.ocs;
  if (end != UfsTransferEnd::Completed) {
    // A doorbell that is still set, or an interrupt with a fatal bit, means the controller may still own the buffer: no
    // cancel/reset/retry, keep it allocated and prohibit all subsequent CPU writes. A clear doorbell with no fatal bit is
    // a different event: the controller handed the buffer back and dropped this one command, which is survivable.
    // Quarantining that case turned a single dropped command into a storage stack that answered NotReady for the rest of
    // the boot, and the window showed it only as a frozen round counter with write_errors 0.
    uint32_t doorbellNow = read32 (gHost.hc, 0x58);
    uint32_t interruptNow = read32 (gHost.hc, 0x20);
    bool wedged = (doorbellNow & 1U) || (interruptNow & kUfsFatalStatus);
    // The reason is also printed, because after a quarantine no log reaches the medium.
    IOLog ("NativeUfs: %s: transfer %s after %u us: lba %llx op %02x bytes %u polls %u db %x/%x is %x/%x ocs %x cmd %llu\n",
           wedged ? "poisoned" : "dropped",
           end == UfsTransferEnd::Timeout ? "timed out" : "fatal", timeoutUs,
           (unsigned long long) gRootDiagnostic.lba, gRootDiagnostic.opcode, dataBytes, state.polls,
           state.doorbellSeen, doorbellNow, state.interruptSeen, interruptNow, state.ocs,
           (unsigned long long) gRootDiagnostic.command);
    if (wedged) {
      // Written before the quarantine is set, while the command path still answers: the header write that normally
      // carries this record needs that same path, so after the quarantine nothing else can land on the medium. The record
      // path resets the controller to drop the stuck request, so the doorbell is read again afterwards.
      hostRecordFailure (end, dataBytes, timeoutUs, state, doorbellNow, interruptNow);
      if (hostClearStuckDoorbell ()) {
        // The request was dropped and the request list is armed again: the LUN configuration the device accepted is
        // untouched, so the next command can use the same slot. Calling that state "poisoned" is what made one slow
        // transfer end the boot - the caller gets the error and retries instead.
        IOLog ("NativeUfs: %s at cmd %llu recovered: stale request dropped, transport kept alive\n",
               end == UfsTransferEnd::Timeout ? "timeout" : "fatal",
               (unsigned long long) gRootDiagnostic.command);
      } else {
        gHost.poisoned = true;
        gHost.ready = false;
      }
    }
    return recordTransport (end == UfsTransferEnd::Timeout ? kIOReturnTimeout : kIOReturnIOError, *ocs);
  }
  cacheInvalidate (gHost.virt, kDataOffset + dataBytes);
  gRootDiagnostic.response = loadBe32 (gHost.virt + kUcdOffset + kRspOffset);
  return recordTransport (*ocs == 0 ? kIOReturnSuccess : kIOReturnIOError, *ocs);
}

static bool hostClearUcd (void)
{
  if (gHost.poisoned) return false;
  // Check before touching the command/response area, not just before ringing.
  if (read32 (gHost.hc, 0x58) != 0) {
    gHost.poisoned = true;
    gHost.ready = false;
    recordTransport (kIOReturnBusy, 15);
    return false;
  }
  if (read32 (gHost.hc, 0x20) & kUfsFatalStatus) {
    gHost.poisoned = true;
    gHost.ready = false;
    recordTransport (kIOReturnIOError, 15);
    return false;
  }
  uint32_t *words = reinterpret_cast<uint32_t *> (gHost.virt + kUcdOffset);
  for (unsigned index = 0; index < (kPrdtOffset + 16) / 4; index++) {
    words[index] = 0;
  }
  return true;
}

static IOReturn hostProtocolError (uint32_t reason)
{
  gRootDiagnostic.protocol = reason;
  gHost.ready = false;
  gHost.poisoned = true;
  return recordTransport (kIOReturnIOError, gRootDiagnostic.ocs);
}

// Validate a completed response before interpreting zero-filled status bytes.
// Protocol codes: 1 type, 2 task tag, 3 LUN, 4 length/EHS, 5 query echo,
// 6 short/overrun transfer, 7 nonzero NOP result.
static IOReturn hostResponseHeader (uint8_t type, uint8_t lun, uint8_t tag)
{
  const uint8_t *rsp = gHost.virt + kUcdOffset + kRspOffset;
  if (rsp[0] != type) return hostProtocolError (1);
  if (rsp[3] != tag) return hostProtocolError (2);
  if (rsp[2] != lun) return hostProtocolError (3);
  unsigned length = (unsigned (rsp[10]) << 8) | rsp[11];
  if (rsp[8] || length > 512 - 32) return hostProtocolError (4);
  return kIOReturnSuccess;
}

static IOReturn hostNop (void)
{
  if (!hostClearUcd ()) return kIOReturnNotReady;
  gHost.virt[kUcdOffset + 3] = 1;
  uint32_t ocs = 15;
  IOReturn result = hostSubmit (0, 0, 200000, &ocs);
  if (result != kIOReturnSuccess) return result;
  result = hostResponseHeader (0x20, 0, 1);
  if (result != kIOReturnSuccess) return result;
  const uint8_t *rsp = gHost.virt + kUcdOffset + kRspOffset;
  if (rsp[10] || rsp[11]) return hostProtocolError (4);
  if (rsp[6] || rsp[7]) return hostProtocolError (7);
  return kIOReturnSuccess;
}

// True while a command runs with gHost.lock held. The quarantine recorder has to know, because it writes its record
// through this same command path: called from inside a transfer it already owns the lock, and re-locking a non-recursive
// IOLock parks the thread forever with the lock held. That is precisely how every silent stall looked from outside - no
// panic, no error record, the periodic writer and every APFS thread stopped at the same instant.
static bool gInsideLockedTransfer = false;


// Run a SCSI command. direction 0: no data, 1: host -> device from the DMA data area (already filled by the caller), 2: device
// -> host into the DMA data area. Callers other than hostWrite4k never pass direction 1.
static IOReturn hostScsiDir (uint8_t lun, const uint8_t *cdb, unsigned cdbLength, unsigned direction, uint32_t dataBytes,
                             uint32_t timeoutUs, uint32_t *scsiStatus)
{
  *scsiStatus = 0xffff;
  if (!cdb || !cdbLength || cdbLength > 16 || dataBytes > kDataMax || direction > 2 ||
      (dataBytes & 3) || (dataBytes ? !direction : direction)) return kIOReturnBadArgument;
  if (!hostClearUcd ()) return kIOReturnNotReady;
  uint8_t *ucd = gHost.virt + kUcdOffset;
  ucd[0] = 0x01;                      // COMMAND UPIU
  ucd[1] = direction == 2 ? 0x40 : direction == 1 ? 0x20 : 0x00;   // R: data in, W: data out
  ucd[2] = lun;
  putBe32 (ucd + 12, dataBytes);
  for (unsigned index = 0; index < cdbLength; index++) {
    ucd[16 + index] = cdb[index];
  }
  uint32_t ocs = 0xf;
  const bool outer = gInsideLockedTransfer;
  gInsideLockedTransfer = true;
  IOReturn status = hostSubmit (direction ? direction : 0U, dataBytes, timeoutUs, &ocs);
  gInsideLockedTransfer = outer;
  if (status != kIOReturnSuccess) return status;
  status = hostResponseHeader (0x21, lun, 0);
  if (status != kIOReturnSuccess) return status;
  const uint8_t *rsp = ucd + kRspOffset;
  *scsiStatus = (uint32_t (rsp[6]) << 8) | rsp[7];
  gRootDiagnostic.scsi = *scsiStatus;
  gRootDiagnostic.residual = loadBe32 (rsp + 12);
  unsigned length = (unsigned (rsp[10]) << 8) | rsp[11];
  if (length) {
    unsigned senseLength = (unsigned (rsp[32]) << 8) | rsp[33];
    if (length < 2 || senseLength > length - 2) return hostProtocolError (4);
    const uint8_t *sense = rsp + 34;
    if (senseLength >= 14 && ((sense[0] & 0x7f) == 0x70 || (sense[0] & 0x7f) == 0x71))
      gRootDiagnostic.sense = (uint32_t (sense[2] & 15) << 16) | (uint32_t (sense[12]) << 8) | sense[13];
    else if (senseLength >= 4 && ((sense[0] & 0x7f) == 0x72 || (sense[0] & 0x7f) == 0x73))
      gRootDiagnostic.sense = (uint32_t (sense[1] & 15) << 16) | (uint32_t (sense[2]) << 8) | sense[3];
  }
  if (!*scsiStatus && ((rsp[1] & 0x60) || gRootDiagnostic.residual)) return hostProtocolError (6);
  IOReturn result = *scsiStatus == 0 ? kIOReturnSuccess : kIOReturnNotReady;
  gRootDiagnostic.result = static_cast<uint32_t> (result);
  return result;
}

// The original read-only entry point: data, when present, always flows device -> host.
static IOReturn hostScsi (uint8_t lun, const uint8_t *cdb, unsigned cdbLength, bool toHost, uint32_t dataBytes,
                          uint32_t timeoutUs, uint32_t *scsiStatus)
{
  if (dataBytes && !toHost) { *scsiStatus = 0xffff; return kIOReturnBadArgument; }
  return hostScsiDir (lun, cdb, cdbLength, dataBytes ? 2U : 0U, dataBytes, timeoutUs, scsiStatus);
}

// Query read request (opcode 0x03 READ ATTRIBUTE / 0x05 READ FLAG). Returns the value field.
static IOReturn hostQueryRead (uint8_t opcode, uint8_t idn, uint32_t *value)
{
  if (!value || (opcode != 3 && opcode != 5)) return kIOReturnBadArgument;
  if (!hostClearUcd ()) return kIOReturnNotReady;
  uint8_t *ucd = gHost.virt + kUcdOffset;
  ucd[0] = 0x16;                      // QUERY REQUEST
  ucd[3] = 1;
  ucd[5] = 0x01;                      // standard read request (header dword 1, byte 1)
  ucd[12] = opcode;
  ucd[13] = idn;
  uint32_t ocs = 0xf;
  IOReturn status = hostSubmit (0, 0, 200000, &ocs);
  if (status != kIOReturnSuccess) {
    return status;
  }
  status = hostResponseHeader (0x36, 0, 1);
  if (status != kIOReturnSuccess) return status;
  const uint8_t *rsp = ucd + kRspOffset;
  if (rsp[10] || rsp[11]) return hostProtocolError (4);
  if (rsp[12] != opcode || rsp[13] != idn || rsp[14] || rsp[15]) return hostProtocolError (5);
  if (rsp[6] != 0) {
    gRootDiagnostic.result = static_cast<uint32_t> (kIOReturnError);
    return kIOReturnError;
  }
  *value = loadBe32 (rsp + 20);
  return kIOReturnSuccess;
}

// Read `blocks` (<= kDataMax / 4096) 4096-byte blocks of LUN `lun` starting at `lba` into dst.
static IOReturn hostRead4k (uint8_t lun, uint64_t lba, uint32_t blocks, void *dst)
{
  if (!gHost.ready || !dst || !blocks || blocks > kDataMax / 4096 || lba > 0xffffffffULL - (blocks - 1)) {
    return kIOReturnBadArgument;
  }
  IOLockLock (gHost.lock);
  gRootDiagnostic.lba = lba;
  uint8_t cdb[10] = { 0x28, 0, uint8_t (lba >> 24), uint8_t (lba >> 16), uint8_t (lba >> 8), uint8_t (lba),
                      0, uint8_t (blocks >> 8), uint8_t (blocks), 0 };
  uint32_t scsi = 0;
  IOReturn status = hostScsi (lun, cdb, 10, true, blocks * 4096U, 3000000, &scsi);
  // A request the controller dropped is not a failed read. The submit path resets the controller when it finds a doorbell
  // it did not get back, so a retry here starts from an armed request list; without it the error went straight to the
  // file system and one lost command ended the boot.
  if (status != kIOReturnSuccess && !gHost.poisoned) {
    IOLog ("NativeUfs: read retry lba %llx blocks %u after %08x\n",
           (unsigned long long) lba, blocks, status);
    status = hostScsi (lun, cdb, 10, true, blocks * 4096U, 3000000, &scsi);
  }
  if (status == kIOReturnSuccess) {
    memcpy (dst, gHost.virt + kDataOffset, blocks * 4096U);
  }
  IOLockUnlock (gHost.lock);
  return status;
}

// ---- Persistent store and log windows ------------------------------------------------------------------------------
// The only place this driver writes. At most two windows of 4096-byte LBAs on LUN 0 (the Data store and the diagnostic log),
// each opened once by its integration after it has proved that the window lies inside the userdata partition and clear of the
// root image; every write is checked against them again here, so nothing outside them can be written even if a caller is wrong.
static bool gStoreOpen = false, gLogOpen = false;
static uint64_t gStoreFirst = 0, gStoreBlocks = 0, gLogFirst = 0, gLogBlocks = 0;

static bool hostLogOpen (uint64_t first, uint64_t blocks)
{
  if (gLogOpen || !blocks || first > 0xffffffffULL || blocks > 0xffffffffULL - first) return false;
  if (gStoreOpen && first < gStoreFirst + gStoreBlocks && gStoreFirst < first + blocks) return false;
  gLogFirst = first;
  gLogBlocks = blocks;
  gLogOpen = true;
  return true;
}

static bool hostInWindow (uint64_t lba, uint32_t blocks)
{
  if (gStoreOpen && lba >= gStoreFirst && lba - gStoreFirst < gStoreBlocks && blocks <= gStoreBlocks - (lba - gStoreFirst)) return true;
  return gLogOpen && lba >= gLogFirst && lba - gLogFirst < gLogBlocks && blocks <= gLogBlocks - (lba - gLogFirst);
}

static bool hostStoreOpen (uint64_t first, uint64_t blocks)
{
  if (gStoreOpen || !blocks || first > 0xffffffffULL || blocks > 0xffffffffULL - first) return false;
  if (gLogOpen && first < gLogFirst + gLogBlocks && gLogFirst < first + blocks) return false;
  gStoreFirst = first;
  gStoreBlocks = blocks;
  gStoreOpen = true;
  return true;
}

// The write itself, with gHost.lock already held by the caller. The quarantine recorder runs from inside a transfer and
// must use this one: hostWrite4k would take the same non-recursive IOLock a second time and hang the whole storage stack.
static IOReturn hostWrite4kLocked (uint8_t lun, uint64_t lba, uint32_t blocks, const void *src, bool fua,
                                   uint32_t timeoutUs)
{
  if (!gHost.ready || !src || !blocks || blocks > kDataMax / 4096 || lun != 0 || !hostInWindow (lba, blocks)) {
    return kIOReturnBadArgument;
  }
  gRootDiagnostic.lba = lba;
  memcpy (gHost.virt + kDataOffset, src, blocks * 4096U);
  uint8_t cdb[10] = { 0x2a, uint8_t (fua ? 0x08 : 0), uint8_t (lba >> 24), uint8_t (lba >> 16), uint8_t (lba >> 8), uint8_t (lba),
                      0, uint8_t (blocks >> 8), uint8_t (blocks), 0 };
  uint32_t scsi = 0;
  return hostScsiDir (0, cdb, 10, 1, blocks * 4096U, timeoutUs, &scsi);
}

// WRITE(10) of `blocks` 4096-byte blocks from src to lba on LUN 0; the range must lie inside one open window.
static IOReturn hostWrite4k (uint8_t lun, uint64_t lba, uint32_t blocks, const void *src, bool fua = false,
                             uint32_t timeoutUs = 8000000)
{
  if (!gHost.ready || !src || !blocks || blocks > kDataMax / 4096 || lun != 0 || !hostInWindow (lba, blocks)) {
    return kIOReturnBadArgument;
  }
  IOLockLock (gHost.lock);
  IOReturn status = hostWrite4kLocked (lun, lba, blocks, src, fua, timeoutUs);
  IOLockUnlock (gHost.lock);
  return status;
}

// Whether another thread holds the command lock. A diagnostic write that queues behind a long page-in looks exactly
// like a machine that lost every CPU: the writer stops mid-round, both of its witnesses (the command counter and the
// message cursor) are sampled by that same writer, and nothing is ever recorded. Probing first lets the writer skip a
// round instead of joining the queue, so its own liveness stops depending on the data path it is watching.
static bool hostCommandBusy (void)
{
  if (!gHost.lock || !gHost.ready) return true;
  if (IOLockTryLock (gHost.lock)) {
    IOLockUnlock (gHost.lock);
    return false;
  }
  return true;
}

// One block, written into the last slot of the log window the moment a transfer is quarantined. This is the only channel
// that survives the quarantine: the periodic writer's header write needs the same command path and is refused from then
// on, which is why every stalled boot so far came back as a frozen round counter with no error recorded. The slot is the
// one rootFailureRecord already uses, so a boot that fails early and a boot that stalls late land in the same place.
static bool gRecordingFailure = false;
// The transport is quarantined by a doorbell that never came back, and that same doorbell is what makes every later
// write impossible - including the one record that would say which command killed it. Disabling the controller clears
// the stuck request without touching the LUN configuration the device already accepted, so the recorder gets one more
// command slot before the quarantine is set. The request list is then put back exactly the way hostBringUp left it,
// because the reset also drops its base address and its enable bit. If the doorbell survives the reset the controller
// is gone and the record is abandoned rather than retried.
static bool hostClearStuckDoorbell (void)
{
  if (!(read32 (gHost.hc, 0x58) & 1U)) return true;
  const uint32_t hce = read32 (gHost.hc, 0x34);
  write32 (gHost.hc, 0x34, hce & ~1U);
  IODelay (2000);
  write32 (gHost.hc, 0x34, hce | 1U);
  IODelay (2000);
  write32 (gHost.hc, 0x24, 0);                  // interrupts stay masked, as hostBringUp left them
  write32 (gHost.hc, 0x60, 0);                  // stop the request list
  write32 (gHost.hc, 0x50, static_cast<uint32_t> (gHost.phys));
  write32 (gHost.hc, 0x54, static_cast<uint32_t> (gHost.phys >> 32));
  write32 (gHost.hc, 0x20, 0xffffffffU);        // clear stale status
  write32 (gHost.hc, 0x60, 1);                  // start it again
  IODelay (2000);
  const uint32_t doorbell = read32 (gHost.hc, 0x58);
  IOLog ("NativeUfs: controller reset after a stuck doorbell: hce %08x -> doorbell %08x\n", hce, doorbell);
  return !(doorbell & 1U);
}

static bool hostRecordFailure (UfsTransferEnd end, uint32_t dataBytes, uint32_t timeoutUs,
                               const UfsTransferPoll &state, uint32_t doorbell, uint32_t interrupt)
{
  if (gRecordingFailure || !gLogOpen || gLogBlocks <= 2) return false;
  // The record used to be abandoned here, because a set doorbell is also what hostClearUcd refuses. That made the one
  // channel built for a quarantine the one channel a quarantine always disabled, and every stalled boot came back with
  // no error at all. Clearing the controller first costs two milliseconds and is the only way this record can land.
  if (!hostClearStuckDoorbell ()) return false;
  gRecordingFailure = true;
  static uint8_t record[4096];
  memset (record, 0, sizeof record);
  snprintf (reinterpret_cast<char *> (record), sizeof record,
            "SHLOGERR reason=%s timeout_us=%u bytes=%u lba=%llx op=%02x type=%02x cmd=%llu polls=%u "
            "db_seen=%x db_now=%x is_seen=%x is_now=%x ocs=%x scsi=%x sense=%x residual=%u hcs=%x hce=%x\n",
            end == UfsTransferEnd::Timeout ? "timeout" : "fatal", timeoutUs, dataBytes,
            (unsigned long long) gRootDiagnostic.lba, gRootDiagnostic.opcode, gRootDiagnostic.type,
            (unsigned long long) gRootDiagnostic.command, state.polls, state.doorbellSeen, doorbell,
            state.interruptSeen, interrupt, state.ocs, gRootDiagnostic.scsi, gRootDiagnostic.sense,
            gRootDiagnostic.residual, read32 (gHost.hc, 0x30), read32 (gHost.hc, 0x34));
  // Last block of the window: a fixed LBA the reader can compute from the header alone (first + window_blocks - 1).
  const uint64_t slot = gLogFirst + gLogBlocks - 1;
  // Short timeout on purpose: this runs on an already failing path and must not add seconds to every retry. The record
  // is written with whatever lock state the caller has: inside a transfer the lock is already ours, and taking it again
  // here was the deadlock that turned one failed command into a frozen machine.
  IOReturn status = gInsideLockedTransfer
      ? hostWrite4kLocked (0, slot, 1, record, false, 200000)
      : hostWrite4k (0, slot, 1, record, false, 200000);
  gRecordingFailure = false;
  return status == kIOReturnSuccess;
}

// SYNCHRONIZE CACHE(10) for the whole LUN: everything written so far is on the medium when this returns.
// The timeout is deliberately short. This call holds gHost.lock for its whole duration, so a flush the device never
// completes does not just fail this one caller: every other storage thread queues behind the lock and the boot stops
// looking like a stalled writer and starts looking like a frozen machine. Three seconds still covers a full flush of
// this device (a checkpoint commit is milliseconds) and bounds the damage of one that never returns.
static IOReturn hostSyncCache (void)
{
  if (!gHost.ready || (!gStoreOpen && !gLogOpen)) return kIOReturnNotReady;
  IOLockLock (gHost.lock);
  uint8_t cdb[10] = { 0x35 };
  uint32_t scsi = 0;
  IOReturn status = hostScsiDir (0, cdb, 10, 0, 0, 3000000, &scsi);
  IOLockUnlock (gHost.lock);
  return status;
}

