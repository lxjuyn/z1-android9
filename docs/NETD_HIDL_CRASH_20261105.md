# netd HIDL 崩溃根因报告 — `_hidl_interfaceChain` / `RefBase::incStrong` SIGSEGV

日期: 2026-11-05
分析对象: `out/z1_v3_system_final/system.img` + tombstone_42/43
结论一句话: **不是 HIDL 库不配套, 是本项目自己打的 `system/netd/server/main.cpp` 补丁把
`NetdHwService mHwSvc;` 从 `main()` 函数作用域挪进了 `if` 块内部, 注册完成后立刻析构了服务对象;
`RefBase::~RefBase()` 无条件把 `mRefs` 置 0, 之后任何 `interfaceChain` 事务都在
`incStrong` 的 `mRefs` 偏移(+4) 上崩。**

统一术语:
- "包装对象" = `BsNetd` 在 `libhidltransport.so:wrapPassthrough()` 里 `new` 出来的 `BnHwNetd`,
  它是真正注册进 hwservicemanager 的 binder。
- "实现对象" = netd `main()` 栈上的 `NetdHwService mHwSvc`, 包装对象通过 `mImpl`(`sp<IBase>`) 指向它。

---

## 1. 证据 (全部来自本机 img 里的真实二进制, 可复核)

### 1.1 崩溃点逐条指令对齐

tombstone:
```
#00 pc 000097fc /system/lib/libutils.so       (RefBase::incStrong(void const*) const+4)
#01 pc 00040789 /system/lib/libhidltransport.so (BnHwBase::_hidl_interfaceChain+120)
#02 pc 00041801 /system/lib/libhidltransport.so (BnHwBase::onTransact+648)
#03 pc 0000d721 /system/lib/android.system.net.netd@1.1.so (BnHwNetd::onTransact+292)
fault addr 0x4, code 1 (SEGV_MAPERR)
```

`_hidl_interfaceChain` = `0x40711`(thumb) → 代码 `0x40710`, `+120(0x78)` = **`0x40788`**,
正好是这一条:

```
40772: add.w r6, r5, #24
4077a: ldr   r0, [r5, #64]        ; r0 = _hidl_this->mImpl  (BnHwBase 偏移 64, 见 1.4 反证)
4077c: str   r0, [sp, #16]
4077e: ldr   r1, [r0, #0]         ; vptr
40780: ldr.w r1, [r1, #-12]       ; 虚基类偏移 (vbase offset)
40784: add   r0, r1               ; r0 = 实现对象的 RefBase 子对象
40786: add   r1, sp, #16
40788: blx   RefBase::incStrong   ; ← tombstone #01 = 这条 (返回地址 0x40789)
4078c: ldr   r5, [sp, #16]
```

即: `_hidl_interfaceChain` 干了 `sp<IBase> tmp(_hidl_this->mImpl);`。

`RefBase::incStrong` (`libutils.so` 0x97f8):
```
97f8: ldr  r0, [r0, #4]      ; r0 = mRefs   (RefBase 偏移 4)
97fa: adds r1, r0, #4
97fc: ldrex r2, [r1]         ; ← tombstone #00 = 这条 (incStrong+4)
```
`mRefs == nullptr` ⇒ `r1 == 4` ⇒ 在地址 4 上 `ldrex` ⇒ **fault addr 0x4, pc=incStrong+4**。
与 tombstone 逐字段吻合。(若 `this` 本身是空, 会崩在 `0x97f8`, 报告成 `incStrong+0`、fault addr 仍为 4,
但 pc 对不上, 所以可以排除"`this` 为空"。)

### 1.2 `mRefs` 只在 `~RefBase()` 里被清零 —— 这是本报告的核心推理

