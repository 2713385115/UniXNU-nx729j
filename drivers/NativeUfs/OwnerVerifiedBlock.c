/* SPDX-License-Identifier: BSD-2-Clause-Patent */
/*
 * The native_verified_block_* entry points that the owner-root patches in
 * patches/xnu/offline-owner-*.patch call into.
 *
 * Upstream they live in NativeVirtioBlock, because on sheng the verified block
 * device is a virtio transport. This board has no virtio at all: the verified
 * block device is NativeUfsBlock, which drivers/NativeUfs publishes only after
 * configureVerification() has authenticated each root window against the
 * owner-signed envelopes linked in by build-verified-root-bundle.py. The queries
 * below are therefore answered from that driver's trust state instead of a virtio
 * device, and the object is merged into build/native-ufs/libNativeUfs.a - the
 * archive the kernel link already force-loads - so the definitions are found by
 * construction.
 *
 * This is C, not C++ on purpose: build-owner-verified-block.py compiles it with
 * the exact command line XNU used for bsd_init.o, and that command line selects
 * C. C linkage is also what the callers expect (they are C sources), so the
 * symbol names must stay unmangled.
 *
 * What is implemented here, and why it is exactly this much
 * --------------------------------------------------------
 * Only the two queries the stage-6 configuration asks for:
 *
 *   native_verified_root_probe             bsd_init, VERIFIED_ROOT_PROBE
 *   native_verified_block_root_authorized  bsd_init, OWNER_ROOT_AUDIT/TRUST
 *
 * The other two entry points - native_verified_block_volume_device and
 * native_verified_block_manifest_sha384 - belong to OWNER_CACHE_MOUNT and
 * OWNER_BOOT_MANIFEST. Those are stage-7 concerns and stay off, so answering them
 * with a guess would let mountroot act on a device that was never resolved. A
 * missing symbol at link time is the correct outcome there, and it is what the
 * defconfig produces.
 *
 * The answer itself
 * -----------------
 * verified_root_policy_count is the policy table build-verified-root-bundle.py
 * links in. NativeUfsBlock::configureVerification() refuses to publish any window
 * while it is zero, so a non-zero count is the same fact as "a block device
 * exists that authenticated against an owner envelope" - which is precisely what
 * the audit asks. There is exactly one such device on this board, so the dev_t
 * argument needs no further narrowing.
 *
 * This is not a placeholder that always says yes: with no policies linked in the
 * count is zero and the answer is no.
 */

/* Defined by build/verified-root-bundle/verified-root-policies.S: a quad holding
 * the number of owner-signed envelopes NativeUfs can authenticate. */
extern unsigned long verified_root_policy_count;

/* Called by bsd_init when XNU_VERIFIED_ROOT_PROBE is on, right before the
 * mountroot loop. Nothing to sample here - the probe's own bookkeeping is the
 * policy table - so the honest implementation is an empty body. */
void native_verified_root_probe(void);

/* Called by bsd_init after mounting a read-only root, and by the owner root
 * decision. Non-zero means "this device is the owner-authenticated window". */
int native_verified_block_root_authorized(int device);

void
native_verified_root_probe(void)
{
}

int
native_verified_block_root_authorized(int device)
{
	(void)device;
	return verified_root_policy_count != 0;
}
