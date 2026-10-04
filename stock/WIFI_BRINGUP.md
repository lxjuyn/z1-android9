# Z1 MT6572 Wi-Fi bring-up

The stock profile already starts the factory WMT loader and firmware launcher,
creates the MTK radio device nodes, and stages generic WMT/WLAN firmware. The
NL80211 private command library from the public Jiabo Z1 MT6572 Android device
tree is now built into `wpa_supplicant` and `hostapd` for the stock profile.
Its source is dual licensed under GPL-2.0-only or the BSD license; the
original MediaTek notices and upstream README are retained.

The exact Z1 3.4.67 kernel provides the factory WLAN driver. This build wiring
does not prove that the driver registers `wlan0`, supports the nl80211 commands
used by Android 9 `wificond`, or completes association and DHCP. Those checks
require a live device and the original radio calibration kept on that device.

The remaining Android 9 integration blocker is the supplicant HIDL transport:
the stock profile has no `/dev/hwbinder` context, and the upstream supplicant
implementation is a service rather than a passthrough factory. The framework
cannot yet control the supplicant through the stock profile. Keep the Wi-Fi
feature permission disabled until that path is resolved and verified.

Never copy WLAN calibration or NVRAM data into the repository or image. The
factory loader and firmware files are generic; calibration stays in the Z1's
own NVRAM.
