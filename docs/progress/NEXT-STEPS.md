# 接手须知与下一步

目标：让这台 nx729j 通过官方 bring-up 阶段 7。现在 1–6 通，阶段 7 的四项判据过了两项
（`launchd` 起、Data 挂载），差③显示服务有输出、④启动检查点。详细数字见
[BOOT-LEDGER](BOOT-LEDGER.md)，历史决策见 [JOURNAL](JOURNAL-2026-11-15_17.md)。

## 一、环境前提（不满足就先解决这些）

| 项 | 事实 |
|---|---|
| 内核构建 | 只能在 macOS runner 上做。目标机是 Windows，没有 clang、没有 hypervisor，`make kernel` 跑不了 |
| CI 仓 | `2713385115/UniXNU`（内核与 XNU 补丁）；设备仓 `2713385115/UniXNU-nx729j` 只放配置与驱动 |
| 打包镜像 | 在本机做，`tools/host/build-boot-swap-kernel.py` 只替换 FAT 里的 `kernel.macho` |
| 参数上限 | payload 硬上限 1000 字节。官方 45 项 + `sheng-log=1` 已占 990，新增参数必须替换旧的 |
| 下载产物 | 单流被限速到约 32 KB/s，必须 `tools/host/ci-fetch-parallel.py --block 4 --workers 12`（约 0.6 MiB/s）；断点续传会留下空洞，下完必须 `zipfile.testzip()` |
| 刷机 | `python tools/host/edl-flash-boot.py out/<img> --port <COM>`；报「target is speaking SAHARA」时去掉 `--no-loader`，报 protocol error 时加上 |
| 读日志 | `python tools/host/edl-read-log.py --port <COM> --scan --slots`，产物落 `work/edl-log/`，归档到 `work/official-decode/nx729j-<boot_id>/` |

## 二、优先级任务

### P0 — 让看门狗 armed 的那一轮不再 34 秒死

现状：同一颗镜像，看门狗没 armed 时能跑 39 分钟，armed 时 34 秒硬复位。补丁已写好但从未编译。

```bash
# 1. 在 CI 仓的构建流程里应用（幂等检查会拒绝重复打补丁）
python3 scripts/prepare-stage7-wdt.py --check        # 基线应通过
python3 scripts/prepare-stage7-wdt.py               # 应用

# 2. 编内核（本机派发）
python tools/host/ci-dispatch.py --defconfig nx729j_stage7_defconfig --steps kernel \
    --device-repo 2713385115/UniXNU-nx729j --device-ref main

# 3. 下载 + 装镜像
python tools/host/ci-fetch-parallel.py --run <run_id> --name unixnu-nx729j_stage7_defconfig \
    --into work/ci-wdt --block 4 --workers 12
python tools/host/build-boot-swap-kernel.py --base out/boot-s7pmgr-skip.img \
    --kernel work/ci-wdt/kernel.macho --out out/boot-s7wdt.img
python tools/host/verify-p7-image.py out/boot-s7pmgr-skip.img out/boot-s7wdt.img
```

验收：读回窗口里出现 `NX729J_WDT_PING` 标记；在 armed 复位那一轮 `uptime_s > 600` 且
`NativeUfs: log round` 连续；`gunyah_wdt since_ping` 不再逼近复位。

### P1 — 判据③：让 render server 有设备可用

两条互斥路线，先试第二条更省事：

1. **sandbox 放行**：稳定基线里 66 次 corpse 全是
   `deny(1) file-read-metadata /private/preboot/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64`。
   pid 1 能读，其他进程不能。改 Data seed 里的 sandbox profile 放行该前缀即可验证是否只需权限。
2. **把 GPU 插件数据合进稳定基线**：稳定基线 seed 是纯 skip-setup seed，没有
   `NativeAdrenoMetal.publish`。用官方 `scripts/add-gpu-plugin-data.py --publish --clients ...`
   扩 seed，同时 args 加 `sheng-gpu-addon=1` 并带上 `NGPUADD2` sidecar（不能只加参数，
   `0x1b6783fe` 已证伪）。

验收：窗口出现 `WindowServer` 或 `CARenderServer`，以及 `NativeAdrenoMetal` 的 publish 行。
这一步是判据③，**不要用「屏幕上看到 logo」当判据**。

### P2 — 判据④：补 PID 1 检查点

官方 `guide/debugging` 要求 `storage-probe=1 log=1` 并把检查点导出成 `startup-checkpoints.txt`，
本内核二进制里 `storage-probe` 命中数为 0，读取器也没有 `--expected-tag`。需要：

1. 在 `drivers/NativeUfs/NativeUfs.cpp` 的 writer 里加 PID 1 检查点环形区（复用现有 slot 机制最省）。
2. 在 `scripts/read-sheng-log.py` 加导出，输出与官方文件名一致。
3. 重编、上机、把文件放进 `docs/progress/`。

### P3 — 仓库迁移（不阻塞启动，但影响被官方目录收录）

本仓 `board.toml` 仍是格式 1。按官方 `porting/records` 需要迁到 `schema_version = 2`
（`[components]` 选择 + `feeds.toml` + `components.toml`），并加
`.github/workflows/porting-record.yml`。根目录 `porting.toml` 已按官方格式写好并通过
`python3 scripts/porting-record.py check --device .`，但它要等 `board.toml` 迁移后才会被目录工作流采用。

## 三、动手前必须知道的坑

- **不要用屏幕判断进度**：绿条在 `br x1` 之前就画好了，和内核无关。
- **不要用「日志全零」下结论**：旧 writer 在 root 校验失败时不写窗口；现在失败原因写最后一个槽。
- **`op 0x28` 是读**：`dir 2` 是 toHost。看到上万条读写流不代表有写风暴。
- **长跑会覆盖早期行**：环形缓冲。早期阶段要靠 `slots.bin` 或短窗口那一轮。
- **同一颗镜像可能一次稳一次不稳**：差别在引导器有没有 arm 看门狗（见 P0）。
- **每次只改一项**：改完必须 `tools/host/verify-p7-image.py` 证明只有预期的那一个 FAT 成员变了。
