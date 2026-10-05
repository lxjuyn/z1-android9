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