`libutils.so` `RefBase::~RefBase()` (0x9b0c):
```
9b12: ldr  r0, [r4, #4]      ; mRefs
9b1c: tst.w r1, #1           ; OBJECT_LIFETIME_WEAK?
9b24: cmp  r1, #0x10000000   ; == INITIAL_STRONG_VALUE?
...
9b46: movs r0, #0
9b48: str  r0, [r4, #4]      ; ★ 无条件 mRefs = nullptr ★  (所有分支都汇到 0x9b46)
9b4a: mov  r0, r4
9b4c: pop  {r4, pc}
```

`0x9b48` 的 `str` 是**无条件**执行的(0x9b3a 与 fallthrough 都到 0x9b46)。
所以:

> `incStrong` 里 `mRefs == nullptr` ⟺ **这个 RefBase 子对象已经跑过析构函数**。

不存在"分配器把内存清了""对象被 free 了"之类的替代解释 —— 只有析构能把 `mRefs` 写成 0。
这一条把根因锁死在"实现对象被提前析构"上。

### 1.3 铁证: 上板那份 `/bin/netd` 真的在注册后立刻析构服务对象

补丁文件 `z1a9_gpt/stock_patches/system__netd__server__main.cpp.patch`:
```diff
-    NetdHwService mHwSvc;
-    if ((ret = mHwSvc.start()) != android::OK) {
-        ALOGE("Unable to start NetdHwService: %d", ret);
-        exit(1);
-    }
-    ALOGI("Registering NetdHwService: %.1fms", subTime.getTimeAndReset());
+    if (!android::base::GetBoolProperty("ro.z1.stock_kernel", false)) {
+        NetdHwService mHwSvc;                      // ← 进了 if 块, 出了 } 就析构
+        if ((ret = mHwSvc.start()) != android::OK) { ... exit(1); }
+        ALOGI("Registering NetdHwService: %.1fms", subTime.getTimeAndReset());
+    } else {
+        ALOGI("Stock kernel: native Binder netd is ready; vendor HIDL publication unavailable");
+    }
```
上游 LineageOS-16 (`system/netd/server/main.cpp`, rev `02458b0a`) 是**函数作用域**:
```cpp
167:    NetdHwService mHwSvc;              // main() 局部
168:    if ((ret = mHwSvc.start()) != android::OK) { ... }
172:    ALOGI("Registering NetdHwService: %.1fms", ...);
176:    IPCThreadState::self()->joinThreadPool();   // main 永不返回 → 对象活到进程结束
```

**上板镜像里 `bin_netd` 的机器码证实了作用域被缩小** (反汇编 `main`, 0x471f0-0x472a6):
```
471a8: add   r6, sp, #288          ; r6 = &mHwSvc (栈)
471c6: mov   r0, r6                 ; string "ro.z1.stock_kernel"
471ca: blx   GetBoolProperty
471de: cbz   r5, 471f0              ; stock_kernel == false → 走注册分支
471e0: ...  ALOGI("Stock kernel: ...") ; else 分支, 0x471ee 直接跳到 0x4727e
471f0: add.w r8, r6, #4             ; r8 = &(mHwSvc 的 RefBase 子对象)
471f6: blx   RefBase::RefBase()     ; ★ 构造实现对象
  ...   vptr 修正 (虚基类偏移)
47232: mov   r0, r6
47234: bl    NetdHwService::start() ; ★ 0x29e98: configureRpcThreadpool(1,false)
                                    ;            + INetd::registerAsService("netd")
47238: cmp   r3, #0 ; bne 472c0     ; 出错才 exit
4723e: ...  ALOGI("Registering NetdHwService: %.1fms")
47278: mov   r0, r8
4727a: blx   RefBase::~RefBase()    ; ★★★ 这里就把 mHwSvc 析构了 (mRefs = nullptr) ★★★
4727e: ...  ALOGI("Netd started in %dms")
472a6: blx   IPCThreadState::joinThreadPool()   ; 进程继续活着, 服务还在 hwservicemanager 里
```
`start()` 本体 (`0x29e98`) 也对得上:
```
29eb8: blx configureRpcThreadpool(1, 0)
29ed8: mov r0, r4                      ; this
29ee0: blx INetd::registerAsService(string const&)
```

