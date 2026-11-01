# LiveProbe 设备离线实录 (2026-11-01 深夜)

## 结论
活体探针任务 1-5 全部**未执行**:用户在探针窗口内将 Z1 整机关机省电,设备本体离线,顺延明早开机后由新会话执行。

## 探针窗口内已确认的环境事实(全部亲测)
1. **CH340 掉线时间线**(本机 dmesg,`sudo dmesg` 实录):
   - `[102043.836761] usb 1-2.1.1: New USB device found, idVendor=1a86, idProduct=7523` — 曾正常挂载 ttyUSB0。
   - `[103599.429934] ch341-uart ttyUSB0: usb_serial_generic_read_bulk_callback - urb stopped: -32` → 断开 → 0.4s 后重连 → 再断再连(接触不良模式,device number 已达 103)。
   - `[111571.623858] urb stopped: -32` → `[111571.716261] usb 1-2.1.1: USB disconnect, device number 103` → **`[111571.716475] usb 1-2.1: clear tt 1 (90b2) error -71`** + `failed to send control message: -19`。
   - **`usb 1-2.1: clear tt ... error -71` 正是 AGENTS.md 坑/刷写节警告的 hub 下挂 error -71 握手失败模式实录**:CH340 挂在 usb 1-2.1.1(深度 3,hub 下挂两层),AGENTS.md 明文"勿经 USB hub(hub 下挂会 error -71 握手失败)"。**历史教训再次命中,建议明早接线直插主板后置 USB。**
2. **恢复尝试**(全程亲测,均失败于物理层):
   - 轮询 60s 等自动重连:未回归。
   - `echo 1-2 > /sys/bus/usb/drivers/usb/unbind; bind` 重枚举上游 hub:**Huasheng hub (214b:7260) 与挂其上的键鼠全部重新枚举成功**,唯独 CH340 未回归 → 判定 CH340 本体/接头物理断开,非软件可救。
   - `lsusb | grep -E '1a86|303a'` → 空;`ls /dev/ttyUSB* /dev/ttyACM*` → 空。**C3 桥 (Espressif 303a) 同样不在线,两条串口链路全断。**
   - escalate 上报后获用户答复:**设备已被用户整机关机省电,不是线缆问题**。
3. **冻结现场(来自落盘串口日志,非本窗口亲测)**:`mainline_recon/z1_boot_mainline_20261101_003004.log`(2504 行)尾部:SwiftShader 三件(libEGL/libGLESv1_CM/libGLESv2_swiftshader.so)全部加载完成,SF 打印 EGL info → GL info(`GL_MAX_TEXTURE_SIZE = 8192` / `GL_MAX_VIEWPORT_DIMS = 8192`,内核时间 377s 附近)后 logcat 戛然而止;`[z1sf] wrote /data/local/z1diag/sf.txt (313535 bytes)` 与 `/cache/z1diag/sf.txt (314513 bytes)` 证明 SF dump 循环在跑;无 panic/hung task 行。与 ask 背景描述一致。
   - 明早探针切入点:SF 的 GL info 打印之后、首次 swap/首次真渲染调用——`/data/local/z1diag/sf.txt` 与 `/cache/z1diag/sf.txt` 是现成的 dump 通道(本轮 init 已内置 z1_sf_dump 服务,`init: Service 'z1_sf_dump' (pid 1920) exited ... starting service` 循环可见)。

## 明早待执行清单(顺延)
1. `ps -A | grep zygote` 看 STAT(D/S/R/Z);
2. logd/SF/heap/installd/servicemanager 的 `/proc/<pid>/wchan`、`/proc/<pid>/stack`(shell 可读性待试);
3. `dmesg | tail -30` 非洪水行;`cat /proc/sys/kernel/printk`;
4. `echo 3 > /proc/sys/kernel/printk`(root 试探);
5. `/proc/binder/proc` 与 binder 统计(SF/system_server binder 线程状态);
6. 命令短、洪水吞则写文件两步法(`> /data/local/tmp/x.txt` 分段 cat)。
