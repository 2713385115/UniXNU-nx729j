# 适配进度：红魔 8 Pro+ 现在到哪一步了

最后更新：2026-10-22 15:10（**阶段 A 真机验证通过**）

一句话总结：**我们编的 UEFI 在红魔 8 Pro+ 上完整启动成功了**——SEC → PEI → DXE → BDS
全走通，屏幕正常显示滚动日志，最后停在 Project Silicium Logo 和
`Failed to Find an Operating System`。这不是崩溃，是"没有操作系统可启动"的正常提示，
因为没有 iOS 内核（那是阶段 B 的产物，需要 macOS）。**阶段 A 到此结束，下一步是阶段 B。**

---

## 一、已完成

| 项目 | 产出 | 依据 |
|---|---|---|
| 机型身份 | `board.toml`：`nx729j` / SM8550-AB / msm-id `0x207` / DDR 16 GiB | 真机 DTB |
| **userdata 起始位置** | `first_lba = 0x296328`（2712360，4 KiB 扇区）**已确定，不用量** | 官方刷机包 `gpt_main0.bin` + `rawprogram0.xml` + `patch0.xml` 三处一致 |
| **ABL 性质** | 已确认是 EDK2 `QcomModulePkg`，不是老式 lk；LZMA 解开后 688 KB 可读内容 | `tools/fv_extract.py` 解包 `abl.img` |
| 目标系统 | iPad7,1 iPadOS 17.7.11（21H461），与 sheng 同一份未修改镜像 | 上游设备页 |
| 内核配置 | `configs/nx729j_defconfig` | 照 sheng 的 SM8550 组合 |
| ADT 生成器 | `scripts/make-nx729j-adt.py` | 真机 DTB；二进制结构已回读校验 |
| 面板参数 | `docs/reference/panel/nx729j-panel.json`：三档时序 + 1349 字节初始化序列 | 真机 DTB，`--active` 追 phandle `0x6b2` |
| 预检 | `scripts/check-nx729j.py`：19 项 | 实跑通过 |
| 固件解包工具 | `tools/fv_extract.py`：UEFI FV → FFS → LZMA → 字符串 | 实跑，从 `abl.img` 解出 2121 条字符串 |
| 手机体检 | `tools/nx729j.py doctor` | 实跑通过（adb/fastboot 已自动找到） |
| 量分区 | `tools/nx729j.py measure --write board.toml` | 模拟设备端到端验证 |
| **fastboot 探测** | `tools/nx729j.py probe`：getvar 全量 + 解锁判定 + `fastboot boot` 阶梯测试 | **真机实跑通过（见下）** |
| **`fastboot boot` 放行** | `Sending 'boot.img' (98304 KB) OKAY` → `Booting OKAY` → `Finished` | **真机实测 2026-10-22，serial `b4b45a27`** |
| **userdata 末位** | `last_lba = 0x718bccd` 已写入 `board.toml`（原为 `0x0` 占位） | **真机 `partition-size:userdata` = `0x718BCD3000`** |
| 分区备份 | `tools/nx729j.py dump` | 逻辑验证 |
| 覆盖脚本 | `scripts/apply-to-sheng-fork.py` | 实跑验证 |
| **阶段 A 编译脚本** | `build/phase-a.sh` + `build/编译阶段A.bat` | 语法校验通过，**未在真实 Linux 上跑过** |
| 文档 | `GUIDE-从零开始.md`、`CHECKLIST-勾选清单.md`、`docs/reference/firmware-package-facts.md` | — |
| **UEFI 固件编译成功** | `SILICIUM_UEFI.fd`，**3145728 字节（= 0x300000，正好等于配置里声明的 FD size）** | WSL2 实跑，clang 20 核并行，约 2 分 20 秒 |
| **boot.img 打包成功** | `out/boot-nx729j.img`，**2437120 字节**，BootShim 192 字节 | `unixnu bootimg` 自带往返自校验全部通过 |
| **真机 boot 本轮结果** | `Sending 'boot.img' (2380 KB) OKAY` → `Booting OKAY` → `Finished. Total time: 10.353s` | 真机实测，serial `b4b45a27` |
| **boot 之后的状态** | adb 无设备、fastboot 无设备、**没有 9008 端口**（不是变砖），USB 完全没有枚举 | 现场排查，见第三节 |

### 这一轮编译踩掉的坑（都已修好，脚本可重跑）

