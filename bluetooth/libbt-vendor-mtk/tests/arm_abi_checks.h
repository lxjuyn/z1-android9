/* Compile-only AOSP 9 32-bit ABI checks; no production behavior added. */
#include <stddef.h>
#include <bt_vendor_lib.h>
_Static_assert(sizeof(void *) == 4, "ARM API28 must use 32-bit pointers");
_Static_assert(sizeof(bt_vendor_interface_t) == 16, "vendor interface ABI");
_Static_assert(sizeof(bt_vendor_callbacks_t) == 40, "AOSP9 callback ABI");
_Static_assert(offsetof(bt_vendor_callbacks_t, fwcfg_cb) == 4, "FW callback offset");
_Static_assert(offsetof(bt_vendor_callbacks_t, audio_state_cb) == 16, "audio callback offset");
_Static_assert(offsetof(bt_vendor_callbacks_t, alloc) == 20, "alloc callback offset");
_Static_assert(offsetof(bt_vendor_callbacks_t, epilog_cb) == 32, "epilog callback offset");
_Static_assert(offsetof(bt_vendor_callbacks_t, a2dp_offload_cb) == 36, "A2DP callback offset");
_Static_assert(CH_MAX == 4, "HCI channel count");
