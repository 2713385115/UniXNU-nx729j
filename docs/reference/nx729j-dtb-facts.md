# nx729j 硬件事实表

本文件记录 `board.toml`、`scripts/make-nx729j-adt.py` 与触控/显示移植里每一个
设备相关数值的**出处**。规则：没有出处的数字不写进仓库。

证据来源只有两类：

| 代号 | 来源 |
| --- | --- |
| `[DTB]` | `UniXNU/uefi-sheng` 的 `Resources/DTBs/nx729j.dts`（真机 ABL 交给 payload 的设备树，由社区 dump 后提交） |
| `[UEFI]` | `UniXNU/uefi-sheng` 的 `Platforms/ZTE/nx729jPkg`（DSC / FDF / 配置文件） |
| `[MEASURE]` | 必须在真机上量，`scripts/collect-nx729j.py` 负责采集 |

---

## 1. 板级身份

| 项 | 值 | 出处 |
| --- | --- | --- |
| 型号 | NX729J（红魔 8 Pro / 8 Pro+ / 8S Pro / 8S Pro+ 同一 codename） | `[UEFI]` DSC SMBIOS |
| SoC | SM8550-AB（`SOC_TYPE = 0`） | `[UEFI]` `nx729j.dsc` |
| `qcom,msm-id` | `0x207` | `[DTB]` 根节点（ADT 的 `chip_id` 取此值） |
| `qcom,board-id` | `0x10008` | `[DTB]` 根节点 |
| 内核 | 5.15.41-android13，`arch/arm64/configs/nx729j_defconfig` | 厂商开源仓库 `ztemt/NX729J` |
| 屏幕 | 1116 × 2480，6.97 英寸，71 × 157 mm 有效区 | `[DTB]` 面板节点 |
| 触控 | Goodix GT9916（`gtx8_spi`，Berlin 系列，`goodix,brl-d`） | `[DTB]` + Device Info HW 报告 |
| 存储 | UFS 4.0，单 LUN 0（`/dev/block/sda`） | `[DTB]` `ufshc@1d84000` |
| 内存 | 16 GiB LPDDR5X | `[DTB]` `ddr-regions` |

## 2. 内存与中断（SoC 级，与 sheng 共用）

| 项 | 值 | 出处 |
| --- | --- | --- |
| DRAM base | `0x80000000` | `[DTB]` `memory/reg` |
| DDR 分区 | `0x80000000`+2 GiB、`0x880000000`+6 GiB、`0xa00000000`+8 GiB = 16 GiB | `[DTB]` `ddr-regions` |
| 可用内存（Android 口径） | `mem=12592218112B` | `[DTB]` `chosen/bootargs` |
| GIC distributor | `0x17100000` / `0x10000` | `[DTB]` `/soc/interrupt-controller@17100000` |
| GIC redistributor | `0x17180000` / `0x200000`，stride `0x40000` | `[DTB]` 同上 |
| CPU timebase | 19200000 Hz（`0x124f800`） | `[DTB]` `arch_timer` 节点 |
| CPU affinity | `0x000 0x100 0x200 0x300 0x400 0x500 0x600 0x700` | `[DTB]` `/cpus/cpu@*` |
| PSCI | `arm,psci-1.0`，`method = "smc"` | `[DTB]` `/psci` |
| UFS HC | `0x1d84000` + `0x3000`（ice `0x1d88000`、ice_hwkm `0x1d90000`） | `[DTB]` `ufshc@1d84000` |
| UFS PHY | `0x1d80000` + `0x2000` | `[DTB]` `ufsphy` |
| QUPv3 SE 基址 | `0xac0000` | `[DTB]` `qcom,qupv3_1_geni_se@ac0000` |

## 3. 显示

面板节点：`qcom,mdss_dsi_bf068_rm692h0_6p97_magic_dsc_cmd`
（BOE `bf068` 屏体 + Raydium `RM692H0` 驱动 IC，DSC，command mode）

| 项 | 值 | 备注 |
| --- | --- | --- |
| 分辨率 | `0x45c` × `0x9b0` = 1116 × 2480 | |
| 刷新率档位 | 60 / 90 / 120 Hz（`0x3c / 0x5a / 0x78`） | ADT 里 `sheng-vsync=120` 取最高档 |
| DSC | 10 bit/px、10 bit/component、slice `0x22e` × `0x14`、2 slice/pkt | 双 slice 合计 1116 像素 |
| HFP / HBP / HPW | `0xf8` / `0x150` / `0x08` | 60 Hz timing@0 |
| VFP / VBP / VPW | `0x38` / `0x08` / `0x08` | |
| PHY timings | `[00 2b 0b 0b 1c 27 0b 0c 0b 02 04 00 23 10]` | |
| display-topology | `<2 2 1>` | |
| mdp-transfer-time | `0x185b` µs | |
| 亮度 | min `0x01`、max `0xdbb`、DBV 反相、12 bit | |
| 初始化序列 | 完整 `qcom,mdss-dsi-on-command`，**1349 字节** | **在 DTB 里，不需要从固件 XML 反推** |

### 面板怎么选出来的（重要）