1. **edk2 嵌套子模块是空的。** `Mu_Basecore` 是从 codeload 压缩包解开的，不是 `git submodule update`
   出来的，`.gitmodules` 里 9 个子模块目录全空。BaseTools 在 meta-data 阶段逐个校验
   `MdePkg.dec` / `MdeModulePkg.dec` 的 `[Includes]`，遇到空目录就 `error 000E` 直接退出。
   真正需要的是 4 个：`mipisyst`、`libfdt`、`brotli`（两处）、`libspdm`。
   已用 `build/seed-edk2-modules.sh` 从本地缓存里按**锁定 commit**灌进去。
2. **`VfrCompile` 二进制没生成。** BaseTools 的 C 工具是在装 `g++` 之前编的，C++ 那几个被跳过。
   后果是 `DtPlatformDxe` 的 `DtPlatformHii.vfr` 没被编译成 `.c`，
   报 `clang: error: no such file or directory: .../DtPlatformHii.c`。
   `cd BaseTools/Source/C && make -j20` 补出来后，13 个工具齐全。
3. **内存映射的区域名超长。** 从 DTB 生成的条目直接用节点名
   （`trust_ui_vm_vblk0_ring@0xf78f8000`，33 字符），而结构体 `Name[]` 只有 32 字节，
   clang 在 `-Werror` 下报 `initializer-string for char array is too long`。
   已去掉 `@地址` 后缀（地址本来就有单独字段），`tools/fix-memmap-names.py` 可复查。
4. **`header_version = 4` 与打包自校验冲突。** `nx729j.toml` 原先写的是 4，
   但 `unixnu` 的 `verify()` 是按 v0/v1/v2 头部布局解析的（偏移 36 读 pagesize、40 读 header_version），
   v4 头部没有这两个字段，必然报 `boot image header does not match the requested parameters`。
   已改成 0 —— 和同平台**已验证能起**的 sheng 配置完全一致。
5. **`boot.img` 产物路径少了一层。** 实际是 `UniXNU/out/nx729j/boot/boot.img`，
   不是 `out/nx729j/boot/boot.img`。脚本已修。

### 这一轮从你的刷机包里新挖到的东西

1. **`lba_bytes` 必须是 4096，不是 512。** 这个包所有分区都写 `SECTOR_SIZE_IN_BYTES="4096"`。
   按 512 算会让 `first_lba` 的含义整体错 8 倍。不过已核实：这个几何在 UniXNU 里
   **只用于"镜像装不装得下"的容量校验**，不参与写入，所以填错是精度问题、不是变砖问题。
2. **userdata 下面紧挨着 256 MiB 的 `rawdump`**，`userdata` 的起点 = `rawdump` 末格 +1，
   这个数是 GPT 表项里直接读出来的。
3. **ABL 编译路径里写的是 NX729S（8S Pro）**，不是 NX729J。努比亚很可能让 8 Pro/8S Pro
   共用同一份 ABL，所以 `getvar product` 有可能回 `NX729S`——那不是手机有问题。
4. **ABL 会改写 DTB 的 splash 和 demura 节点**（`Splash memory region after updating:`、
   `/reserved-memory/demura_region_1`）。`nx729j.dtb` 里已有 `cont_splash_region`，所以暂时不是问题。
5. **阶段 A 完全不需要面板参数**：`nx729j.dsc` 里 `USE_CUSTOM_DISPLAY_DRIVER = 0`，
   直接用 bootloader 点亮的 1116×2480×32bpp 帧缓冲。DSI、DSC、初始化序列统统不用碰。

---

## 二、环境：只差一次重启

| 需要什么 | 现在状态 | 谁能解决 |
|---|---|---|
| adb / fastboot | ✅ 已装到 `tools/platform-tools/`（adb 1.0.41 / fastboot 37.0.1） | — |
| Python | ✅ 3.13 可用 | — |
| `wsl.exe` 调用 | ✅ **已解除黑名单**，现在能正常调用 | 你已处理 |
| WSL 功能 | ✅ `Microsoft-Windows-Subsystem-Linux` + `VirtualMachinePlatform` **已启用** | 我做了，**等重启** |
| Ubuntu rootfs | ✅ 已预下载 `work/wsl/ubuntu-base-24.04.4-base-amd64.tar.gz` | 我做了，重启后免下载直接导入 |
| 硬件虚拟化 | ✅ `VirtualizationFirmwareEnabled = True` | 本来就有 |
| macOS（编内核、做系统镜像） | ❌ 没有 | 阶段 B 才需要，现在不用管 |

### 重启之后，只有一步

```powershell
powershell -ExecutionPolicy Bypass -File "D:\刷机\IOS\build\安装WSL并编译.ps1"
```

