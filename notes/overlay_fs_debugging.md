# OverlayFS Debugging Log

This document summarizes the debugging process for the OverlayFS implementation.

## Current Understanding of the Problem

The core issue is that the `overlayroot` script, which is intended to run within the `initramfs` during boot, is not successfully creating the overlay filesystem.

We have now proven with `dmesg` logs that the script **is being executed**. The kernel's `overlayfs` module prints a message indicating an attempt to create an overlay, which could only be triggered by our script. However, the script appears to fail silently after this attempt, as the final booted system does not have an overlay mount and the root filesystem remains read-only.

The primary evidence is the persistent `overlayfs: fs on '/mnt/nfs_root' does not support file handles, falling back to xino=off.` message in the `dmesg` log. Despite having the correct `fsid=0` option on the NFS server and forcing the client to use NFSv3, the `initramfs` environment still fails to mount the overlay. This points to a more subtle, fundamental incompatibility between OverlayFS and an NFS root during the early boot stage.

## What We Have Ruled Out

Through extensive debugging, we have confirmed that the following potential issues are **NOT** the cause:

1.  **Script Not Executing:** The `dmesg` log proves the script is running.
2.  **Stale `initrd` File:** We have verified that the freshest `initrd` is being served and loaded.
3.  **Firewall Issues:** `ufw` on the control node is inactive.
4.  **DHCP/TFTP Failures:** `dnsmasq` logs show successful IP assignment and file transfer.
5.  **Missing Network Drivers:** The node successfully mounts the NFS root, proving network drivers are working.
6.  **Incorrect Script Permissions:** Verified via Ansible that the script is executable.
7.  **Incorrect Script Location:** The script is correctly located in the `/etc/initramfs-tools/scripts/nfs-premount` directory.
8.  **Missing `boot=nfs` Kernel Parameter:** We added this parameter, which was the key to getting the `nfs-premount` scripts to run.
9.  **Incorrect `grub.cfg` syntax:** Verified correct.
10. **NFS Version Incompatibility:** Explicitly forcing the `initramfs` to use NFSv3 with the `nfsvers=3` kernel parameter did not resolve the "file handles" error.

## Brainstorming: New Avenues of Investigation

The problem is now much more narrow: the script runs, but the `mount -t overlay` command within it is failing silently. The standard fixes (`fsid=0`, `nfsvers=3`) have not worked.

1.  **Silent `mount` Command Failure:**
    *   **Hypothesis:** The final `mount -t overlay ...` command is failing, but because of the way `/init` handles script output, we are not seeing the error message. The "file handles" message might be a warning, not the fatal error.
    *   **Next Step:** Modify the `overlayroot` script to be more robust. We can capture the output and error of the `mount` command and explicitly print it to the kernel log with a memorable tag. For example: `mount ... > /dev/kmsg 2>&1`. This bypasses any potential stdout/stderr suppression.

2.  **Missing `d_type` Support on the Host Filesystem:**
    *   **Hypothesis:** OverlayFS requires that the underlying filesystem of the *NFS server* supports `d_type`. The control node is running on some filesystem (likely ext4, but we haven't checked) that might have this feature disabled. If the host filesystem doesn't support it, the NFS server cannot provide it to the client, regardless of export options.
    *   **Next Step:** Check the filesystem type on the control node (`df -T /srv/nfs/ubuntu2204`) and verify that it has `d_type` support enabled (for ext4, this is usually on by default on modern systems, but can be checked with `tune2fs`).

3.  **A More Fundamental `initramfs` Problem:**
    *   **Hypothesis:** We are fighting the default `initramfs` scripts. It's possible the standard Ubuntu NFS boot script is mounting the root filesystem *before* our script gets a chance to run, making the `mount --move` operation impossible. The order of execution within the `nfs-premount` directory might be the issue.
    *   **Next Step:** Instead of trying to "intercept" the mount, we could try a different approach. We could let the system boot normally to the read-only NFS root, and then run a script from within the booted system (e.g., via an `rc.local` or systemd service) that sets up the overlay. This is a different architecture but might bypass the `initramfs` complexities.
