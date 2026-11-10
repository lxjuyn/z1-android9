#!/usr/bin/env python3
"""Offline, SHA-pinned STA-only repair of Z1's donor libwifi-hal.so.

AOSP android-9.0.0_r61 contracts (official source paths):
  frameworks/opt/net/wifi/libwifi_hal/wifi_hal_common.cpp:200-209:
    wifi_get_fw_path(mode) returns a configured path, or NULL.
  frameworks/opt/net/wifi/libwifi_hal/driver_tool.cpp:59-73:
    NULL means no reload needed; ChangeFirmwareMode returns true.
  hardware/interfaces/wifi/1.2/default/wifi_chip.cpp:1085-1098:
    failed firmware switching aborts chip configuration before legacy HAL start.

This exact donor has bcmdhd STA/AP paths and opens dhd/parameters/firmware_path.
MTK firmware startup uses the existing WMT/kernel path. Recorded failures:
mainline_recon/z1_boot_20261109_1838.log, LF lines 30053-30054. Repeated z1warn
output is replay, not independent attempts. This fix removes that one STA
blocker; it does not establish association, DHCP, DNS or Settings connectivity.

Replace ONLY the 24-byte wifi_get_fw_path function, in its existing extent:
  subs r1,r0,#1; cmp r1,#1; bhi return_null
  ldr r1,table_delta; add r1,pc; ldr.w r0,[r1,r0,lsl#2]; bx lr
  return_null: movs r0,#0; bx lr
  table_delta: .word 0x8b60
Unsigned (mode-1)>1 selects STA=0 and invalid modes. AP=1 and P2P=2 retain
exact original table entries. Preserve ChangeFirmwareMode, IsFirmwareMode-
ChangeNeeded, the table, packed Android relocations, imports and ELF layout.

Do NOT use the rejected unconditional branches at 0x555e: E006 skips the
return-true assignment; E007 would also bypass AP errors. Neither is shipped.
No image/device/service operations; input is never overwritten. Generated
prebuilt files are local artifacts, not source files to commit.
"""
import argparse
import hashlib
import os
from pathlib import Path
import stat
import struct
import tempfile

ORIGINAL_SHA256 = "0a9ba41186680851eb559224c01b9c5231c6cea748c4cfa18424a0c2116b46f7"
PATCHED_SHA256 = "bc7e05250c6849e883d66856470e185e5cb61e71cd56675c01fd441cd9493088"
ORIGINAL_SIZE = 95116
FUNC_VADDR = 0xDF38
FUNC_SIZE = 24
ORIGINAL_BYTES = bytes.fromhex("022884bf002070470249794451f82000704700bf5e8b0000")
PATCHED_BYTES = bytes.fromhex("411e012904d80349794451f82000704700207047608b0000")
SYMBOL = b"wifi_get_fw_path"
STA_SYMBOL = b"_ZN7android8wifi_hal10DriverTool16kFirmwareModeStaE"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def elf_layout(data):
    require(len(data) >= 52, "truncated ELF header")
    require(data[:7] == b"\x7fELF\x01\x01\x01", "expected ELF32 little-endian version 1")
    hdr = struct.unpack_from("<HHIIIIIHHHHHH", data, 16)
    etype, machine, version, _, phoff, shoff, _, ehsize, phsize, phnum, shsize, shnum, _ = hdr
    require((etype, machine, version, ehsize) == (3, 40, 1, 52), "expected ARM ET_DYN ELF32")
    require(phsize == 32 and phnum > 0 and phoff + phsize * phnum <= len(data),
            "invalid program header table")
    require(shsize == 40 and shnum > 0 and shoff + shsize * shnum <= len(data),
            "invalid section header table")
    segments = [struct.unpack_from("<8I", data, phoff + i * phsize) for i in range(phnum)]
    sections = [struct.unpack_from("<10I", data, shoff + i * shsize) for i in range(shnum)]
    for kind, offset, _, _, filesz, memsz, _, _ in segments:
        if kind == 1:
            require(filesz <= memsz and offset + filesz <= len(data), "invalid PT_LOAD extent")
    for section in sections:
        if section[1] != 8:  # SHT_NOBITS has no file payload.
            require(section[4] + section[5] <= len(data), "invalid section extent")
    return segments, sections


