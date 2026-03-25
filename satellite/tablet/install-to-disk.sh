#!/usr/bin/env bash
set -euo pipefail

# ── Colors ────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${BLUE}ℹ${NC}  $*"; }
ok()    { echo -e "${GREEN}✔${NC}  $*"; }
warn()  { echo -e "${YELLOW}⚠${NC}  $*"; }
err()   { echo -e "${RED}✖${NC}  $*"; }
step()  { echo -e "\n${BOLD}── $* ──${NC}"; }

echo -e "${BLUE}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  Atlas Tablet OS — Install to Disk            ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════╝${NC}"

# Must be root
if [ "$EUID" -ne 0 ]; then
    err "Run as root: sudo install-to-disk"
    exit 1
fi

# ── 1. Detect internal drive ──────────────────────────────────
step "Detecting internal storage"

# Find the internal drive (exclude USB, loop, ram)
# Surface Go uses /dev/nvme0n1 or /dev/mmcblk0
CANDIDATES=()
for disk in /dev/nvme0n1 /dev/mmcblk0 /dev/sda; do
    if [ -b "$disk" ]; then
        # Skip if this is the USB we booted from
        BOOT_DEV=$(findmnt -n -o SOURCE / 2>/dev/null | sed 's/p[0-9]*$//' | sed 's/[0-9]*$//')
        if [ "$disk" != "$BOOT_DEV" ]; then
            SIZE=$(lsblk -b -n -d -o SIZE "$disk" 2>/dev/null)
            SIZE_GB=$(( ${SIZE:-0} / 1024 / 1024 / 1024 ))
            CANDIDATES+=("$disk (${SIZE_GB}GB)")
        fi
    fi
done

