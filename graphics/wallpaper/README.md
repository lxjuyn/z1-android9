# Z1 default wallpaper

`z1_default.png` is a 240 x 320, 8-bit RGB (color type 2), solid-color
`#1C2630` PNG. It is a candidate to reduce the amount of image data decoded for
the factory-default wallpaper on a software-rendered display; it is not a claim
of a rendering-performance fix and has not been tested on the device.

`default.mk` supplies the image as `/system/etc/z1_wallpaper.png` and sets
`ro.config.wallpaper` to that path. Include it from the product makefile when
ready; this directory intentionally does not change `device.mk` or init files.
This assumes `LOCAL_PATH` is the device product directory, as in the Z1
`device.mk`.

## Android 9 behavior and limits

AOSP `frameworks/base` `android-9.0.0_r1`, file
`core/java/android/app/WallpaperManager.java`, declares
`PROP_WALLPAPER = "ro.config.wallpaper"` (line 98).
`openDefaultWallpaper()` (lines 1838-1867) reads the system property, opens the
specified existing file, and falls back to
`com.android.internal.R.drawable.default_wallpaper` if the property is empty or
the file cannot be opened. Thus the path and PNG are supported by this Android
version for the system default wallpaper.

Source: https://android.googlesource.com/platform/frameworks/base/+/android-9.0.0_r1/core/java/android/app/WallpaperManager.java

The property does **not** override a saved or user-selected wallpaper.
`WallpaperManager.Globals.peekWallpaperBitmap()` first calls
`getCurrentWallpaperLocked()` (lines 411-427) and returns that bitmap if
present; only when there is no current bitmap and `returnDefault` is true does
it load the default (lines 428-436). The service returns the current wallpaper
for the user. Existing wallpaper data may therefore remain in
`/data/system/users/<userId>/wallpaper` (and associated user-state data),
depending on device state. Preserve user data for upgrades: do not erase `/data`
just to switch the factory default. A factory-reset/new-user installation or an
explicit wallpaper change is needed when testing the default on a device that
already has a saved wallpaper. Lock-screen wallpaper behavior is separate.

No system image, device, flash operation, or git commit is part of this change.