这个脚本会自己做完：确认 WSL2 内核 → 导入 Ubuntu（用预下载的 rootfs，不走应用商店）→
换清华 apt 源 → 调 `build/phase-a.sh` 编译 → 把 `boot-nx729j.img` 拷到 `out\`。

想手工来也行，只要记住 Ubuntu 是**导入**的而不是商店装的：

```powershell
wsl --set-default-version 2
wsl --import Ubuntu "D:\刷机\IOS\work\wsl\Ubuntu" "D:\刷机\IOS\work\wsl\ubuntu-base-24.04.4-base-amd64.tar.gz" --version 2
wsl -d Ubuntu -u root -- bash "/mnt/d/刷机/IOS/build/phase-a.sh"
```

> 为什么不用 `wsl --install -d Ubuntu`：那条路要连 Microsoft Store，本机到 store 的
> 网络时好时坏；而 `cdimage.ubuntu.com` 实测可达，导入这条路更稳。

---

## 三、真机实测结果（2026-10-22，已连机完成）

### A. 我们自己编的固件：完整启动到 BDS（本轮里程碑）

把 `out/boot-nx729j.img`（我们自己编的 UEFI，不是刷机包里的）送进去：

```
Sending 'boot.img' (2380 KB)                       OKAY [  0.075s]
Booting                                            OKAY [ 10.117s]
Finished. Total time: 10.195s
```

**ABL 接受了镜像并跳转了**——说明 BootShim 的 ARM64 头、FD 基址 `0xA7100000`、
DTB 附加方式都是对的，否则 ABL 会在 `Booting` 之前就拒绝。

#### 日志是怎么拿到的

设备没有 UART，唯一的观测通道就是屏幕本身。用手机录屏 + `ffmpeg` 抽帧还原：

```
adb pull /sdcard/DCIM/Camera/video_20261022_145758.mp4
ffmpeg -i run-1457.mp4 -vf "fps=1,scale=1200:-1" frames/f_%02d.jpg    # 22 帧
```

录像 4K / 60fps / 22.5 秒，覆盖了 boot 之后的前 22 秒。

#### 实际看到的启动序列

| 时刻 | 画面 | 阶段 |
|---|---|---|
| 14:57:58 | REDMAGIC Logo + Powered by Android | ABL 开机图 |
| 14:58:00 | 黑屏一闪 | 切到 UEFI 帧缓冲 |
| 14:58:03 | 满屏白色滚动日志 `Loading driver at 0x... EntryPoint=0x...` | DXE 派发驱动 |
| 14:58:09 | 日志停在顶部，中央出现 **Project Silicium** 大 Logo | BDS 进入 |
| 14:58:09 ~ 14:58:16 | 画面静止 | BDS 枚举启动项 |
| 14:58:17 | 感叹三角 + `Failed to Find an Operating System` | 无 OS → 复位 |

#### 从日志确定的事实

**1. 显示通路完全正常。**

`USE_CUSTOM_DISPLAY_DRIVER = 0`（直接复用固件自己初始化的帧缓冲）这条路走通了。
不需要写 DSI 面板初始化、不需要碰 DSC 压缩、不需要提取时序参数。
待办里"显示绑定"那一项可以直接划掉。

**2. DXE 驱动基本全部加载成功。**

日志里有大段连续的 `Loading driver at 0x... EntryPoint=0x...`，覆盖
`HardwareTimer` / `RealTimeClock` / `SecurityStubDxe` / `PrintDxe` 等；
`Installed Fat filesystem on BE0F1A18` 说明块设备栈也起来了。

**3. QCOM 安全世界调用失败，但全部良性。**

```
_diag_get_config_info(): Target 0x7F not supported
_scm_call failed, TxStatus = 0xFFFFFEF7
_diag_get_debug_policy(): System call failed
Failed to start <xxx> Enabler, a TA, status = 1
PIL-SPSS Failed to get MDT size
```

这些是 SCM / PIL / TrustZone 相关调用。**设备 `secure=yes` 且
`critical unlocked=false`，未签名的 TZ 镜像本来就加载不了**，所以失败是预期内的；
而且它们都没有阻断启动——UEFI 照样走到了 BDS。

**4. 两条真实缺口。**

```
Detecting USB Storage
... no USB devices found on first attempt
```

- **没有输入设备**：USB 控制器起来了但没枚举到键盘；触控（Goodix GT9916）
  驱动还没写，所以屏幕点不了。
- **NVRAM 未持久化**：`... Dxe at 0x0008555000 start failed: Not Found`。
  影响是启动项存不下来，每次都要重新枚举。

**5. 最后的三角不是崩溃。**

`Failed to Find an Operating System` 是 edk2 BDS 在没有任何可启动项时的标准提示，
随后 `ResetSystem()` 复位（所以 14:58:44 回到关机充电）。换句话说：

> UEFI 走完了它该走的全部流程，只是手上没有 iOS 内核可以交棒。

#### 阶段 A 判定

| 项目 | 结论 |
|---|---|
| BootShim 头 / FD 基址 / DTB 附加 | 通过，ABL 放行 |
| SEC / PEI / DXE 初始化 | 完成 |
| 帧缓冲显示 | 正常，日志与 Logo 都能画 |
| BDS 启动管理 | 正常，无启动项时报错语义正确 |
| USB 输入 | 缺（无键盘、无触控驱动） |
| NVRAM | 未持久化 |
| OS 内核 | 阶段 B，需 macOS |

**阶段 A 完成。** 剩下两项（USB 输入、NVRAM）不影响"能不能启动 iOS"这个主目标，
属于可用性改进，可以放到阶段 C 之后再做。

---

设备 `b4b45a27`，RedMagicOS 10.0.12（Android 15）。实测拿到的东西：

| 变量 | 实测值 | 意义 |
|---|---|---|
| `unlocked` | **yes** | bootloader 已解锁，可以刷 |
| `secure` | yes | 仍会验签，所以**不能**随便刷引导链分区 |
| `product` | **kalama** | 平台代号，**不是**原先猜的 `NX729S`，ABL 没上报机型名 |
| `current-slot` | a | 当前在 A 槽 |
| `partition-size:userdata` | `0x718BCD3000` = 454.2 GiB | 推出 `last_lba = 0x718bccd` |
| **`Device critical unlocked`** | **false** | **critical 分区未解锁**，`xbl`/`abl` 这类不能动 |
| `fastboot boot` | **OKAY** | **总闸门通过** |

`fastboot boot` 的完整输出（这是最重要的一行）：

```
Sending 'boot.img' (98304 KB)                      OKAY [  3.020s]
Booting                                            OKAY [ 10.160s]
Finished. Total time: 13.228s
```

跑完之后手机自己回到了 Android，`adb devices` 正常，**没有写任何分区**。

### 注意 `critical unlocked: false`

这个不影响阶段 A，因为 `fastboot boot` 只往内存里放镜像、不落盘。
但它意味着**以后不能直接刷 `xbl` / `abl` / `tz` 等关键分区**，一旦刷坏就是高通 9008 救砖。
所以后续任何需要"写引导链"的方案都要先绕开这一点，优先走 `fastboot boot` 的路线。

---

## 四、待做，需要真正写代码

| 项目 | 现状 | 缺什么 | 难度 |
|---|---|---|---|
| 编内核 | **唯一卡点** | **必须 macOS**（`xcodebuild` + iphoneos SDK + `compression_tool`） | 4-8 小时机器时间 |
| 系统镜像 | 未开始 | **必须 macOS**（`hdiutil`） | 1-2 小时 |
| Goodix GT9916 触控 | 只有规格 | 协议层 + 报告解析层。Goodix 是**芯片算坐标**，和小米平板的 Novatek（软件算质心）完全不同，不能照抄。**不阻塞 iOS 启动**，只影响 UEFI 菜单能不能点 | 中高 |
| NVRAM 持久化 | 报 `start failed: Not Found` | 找一个能落盘的变量存储后端（UFS 分区或文件） | 中 |
| ~~显示绑定~~ | **已完成** | 帧缓冲直接可用，`USE_CUSTOM_DISPLAY_DRIVER = 0` 走通 | — |

---

## 五、我不会做的事

- 不编造 `last_lba`。现在已经从真机读到了确切值，这条不再有争议；顺带纠正早先的说法：
  这个几何在 UniXNU 里**只参与"镜像装不装得下"的容量校验**，不参与写入，
  所以算错是精度问题，不是变砖问题。
- 不假装编译成功。`phase-a.sh` 写好了但**没有在真实 Linux 上跑过**，跑完之前它都算"未验证"，
  而不是"已经能用了"。
- 不越过 `critical unlocked: false` 去刷引导链分区。任何需要写 `xbl`/`abl`/`tz` 的方案，
  在拿到 9008 救砖能力之前都不做。
- 不说"再等一会儿就好了"。`fastboot boot` 这次是明确放行（`Booting OKAY`），
  如果后面编出来的固件被拒，那就是被拒，我会直接说。
