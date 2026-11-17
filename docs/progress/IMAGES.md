# 镜像与原始窗口

镜像每颗 78–89 MiB、日志窗口每份 32 MiB，进 git 会把仓库撑成几百 MB，所以它们放在
Release 资产里，仓库里只留可以逐字节校验的元数据。

Release：<https://github.com/2713385115/UniXNU-nx729j/releases/tag/s7-handoff-2026-11-17>

下载：

```bash
gh release download s7-handoff-2026-11-17 -R 2713385115/UniXNU-nx729j -D out/
# 或者直接翻页面点资产名
```

## 镜像

五颗镜像全部验证过 `boot_a` 回读逐块一致，也就是说「跑的是什么」这点没有疑问。
`size` 是 boot 镜像文件大小，`kernel` 是镜像内 `EFI/UniXNU/Payloads/kernel.macho` 解压后的
SHA-256 前 16 位 —— 同一颗内核可以配不同 args，所以镜像 sha 不同不代表内核变了。

| 资产 | 大小 | SHA-256 | 内核 | args | 对应 boot_id |
|---|---|---|---|---|---|
| `boot-s7pmgr-skip.img` | 81,309,696 | `aff8b7c2958e0026d26aa04949f6d1d77d05d6e0002e062db1681d6242ec7f75` | `fa264bc2e9d760c0` | 990 | `0x267a1232`、`0x266eff40` |
| `boot-s7v5pon.img` | 81,313,792 | `6365c1a083a0451ca566df1709c9f07eb3d8ae6150a4e80b94206e73868e7e33` | `39d1efad116155d1` | 983 | `0x1a5d5b1a`、`0x12c714bd`、`0x1aadfabe` |
| `boot-s7v5pon-gpuaddon.img` | 81,313,792 | `8f1ec64efe95d8be69f7d9ec2c84dc64193095cd229a2b8d59bbb2c2f31fd296` | `39d1efad116155d1` | 980 | `0x1b6783fe` |
| `boot-s7union-t.img` | 93,380,608 | `471588f1b015ce575f7720903db92f30bb2edeac6af92f3f69181a22b0940a88` | `73da06e268cc1fd0` | 982 | `0x1f44cebb` |
| `boot-s7trace.img` | 93,380,608 | `244b367914fd195875a442bc942baba4401c90a26b87ed1ec84bcf96b4b6f76a` | `39d1efad116155d1` | 982 | 未单独取窗口 |

### 想复现哪一条结论，就刷哪一颗

| 目标 | 刷这颗 | 结果 |
|---|---|---|
| **目前最好的一轮**（2354 s、254 轮、零崩溃、`backboardd[446]` 启动） | `boot-s7pmgr-skip.img` | 头部 `phase 5 published`、`write_errors 0`；无 WindowServer，无画面 |
| **shared cache 唯一一次映射成功**（`SRT: setup done err=0`、53 个分片 `rej=0`） | `boot-s7v5pon.img` | 短窗口 `0x1a5d5b1a` 能看到完整 `SRT: setup`；长跑 `0x12c714bd` 推到 SpringBoard |
| 验证「只加参数不加实体」不成立 | `boot-s7v5pon-gpuaddon.img` | args 有 `sheng-gpu-addon=1`，payload 里 `NGPUADD2` 计数 0，GPU 计数恒 0 |
| union SystemOS 路线（判据③ 的候选方向） | `boot-s7union-t.img` | `cache mount target /`、`probe cache dir err=0`，但 `SRT: setup` 0 条 ⇒ 相对 v5pon 是退步 |
| 追 launchd 之后的时序（带 `sheng-build-tag=s7p`、`boot-ui-focus=launchd`） | `boot-s7trace.img` | 参数 982 B，取证用，未单独编轮次 |

### 参数差异（其余 40 多项五颗都相同）

| 差异项 | pmgr-skip | v5pon | v5pon-gpuaddon | union-t | s7trace |
|---|---|---|---|---|---|
| `sheng-gpu-addon=1` | — | — | ✅ | ✅ | ✅ |
| `NGPUADD2` 实体在 payload 里 | ❌ | ❌ | ❌ | ❌ | ❌ |
| `offline-hid-home` / `offline-key-events` | ✅ | ✅ | ✅ | ❌ | ❌ |
| `cpus=1` | ✅ | — | — | — | — |
| `sheng-build-tag` / `boot-ui-focus` | — | — | — | `s7u` / `launchd` | `s7p` / `launchd` |

