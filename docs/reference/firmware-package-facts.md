# 从官方刷机包里读出来的硬件事实

来源：`D:\刷机\10.0.11_A分区\`（红魔 8 Pro+ 的 QFIL/DirtyFrag 完整包）。
这个包比任何公开资料都可靠——它是这台机器出厂时真正写进去的东西。

下面每条都标了是从哪个文件读出来的，你可以自己复核。

---

## 1. 分区几何（这条解决了移植里最大的空白）

`userdata` 的起始位置是**确定的**，不需要在手机上量：

| 证据 | 内容 |
|---|---|
| `gpt_main0.bin` 第 11 号表项 | `label="userdata"`，`first_lba = 2712360` |
| `rawprogram0.xml` | `<program label="userdata" start_sector="2712360" num_partition_sectors="0">` |
| `patch0.xml` | "Update last partition 12 'userdata' with actual size in Primary Header" → 值 `NUM_DISK_SECTORS-6.` |

三处一致。所以：

```
first_lba = 2712360 = 0x296328          ← 固定，不随机型容量变化
last_lba  = 总扇区数 - 6                 ← 取决于你买的是 512G 还是别的容量
lba_bytes = 4096                        ← 这个包是 4 KiB 逻辑块，不是 512！
```

**`lba_bytes = 4096` 这条特别容易错。** 手机 UFS 的 LUN 0 按 4 KiB 逻辑块寻址，
`rawprogram0.xml` 里每个分区都写着 `SECTOR_SIZE_IN_BYTES="4096"`。
按 512 去算，`first_lba` 的含义就整体错 8 倍。

需要说清楚的是：这个几何**只用来算容量**。UniXNU 里唯一读它的地方是
`scripts/unixnu/userdata.py`：

```python
capacity = (spec['last_lba'] - spec['first_lba'] + 1) * spec.get('lba_bytes', 4096)
...
if total > capacity:
    raise Error('the image is larger than the userdata partition')
```

也就是说填错不会写坏任何分区，只会让"镜像装不装得下"这个检查失效。
真正写入靠的是 `fastboot flash userdata`，它写整块分区，跟 LBA 无关。
所以这一项是**精度问题，不是安全问题**——但填对仍然重要，因为它是唯一能提前
发现"镜像比分区大"的地方。

LUN 0 的完整分区表（从 `gpt_main0.bin` 直接解出来的）：

| # | label | first_lba | last_lba | 大小 |
|---|---|---|---|---|
| 0 | ssd | 6 | 7 | 8 KiB |
| 1 | persist | 8 | 8199 | 32 MiB |
| 2 | misc | 8200 | 8455 | 1 MiB |
| 3 | parameter | 8456 | 8711 | 1 MiB |
| 4 | keystore | 8712 | 8839 | 512 KiB |
| 5 | frp | 8840 | 8967 | 512 KiB |
| 6 | super | 8968 | 2630407 | 10 GiB |
| 7 | vbmeta_system_a | 2630408 | 2630423 | 64 KiB |
| 8 | vbmeta_system_b | 2630424 | 2630439 | 64 KiB |
| 9 | metadata | 2630440 | 2646823 | 64 MiB |
| 10 | rawdump | 2646824 | 2712359 | 256 MiB |
| 11 | **userdata** | **2712360** | 总扇区-6 | 剩余全部 |

注意 `userdata` 上面紧挨着的是 `rawdump`（256 MiB），而 `userdata` 的下界刚好
是 `rawdump` 的最后一格 +1。这个数是从 GPT 表项直接读出来的，不是我推测的。

`last_lba` 仍然需要手机回答，因为 Qualcomm 的包把 userdata 定义成"剩下的全给它"
（`num_partition_sectors="0"`），磁盘总容量不在包里。连上手机跑一句就有了：

```
python tools/nx729j.py measure --write UniXNU-nx729j\board.toml
```

---

## 2. ABL 不是老式 lk，是 UEFI

`abl.img` 表面上看是 1 MiB 的二进制，其实结构是：

```
偏移 0x0000  ELF32 头（PT_LOAD 到 0x9FA00000）
偏移 0x1000  UEFI Firmware Volume（FvLength = 0x32000，签名 _FVH）
              └─ 1 个 FFS 文件，里面是 LZMA 压缩段
                 └─ 解开是 688328 字节，一个嵌套 FV
```

所以 `strings abl.img` 什么也搜不到——所有内容都是 LZMA 压过的。
解开之后能看到它编译自：

```
/home/zte/workspace/NX729S_V_SM8550_TARGET_V_TA_20250310_12_USER/
    android/kernel_platform/bootable/bootloader/edk2/QcomModulePkg/
