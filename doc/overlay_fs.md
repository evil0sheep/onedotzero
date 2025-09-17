# Stateless Booting with NFS and OverlayFS

This document describes the technical implementation for PXE booting the cluster's compute nodes. The system uses a stateless "golden image" served over NFS combined with an OverlayFS to provide a consistent, robust, and flexible environment.

---

## Core Concepts

-   **NFS Root:** Instead of booting from a local disk, compute nodes load their kernel and initrd via PXE/TFTP, and then mount their root filesystem (`/`) from a central NFS share hosted on the control node.
-   **Golden Image:** The filesystem served via NFS is a "golden image" created with `debootstrap`. This ensures all nodes boot from an identical, centrally managed OS.
-   **OverlayFS:** This is a union filesystem that merges a read-only directory (the NFS share) with a writable in-memory directory (`tmpfs`). This gives the running node a filesystem that appears fully writable but discards all changes on reboot, providing a pristine environment every time.

---

## Implementation Workflow

The entire process is automated by the `nfs_server` Ansible role. It prepares a golden image that is pre-configured to boot into a stateless overlay environment.

1.  **Golden Image Creation:** A minimal Ubuntu 22.04 filesystem is created in `/srv/nfs/ubuntu2204` on the control node using `debootstrap`.

2.  **Package Installation:** The `overlayroot` package, along with other essentials like the kernel and SSH server, are installed directly into the golden image using `chroot`.

3.  **Configuration:** The `overlayroot` package is configured by creating the file `/srv/nfs/ubuntu2204/etc/overlayroot.conf` with the following content:
    ```
    overlayroot="tmpfs:swap=1,recurse=0"
    ```
    This simple configuration tells the `overlayroot` initramfs scripts to use a temporary RAM disk (`tmpfs`) as the writable upper layer for the overlay.

4.  **Initramfs Update:** The `update-initramfs` command is run within the chroot. This is a critical step that integrates the `overlayroot` scripts into the initial RAM disk, ensuring they run early in the boot process.

5.  **PXE Boot Configuration:** The GRUB configuration file, located at `/srv/tftp/boot/grub/grub.cfg`, is templated with the correct kernel parameters to enable the NFS boot. The kernel command line includes:
    -   `root=/dev/nfs`: Tells the kernel to use an NFS mount as its root.
    -   `nfsroot=<control_node_ip>:/srv/nfs/ubuntu2204`: Specifies the path to the golden image.
    -   `overlayroot=tmpfs`: This parameter explicitly enables the `overlayroot` functionality that was built into the initramfs.

---

## Boot Process and Result

When a compute node is powered on, the following occurs:

1.  The node PXE boots and receives its network configuration and the location of the TFTP server from `dnsmasq`.
2.  It downloads and runs the GRUB bootloader, which loads the kernel and the modified initrd.
3.  The kernel starts and mounts the NFS share from the control node.
4.  The `overlayroot` scripts within the initrd are triggered. They create a `tmpfs` RAM disk and construct an OverlayFS, mounting the read-only NFS share as the "lower" layer and the RAM disk as the "upper" writable layer.
5.  The node finishes booting into a system that appears to have a normal, writable root filesystem.

### Benefits of this Approach

-   **Stateless & Consistent:** Every boot is a fresh start from the known-good golden image. This eliminates configuration drift and makes the cluster highly predictable.
-   **Centralized Management:** All system configuration is managed in one place (the golden image on the control node). Updates are applied once and are instantly available to all nodes on their next reboot.
-   **Flexibility:** Although the underlying image is read-only, the overlay allows for temporary changes. One can SSH into a node, install packages, and modify files for debugging. A simple reboot securely wipes all these changes and restores the node to its pristine state.
