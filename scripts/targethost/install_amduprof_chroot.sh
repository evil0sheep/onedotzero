#!/bin/bash
# install_amduprof_chroot.sh
# Safely install AMD uProf into a chroot golden image
# Usage: ./install_amduprof_chroot.sh /path/to/golden_image /path/to/amduprof.deb
# ./install_amduprof_chroot.sh /home/user0/build/0.2/ubuntu_golden /opt/assets/amduprof_5.2-606_amd64.deb

set -euo pipefail

if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <golden_image_root> <amduprof_deb>"
    exit 1
fi

GOLDEN_ROOT="$1"
DEB_FILE="$2"

# Check inputs
if [ ! -d "$GOLDEN_ROOT" ]; then
    echo "Error: golden image root '$GOLDEN_ROOT' does not exist"
    exit 1
fi

if [ ! -f "$DEB_FILE" ]; then
    echo "Error: deb file '$DEB_FILE' does not exist"
    exit 1
fi

echo "==> Mounting pseudo-filesystems..."
sudo mount --bind /dev      "$GOLDEN_ROOT/dev"
sudo mount --bind /dev/pts  "$GOLDEN_ROOT/dev/pts"
sudo mount --bind /proc     "$GOLDEN_ROOT/proc"
sudo mount --bind /sys      "$GOLDEN_ROOT/sys"

echo "==> Bind-mounting debugfs, tracefs, and bpffs..."
sudo mkdir -p "$GOLDEN_ROOT/sys/kernel/debug" \
             "$GOLDEN_ROOT/sys/kernel/tracing" \
             "$GOLDEN_ROOT/sys/fs/bpf"

sudo mount --bind /sys/kernel/debug   "$GOLDEN_ROOT/sys/kernel/debug"   || true
sudo mount --bind /sys/kernel/tracing "$GOLDEN_ROOT/sys/kernel/tracing" || true
sudo mount --bind /sys/fs/bpf         "$GOLDEN_ROOT/sys/fs/bpf"         || true

echo "==> Copying host resolv.conf..."
sudo cp /etc/resolv.conf "$GOLDEN_ROOT/etc/resolv.conf"

echo "==> Creating modprobe stub to skip kernel module load..."
sudo tee "$GOLDEN_ROOT/sbin/modprobe" >/dev/null <<'EOF'
#!/bin/sh
exit 0
EOF
sudo chmod +x "$GOLDEN_ROOT/sbin/modprobe"

echo "==> Creating policy-rc.d to prevent service start..."
sudo tee "$GOLDEN_ROOT/usr/sbin/policy-rc.d" >/dev/null <<'EOF'
#!/bin/sh
exit 101
EOF
sudo chmod +x "$GOLDEN_ROOT/usr/sbin/policy-rc.d"

echo "==> Copying AMD uProf deb into chroot..."
sudo mkdir -p "$GOLDEN_ROOT/opt/assets"
sudo cp "$DEB_FILE" "$GOLDEN_ROOT/opt/assets/"

echo "==> Entering chroot to install AMD uProf..."
sudo chroot "$GOLDEN_ROOT" /bin/bash -c "
set -e
dpkg -i /opt/assets/$(basename "$DEB_FILE") || true
apt-get -f -y install
"

echo "==> Cleaning up stubs..."
sudo rm -f "$GOLDEN_ROOT/sbin/modprobe"
sudo rm -f "$GOLDEN_ROOT/usr/sbin/policy-rc.d"

echo "==> Unmounting debugfs, tracefs, and bpffs..."
sudo umount "$GOLDEN_ROOT/sys/kernel/debug"   || true
sudo umount "$GOLDEN_ROOT/sys/kernel/tracing" || true
sudo umount "$GOLDEN_ROOT/sys/fs/bpf"         || true

echo "==> Unmounting pseudo-filesystems..."
sudo umount "$GOLDEN_ROOT/dev/pts" || true
sudo umount "$GOLDEN_ROOT/dev"     || true
sudo umount "$GOLDEN_ROOT/proc"    || true
sudo umount "$GOLDEN_ROOT/sys"     || true

echo "==> AMD uProf installation in chroot complete."
echo "Note: Kernel module and BPF support must be enabled on first boot:"
echo "      sudo mount -t debugfs none /sys/kernel/debug"
echo "      sudo mount -t tracefs none /sys/kernel/tracing"
echo "      sudo mount -t bpf none /sys/fs/bpf"
echo "      sudo modprobe AMDPowerProfiler"
echo "      sudo dpkg --configure amduprof"