**注意顺序: `registerAsService()` 返回 → 打日志 → `~RefBase()` → `joinThreadPool()`。**
也就是说: 服务在 hwservicemanager 里登记着, 但它的实现对象在**同一线程、4 条指令之后**就死了。

`ro.z1.stock_kernel` 在本 img 的 `build.prop` / `vendor/build.prop` / `prop.default` / `default.prop`
**全都没有定义** (`debugfs -R "cat"` 逐个查过) ⇒ `GetBoolProperty(..., false)` 返回 false
⇒ 走的就是上面这条有 bug 的分支。这也解释了为什么它一定触发而不是偶发。

### 1.4 包装对象与实现对象的关系 (为什么 `mImpl` 会指到栈上)

- `NetdHwService : public android::system::net::netd::V1_1::INetd`(`NetdHwService.h:27`),
  不是 `BnHwNetd` 的子类 —— 它只是一个 `IBase` 实现。
  反证: `nm -D bin_netd | grep BnHwBase` = **0 条**; 而 `INetd::registerAsService`、
  `IBase::interfaceChain` 在 dynsym 里都有(`bin_netd` dynsym 共 25 条 hidl 符号)。
- 真正的 `BnHwNetd` 包装对象由 `libhidltransport.so` 的
  `android::hardware::details::wrapPassthroughInternal(sp<IBase>)` (`0x1c9cd`) 在写信封时创建:
  它遍历 `interfaceChain`, 用 `BsConstructorMap` 里的 `BsNetd` 构造器 `new` 一个 `BnHwNetd`。
- `BnHwBase` 的布局(用 `BnHwBase::BnHwBase(sp<IBase> const&)` @`0x3ff34` 反推):
  `mImpl`(即那个 `sp<IBase>`) 在**偏移 64**, 构造时对 `mImpl` 调 `incStrong`:
  ```
  3ffea: ldr.w r7, [r8]          ; r7 = impl
  3fff8: add   r0, r7 ; ... blx RefBase::incStrong
  40018: str   r7, [r6, #0]      ; r6 = this+64 → mImpl = impl
  ```
  与 `_hidl_interfaceChain` 里 `ldr r0, [r5, #64]` 完全对上。

所以链条是: 栈上 `NetdHwService` ←(强引用 `mImpl`)← 堆上 `BnHwNetd` ←(binder 节点)← hwservicemanager。
`~RefBase` 一跑, `mImpl` 就变成指向"已析构对象"的毒指针, 包装对象本身却完好无损
(这也解释了为什么 `BnHwNetd::onTransact` 的虚函数分派还能正常走到 `_hidl_interfaceChain`)。

### 1.5 HIDL 库自洽性核查结果: **没问题** (排除"拼装 ROM 乱配库")

| 文件 | 大小 | SONAME | NEEDED 数 | RPATH/RUNPATH | 符号版本 |
|---|---|---|---|---|---|
| `/lib/libhidltransport.so` | 321972 | `libhidltransport.so` | 11 | 无 | 无(全 `@@Base`) |
| `/lib/libhwbinder.so` | 131172 | `libhwbinder.so` | 8 | 无 | 无 |
| `/lib/libhidlbase.so` | 113140 | `libhidlbase.so` | 8 | 无 | 无 |
| `/lib/libutils.so` | 96924 | `libutils.so` | 7 | 无 | 无 |
| `/lib/android.system.net.netd@1.1.so` | 96652 | 同名 | 11 | 无 | 无 |
| `/bin/netd` | 412220 | (PIE) | 25 | 无 | 无 |

- 所有 SONAME 与文件名严格一致; 所有 NEEDED 都能在 img 里找到(AOSP 9 的 `libbase/liblog/libcutils/
  libvndksupport/libc++/libc/libm/libdl/libbinder/libbpf/libpcap/libqtaguid/...` 全在 `/lib`)。