```

**这是 EDK2 的 QcomModulePkg**，和 `uefi-sheng` 里 `Platforms/ZTE/nx729jPkg`
是同一套代码谱系。这也解释了为什么社区能做出 nx729jPkg——照着这个 ABL 抄的。

> 顺带一个值得留意的点：编译路径里写的是 **NX729S**（红魔 8S Pro），
> 而包本身是给 NX729J 的。努比亚很可能让 8 Pro / 8S Pro 共用同一份 ABL。
> 所以 `fastboot getvar product` 有可能回 `NX729S` 而不是 `NX729J`，
> 这不是你手机有问题。跑 `probe` 会把真实值打出来。

解开后还能看到这些只属于努比亚的东西：

| 字符串 | 含义 |
|---|---|
| `oem select-display-panel` | 努比亚自定义 OEM 命令，用来选屏幕 |
| `oem disable-charger-screen` / `oem enable-charger-screen` | 关机充电画面 |
| `oem device-info` | 解锁状态查询 |
| `Splash memory region after updating:` | ABL 会**改写** DTB 里的 splash 区域 |
| `/reserved-memory/demura_region_1` | ABL 会往 DTB 里写 demura（屏幕均匀性）数据 |
| `Could not update demura panel id` | 面板 ID 也是 ABL 填进 DTB 的 |
| `State: Unlocked, AvbSlotVerify returned %a, continue boot` | 解锁状态下校验失败也继续启动 |

**最后一条对我们是好消息**：解锁后 AVB 校验失败不会拦住启动。
但也要注意 `demura` / `splash` 那几条——ABL 在把 DTB 交给上层之前会往里塞东西，
如果拿到的 DTB 里没有对应节点，它可能打印错误或者跳过，这会影响屏幕点亮。
`nx729j.dtb` 里已经有 `cont_splash_region`，所以这条目前不是问题。

---

## 3. UEFI 固件的位置和大小，和社区配置对得上

`uefi.img` 同样是 ELF 包 UEFI FV，PT_LOAD 到 `0xA7000000`。

对照 `uefi-sheng` 的 `Resources/Configs/nx729j.toml`：

```toml
[uefi_fd]
base = 0xA7100000
size = 0x300000
```

固件卷要放在 `0xA7100000`，长度 3 MiB，正好落在原厂 `uefi.img` 的载入地址上方
1 MiB 处。这说明社区那份配置是照着真实内存布局做的，不是猜的。
栈在 `0xA760D000`，也在 3 MiB 区域内。

---

## 4. 屏幕：阶段 A 不需要面板参数

`nx729jPkg/nx729j.dsc` 里有：

```
USE_CUSTOM_DISPLAY_DRIVER      = 0
gSiliciumPkgTokenSpaceGuid.PcdFrameBufferWidth|1116
gSiliciumPkgTokenSpaceGuid.PcdFrameBufferHeight|2480
gSiliciumPkgTokenSpaceGuid.PcdFrameBufferColorDepth|32
```

也就是说阶段 A 直接用 bootloader 已经点亮的帧缓冲，只有 1116×2480×32bpp
的简单读写。**这代表"屏幕上出现 UniXNU 菜单"这一步不需要碰 DSI、不需要面板初始化
序列、不需要 DSC。** 我之前提取的 `docs/reference/panel/nx729j-panel.json`
是给后面真正接管屏幕时用的，阶段 A 用不到。

---

## 5. `dtbo.img` 里有 29 个 overlay

`dt_table` 头（大端）：`magic=0xd7b7ab1e`，29 个表项，总长 7.8 MB。
这些是努比亚按硬件版本/面板 ID 下发的叠加层，由 ABL 的 `oem select-display-panel`
挑一个合进主 DTB。阶段 A 不用动它，但以后如果要接管屏幕，得先搞清哪一条是
你这块 BOE BF068 面板对应的。

---

## 6. 这个包能当"后悔药"

`10.0.11_A分区\` 是完整包，包含：

- `super.img` 10 GiB —— 整个安卓系统
- `boot.img` / `init_boot.img` / `dtbo.img` / `vbmeta*.img` —— 启动链
- 全部固件分区（`abl` `xbl` `hyp` `tz` `modem` `dsp` `bluetooth` …）
- `rawprogram*.xml` + `patch*.xml` + `gpt_*.bin` —— 刷写脚本和分区表

最坏的情况（把 boot 分区写坏了、开不了机），用 QFIL 或者 `fastboot flash`
按这套包重刷，就能回到出厂的安卓。**这也是为什么阶段 A 一定要用
`fastboot boot` 而不是 `fastboot flash`——`boot` 一个字节都不写。**
