# Stock ramdisk：必需挂载门禁

本目录包含原厂3.4.67路线的mount gate源码、单文件构建脚本、主机验证fixture和init接入。WMT三节点guarded exec原文保留，补丁0002及其说明不变。

## 为什么需要门禁

分区helper失败不取消后续init命令；Pie fs_mgr对wait超时可能只跳过该挂载，并返回NOT_ENCRYPTABLE。独立门禁因此检查实际挂载，不把mount_all返回成功当作充分条件。这是源码确认的条件风险，不是S1R已发生的挂载失败。

`stock/init.z1.rc`的on init先置`sys.z1.stock_mount_ready=0`；on post-fs首部同步exec `/z1_stock_mount_guard`，接着执行Pie支持的`wait_for_prop sys.z1.stock_mount_ready 1`。失败或helper退出不会释放等待。正常事件顺序中它位于WMT shell调用、post-fs-data、zygote-start和nonencrypted/class main之前。

Guard来自本仓`stock/z1_stock_mount_guard.c`：静态ARM32 ELF，无PT_INTERP和Android shared-library依赖，不用/system shell。检查system/cache/data实际ext4根挂载、mountinfo和stat设备号与三个z1-stock块alias一致、vendor解析为/system/vendor；检查app_process/linker/sh为非空可执行ARM32 ELF，boot.art/oat、framework.jar、services.jar为同一system filesystem上的非空文件。仅全部通过才发送Pie property socket SETPROP2并检查成功ack，发布readiness=1。失败写stderr和/dev/kmsg，早期ACM可继续取证。没有格式化、重挂载、猜编号或伪造成功。

## 在本仓重建静态helper

需要bash、Python3及具有ARM hard-float静态libc的`arm-linux-gnueabihf-gcc`。不需要6572工作区源码，不启动Android整构建。

```sh
bash stock/build_mount_guard.sh out/mount_guard/z1_stock_mount_guard
```

可用`Z1_GUARD_CC=/path/to/arm-linux-gnueabihf-gcc`指定编译器；该值必须是单一可执行文件路径，不能附带shell参数。固定参数为-static -Os -Wall -Wextra -Werror -march=armv7-a -mfpu=vfpv3-d16 -mfloat-abi=hard。脚本先编译临时文件，成功后原子替换OUTPUT，编译失败不会覆盖之前输出。

## 本仓最终stock打包接口

`python3 scripts/z1_make_stock_android9.py`默认从本仓源码临时编译guard，验证static ARM及marker，放入最终cpio的`/z1_stock_mount_guard`并确保0755。可选`--mount-guard FILE`复用已生成static ARM guard；报告区分预编译与本仓编译来源，并记录源码与ELF SHA256。预编译marker检查不等于证明任意传入binary匹配当前源码，优先用默认从源编译。

A7/factory原内核boot、LOS gzip-cpio ramdisk、已有静态诊断helper和policy26是外部输入；本仓不包含用户数据、NVRAM或厂商镜像。示例以INPUT_DIR指向你提供的物料：

```sh
python3 scripts/z1_make_stock_android9.py \
  --a7-boot "$INPUT_DIR/a7-boot.img" --factory-boot "$INPUT_DIR/factory-boot.img" \
  --ramdisk "$INPUT_DIR/ramdisk.img" --helper "$INPUT_DIR/z1_bootdiag" \
  --policy "$INPUT_DIR/sepolicy" --output out/stock.boot.img
```

Packer沿用本仓stock board RC、显示脚本和真实factory kernel；guard必须在post-fs第一段，WMT段不被替换。原kernel hash及6MiB限制继续检查。它支持已固定stock import的旧ramdisk输入，不要求修改其它代理的native路径补丁。

**构建接口边界：** `scripts/build_z1.sh`产生LOS目标；普通`m ramdisk/bootimage`不自动调用本MTK packer，也不自动提供guard。最终交付stock boot必须经过上述packer。仅使用带gate的普通LOS ramdisk会停在等待，不能发布为完成的stock boot。make bootimage防漏hook由构建脚本负责人接入；本次不改device.mk或build_z1.sh。需要的环境仅Z1_GUARD_CC，需要的步骤是最终stock packer调用，无需预先在共享工作区构建guard。

## 精简验证与限制

主机fixture直接复用生产函数，不发布ready属性：

```sh
mkdir -p out/mount_guard
cc -Os -Wall -Wextra -Werror stock/mount_guard_test.c -o out/mount_guard/host_check
out/mount_guard/host_check
```

覆盖12项生产函数断言：错误mountpoint/dev/type/subdir、缺system、错误filesystem、非执行及空文件。解析/临时打包验收还需检查guard为static ARM且0755、当前init全文与WMT guarded exec保留、kernel不变且boot≤6MiB、gate在post-fs第一段；测试boot不是发布候选。

没有板级property/SELinux/启动验证。默认交叉glibc标注minimum Linux3.2 ABI，但vendor3.4实际syscall行为仍须上板确认；socket拒绝会停在诊断等待，而不会假启动。挂载和文件检查不证明ART/HAL/framework完整启动。发行物S3没有因源码改动自动获得gate。出现BLOCKED时应修真实挂载或文件错误，不能手动置1冒充成功。

### 2026-10-05 本仓验证结果

独立build_mount_guard.sh编译成功。默认从源编译与--mount-guard复用两种packer入口均exit0，guard SHA256一致为`5334ffaf4da300cdc41d536fe9f9e67794f60c8868f95cdcd99b0ba2e996a0d4`，481388 B；两次临时boot均5,584,896 B，kernel精确hash不变。cpio读回确认0755/static ARM、init全文与本仓一致、三WMT guarded exec保留。临时boot均删除。主机12项断言、bash -n、Python解析、git diff --check与补丁reverse --check通过。

仅生成本仓忽略的out/mount_guard验证记录：HOST_CHECK.log、PACK_CHECK.json以及BUILD_STATUS.json；没有写入/暂存/清理外部6572工作区，没有提交或上传。property socket/SELinux与板级启动仍未实测。
