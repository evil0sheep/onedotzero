# PXE Boot Debugging Summary

## Problem Description

The primary goal is to PXE boot a compute node into a live Ubuntu 22.04 server environment for development and testing of Ansible playbooks. However, despite all configurations, the compute node consistently boots into the Ubuntu installer (Subiquity) instead of the intended live session.

This occurs even when the GRUB boot menu is displayed and the "Ubuntu 22.04 Live Boot (Development)" option is manually selected.

## Current Understanding

The root cause appears to be the Ubuntu Server ISO's `initrd` defaulting to an installer-led boot process, especially when it detects any ambiguity or `cloud-init` related parameters. The core of the problem is finding the precise combination of kernel parameters that forces the `casper` live boot system to take precedence and completely bypass the `cloud-init` and Subiquity installer.

We have confirmed that the underlying PXE infrastructure (DHCP, TFTP, HTTP) is now working correctly for both UEFI and legacy BIOS clients. The issue is confined to the boot parameters passed to the kernel.

## What We Have Tried and Ruled Out

### 1. Initial State
- **Problem:** Always booting to the installer.
- **Hypothesis:** Incorrect PXE boot menu configuration.

### 2. UEFI GRUB Configuration (`grub.cfg.j2`)
- **Action:** Modified the "Live Boot" entry to use `boot=casper` and remove `maybe-ubiquity`.
- **Result:** No change.
- **Action:** Added `quiet splash` for better logging.
- **Result:** No change.
- **Action:** Added `cloud-config-url=/dev/null` to prevent `cloud-init` from running.
- **Result:** No change.
- **Action:** Added `netboot=url` to be more explicit about the boot method.
- **Result:** No change.
- **Action:** Added `cloud-init=disabled` to explicitly disable `cloud-init`.
- **Result:** No change.

### 3. Ansible Playbook Configuration
- **Problem:** Playbook was not running on the control node.
- **Action:** Changed `hosts: control` to `hosts: localhost` and `connection: local`.
- **Result:** Playbook now runs correctly on the control node.

### 4. Network Services on Control Node
- **Problem:** `wget` failed to download the ISO from `http://localhost`.
- **Hypothesis:** `nginx` was not running or misconfigured.
- **Investigation:**
    - Checked `nginx` configuration file: Confirmed correct.
    - Checked `nginx` service status: Confirmed running.
    - Checked firewall status: Confirmed inactive.
    - Checked listening ports with `ss`: Confirmed a process was listening on `192.168.1.1:80`.
- **Resolution:** The issue was `localhost` resolving incorrectly for `wget`. Using the explicit IP `19.168.1.1` confirmed the web server was working perfectly.

### 5. Legacy BIOS Boot (`pxe.cfg.j2`)
- **Problem:** The compute node might be falling back to legacy BIOS boot.
- **Hypothesis:** The legacy configuration was incomplete and only offered an installer option.
- **Action:**
    - Created a "Live Boot" entry in `pxe.cfg.j2` and made it the default.
    - Added Ansible tasks to create the `/srv/tftp/pxelinux.cfg` directory and copy the `pxe.cfg.j2` template.
    - Added Ansible tasks to copy the required `pxelinux.0`, `ldlinux.c32`, and `vesamenu.c32` files.
    - Corrected the paths for the legacy bootloader files.
- **Result:** The `dnsmasq` logs confirmed that the compute node was indeed making a legacy boot request after a UEFI attempt, and was being served the correct legacy boot files. However, it still booted to the installer.

### 6. `dnsmasq` Configuration (`dnsmasq.conf.j2`)
- **Problem:** No DHCP requests were appearing in the `dnsmasq` logs.
- **Hypothesis:** `dnsmasq` was not correctly binding to the interface or listening for DHCP requests.
- **Action:** Corrected the `dhcp-range` parameter to explicitly include the `compute_interface` (`dhcp-range={{ compute_interface }},...`).
- **Result:** `dnsmasq` logs now show successful DHCP and TFTP transactions for both UEFI and legacy boot attempts. This was a critical fix that allowed the boot process to proceed, but did not solve the final installer issue.

### 7. Final Legacy BIOS Fix
- **Problem:** The legacy boot path was still booting to the installer.
- **Hypothesis:** The kernel parameters in `pxe.cfg.j2` were incorrect and did not match the fixes applied to the UEFI `grub.cfg.j2`.
- **Action:** Updated the `APPEND` line in `pxe.cfg.j2` to include `netboot=url` and `cloud-init=disabled`.
- **Result:** Still boots to the installer, which is the current state.

---

## Next Steps: NFS Root Filesystem Plan

Given the persistent issues with the `casper`-based live boot from the ISO, the next logical step is to pivot to a more robust and controllable method: booting from a network-mounted root filesystem (NFS root). This aligns with the project's Phase 2 goals and will bypass the ambiguities of the ISO's boot process.

The plan involves the following steps, which will be implemented via a new Ansible role (`nfs_server`):

### 1. Create a Base Ubuntu Filesystem
- **Tool:** Use `debootstrap` on the control node to create a minimal Ubuntu 22.04 filesystem in a dedicated directory (e.g., `/srv/nfs/ubuntu2204`).
- **Configuration:** This filesystem will be a clean, minimal server environment. We will need to perform some basic configuration, such as setting a root password and configuring `fstab` to mount the NFS root as read-only.

### 2. Configure NFS Server
- **Installation:** Install the necessary NFS server packages (`nfs-kernel-server`) on the control node.
- **Exports:** Configure the NFS server to export the `debootstrap`-created directory (`/srv/nfs/ubuntu2204`) to the compute nodes on the private network (192.168.1.0/24). The export will be read-only to ensure a stateless boot.

### 3. Update PXE Boot Menus
- **Kernel & Initrd:** We will continue to use the same `vmlinuz` and `initrd` files served via TFTP.
- **New Kernel Parameters:** The `APPEND` (for legacy) and `linux` (for UEFI) lines in the boot menus will be significantly changed:
    - The `url=` parameter will be removed.
    - We will add the `root=/dev/nfs` parameter to tell the kernel to use an NFS root.
    - We will add the `nfsroot={{ nfs_server_ip }}:/srv/nfs/ubuntu2204` parameter, specifying the IP of the NFS server and the path to the exported filesystem.
    - Additional parameters like `ip=dhcp` and `ro` (for read-only) will be included.

### 4. Ansible Implementation
- A new Ansible role, `nfs_server`, will be created to handle the installation and configuration of `debootstrap` and the NFS server.
- The existing `pxe_boot` role will be modified to update the `grub.cfg.j2` and `pxe.cfg.j2` templates with the new NFS-based boot entries.
- The main `pxe_setup.yml` playbook will be updated to include the new `nfs_server` role.

This approach replaces the unpredictable ISO boot process with a standard Linux network boot, which should give us a stable and reliable "live" environment for our development work.