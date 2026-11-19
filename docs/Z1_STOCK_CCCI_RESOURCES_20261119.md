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
| GIC SPI `75`, level low | `get_ccif_hw_info` writes legacy IRQ `107` at structure offset 8. `ccif_create_instance` copies it to offset 20 of the runtime object. `__ccif_v1_reg_intr` passes that IRQ and flag `0x8` (`IRQF_TRIGGER_LOW`) to `request_threaded_irq`. The stock `mt_irq_unmask` at `0xc0019ab4` uses the IRQ number directly to select the bit in the GIC distributor enable array at `0xf0211100`. INTID 107 corresponds to SPI 75. The mainline DTS routes peripheral IRQs through `sysirq`, which handles active-low polarity. |
| MD1 watchdog MMIO `0x20050000`, GIC SPI `120`, falling edge | `md_init` at `0xc0546d38` maps 64 bytes at `0x20050000` and registers `MD-WDT` with legacy IRQ 152 and flag `0x2` (`IRQF_TRIGGER_FALLING`). The handler literal resolves to `md_wdt_isr`, which checks for IRQ 152. `recover_md_wdt_irq` reads watchdog status at offset `0x0c`. |
| Modem allocation alignment `0x02000000` | `ccci_md_mem_reserve` at `0xc003c840` passes the computed size in `r0` and 32 MiB alignment in `r1` to `arm_memblock_steal`. The return value supplies the start address. |

`parse_ccci_dfo_setting` at `0xc003c898` reads `MTK_ENABLE_MD1`, `MD1_SIZE`
and `MD1_SMEM_SIZE`. It rounds both sizes up to 2 MiB boundaries. With MD1
enabled and either size missing, the error branch at `0xc003ca28` installs
22 MiB for the modem and 2 MiB for shared memory. The compiled table at
`0xc0960d58` initializes the enable flag and both sizes to zero. The fallback
therefore provides a candidate size conditional on the boot-time configuration.
Capture the factory DFO and reservation logs to learn the actual size and base.

The factory firmware SHA-256 starts with `5264c9c2` and its final 188 bytes
contain a version 2 `CHECK_HEADER` for `MT6572_S00` bound to MD1. The memory
size at header offset `0xac` is `0x00800000` (8 MiB). In `check_md_header`,
`get_resv_mem_size_for_md` supplies the live reservation for comparison.
The larger reservation branch at `0xc054485c` emits a DFO mismatch warning
and accepts that size check. The header's 8 MiB is the firmware requirement,
while the 22 MiB fallback is a kernel allocation rule. The stock kernel has separate hooks for shared-memory remap,
firmware memory remap, watchdog, and power. Those hooks and the firmware
verification sequence must be recovered before attempting modem power-on.
