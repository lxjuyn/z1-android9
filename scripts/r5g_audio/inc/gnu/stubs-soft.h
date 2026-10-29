/*
 * stubs-soft.h — 本目录私有桩, 供 arm-linux-gnueabihf-gcc 以 -mfloat-abi=softfp
 * 编译时喂给 Ubuntu armhf glibc 的 gnu/stubs.h (它只带 stubs-hard.h)。
 * 内容为空是安全的: stubs 头只是 __stub_* 咨询宏列表, armhf glibc 所有标准函数
 * 都有实现, 不存在需要标记的桩函数。搜索顺序 -I inc/ 在 glibc 系统头之前。
 */
