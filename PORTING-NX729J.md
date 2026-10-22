# 红魔 8 Pro / 8 Pro+（NX729J）UniXNU 移植笔记

> 目标：把 [UniXNU](https://github.com/UniXNU/UniXNU) 跑到 NX729J 上（SM8550，16+512，
> Adreno 740）。本文所有数值都标注了出处，未标注的都在"待测量"清单里，没有编造。

## 0. 结论先说

这个机型的移植难度比想象中低，因为**最重的两块已经有人做完了**：

1. **UEFI 固件已经有了。** `UniXNU/uefi-sheng`（Mu-Silicium 派生）里已经有
   `Platforms/ZTE/nx729jPkg`——完整的 DSC/FDF、内存映射、配置映射、SMBIOS
   （`RedMagic 8 Pro`）、`1116×2480` 帧缓冲、`SM8550-AB`，还有从真机 dump 出来的
   `Resources/DTBs/nx729j.dts`。所以**不需要另建 `uefi-nx729j` 仓库**，
   `--uefi` 指向 `uefi-sheng` 即可。
2. **XNU 侧的 SM8550 代码可以整套复用。** GIC、arch timer、PMGR/RPMh、UFS、
   DPU、GMU、Adreno 740 全是 SoC 级组件，红魔 8 Pro+ 与小米平板 6S Pro 同芯同 GPU。

真正要做的是三件事：

| 要做的 | 工作量 | 本仓库对应文件 |
| --- | --- | --- |
| 设备仓库（board.toml / defconfig / ADT 生成器） | 小，已写好 | `board.toml`、`configs/`、`scripts/make-nx729j-adt.py` |
| 量一次 userdata 分区几何 | 5 分钟，免 root，必须在真机上做 | `tools/nx729j.py measure` |
| Goodix GT9916 触控驱动 | **大，唯一真正的开发量** | `drivers/NativeGoodixTouch/`（目前只有规格） |

外加一个前置条件：**解 BL 锁**。

## 1. 为什么能复用 sheng

| 维度 | 小米平板 6S Pro (sheng) | 红魔 8 Pro+ (nx729j) | 结论 |
| --- | --- | --- | --- |
| SoC | SM8550 | SM8550-AB | 同 |
| `qcom,msm-id` | `0x207` | `0x207` | 同 |
| GPU | Adreno 740 | Adreno 740 | 同，NativeAdreno 直接可用 |
| GIC / timer | `0x17100000` / 19.2 MHz | `0x17100000` / 19.2 MHz | 同 |
| UFS | `ufshc@1d84000` | `ufshc@1d84000` | 同 |
| DRAM base | `0x80000000` | `0x80000000` | 同 |
| 面板 | 3048×2032 平板屏 | 1116×2480 BOE bf068 + RM692H0，DSC，command mode | **不同** |
| 触控 | Novatek NT36532E | Goodix GT9916（`gtx8_spi`） | **不同，要写驱动** |
| 刷新率 | 144 Hz | 60/90/120 Hz（DTB 档位） | 参数不同 |
| userdata 几何 | 已知 | **待测量** | 必须量 |

## 2. 这个仓库是什么

它是 **sheng 仓库的覆盖层（delta）**，不是一份能直接 `unixnu defconfig` 的完整仓库：
包里那些 GPU 探针、kit 源码、Data seed 工具都在 `UniXNU-sheng`，重复一遍没有意义。
两种落地方式：

### 方式一（推荐）：fork sheng，再用本仓库覆盖

```bash
git clone https://github.com/UniXNU/UniXNU-sheng UniXNU-nx729j && cd UniXNU-nx729j
git submodule update --init --recursive
python3 /path/to/UniXNU-nx729j/scripts/apply-to-sheng-fork.py --target . --dry-run
python3 /path/to/UniXNU-nx729j/scripts/apply-to-sheng-fork.py --target .
git checkout -b nx729j && git status
```

脚本只会写 8 个文件，并且会先校验目标确实是 sheng 仓库（`board.toml` 里有
`name = "sheng"`），不会误覆盖别的仓库。社区的 fuxi 移植也是同样的路子
（fork sheng + 改 board.toml）。

### 方式二：保持独立小仓库

用文档里的本地模块覆盖机制，把 sheng 当外部依赖：

```bash
./unixnu defconfig nx729j_defconfig -D ../UniXNU-nx729j \
    --local-kit ../UniXNU-sheng/kits \
    --local-driver ../UniXNU-sheng/drivers
```

`--local-*` 的优先级见官方 Porting/Device repository 文档。缺点：`[kernel] archives`
里 `build-sheng-platform-power.py`、`build-native-legacy-display.py` 等脚本位于
sheng 的 `scripts/`，需要手工复制进本仓库，否则内核步骤会找不到脚本。**不想折腾
就用方式一。**

## 3. 目录

```
UniXNU-nx729j/
  board.toml                       板级描述：IPSW、userdata 几何、内核、固件、payload、ADT
  configs/nx729j_defconfig         内核配置（与 sheng 相同，因为全是 SoC 级开关）
  scripts/
    make-nx729j-adt.py             生成 XNU 需要的 Apple 格式设备树（已实跑验证）
    check-nx729j.py                预检：board.toml 一致性 + UEFI 内存映射 vs DTB 保留区
    collect-nx729j.py              真机采集 GPT / userdata 几何 / 内存总量
    apply-to-sheng-fork.py         把本仓库覆盖到 sheng fork 上
  package/unixnu.toml              UniXNU 启动菜单
  drivers/NativeGoodixTouch/       触控驱动规格（待实现）
  docs/reference/
    nx729j-dtb-facts.md            每个数值的出处（[DTB] / [UEFI] / [MEASURE]）
```

## 4. 与 sheng 的具体差异

| 位置 | sheng | nx729j | 依据 |
| --- | --- | --- | --- |
| `[board] name/soc/kit` | sheng | nx729j | — |
| `[firmware] dsc` | `Platforms/Xiaomi/shengPkg/sheng.dsc` | `Platforms/ZTE/nx729jPkg/nx729j.dsc` | uefi-sheng |
| `[firmware] config` | `sheng` | `nx729j` | `Resources/Configs/nx729j.toml` |
| `[firmware] packages` | `Platforms/Xiaomi`、`Silicium-ACPI/Platforms/Xiaomi` | `Platforms/ZTE`、`Silicium-ACPI/Platforms/ZTE` | 同上 |
| `[payload] volume` | `SHENGESP` | `NX729JESP` | 11 字符 FAT 标签限制 |
| `[payload] dram_size` | `0x80000000`（2 GiB 窗口） | `0x400000000`（16 GiB） | `[DTB] ddr-regions` |
| `sheng-vsync` | `144` | `120` | `[DTB]` 档位 60/90/120 |
| `[payload.adt] tool` | `make-sheng-adt.py` | `make-nx729j-adt.py` | 身份 + 面板几何 |
| ADT `model/compatible` | `xiaomi,sheng` | `nubia,nx729j` | `[DTB]` |
| ADT `artwork-device-subtype` | `2732` | `2480` | 面板高度 |
| ADT `artwork-scale-factor` | `2` | `3` | 1116/3 = 372 pt |
| `[[bootimg.dtb_node]]` | 需要追加 `splash_region` | **不需要** | `[DTB]` 里已有 `cont_splash_region` 0xb8000000+0x2b00000 |
| `CONFIG_XNU_*` | — | 不变 | 全是 SoC 级 |

`board.toml` 里保留了 `sheng-` 前缀的内核参数（`sheng-pmgr=2`、`sheng-touch=4` 等），
因为解析它们的代码来自 sheng 套件。**这些参数名与机型无关**，等套件改名时再一起改，
现在改反而会让驱动读不到。

## 5. 准备工作

### 5.1 解锁 BL（会清空数据，先备份）

```bash
# 开发者选项里打开 OEM 解锁 + USB 调试，关机后 音量下 + 电源 进 fastboot
fastboot oem nubia_unlock NUBIA_NX729J
fastboot flashing unlock
```

解锁后开机会常驻黄色警告，正常现象。**解锁状态下的保修与指纹/安全相关问题自己承担。**

### 5.2 全分区备份（强烈建议）

用 9008 工具箱（redmagic 工具箱一类）导出 `boot`、`init_boot`、`persist`、
`modem`、`abl`、`xbl`、`super` 的原样镜像。NX729J 的坑之一是"解锁后卡 fastboot"，
有原厂 boot 备份就能回滚。

### 5.3 取厂商完整包

`https://romdownload.nubia.com/红魔8Pro/.../NX729J-update.zip`（或 9008 全量包）。
用途：确认面板/触控固件版本，必要时取 `goodix_firmware_9916r.bin`。

### 5.4 构建环境

官方 CI 跑在 macOS runner 上；Linux 亦可。Windows 上这条链路（XNU + EDK2 +
mtools）建议走 **WSL2 Ubuntu**；本仓库的 Python 脚本两边都能跑。

需要：Python 3.11+（脚本用到 `tomllib`）、`git`、`fdtget`（`device-tree-compiler`，
预检的 DTB 交叉校验用，缺了只降级为警告）。

## 6. 构建

```bash
git clone --recurse-submodules https://github.com/UniXNU/UniXNU
git clone https://github.com/UniXNU/uefi-sheng
cd UniXNU
git submodule update --init --recursive

./unixnu defconfig nx729j_defconfig -D ../UniXNU-nx729j
./unixnu fetch
./unixnu ipsw fetch                    # iPad7,1 17.7.11，约 7 GB
./unixnu build kernel
./unixnu build package
./unixnu build firmware --uefi ../uefi-sheng
./unixnu build payload --unique-chip-id <hex> --trust-cache <bundle> --data-ramdisk <seed>
./unixnu bootimg --uefi ../uefi-sheng --dtb ../uefi-sheng/Resources/DTBs/nx729j.dtb
```

构建前先跑预检，它会挡住"userdata 没量"和"内存映射与 DTB 对不上"这两类致命错误：

```bash
python3 scripts/check-nx729j.py --uefi ../uefi-sheng
```

`--unique-chip-id` 是每台设备自己的平台种子，不在任何仓库里。

## 7. 首次启动

```bash
fastboot boot out/nx729j/boot/boot.img
```

临时启动不写分区，失败也能直接长按电源重启回 Android。**不要一上来就 flash。**

- 若 `fastboot boot` 被拒（部分努比亚 ABL 限制未签名 boot），退路是：先把
  `boot.img` 写入 `boot` 分区（务必先备份原厂 boot），或先只刷 UEFI 的 payload 卷。
- 首次启动用 userdata 镜像：

```bash
./unixnu build userdata
fastboot flash userdata out/nx729j/userdata/userdata-root.img
fastboot reboot bootloader
fastboot boot out/nx729j/boot/boot.img
```

## 8. 分阶段 bring-up（判据与对策）

| 阶段 | 期望现象 | 卡住时先看什么 |
| --- | --- | --- |
| 1 固件 | 开机进 UniXNU 菜单 | `nx729jPkg` 的内存映射与 DTB 保留区（预检会比对）；`0xA7100000/0x300000` FD 窗口 |
| 2 帧缓冲 | 菜单有画面 | 面板是否 stay on：`cont_splash_region` 是否被正确识别；1116×2480 竖屏 |
| 3 payload | 菜单能读到 `NX729JESP` 卷 | FAT 标签 ≤11 字符；`--dtb` 用的是 `Resources/DTBs/nx729j.dtb` |
| 4 内核入口 | 出现 XNU 早期日志/panic 屏 | ADT 是否正确附加；`panic_screen=true` 下能在屏上看到 panic 文本 |
| 5 platform expert | 过 `AppleARMPlatform`，出现 IOPlatformUUID | `unique-chip-id`+`chip-id` 必须同时给；`/psci` method=smc |
| 6 存储 | `native-ufs-root=1` + `sheng-pmgr=2` 挂上 UFS | UFS 时钟/PHY 在 sheng 代码里写死为 SM8550 值，正常应直接可用 |
| 7 root/userland | 进 Setup，能跳过，SpringBoard 起来 | 信任缓存哈希是否在 `trust_cache_sha256` 白名单里；Data seed 是否 persistent |
| 8 输入 | 触摸可点 | 写 Goodix GT9916 驱动（见 `drivers/NativeGoodixTouch/README.md`） |
| 9 GPU | 合成走 Adreno 740 | sheng 的 GPU 链路参数（`sheng-gpu-*=…`）保持原值 |

参考 sheng 的实测状态：它已经到"GPU 合成桌面 + 逐进程 Metal 设备"。红魔 8 Pro+
只要面板时序对上、触控驱动写出来，天花板与 sheng 相同。

## 9. 还没做 / 需要你补的

| 项 | 说明 |
| --- | --- |
| `userdata` LBA | 跑 `python tools/nx729j.py measure --write board.toml`（免 root；`scripts/collect-nx729j.py` 是 root/GPT dump 路线）。当前是 `0x0`：`--require-userdata-geometry` 会拒绝，默认预检只警告，里程碑 A 不需要它 |
| Goodix GT9916 驱动 | 唯一需要真正开发的模块，协议以厂商 5.15 内核 `goodix_berlin` 为准 |
| 面板显示输入 | DTB 里已有完整初始化序列与时序（见 facts 表），但把它变成 `build-native-legacy-display.py` 的设备输入还没做；在此之前画面停留在 UEFI 帧缓冲 |
| 亮度 / AOD / HBM | DTB 里有 ZTE 私有命令（`zte,mdss-dsi-hbm-on-commands` 等），属于后期打磨 |
| 8S Pro / 8S Pro+ | 同为 NX729J，面板与触控一致，理论上同一份移植；`board-id` 可能不同，先量 GPT |
| 256 GB / 12 GB SKU | userdata 几何与 `dram_size` 都不同，需单独一组值 |

## 10. 风险

- **变砖风险真实存在。** 只做 `fastboot boot`（临时启动）永远不改分区，是最安全的做法；
  每次 `flash` 之前确认有对应分区的原厂镜像。
- **保修与安全**：解锁 BL 会清数据、常驻警告，可能影响指纹/支付类应用的完整性校验。
- **不是模拟器**：本机没跑通之前，本文的阶段表只是"应然"，不是"已验证"。
  我没有红魔 8 Pro+ 实机，所有 `[DTB]`/`[UEFI]` 数值来自社区已提交的 dump，
  `[MEASURE]` 项必须你自己量。
- **法律**：UniXNU 本身是 BSD-2-Clause-Patent；但它要你自备 IPSW 与平台种子，
  自行确认在你所在地区的合规性，不要分发 Apple 私有文件或厂商专有 blob。