`nx729j.dts` 里不是只有一个面板，而是整套**面板库**（11 个 `*_dsc_cmd` / `*_dsc_vid`
节点）。ABL 开机时按屏体 ID 选一个，选择结果写在设备树里：

```
panel = <0x6b2>;          (line 20964)
→ 0x6b2 是 phandle，对应 qcom,mdss_dsi_bf068_rm692h0_6p97_magic_dsc_cmd
```

所以 `bf068` 在树里有两个变体，**必须按 phandle 选，不能按名字 grep 第一个**：

| 节点 | panel-name | 帧率 | 是否在用 |
| --- | --- | --- | --- |
| `qcom,mdss_dsi_bf068_rm692h0_6p97_1116p_dsc_cmd` | `BF068_RM692H0_Full_Screen_1116P_DSC_FHD_6P7Inch` | 60 / 120 | 否（phandle `0x68b`） |
| `qcom,mdss_dsi_bf068_rm692h0_6p97_magic_dsc_cmd` | `BF068_RM692H0_Full_Screen_MAGIC_DSC_FHD_6P7Inch` | 60 / 90 / 120 | **是**（phandle `0x6b2`） |

重新生成这份数据：

```bash
python3 scripts/extract-panel-timings.py --dts nx729j.dts --active \
    --output docs/reference/panel/nx729j-panel.json
```

`--active` 就是走上面那条 `panel = <phandle>` 追踪；不带它就得自己指定 `--node magic`。

### 三档时序（由 `extract-panel-timings.py` 算出，非估算）

| 档位 | H 前/后/脉宽 | V 前/后/脉宽 | 行总 | 场总 | 像素时钟 | init |
| --- | --- | --- | --- | --- | --- | --- |
| 60 Hz | 248 / 336 / 8 | 56 / 8 / 8 | 1708 | 2552 | 261.53 MHz | 1349 B |
| 90 Hz | 248 / 336 / 8 | 56 / 8 / 8 | 1708 | 2552 | 392.29 MHz | 1349 B |
| 120 Hz | 26 / 76 / 8 | 56 / 8 / 8 | 1226 | 2552 | 375.45 MHz | 无（复用 60 Hz 的） |

注意 120 Hz 的消隐换了一套（`hbp 0x4c / hfp 0x1a`），**不能把 60 Hz 的 porch 套到
120 Hz**，否则时钟算出来是 364 MHz、帧率对不上。

> 结论：显示参数可以直接从 `nx729j.dts` 生成，不必像 sheng 那样去厂商 UEFI 里
> 找 `Panel_*.xml`。`docs/reference/panel/` 里现在放的就是提取结果。


## 4. 触控

| 项 | 值 |
| --- | --- |
| 控制器 | `/soc/qcom,qupv3_1_geni_se@ac0000/spi@a8c000`（QUPv3 SE3，SPI） |
| compatible | `goodix,brl-d` |
| IRQ GPIO | `<0x88 0x19 0x00>`（TLMM pin 25，电平触发） |
| RESET GPIO | `<0x88 0x18 0x00>`（TLMM pin 24） |
| 供电 | `avdd`（`goodix,avdd-name`），另有 iovdd |
| 固件/配置 | `goodix_firmware_9916r.bin` / `goodix_cfg_group_9916r.bin` |
| 上报坐标 | `panel-max-x = 0x45c0` = 17856 = 1116 × 16，`panel-max-y = 0x9b00` = 39680 = 2480 × 16 |

> 坐标是 **16 倍缩放**的原始值，驱动必须除 16 再按 1116 × 2480 归一化。
> 这是 Goodix 在 9916 上常见的 16x 定点格式，若直接当像素用，触摸点会跑到屏幕外。

## 5. 启动与固件

| 项 | 值 | 出处 |
| --- | --- | --- |
| UEFI FD 基址 | `0xA7100000`，大小 `0x300000` | `[UEFI]` `Resources/Configs/nx729j.toml` |
| UEFI 栈 | `0xA760D000`，主核栈 `0x40000` | `[UEFI]` `nx729j.dsc` |
| FD 输出名 | `Build/nx729jPkg/{target}_CLANGPDB/FV/SILICIUM_UEFI.fd` | `[UEFI]` `nx729j.fdf` `[FD.SILICIUM_UEFI]` |
| boot image header | version 4，kernel gzip，append_dtb | `[UEFI]` `nx729j.toml` |
| splash 区域 | `/reserved-memory/splash_region` = `0xb8000000` + `0x2b00000` | `[DTB]`，**已存在**，因此 board.toml 不再追加 `[[bootimg.dtb_node]]` |
| BL 解锁 | `fastboot oem nubia_unlock NUBIA_NX729J` → `fastboot flashing unlock` | 官方开放方式，解锁会清数据 |

## 6. 需要真机测量

| 项 | 采集方式 | 填到 |
| --- | --- | --- |
| `userdata` 的 `first_lba` / `last_lba` | `python3 scripts/collect-nx729j.py` | `board.toml [userdata]` |
| 16 GiB 机型的实际可用内存上限 | 同上（`MemTotal`） | 交叉核对 `payload.dram_size` |
| 面板是否真的点亮（splash 是否生效） | 首次 `fastboot boot` 目视 | 无需进配置文件 |
