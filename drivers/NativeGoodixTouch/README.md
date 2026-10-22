# NativeGoodixTouch — 红魔 8 Pro+ 触控驱动（待实现）

这是设备仓库里唯一**必须自己写**的驱动。sheng 用 Novatek NT36532E，红魔 8 Pro+
用 Goodix GT9916（`gtx8_spi`，Berlin 系列），两者除了都挂在 QUPv3 GENI SPI 上之外
没有可复用的部分。

> 状态：只有移植规格，没有实现。`scripts/check-nx729j.py` 会把这一项报成 warn，
> 不阻塞 firmware/kernel 的构建——触摸属于 bring-up 第 7 阶段。

## 1. 硬件（全部来自 nx729j.dts，见 docs/reference/nx729j-dtb-facts.md）

| 项 | 值 |
| --- | --- |
| SPI 控制器 | `/soc/qcom,qupv3_1_geni_se@ac0000/spi@a8c000`（QUPv3 SE3，SE 基址 `0xac0000`，SPI 基址 `0xa8c000` + `0x4000`） |
| compatible | `goodix,brl-d` |
| 中断 | TLMM pin 25（`<0x88 0x19 0x00>`），电平触发 |
| 复位 | TLMM pin 24（`<0x88 0x18 0x00>`） |
| 供电 | `avdd`、`iovdd`（DTS 里还有 `goodix,avdd-name`） |
| 上报坐标 | 1116 × 2480 的 **16 倍定点**：`panel-max-x = 0x45c0`（17856）、`panel-max-y = 0x9b00`（39680） |
| 固件 / 配置 blob | `goodix_firmware_9916r.bin`、`goodix_cfg_group_9916r.bin` |

坐标缩放是这里最容易踩的坑：17856 = 1116 × 16，39680 = 2480 × 16。驱动必须把它
除以 16 再按 `[0, 1116) × [0, 2480)` 归一化，否则触摸点会落在屏幕外。

## 2. 现成可复用的三块

| 复用对象 | 用途 |
| --- | --- |
| `UniXNU/NativeGeniSpi` | GENI SPI 控制器。SE3 的时钟、FIFO 和 DMA 由它负责，触控驱动只发 SPI 事务 |
| `UniXNU/NativeNovatekTouch` | 内置 XNU 驱动骨架：IOKit 匹配、GPIO 复位时序、中断处理、`IOHIDEventService` 上报、休眠策略。**结构照着抄，协议全部换掉** |
| 厂商内核 `ztemt/NX729J` | GT9916 的真实 SPI 协议（`drivers/input/touchscreen/goodix_berlin_*`）：握手、配置下发、固件升级、报文解析、触点格式 |

协议细节**不要猜**，以厂商 5.15 内核里的 `goodix_berlin` 为准。sheng 的
`NvtProtocol.c` 只提供"一个触控协议驱动长什么样"的参考，不提供 Goodix 的字节序。

## 3. 要写的文件

```
drivers/NativeGoodixTouch/
  GtxTransport.c/h      SPI 事务 + 复位/上电时序 + IRQ 注册
  GtxProtocol.c/h       握手、配置下发、报文解析 → 触点结构
  GtxTouchCore.c/h      IOKit 服务：probe / start / 中断 / IOHIDEvent 上报
  Info.plist            IOKitPersonalities（匹配 provider 与 compatible）
```

`Info.plist` 的匹配项要和 ADT 对齐：ADT 里 `arm-io/ufshc` 那种"只做类匹配"的写法
在这里不适用，触控节点需要真实的 `compatible`（`goodix,brl-d`）+ provider 名字。
参考 `UniXNU/NativeNovatekTouch` 的 `Info.plist` 与 `IOProbeScore` 取值。

## 4. 分阶段验证（不要一步到位）

1. **总线通**：只发一次 SPI 读 ID，把返回值打到 `dmesg`。ID 错就查 SE 号、
   CS 极性、`spi-max-frequency`。DTB 里 SE3 的 `spi-max-frequency` 要先读出来。
2. **复位 + 中断**：拉 RESET、等待 IRQ，确认中断真的进（打计数）。
3. **配置下发**：把 `goodix_cfg_group_9916r.bin` 塞进 payload 卷，按厂商内核的
   流程下发；失败时不要重试刷写，先确认配置版本号。
4. **单点上报**：只解码第一个触点，`sheng-touch-hid=1` 打开后观察 SpringBoard
   是否能收到点按。
5. **多点 + 手势**：最后再做坐标缩放和边缘校正。

## 5. 不写也不会卡住 bring-up

Android 的 bootloader 与 UEFI 层已经能把按键作为 HID 输入交给 UniXNU 菜单
（`offline-hid-input=1`），所以第 1–6 阶段不需要这个驱动。触摸驱动是"能进桌面
并且能点"的最后一步，不是前置条件。

## 6. 许可

本目录下的代码需与 `UniXNU/NativeNovatekTouch` 一致，采用仓库根的
BSD-2-Clause-Patent；从厂商内核移植的协议常量属于另一份许可，落代码时单独标注
来源。