- Android/bionic 不用 ELF symbol versioning(`readelf -V` 里 `File:` 段数 = 0), 所以
  "符号版本不匹配"这个假设在 Android 上本来就不成立。
- `libhidltransport` 引用的 `RefBase::incStrong/decStrong` 在 `libutils.so` 里确实存在
  (`0x97f9` / `0x984d`, PROTECTED), 签名一致 —— **不是"调用了不存在的符号"**。
- 单 build 指纹: `ro.build.fingerprint=Jiabo/lineage_z1/z1:9/PQ3A.190801.002/lxj10061753:userdebug/test-keys`,
  `ro.vendor.build.fingerprint` 相同; `system/libhidl` manifest 钉死 AOSP `android-9.0.0_r46`。
- `/vendor/lib` 里**没有** `libutils/libhidl*/libhwbinder` 的副本(只有 MTK 的
  `libril/libwifi-hal/libnvram/...`), 不存在 namespace 内重复库互相打架。
- 全 img 只有 3 个文件提到 `android.system.net.netd@1.0/1.1`: `/lib/...@1.0.so`、
  `/lib/...@1.1.so`、`/bin/netd`(用 `grep -abo` + `debugfs icheck/ncheck` 定位)。
  **没有任何客户端/框架代码链接这个 HIDL 接口。**

---

## 2. 根因排序

### R1 — (置信度: 高, 有二进制级铁证) 补丁缩小了 `mHwSvc` 的作用域, 实现对象在注册后立即析构
- 位置: `z1a9_gpt/stock_patches/system__netd__server__main.cpp.patch`
- 机制: 见 §1.3 + §1.2 + §1.4。
- 证据强度: 上板 `bin_netd` 的 `main` 反汇编里, 构造(0x471f6)→注册(0x47234)→析构(0x4727a)
  →`joinThreadPool`(0x472a6) 的顺序是逐条指令可核对的; `~RefBase` 无条件清零 `mRefs` 也是反汇编可核对的。
- 为什么"一定是它"而不是别的: fault addr 0x4 + pc=incStrong+4 要求 `mRefs == nullptr`;
  `mRefs` 只在 `~RefBase()` 里被写 0; 而实现对象恰恰就是在这份二进制里被提前析构的唯一 `IBase`。

### R2 — (已排除) HIDL 库版本不一致 / 拼装 ROM 乱配
§1.5 全部核查通过: 单 build 指纹、SONAME/NEEDED 自洽、无 RPATH、无符号版本、vendor 无重复副本。
另外工程本身是自建 LineageOS 16, 不是拼装, `system/libhidl` 钉死 AOSP r46。

### R3 — (已排除) `netd` 二进制与 `android.system.net.netd@1.1.so` 不配套
两者同 build; `bin_netd` 只 import `@1.0.so`/`@1.1.so` 的 `INetd` 符号, 全部能解析;
而且崩溃发生在 `.so` 内部正常的 `onTransact` 分派之后, 属于 `.so` 自己的代码正常跑到了数据坏掉的地方,
不是"符号缺失/错位"。

### R4 — (已排除) hwservicemanager 里残留已死亡服务的注册项
残留项只会让 `get()` 返回一个已死 binder, 走 `BR_DEAD_BINDER` → `DEAD_OBJECT`,
不会进 `BnHwNetd::onTransact`。这里崩溃点明确在 netd 进程内正常执行中, 说明 binder 通道是活的。

### R5 — (已排除) SELinux permissive 下 binder 域串味
崩溃链完全在 netd 自己的对象图里(`this` 是 netd 自己 new 的包装对象), 没有跨域指针。
permissive 只是不作为, 不会制造这种指针。

---

## 3. 残留不确定点 (如实说明)