`sheng-gpu-addon=1` 那一行全是「参数在、实体不在」的状态 —— 这是当前最该补的缺口，
不是参数问题，是 `NGPUADD2` addon 没有被打进 payload。

## 原始日志窗口

`nx729j-<boot_id>-log-window.bin`，每份 33,554,432 B，是 `userdata` 尾部 32 MiB 窗口的原始回读：

| 资产 | boot_id | 头部 | 为什么留它 | SHA-256 |
|---|---|---|---|---|
| `nx729j-0x1a5d5b1a-log-window.bin` | `0x1a5d5b1a` | 27 s / 12 轮 | **唯一一次 `SRT: setup` 全绿**，判据③ 的参考样本 | `7287ee81c35416d404221261aec366c373530039868154e263156178033e2c27` |
| `nx729j-0x12c714bd-log-window.bin` | `0x12c714bd` | 490 s / 73 轮 / phase 5 | 推到 SpringBoard，pid 397；corpse 66 全因 sandbox 拒读 `/private/preboot/.../dyld_shared_cache_arm64` | `dc8bb492277398a74e5e036be33f88683495d72de55cc42b5998660e28d535a0` |
| `nx729j-0x1f44cebb-log-window.bin` | `0x1f44cebb` | 12 s / 6 轮 | union 生效但 `SRT: setup` 0 条，39.4 s 结束 | `cf2aa62889e8326cd7af12c3e364f0e98aa18ed9e31484796f36f2ab73e29df5` |
| `nx729j-0x1aadfabe-log-window.bin` | `0x1aadfabe` | 10 s / 5 轮 | 看门狗 armed 的证据：`gunyah_wdt status=0 since_ping=22229ms` | `2d227f8d1aae26c142f3f628e202d98e291ff97a219cdeb03b806817ef3978f3` |

解析：

```bash
python tools/host/decode-shlog.py nx729j-0x1a5d5b1a-log-window.bin          # 头部 + msgbuf
python tools/host/decode-shlog.py nx729j-0x1a5d5b1a-log-window.bin --slots  # 逐槽状态时间线
```

这四轮的 msgbuf 文本也直接进了仓库（`docs/progress/logs/nx729j-0x<boot_id>-msgbuf.txt`，
每份 35–132 KB），只是想 grep 某条日志时不必下 32 MiB 的窗口；要做严格结论仍然用原始窗口，
因为文本是解码结果，环形缓冲的覆盖边界只有原始字节能确认。

窗口是环形缓冲，跑久了会覆盖开头。要看全程用 `slots`，不要用「某标记计数为 0」下结论。

## 没有随 Release 发布的两样

1. **`0x267a1232` 的原始窗口**。最好那一轮的回读文件后来被 `0x1aadfabe` 等轮次覆盖，
   本机已无副本；该轮的字段结论只存在于 [BOOT-LEDGER](BOOT-LEDGER.md)。
   要拿原始字节，刷 `boot-s7pmgr-skip.img` 重跑一轮即可。
2. **`userdata-root-fixed.img` / `userdata-root.simg`**（各 8.7 GiB）。太大，而且可以用
   `scripts/build-userdata-root-image.py` 从原始镜像重建；manifest 合法性已由
   `tools/host/check-root-manifest.py` 离线校验通过，结论写在 `porting.toml` 的 `storage` 区。

## 刷入与回读

```bash
# 刷（会自动确认 9008 端口消失）
python tools/host/edl-flash-boot.py --port '\\.\COM3' --image out/boot-s7pmgr-skip.img --power=reset,1

# 证明设备上跑的是哪颗
python tools/host/edl-verify-boot.py --port '\\.\COM3'

# 回读窗口
python tools/host/edl-read-log.py --port '\\.\COM3' --scan --slots
```

**刷机中途不要切 EDL**，否则头部记 `write_errors`，那一轮的窗口不能当判据用。