def vaddr_to_file_offset(data, address, size=1, executable=False):
    segments, _ = elf_layout(data)
    found = []
    for kind, offset, va, _, filesz, _, flags, _ in segments:
        if kind == 1 and va <= address and address + size <= va + filesz:
            require(not executable or flags & 1, "function is not in executable PT_LOAD")
            found.append(offset + address - va)
    require(len(found) == 1, "address has no unique file-backed PT_LOAD")
    return found[0]


def dynsymbol(data, name):
    _, sections = elf_layout(data)
    found = []
    for section in sections:
        if section[1] != 11:  # SHT_DYNSYM
            continue
        _, _, _, _, offset, size, link, _, _, entsize = section
        require(entsize == 16 and size % entsize == 0 and link < len(sections),
                "invalid dynamic symbol table")
        strings = sections[link]
        require(strings[1] == 3, "invalid dynamic string table")
        table = data[strings[4]:strings[4] + strings[5]]
        for pos in range(offset, offset + size, entsize):
            entry = struct.unpack_from("<IIIBBH", data, pos)
            require(entry[0] < len(table), "invalid symbol name offset")
            end = table.find(b"\x00", entry[0])
            require(end >= 0, "unterminated symbol name")
            if table[entry[0]:end] == name:
                found.append(entry)
    require(len(found) == 1, "expected one exported symbol: " + name.decode())
    return found[0]


def validate_elf(data):
    symbol = dynsymbol(data, SYMBOL)
    require(symbol[1:3] == (FUNC_VADDR | 1, FUNC_SIZE) and symbol[3] & 15 == 2
            and symbol[5] != 0, "unexpected Thumb function symbol/size")
    offset = vaddr_to_file_offset(data, FUNC_VADDR, FUNC_SIZE, executable=True)
    require(offset == 0xCF38, "unexpected wifi_get_fw_path file offset")
    sta = dynsymbol(data, STA_SYMBOL)
    require(sta[2] == 4, "unexpected STA constant size")
    sta_offset = vaddr_to_file_offset(data, sta[1], 4)
    require(struct.unpack_from("<I", data, sta_offset)[0] == 0, "STA mode is not zero")
    return offset


def patch_bytes(data):
    require(len(data) == ORIGINAL_SIZE, "unexpected input size")
    require(sha256(data) == ORIGINAL_SHA256, "unexpected input SHA-256; refusing unknown/patched build")
    offset = validate_elf(data)
    require(data[offset:offset + FUNC_SIZE] == ORIGINAL_BYTES, "unexpected function bytes")
    require(data.count(ORIGINAL_BYTES) == 1, "function signature is not unique")
    result = data[:offset] + PATCHED_BYTES + data[offset + FUNC_SIZE:]
    require(len(result) == len(data) and sha256(result) == PATCHED_SHA256,
            "unexpected candidate digest/extent")
    validate_elf(result)
    return result


def write_candidate(source, output, force=False):
    source, output = Path(source), Path(output)
    require(source.is_file(), "input is not a regular file")
    require(not output.is_symlink(), "refusing symlink output")
    require(source.resolve() != output.resolve(), "input and output must differ")
    if output.exists():
        require(not os.path.samefile(source, output), "input and output refer to same inode")
        require(output.is_file() and force, "output exists; use --force only after backup")
    data = source.read_bytes()
    candidate = patch_bytes(data)  # All validation before creating any output.
    fd, tmp = tempfile.mkstemp(prefix=".libwifi-hal.", dir=str(output.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(candidate)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
        if force:
            os.replace(tmp, output)
        else:
            os.link(tmp, output)  # Atomic create; never replace a racing output.
        require(sha256(output.read_bytes()) == PATCHED_SHA256, "output read-back failed")
        require(source.read_bytes() == data, "input changed during operation")
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--force", action="store_true", help="replace output only after backing it up")
    args = parser.parse_args()
    try:
        candidate = write_candidate(args.input, args.output, args.force)
    except (OSError, ValueError, struct.error) as exc:
        parser.exit(2, "ERROR: %s\n" % exc)
    print("input sha256  = " + ORIGINAL_SHA256)
    print("output sha256 = " + sha256(candidate))
    print("function      = wifi_get_fw_path @ 0xdf38; 24-byte extent at file 0xcf38")
    print("behavior      = STA returns NULL; AP/P2P/invalid modes unchanged")
    print("output        = " + str(Path(args.output).resolve()))


if __name__ == "__main__":
    main()
