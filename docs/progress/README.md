# 进度与交接

本目录是 nx729j 的阶段性证据与交接材料。所有结论都指向可复核的日志字段，不指向截图。

**一句话状态（2026-11-17）**：官方 bring-up 阶段 1–6 通过；阶段 7 四项判据过了两项
（`launchd` 从根卷启动、Data 卷挂载），差「显示服务或控制台有输出」与「启动进度层最后检查点」。
最好的一轮跑了 **2354 秒、零崩溃**，但没有画面。

**要镜像和原始日志**：[Release `s7-handoff-2026-11-17`](https://github.com/2713385115/UniXNU-nx729j/releases/tag/s7-handoff-2026-11-17)，
五颗镜像 + 四份 32 MiB 窗口，清单与哈希见 [IMAGES](IMAGES.md)。

| 文件 | 作用 |
|---|---|
| [BOOT-LEDGER.md](BOOT-LEDGER.md) | 每次启动的 `boot_id`、头部字段、窗口时间与结论；字段读法与已证伪清单 |
| [IMAGES.md](IMAGES.md) | Release 里的五颗镜像与四份原始窗口：SHA-256、内嵌内核哈希、参数差异、刷哪颗能复现哪条结论 |
| [JOURNAL-2026-11-15_17.md](JOURNAL-2026-11-15_17.md) | 按时间记「改了什么、怎么验证、结果如何」，含失败的尝试 |
| [NEXT-STEPS.md](NEXT-STEPS.md) | 接手须知、环境前提、P0–P3 任务与验收标准 |

## 当前最好的一轮

```
镜像     out/boot-s7pmgr-skip.img
大小     81,309,696 B
SHA-256  aff8b7c2958e0026d26aa04949f6d1d77d05d6e0002e062db1681d6242ec7f75
boot_id  0x267a1232
头部     2354 s / 254 轮 / phase 5 published / write_errors 0
窗口     1875.7 s → 2388.4 s，零 corpse、零 deny、零 crash
显示     backboardd[446] 于 1894 s 启动，之后无 WindowServer / CARenderServer
```

`boot_a` 回读与该镜像逐块一致，所以「跑的是什么」这点没有疑问。

## 三个必须先读的事实

1. **屏幕不是判据**。绿条由 DarwinBoot `Entry.S` 在 `br x1` 前自绘，u055 系内核没有画条代码。
2. **日志窗口是环形缓冲**。跑久了会盖掉开头，早期阶段只能靠短窗口那一轮或 `slots.bin`。
3. **同一颗镜像可能一次稳一次不稳**。差别在引导器有没有 arm Gunyah 看门狗，内核从不喂狗；
   补丁已写好（`patches/stage7-wdt-keepalive.patch`），尚未编进内核。

## 镜像和原始窗口在哪

镜像每颗 78–89 MiB、窗口每份 32 MiB，进 git 会把仓库撑成几百 MB，所以都在 Release 里：

```bash
gh release download s7-handoff-2026-11-17 -R 2713385115/UniXNU-nx729j -D out/
```

五颗镜像分别对应哪条结论、SHA-256 与参数差异见 [IMAGES](IMAGES.md)。
最好那一轮 `0x267a1232` 的原始窗口已被之后的回读覆盖，本机无副本 —— 它的字段结论只在本目录的
台账里；要原始字节就刷 `boot-s7pmgr-skip.img` 重跑一轮。

## 复现一次取证

```bash
python tools/host/edl-read-log.py --port <COM> --scan --slots
python tools/host/edl-verify-boot.py --port <COM>          # 证明设备上跑的是哪颗镜像
python3 scripts/read-sheng-log.py work/edl-log/msgbuf.txt --decode
```

读之前先比 `boot_id`：和上一轮相同说明读到的是旧窗口，本次启动根本没发生。

## 相关文档

- 汇总进度：[STATUS-适配进度.md](../../STATUS-适配进度.md)
- 阶段 7 诊断分支：[STATUS-STAGE7-EXIT-DIAGNOSTIC-2026-11-17.md](../../STATUS-STAGE7-EXIT-DIAGNOSTIC-2026-11-17.md)
- 官方口径：[bring-up 阶段](https://unixnu.com/zh/porting/bring-up) ·
  [驱动清单](https://unixnu.com/zh/porting/drivers) ·
  [调试与日志](https://unixnu.com/zh/guide/debugging)