if [ ${#CANDIDATES[@]} -eq 0 ]; then
    err "No internal drive found!"
    err "This installer expects an NVMe, eMMC, or SATA drive."
    exit 1
fi

echo ""
echo "  Available drives:"
for i in "${!CANDIDATES[@]}"; do
    echo "    $((i+1)). ${CANDIDATES[$i]}"
done
echo ""

if [ ${#CANDIDATES[@]} -eq 1 ]; then
    TARGET_DISK=$(echo "${CANDIDATES[0]}" | cut -d' ' -f1)
    info "Only one drive found: $TARGET_DISK"
else
    read -rp "  Select drive [1]: " CHOICE
    CHOICE=${CHOICE:-1}
    IDX=$((CHOICE - 1))
    TARGET_DISK=$(echo "${CANDIDATES[$IDX]}" | cut -d' ' -f1)
fi

# ── 2. Confirm ────────────────────────────────────────────────
step "Confirmation"

DISK_SIZE=$(lsblk -b -n -d -o SIZE "$TARGET_DISK" 2>/dev/null)
DISK_SIZE_GB=$(( ${DISK_SIZE:-0} / 1024 / 1024 / 1024 ))
DISK_MODEL=$(lsblk -n -d -o MODEL "$TARGET_DISK" 2>/dev/null | xargs)

echo ""
echo -e "  ${RED}⚠  WARNING: This will ERASE ALL DATA on:${NC}"
echo ""
echo "      Drive: $TARGET_DISK"
echo "      Size:  ${DISK_SIZE_GB}GB"
echo "      Model: ${DISK_MODEL:-Unknown}"
echo ""
echo -e "  ${RED}All existing data (including Windows) will be permanently deleted.${NC}"
echo ""
read -rp "  Type 'YES' to continue: " CONFIRM
if [ "$CONFIRM" != "YES" ]; then
    echo "  Cancelled."
    exit 0
fi

# ── 3. Partition the drive ────────────────────────────────────
step "Partitioning $TARGET_DISK"

# Wipe existing partitions
wipefs -a "$TARGET_DISK" > /dev/null 2>&1

# Create GPT partition table:
# Part 1: 512MB EFI System Partition (FAT32)
# Part 2: Rest → Linux root (ext4)
parted -s "$TARGET_DISK" mklabel gpt
parted -s "$TARGET_DISK" mkpart ESP fat32 1MiB 513MiB
parted -s "$TARGET_DISK" set 1 esp on
parted -s "$TARGET_DISK" mkpart ATLASROOT ext4 513MiB 100%

# Determine partition names (nvme vs sda naming)
if [[ "$TARGET_DISK" == *nvme* ]] || [[ "$TARGET_DISK" == *mmcblk* ]]; then
    EFI_PART="${TARGET_DISK}p1"
    ROOT_PART="${TARGET_DISK}p2"
else
    EFI_PART="${TARGET_DISK}1"
    ROOT_PART="${TARGET_DISK}2"
fi

# Wait for partitions to appear
sleep 2
partprobe "$TARGET_DISK" 2>/dev/null || true
sleep 1

# Format
mkfs.fat -F 32 -n ATLASEFI "$EFI_PART" > /dev/null
mkfs.ext4 -L ATLASROOT -q "$ROOT_PART"

ok "Partitioned: EFI (512MB) + Root (${DISK_SIZE_GB}GB)"

# ── 4. Copy filesystem ───────────────────────────────────────
step "Copying Atlas OS to disk (this takes 2-5 minutes)"

MOUNT_DIR="/mnt/atlas-install"
mkdir -p "$MOUNT_DIR"
mount "$ROOT_PART" "$MOUNT_DIR"
mkdir -p "$MOUNT_DIR/boot/efi"
mount "$EFI_PART" "$MOUNT_DIR/boot/efi"

# Copy the live filesystem to disk
# The live rootfs is mounted as an overlay — copy the lower (squashfs) contents
if [ -d /run/live/rootfs ]; then
    # live-boot puts the squashfs here
    info "Copying from live rootfs..."
    cp -a /run/live/rootfs/filesystem.squashfs/. "$MOUNT_DIR/" 2>/dev/null || \
    rsync -aHAXx --info=progress2 / "$MOUNT_DIR/" \
        --exclude='/proc/*' --exclude='/sys/*' --exclude='/dev/*' \
        --exclude='/run/*' --exclude='/tmp/*' --exclude='/mnt/*' \
        --exclude='/media/*' --exclude='/live/*'
else
    # Fallback: copy the running system
    info "Copying running system..."
    rsync -aHAXx --info=progress2 / "$MOUNT_DIR/" \
        --exclude='/proc/*' --exclude='/sys/*' --exclude='/dev/*' \
        --exclude='/run/*' --exclude='/tmp/*' --exclude='/mnt/*' \
        --exclude='/media/*' --exclude='/live/*' --exclude='/cdrom/*'
fi

# Create empty mountpoints
mkdir -p "$MOUNT_DIR"/{proc,sys,dev,run,tmp,mnt,media}

ok "Filesystem copied"

# ── 5. Configure for installed system ─────────────────────────
step "Configuring installed system"

# fstab
cat > "$MOUNT_DIR/etc/fstab" << FSTAB
# Atlas Tablet OS
LABEL=ATLASROOT  /          ext4  errors=remount-ro,discard  0  1
LABEL=ATLASEFI   /boot/efi  vfat  umask=0077                 0  1
FSTAB

# Hostname
echo "atlas-tablet" > "$MOUNT_DIR/etc/hostname"

ok "System configured"

# ── 6. Install GRUB bootloader ────────────────────────────────
step "Installing GRUB bootloader"

# Mount pseudo-filesystems for chroot
mount --bind /dev     "$MOUNT_DIR/dev"
mount --bind /dev/pts "$MOUNT_DIR/dev/pts"
mount -t proc proc    "$MOUNT_DIR/proc"
mount -t sysfs sys    "$MOUNT_DIR/sys"

# Install GRUB EFI
chroot "$MOUNT_DIR" grub-install --target=x86_64-efi \
    --efi-directory=/boot/efi --bootloader-id=atlas \
    --recheck 2>/dev/null

# Update GRUB config
chroot "$MOUNT_DIR" update-grub 2>/dev/null

# Also install to the fallback EFI path (for Surface UEFI)
mkdir -p "$MOUNT_DIR/boot/efi/EFI/BOOT"
cp "$MOUNT_DIR/boot/efi/EFI/atlas/grubx64.efi" \
   "$MOUNT_DIR/boot/efi/EFI/BOOT/BOOTX64.EFI" 2>/dev/null || true

ok "GRUB installed"

# ── 7. Cleanup ────────────────────────────────────────────────
step "Cleaning up"

umount -lf "$MOUNT_DIR/dev/pts" 2>/dev/null || true
umount -lf "$MOUNT_DIR/dev"     2>/dev/null || true
umount -lf "$MOUNT_DIR/proc"    2>/dev/null || true
umount -lf "$MOUNT_DIR/sys"     2>/dev/null || true
umount -lf "$MOUNT_DIR/boot/efi" 2>/dev/null || true
umount -lf "$MOUNT_DIR"          2>/dev/null || true

sync

# ── Done ──────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  Atlas Tablet OS — Installed! ✓               ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo "  1. Remove the USB drive"
echo "  2. Reboot the tablet"
echo "  3. Atlas will boot from the internal drive"
echo ""
echo "  If it doesn't boot, enter UEFI (Volume Down + Power)"
echo "  and set the boot order to the internal drive."
echo ""
read -rp "  Press Enter to reboot now (or Ctrl+C to stay)..."
reboot
