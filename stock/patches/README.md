# Stock-kernel S3 diagnostic patch

`0001-stock-disable-healthd-restart-on-servicemanager-restart.patch` removes
only the `onrestart restart healthd` action from the stock servicemanager init
file. Apply it at the Android 9 source-tree root with `patch -p1` before
building a new stock system image.

The matching S3 diagnostic candidate is derived from the archived S2 system
image. It retains the S2 `disabled` option on `/system/bin/healthd`, then
removes this single restart hook. This closes the discovered init path where a
servicemanager restart could explicitly start disabled healthd. The other
servicemanager restart actions and the `critical` options remain unchanged.

This change does not restore `/dev/hwbinder` or the standard Health HIDL
service. The existing system-server sysfs battery fallback remains the only
framework battery path in this candidate. Runtime boot, battery readings, and
all hardware features are unverified; treat S3 as diagnostic only.

The paired S3 images and static validation are maintained in the external
candidate archive under the `A9_STOCK_S3_20261005` label; no image binaries are
stored in this source repository.

## 0002: create WMT Wi-Fi, control and Bluetooth nodes with `exec`

`0002-stock-create-wmtwifi-node.patch` records the source delta for
`stock/init.z1.rc` against its pre-fix version. It can be applied to that
version from the Z1 repository root with `patch -p1`. The regular build does
not apply files from `stock/patches`: its active input is `stock/init.z1.rc`
itself (see `device.mk` and `scripts/z1_make_stock_android9.py` below).

Android 9 init has no `mknod` builtin. The patch replaces the `/dev/wmtWifi`,
`/dev/stpwmt` and `/dev/stpbt` builtin lines with guarded `exec` commands.
Each command checks whether its path is already a character device, then
invokes `/system/bin/mknod` through `/system/bin/sh` only if it is not already
one. Toybox's Android 9 `mknod` applet supports `-m MODE`; the existing
following `chmod` and `chown` actions set final permissions and ownership:
`/dev/wmtWifi` is `0660 system:system`, `/dev/stpwmt` is `0660 system:system`,
and `/dev/stpbt` is `0660 bluetooth:radio`. `stock/ueventd.z1.rc` already has
matching rules for all three nodes; those rules set permissions but do not
create the nodes.

The S1R serial capture recorded `Invalid keyword 'mknod'` for the node actions.
Android 9 init source documents and registers `exec`; its tokenizer keeps the
quoted shell body as one argument, and the `exec` parser passes arguments
after `--` to the child command. The Android 9 Toybox source enables `mknod`
and documents `-m MODE`. The factory init profile explicitly creates
`/dev/wmtWifi` and `/dev/stpwmt`; their ueventd entries set mode and owner but
do not themselves create nodes. The WMT driver uses character major/minor
`190:0`, and the factory launcher opens `/dev/stpwmt` as its firmware/control
channel. The captured Z1 boot shows the WLAN driver registering character
major 153, but there is no runtime listing of these three nodes from this
candidate.

This is a node-creation fix only. It does not start or enable Wi-Fi or
Bluetooth, write to any of the device nodes, add a Wi-Fi property producer, or
change the Bluetooth HCI path. Missing `/dev/stpwmt` can prevent the combo WMT
firmware/control transport from starting, so this parser fix matters for
radio bring-up even though it is not an Android boot-fatal error. The S3
profile uses a diagnostic permissive SELinux policy. These `mknod` calls have
not been made safe for enforcing mode; no SELinux policy or file-context
changes are included. Do not treat this as proof that either radio works.

After a separate image is reviewed and booted, the node-only checks are: no
`Invalid keyword 'mknod'` for the three modified paths; `ls -lZ` shows
character devices `/dev/wmtWifi` (`153,0`), `/dev/stpwmt` (`190,0`) and
`/dev/stpbt` (`192,0`), each at mode `0660`, with owners/groups
`system:system`, `system:system` and `bluetooth:radio`; and init continues
past `post-fs`. Do not write to any node during this check; that can trigger
radio hardware activity. No image has been built or uploaded from this source
change.

## Stock source packaging path

For the Android product build, the `Z1_KERNEL_VARIANT=stock` branch in
`device.mk` copies `stock/init.z1.rc` to `root/init.z1.rc` and
`stock/ueventd.z1.rc` to `root/ueventd.z1.rc`. The standalone stock boot
repacker `scripts/z1_make_stock_android9.py` reads those same source files and
embeds the init file as `init.z1.rc` and the ueventd rules as
`ueventd.mt6572.rc` in the generated ramdisk. The root-level
`scripts/apply_stock_patches.py` uses `stock_patches/manifest.json`; it does
not read or apply this `stock/patches` series.

## 0004: restore factory WLAN function-on/off actions

`0004-stock-toggle-wifi-on-driver-status.patch` adds the two property actions
from the factory Z1 `init.mt6572.rc`: `wlan.driver.status=ok` writes `1` to
`/dev/wmtWifi`, and `wlan.driver.status=unloaded` writes `0`. Android 9's
`libwifi_hal` uses the same property name and sets these values from its
`wifi_load_driver()` and `wifi_unload_driver()` paths. The actions are separate
from `post-fs`. A local `sys.z1.wmt_nodes_ready` condition re-evaluates an
early status transition after node setup. Each action also checks that
`/dev/wmtWifi` is a character device before opening it, since Android 9's init
`write` builtin creates a regular file when a target is absent.

This restores only the factory low-level WLAN function switch. It does not
provide `wificond`'s missing nl80211 protocol feature support, the absent
supplicant HIDL service, or prove that Wi-Fi associates. The candidate's
permissive diagnostic policy remains required for runtime evaluation; this
patch adds no SELinux rules and makes no enforcing-mode claim. The existing
`/dev/stpwmt` and `/dev/stpbt` repairs are unchanged. No Bluetooth init change
is justified by the source/runtime evidence in this patch, and no calibration,
MAC, or NVRAM data is added. This source change has not been built into an
image.

Parser review against Android 9 `system/core/init` confirms the syntax: the
`on` parser accepts multiple `property:name=value` triggers joined with `&&`,
and reevaluates the action when either property changes. Its `exec` parser
passes the shell command after `--` as child arguments. The Android shell's
`printf` builtin emits the one-byte control value without a newline; `[ -c ]`
ensures the redirection only opens an existing character device. The parser
and command syntax are defined in `action_parser.cpp`, `builtins.cpp`,
`system/core/init/README.md`, and Android's `mksh` source. After a reviewed
image boots, runtime validation must still confirm that each property
transition reaches the node; do not manually write to `/dev/wmtWifi` during
this check.
