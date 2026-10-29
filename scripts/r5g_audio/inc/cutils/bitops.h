#pragma once
/* Minimal stand-in for Android's cutils/bitops.h.
 * AOSP's version defines popcount() as a wrapper over the compiler builtin;
 * audio.h uses popcount() for the single-device mask checks. */
#include <stdint.h>
static inline int popcount(uint32_t x) { return __builtin_popcount(x); }
