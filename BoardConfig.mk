DEVICE_PATH := device/jiabo/z1

# MT6572 uses ARM32 userspace. Modern mainline Binder exposes protocol 8
# (64-bit transfer fields), which Pie supports independently of CPU bitness.
ifeq ($(Z1_KERNEL_VARIANT),stock)
# The exact A7/factory 3.4.67 kernel exposes protocol 7 and 32-bit fields.
TARGET_USES_64_BIT_BINDER := false
else
TARGET_USES_64_BIT_BINDER := true
endif
TARGET_ARCH := arm
TARGET_ARCH_VARIANT := armv7-a-neon
TARGET_CPU_ABI := armeabi-v7a
TARGET_CPU_ABI2 := armeabi
TARGET_CPU_VARIANT := cortex-a7
TARGET_BOARD_PLATFORM := mt6572
TARGET_BOOTLOADER_BOARD_NAME := z1
TARGET_NO_BOOTLOADER := true

# The mainline kernel and appended DTB are built outside the LOS tree. Keep
# LOS responsible for ramdisk.img and system.img only; the project packer
# combines them into the actual MTK boot image after fixing DT initrd-end.
TARGET_NO_KERNEL := true

# Legacy ramdisk boot, with /system mounted separately by init.z1.rc.
BOARD_BUILD_SYSTEM_ROOT_IMAGE := false
BOARD_KERNEL_BASE := 0x10000000
BOARD_KERNEL_PAGESIZE := 2048
BOARD_KERNEL_CMDLINE := console=tty0 console=ttyS0,921600n8 root=/dev/ram panic=10 loglevel=7 clk_ignore_unused regulator_ignore_unused oops=panic sysrq_always_enabled rdinit=/init earlycon=mtk8250,0x11005000 androidboot.hardware=z1
BOARD_MKBOOTIMG_ARGS := --kernel_offset 0x8000 --ramdisk_offset 0x1000000

# The boot header command line is ignored by this LK. Keep the same parameters
# in the DT chosen/bootargs; recalculate linux,initrd-end for every ramdisk.
# LOS mkbootimg cannot emit the required MTK kernel/rootfs headers. Its default
# boot.img is a build artifact only: use the project MTK packer before flashing.
BOARD_BOOTIMAGE_PARTITION_SIZE := 6291456
BOARD_RECOVERYIMAGE_PARTITION_SIZE := 6291456
BOARD_FLASH_BLOCK_SIZE := 131072

# p4 (ANDROID) and p5 (CACHE) match the factory scatter and board logs.
TARGET_USERIMAGES_USE_EXT4 := true
BOARD_SYSTEMIMAGE_FILE_SYSTEM_TYPE := ext4
BOARD_CACHEIMAGE_FILE_SYSTEM_TYPE := ext4
BOARD_SYSTEMIMAGE_PARTITION_SIZE := 943718400
BOARD_CACHEIMAGE_PARTITION_SIZE := 236978176

# TODO: Set BOARD_USERDATAIMAGE_PARTITION_SIZE only after measuring p6's
# effective, EOD-truncated size on this unit. Factory scatter and MBR/EBR
# disagree about its start; never infer a flashable size from the sentinel.

TARGET_RECOVERY_FSTAB := $(DEVICE_PATH)/rootdir/fstab.z1

USE_XML_AUDIO_POLICY_CONF := 1
ifeq ($(Z1_KERNEL_VARIANT),stock)
DEVICE_MANIFEST_FILE := $(DEVICE_PATH)/stock/manifest.xml
else
DEVICE_MANIFEST_FILE := $(DEVICE_PATH)/rootdir/manifest.xml
endif

# Non-Treble: no BOARD_VNDK_VERSION, independent vendor image, or
# TARGET_COPY_OUT_VENDOR override. Pie will use system/vendor.
# TODO: Add only audited 32-bit HALs, VINTF declarations, sepolicy, and
# display implementation after the relevant hardware gate passes.

# Health HAL board suffix uses the standard health domain transition.
BOARD_SEPOLICY_DIRS += $(DEVICE_PATH)/sepolicy/vendor
