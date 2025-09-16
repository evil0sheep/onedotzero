# Stateless Booting with NFS and OverlayFS

This document outlines the technical approach for PXE booting the compute nodes. We will use a stateless "golden image" served over NFS, combined with an OverlayFS for a flexible development environment. This strategy is divided into two phases: a development phase for building and testing our configuration, and a production phase for running the stable cluster.

---

## Core Concepts

-   **NFS Root:** Instead of booting from a local disk or an ISO, the compute nodes will load their kernel and initrd via PXE/TFTP, and then mount their root filesystem (`/`) from a central NFS share hosted on the control node.
-   **Golden Image:** The filesystem served by NFS is a "golden image" created with `debootstrap`. This ensures all nodes boot from an identical, centrally managed OS.
-   **OverlayFS:** This is a union filesystem that allows us to merge a read-only directory (the NFS share) with a writable directory (a RAM disk). This gives us a filesystem that appears fully writable but discards all changes on reboot, providing a perfect, clean slate for development and testing.

---

## Phase 1: Live Development & Testing

The goal of this phase is to provide a rapid and iterative development cycle for creating and validating our Ansible playbooks.

### Workflow

1.  A minimal Ubuntu 22.04 golden image is created on the control node using `debootstrap`.
2.  This image is modified to include a custom script in its `initramfs` (initial RAM disk).
3.  The compute nodes PXE boot. The kernel is instructed to mount the NFS share as **read-only (`ro`)**.
4.  During the boot process, our custom `initramfs` script executes. It creates a writable RAM disk (`tmpfs`) and sets up an OverlayFS, merging the read-only NFS share with the new RAM disk.
5.  The node finishes booting into a system that appears fully writable.
6.  We can now SSH into the node and run our Ansible playbooks. All filesystem changes (installing packages, creating files, etc.) are written to the RAM disk.
7.  A simple reboot wipes the RAM disk, returning the node to its pristine, original state, ready for another test run.

### Implementation Steps

#### 1. Create the `initramfs` Hook Script

This script sets up the overlay. It will be created as an Ansible template (`ansible/roles/nfs_server/templates/overlayroot.j2`).

```sh
#!/bin/sh
#
# This script sets up a writable overlay filesystem on top of a read-only NFS root.
#

# Exit if we're not using an NFS root
[ "$ROOT" = "/dev/nfs" ] || exit 0

echo "==> Setting up OverlayFS on top of NFS root..."

# The initramfs has already mounted our NFS share at ${rootmnt}.
# We need to move it to a new location to use as our read-only "lower" layer.
mkdir -p /mnt/nfs_root
mount --move ${rootmnt} /mnt/nfs_root

# Create a tmpfs (RAM disk) to use as our writable "upper" layer.
mkdir -p /mnt/ram_overlay
mount -t tmpfs tmpfs /mnt/ram_overlay

# Create the required 'upper' and 'work' directories for the overlay.
mkdir -p /mnt/ram_overlay/upper
mkdir -p /mnt/ram_overlay/work

# Finally, create the overlay mount on top of the original root mount point.
# This merges the read-only NFS share with the writable RAM disk.
mount -t overlay overlay -o lowerdir=/mnt/nfs_root,upperdir=/mnt/ram_overlay/upper,workdir=/mnt/ram_overlay/work ${rootmnt}

echo "==> OverlayFS setup complete."
```

#### 2. Add Ansible Tasks to the `nfs_server` Role

These tasks will be added to `ansible/roles/nfs_server/tasks/main.yml` to inject the script into the golden image and rebuild the `initramfs`.

```yaml
- name: Create init-premount directory in chroot
  file:
    path: /srv/nfs/ubuntu2204/etc/initramfs-tools/scripts/init-premount
    state: directory
    mode: "0755"

- name: Copy OverlayFS setup script into the chroot
  template:
    src: overlayroot.j2
    dest: /srv/nfs/ubuntu2204/etc/initramfs-tools/scripts/init-premount/overlayroot
    mode: "0755"

- name: Update the initramfs in the chroot to include the new script
  command: "chroot /srv/nfs/ubuntu2204 update-initramfs -u"
  changed_when: false
```

#### 3. Update PXE Boot Parameters

The kernel parameters in `ansible/roles/pxe_boot/templates/grub.cfg.j2` must include the `ro` flag to ensure the NFS root is mounted read-only, allowing the overlay to function correctly.

```jinja
# /.../
linux /boot/grub/vmlinuz root=/dev/nfs nfsroot={{ hostvars['localhost']['ansible_' + compute_interface]['ipv4']['address'] }}:/srv/nfs/ubuntu2204 ip=dhcp ro quiet splash
# /.../
```

---

## Phase 2: Golden Image Production

Once the Ansible playbooks are mature and reliable, we transition to a production setup. The goal is a fast, consistent, and centrally managed boot for the entire cluster.

### Workflow

The transition is simple: we "bake" our configurations into the golden image itself.

1.  We modify the `nfs_server` Ansible playbook. After the `debootstrap` and minimal setup is complete, we will **run our validated Ansible playbooks inside the `chroot`**. This installs all necessary drivers, libraries, and software directly into the golden image on the control node's disk.
2.  The boot process for the compute nodes **remains identical to Phase 1**. They will still boot with a read-only NFS root and set up an OverlayFS.

### Result

-   **Fast Boots:** The nodes boot into a fully pre-configured environment. No time is spent running Ansible on startup.
-   **Stateless & Robust:** The root filesystem is still read-only, preventing configuration drift and ensuring stability.
-   **Flexible:** The OverlayFS is still active. While it won't be used for day-to-day operations, it provides a powerful "safety net." If we need to SSH into a node for emergency debugging or temporary changes, the writable RAM disk allows us to do so without permanently altering the golden image. A reboot still wipes any temporary changes.

This two-phase strategy combines rapid, iterative development with a robust, scalable, and easily maintainable production cluster environment.