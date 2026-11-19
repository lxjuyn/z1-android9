# Z1 stock MD1 resources recovered from the 3.4.67 kernel

The factory `j72_z1` system selects one SIM and primary modem MD1. The
original 2G firmware is the system payload `modem_1_2g_n.img`. Mainline still
needs a modem driver and a verified allocation for the firmware and shared
memory.

Run from the workspace root with the decompressed kernel extracted from
`1/boot.img`:

```sh
python3 z1a9_gpt/scripts/diag/z1_stock_ccci_symbols.py out/forensic/stock_kernel.bin
```

The script checks the original kernel SHA-256 and decodes its compressed
kallsyms table. It checks all 208 markers while walking 53,201 symbols.
To reproduce the instruction evidence, disassemble any listed function with
`arm-linux-gnueabihf-objdump -D -b binary -m arm --adjust-vma=0xc0008000`,
using `--start-address` and `--stop-address` to select the function.

| Resource | Factory binary evidence |
| --- | --- |
| AP CCIF physical base `0x1000c000` | `get_ccif_hw_info` at `0xc05442f8` returns `0xf000c000` in its base fields for modem index 0. `mt_map_io` at `0xc090df40` calls `iotable_init` with eight mappings. The fourth maps `0xf0000000` to `0x10000000` for `0x20000` bytes. |
| Legacy Linux IRQ `107` | `get_ccif_hw_info` writes `107` at structure offset 8. `ccif_create_instance` copies it to offset 20 of the runtime object, and `__ccif_v1_reg_intr` passes that value to `request_threaded_irq`. Confirm the GIC SPI translation and trigger type before describing it in DT. |
| Modem allocation alignment `0x02000000` | `ccci_md_mem_reserve` at `0xc003c840` passes the computed size in `r0` and 32 MiB alignment in `r1` to `arm_memblock_steal`. The return value supplies the start address. |

The reservation address and size still need the factory board's boot-time
configuration. The stock kernel has separate hooks for shared-memory remap,
firmware memory remap, watchdog, and power. Those hooks and the firmware
verification sequence must be recovered before attempting modem power-on.