唯一没钉死的是"**到底是谁发了那个 `interfaceChain` 事务**"。

- AOSP 9 里 `interfaceChain` 的发送方只有两处:
  1. `system/hwservicemanager/ServiceManager.cpp:237`
     `auto ret = service->interfaceChain([&](...){...});` —— 同步, 在 `add()` 内部;
  2. 客户端 `getService()` 的校验(AOSP 9 的 `ServiceManagement.cpp` 里 **没有** `interfaceChain`,
     逐个 grep 过 —— 所以这条其实**不存在**)。
- 而 `add()` 是在 `mHwSvc.start()` **内部**调的, 那时对象还活着 ⇒ 那一次 `interfaceChain` 不会崩。
- 因此崩溃的那次 `interfaceChain` 必然发生在 0x4727a 析构之后。
  但按 §1.5 的结论, **本 img 里没有任何进程链接 netd 的 HIDL 接口**, 理论上没人会再发。
- 我的判断: 这份 tombstone(pid 203)对应的 netd 实例**不在当前这三段 boot log 里**
  (boot1 netd=346, boot2 netd=334, boot3 netd=324, 都 > 203), 说明它来自更早的一次开机;
  那次开机里可能有我们后来改掉的东西(比如当时 `ro.z1.stock_kernel` 的取值、或某个已下线的 HIDL 客户端)
  触发了额外一次 `add()`/`interfaceChain`。

**这不影响定论**: 无论触发者是谁, 服务对象在注册后 4 条指令就被析构这件事本身就是致命 bug ——
它使 `netd` 的 HIDL 接口**从设计上就永远是坏的**, 任何人第一次真正用到它都会 100% 崩。
现在之所以"看起来没炸", 只是因为暂时没人用它。

要在板上把触发者钉死(可选, 1 分钟):
```
logcat -b all | grep -E 'hwservicemanager|BnHw|interfaceChain'
# 或者临时把 netd 的 HIDL 注册关掉做对照: 见 §5 方案 C
```

---

## 4. 影响面

### 4.1 netd 同时提供两套接口
`system/netd/server/main.cpp`:
- 第 147 行 `NetdNativeService::start()` —— **libbinder(native)** 的 `android::net::INetd`,
  给 framework 用;
- 第 167-171 行 `NetdHwService mHwSvc; mHwSvc.start()` —— **HIDL(hwbinder)** 的
  `android.system.net.netd@1.1::INetd`, 给 vendor 用。

两套跑在同一个进程里。**HIDL 那边线程 SIGSEGV → 整个 netd 进程被信号杀掉 → 两套一起没。**

### 4.2 system_server 依赖 netd 的具体调用点 (AOSP 9)
- `frameworks/base/services/core/java/com/android/server/NetworkManagementService.java`
  → `INetd.Stub.asInterface(ServiceManager.getService("netd"))`(native binder);
  它是 `ConnectivityService` / `NetworkPolicyManagerService`(流量管控、防火墙 uid 规则) /
  `Tethering` / `Vpn` / 以太网 的底层通道。
- 关键点: **`NetworkManagementService` 不自动重连**。netd 一死, 后续
  `setFirewallUidRule` / `setDataSaverModeEnabled` / `networkAddRoute` / `setDnsConfigurationForNetwork`
  等全部抛 `RemoteException` / `IllegalStateException`, 而这些很多发生在 boot 早期和
  app 安装/网络切换的热路径上。
- 所有 app 进程的 DNS 走 `/dev/socket/dnsproxyd` + FwmarkServer(**unix socket, 不是 binder**)。
  netd 一死, DNS 代理全断, bionic 会退化成直连 DNS(有 fallback, 但会慢/不稳)。
