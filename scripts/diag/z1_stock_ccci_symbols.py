#!/usr/bin/env python3
"""Extract selected CCCI evidence from the known Z1 stock ARM kernel."""
import argparse
import hashlib
from pathlib import Path
import struct


STOCK_SHA256 = '3128816c91314be52586f45a592a1f642c8a80baabae3c08d19b451c755b2500'
STOCK_VMA = 0xc0008000
SYMBOLS = {
    'get_ccif_hw_info', 'ccif_create_instance', '__ccif_v1_reg_intr',
    '__ccif_irq_handler', 'request_threaded_irq', 'mt_map_io', 'iotable_init',
    'ccci_md_mem_reserve', 'arm_memblock_steal', 'get_md_mem_start_addr',
    'get_md_smem_layout', 'set_md_smem_remap', 'set_md_rom_rw_mem_remap',
    'md_power_on', 'md_power_off', 'md_wdt_isr',
}


def read_symbols(data):
    # File offsets of the old kallsyms arrays in this exact kernel build.
    address_table = 0x71e060
    count_offset = 0x751fb0
    names_offset = 0x751fc0
    markers_offset = 0x7e3610
    token_table = 0x7e3950
    token_index = 0x7e3d00
    count, = struct.unpack_from('<I', data, count_offset)
    addresses = struct.unpack_from(f'<{count}I', data, address_table)
    markers = struct.unpack_from(f'<{(count + 255) // 256}I', data, markers_offset)
    offsets = struct.unpack_from('<256H', data, token_index)
    tokens = [data[token_table + offset:data.index(0, token_table + offset)]
              for offset in offsets]
    cursor = names_offset
    for index, address in enumerate(addresses):
        if index % 256 == 0 and cursor - names_offset != markers[index // 256]:
            raise ValueError(f'Invalid kallsyms marker at symbol {index}')
        length = data[cursor]
        cursor += 1
        name = b''.join(tokens[token] for token in data[cursor:cursor + length])
        yield name[1:].decode('ascii'), address
        cursor += length
    if cursor != markers_offset:
        raise ValueError('Invalid end of kallsyms names')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kernel', type=Path, help='Decompressed stock kernel binary')
    args = parser.parse_args()
    data = args.kernel.read_bytes()
    if hashlib.sha256(data).hexdigest() != STOCK_SHA256:
        parser.error('kernel SHA-256 does not match the Z1 stock build')
    symbols = {name: address for name, address in read_symbols(data) if name in SYMBOLS}
    if symbols.keys() != SYMBOLS:
        parser.error('required CCCI symbols are missing')
    for name, address in sorted(symbols.items(), key=lambda item: item[1]):
        print(f'{address:#010x} {name}')
    # ARM map_desc entry used by mt_map_io: virtual, PFN, length, type.
    mapping = struct.unpack_from('<4I', data, 0xc0938120 - STOCK_VMA)
    virtual, pfn, length, _ = mapping
    ccif_virtual = 0xf000c000  # get_ccif_hw_info(), MD1 case
    if not virtual <= ccif_virtual < virtual + length:
        parser.error('stock CCIF virtual address lies outside the expected mapping')
    physical = (pfn << 12) + ccif_virtual - virtual
    print(f'AP CCIF: virtual={ccif_virtual:#010x} physical={physical:#010x}')


if __name__ == '__main__':
    main()
