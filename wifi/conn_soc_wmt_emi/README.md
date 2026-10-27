# conn_soc WMT EMI 契约核查与重编产物（2026-10-28）

上游：https://github.com/gabin8/mt6572-connsys-modules（main，2026-10-28 clone）。
上游 conn_soc 全量源码树 8/12 归档（products_connsys_modules_20260812_022121）已被清理，
本目录是重建编译树的源码底座：clone 后把本目录四个 .c/.h 覆盖同名文件即得
"A 版"（上游原样 EMI + wakeup 兼容）构建树。

## 契约结论（一句话）

上游 EMI 基址就是硬编码 0x80080000（mtk_wcn_consys_hw.h:166），不是移植抄错；
上游配套是 dts reserved-memory no-map 该窗（mtk_wcn_consys_hw.c:604-608
"Reserved memory by device tree!"）。Z1 板上 "EMI mapping fail" 的根因是缺
no-map 配套，不是基址错。审计 A8-4 选项 (b)（改模块到 0xbe080000）在上游
无依据，且片上无 remap 寄存器时存在 AP/FW 共享窗错位风险——B 版 .ko 仅作为
主会话的裁决候选编出归档，不是推荐路线。

## 本目录文件

- `wmt_plat_alps.c` / `osal.c` / `osal.h`：A 版构建树用的覆盖文件
  （相对上游的 diff = compat_wakeup_*.diff 三份；复刻在役 .ko 的 wakeup 指针适配，
  反汇编对照证据见 BUILD.txt）。
- `mtk_wcn_consys_hw.h`：上游原样（EMI 0x80080000）。B 版 = 对本文件打
  emi_base_0xbe080000.diff（166 行单宏）。
- `CONTRACT_EXCERPT_gabin8.md`：上游契约摘录（行号可复核）。
- `BUILD.txt`：构建/复证/裁决矩阵全文（同 bridge_backups 归档）。

## 重编配方（KDIR=fork_linux，产物 vermagic 7.0.0-rc7-z1+ SMP mod_unload ARMv7 p2v8）

    git clone --depth 1 https://github.com/gabin8/mt6572-connsys-modules.git
    cp wmt_plat_alps.c  <clone>/conn_soc/mt6572/
    cp osal.c           <clone>/conn_soc/linux/pub/
    cp osal.h           <clone>/conn_soc/linux/include/
    # B 版再打：patch <clone>/conn_soc/mt6572/include/mtk_wcn_consys_hw.h < emi_base_0xbe080000.diff
    make -C fork_linux M=<clone>/conn_soc ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- \
      KBUILD_EXTRA_SYMBOLS=fork_linux/net/wireless/Module.symvers KBUILD_MODPOST_WARN=1 modules

成品归档：bridge_backups/products/products_wmt_emi_20261028_005306/
（A/B 两枚 mtk_stp_wmt_soc.ko + SHA256SUMS；均未上板验证，等 R4 WMT 日志裁决换入。）