- Watchdog 关联: 团队看到的 NMI 是 `Binder:2515_1B` 卡在
  `sock_def_readable` / `unix_dgram_sendmsg` 自旋锁 —— 这正是**往 netd 的 unix dgram socket 写**
  (`CommandListener` / `FwmarkServer` / `dnsproxyd`) 的形态。netd 死了或半死时, 写端会阻塞/重试,
  system_server 的 binder 线程被卡住 → 60s 后 Watchdog 打 sysrq `w` 并 SIGKILL zygote。
  **所以 netd 崩溃是可以间接把 system_server 拖进 Watchdog 的, 但它不是唯一原因**
  (log 里 boot1 里 netd 每 ~5s 被 init 杀一次是 zygote 重启级联, 那是另一条因果)。

### 4.3 现在有没有"可以先不管"的余地
**有, 但不能不管进程本身。** netd 的 HIDL 接口在本机没有消费者(§1.5), 所以
"这个 HIDL 服务坏了"本身不影响功能; 但它会让 **netd 整个进程随机被杀**, 而 netd 的
native binder 接口是 framework 的必需依赖。所以不能靠"不用它"绕过, 必须修。

---

## 5. 修法 (按优先级)

### 方案 A(推荐, 代价最小): 把 `mHwSvc` 挪回 `main()` 函数作用域

`system/netd/server/main.cpp`(补丁目标)改成:
```cpp
    // Now that netd is ready to process commands, advertise service
    // availability for HAL clients.
    NetdHwService mHwSvc;                       // ← 回到 main() 作用域, 活到进程结束
    if (!android::base::GetBoolProperty("ro.z1.stock_kernel", false)) {
        if ((ret = mHwSvc.start()) != android::OK) {
            ALOGE("Unable to start NetdHwService: %d", ret);
            exit(1);
        }
        ALOGI("Registering NetdHwService: %.1fms", subTime.getTimeAndReset());
    } else {
        ALOGI("Stock kernel: native Binder netd is ready; vendor HIDL publication unavailable");
    }
```
- 语义完全保持补丁原意(stock kernel 下不注册 HIDL), 只是**不再析构对象**。
- 代价: 改 `stock_patches/system__netd__server__main.cpp.patch` 一处 + 重编 `system/netd` +
  重打 `system.img` + 刷机验证。约一次全量 system 编译的量级。
- 顺手建议: 用 `static android::sp<NetdHwService> gHwSvc = new NetdHwService();` 更保险
  (堆分配 + 进程级生命周期), 彻底杜绝"析构先于取消注册"。

### 方案 B(同一批顺手做): 把 keystore 的同类写法也提到函数作用域
`stock_patches/system__security__keystore__keystore_main.cpp.patch` 里:
```cpp
if (!GetBoolProperty("ro.z1.stock_kernel", false)) {
    configureRpcThreadpool(1, false);
    android::sp<IKeystore> wifiKeystoreHalService = new Keystore();   // ← 局部 sp, 出块就 decStrong
    ...
}
```
它是**堆对象 + `sp`**, 目前靠 hwbinder 节点持有的 weak ref 才没被删, 属于"侥幸能跑"。
同样提到 `main()` 作用域(或改 `static sp<>`)。代价: 同方案 A 一个补齐补丁。

### 方案 C(临时对照/止血, 不推荐长期): `setprop ro.z1.stock_kernel 1`
会让 netd 走 else 分支完全不注册 HIDL(`Stock kernel: native Binder netd is ready`)。
**但在 mainline 内核镜像上这是个坏主意** —— 同一个 property 还会:
- 让 `configureRpcThreadpool()` 变空函数(`HidlTransportSupport.cpp` 补丁);
- 让 `defaultServiceManager1_1()` 返回 nullptr、`getService()` 只走 passthrough(`ServiceManagement.cpp` 补丁);
- 让 wificond 不 setup hwbinder FD、keystore 不注册 HIDL、surfaceflinger 不注册 DisplayService、
  `media.codec` 直接 `return 1`。
等于把当前还能用的 HIDL 栈全关掉。**只适合做"是不是它导致的" 对照实验**, 不适合上线。

