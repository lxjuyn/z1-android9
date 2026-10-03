# Stock LOS16 patch series

This directory records the Z1 stock-kernel compatibility changes relative to the pinned LineageOS 16.0 source manifest. `manifest.json` contains each source path and the expected pre/post SHA-256. Apply with `scripts/apply_stock_patches.py` from the source workspace root. The S1I AlarmManager fix is kept in its own later commit.
Stock 3.4.67 has no hwbinder context. The `hidl_memory` allocator service is
kept installed for dependency checks but marked `disabled` so init does not
try to launch its HIDL server on the stock-kernel profile.