### 方案 D(不推荐): 换一套 AOSP 原版 `netd`/`libhidl*`
§1.5 已证明库本身是自洽的, 换库解决不了"源码作用域"这种问题, 白付一次回归风险。

---

## 6. 上板验证判据

修完重刷后, 按顺序核:

1. **注册成功且在**
   ```
   logcat -d | grep -E 'Registering NetdHwService|Netd started in'
   # 期待两条都在, 且后面没有 SIGSEGV
   ```
2. **进程存活不重启**(最关键)
   ```
   adb shell 'pidof netd; sleep 300; pidof netd'   # 两次 pid 必须相同
   ```
3. **没有新 tombstone**
   ```
   adb shell 'ls -l /data/tombstones/ ; logcat -b crash -d'
   # 期望: netd 相关 0 条; tombstone 计数不再增长
   ```
4. **HIDL 服务真的可见**(证明包装对象和实现对象都活着)
   ```
   adb shell 'lshal | grep -i netd'
   # 期望: 能看到 android.system.net.netd@1.0::INetd / @1.1::INetd, 且 lshal 不崩
   ```
   `lshal` 会去 `get()` 各服务; 如果对象还是死的会立刻复现崩。这是最强的单条验收。
5. **native binder 接口仍正常**(证明没把 netd 整体搞坏)
   ```
   adb shell 'service list | grep -w netd'
   adb shell 'dumpsys netd | head -20'
   ```
6. **回归**: `dumpsys connectivity` / 开关飞行模式 / 连一次 WiFi, 确认
   `NetworkManagementService` 不再刷 `RemoteException`。
7. **对照(可选, 钉死触发者)**: 临时 `setprop ro.z1.stock_kernel 1` 重启, 看 tombstone 是否消失 ——
   若消失, 则 100% 坐实来源是这条 HIDL 注册路径(但按方案 C 的说明, 该状态下别指望 HIDL 功能)。

---

## 附: 本次分析用到的复核命令

```bash
IMG=out/z1_v3_system_final/system.img
debugfs -R "dump /lib/libhidltransport.so /tmp/x/libhidltransport.so" $IMG
debugfs -R "dump /bin/netd /tmp/x/netd" $IMG
arm-linux-gnueabihf-objdump -d --start-address=0x47180 --stop-address=0x472c0 /tmp/x/netd
arm-linux-gnueabihf-objdump -d --start-address=0x40710 --stop-address=0x40790 /tmp/x/libhidltransport.so
arm-linux-gnueabihf-objdump -d --start-address=0x9b0c --stop-address=0x9b72 /tmp/x/libutils.so
arm-linux-gnueabihf-objdump -d --start-address=0x97f8 --stop-address=0x9840 /tmp/x/libutils.so
nm -D bin_netd | grep -c BnHwBase                         # → 0
grep -abo 'android\.system\.net\.netd@1\.[01]' $IMG       # 只有 @1.0.so / @1.1.so / bin_netd
debugfs -R "icheck <blk>" $IMG ; debugfs -R "ncheck <ino>" $IMG   # 字节偏移 → 文件路径
debugfs -R "cat /build.prop" $IMG | grep -i 'z1\.'        # → 空 (ro.z1.stock_kernel 未定义)
```

参考源码 (AOSP / LineageOS 16):
- `platform/system/netd` @ `02458b0a19ce2d3214a00f9779bd36868541b7ca` `server/main.cpp`, `server/NetdHwService.{h,cpp}`
- `platform/system/libhidl` @ `android-9.0.0_r46` `transport/HidlBinderSupport.cpp`,
  `transport/HidlPassthroughSupport.cpp`, `transport/ServiceManagement.cpp`,
  `transport/manager/1.0/IServiceManager.hal`
- `platform/system/hwservicemanager` @ `android-9.0.0_r46` `ServiceManager.cpp:228-300`
