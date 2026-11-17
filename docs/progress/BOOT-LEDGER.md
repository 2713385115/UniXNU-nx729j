# 启动台账（boot ledger）

这台设备的每一次结论都来自 9008 回读的 SHLOG 窗口，不看屏幕、不拍照。本文件是唯一的判据来源：
新增一轮启动就在这里加一行，改结论就改对应行的证据，不另开文档。

镜像路径相对仓库根；`out/` 下的镜像不随仓库发布，SHA-256 记在下面，重建方式见
[NEXT-STEPS](NEXT-STEPS.md)。

## 读数字段口径

窗口头部 512 字节是版本 5 记录，字段含义只有一种读法：

| 字段 | 含义 | 常见误读 |
|---|---|---|
| `boot_id` | 本次启动的 ID，写在每次发布轮里 | 与上一轮相同 ⇒ 这是旧窗口，本次启动没发生 |
| `uptime_s` | **最后一次发布轮**时的秒数，不是设备死亡时刻 | 头部 490 s 而窗口到 496.5 s 是正常的；39 分钟那轮头部 2354 s、窗口到 2388.4 s |
| `rounds` | 已发布轮数，每轮约 10 s | 轮数停住 = writer 停住，不等于内核死 |
| `slots_written` | 已写状态槽数（4 KiB/槽） | 槽里是**状态快照**，不是 msgbuf 文本副本 |
| `write_errors` | UFS 写失败次数 | 非 0 常常是切 EDL 打断写入，不是存储坏 |
| `phase` / `phase_round` | **日志写入器**的发布阶段 | 与官方 bring-up 阶段编号无关，禁止混用 |

msgbuf 是环形缓冲：跑久了会覆盖自己的开头。所以「某标记计数为 0」在长跑里只能说明可见窗口里没有，
不能说明整轮没发生。要看全程，用 `slots.bin` 的逐槽时间线。

另外两条容易踩的：

- `SRT: ...` 行是离线根挂载诊断，只有它打了才算走到 shared cache 解析。
- `NativeUfs: submit cmd ... op 28 dir 2 bytes 131072` 是 **SCSI READ(10)、方向 device→host**，
  不是 union copy-up 写入。单轮上万条也只是在读缓存。

## 台账

| boot_id | 镜像（SHA-256 前 8） | 头部 ver/uptime/rounds/phase/err | 窗口 msgbuf 秒 | 结果 |
|---|---|---|---|---|
| `0x267a1232` | `boot-s7pmgr-skip.img` (`aff8b7c2`) | 5 / **2354** / 254 / 5 published / 0 | 1875.7 → **2388.4** | **目前最好的一轮**。零崩溃、零 deny；`backboardd[412]` 于 1894.155 s 获准一个 corpse，`/usr/libexec/backboardd[446]` 于 1894.207 s 启动，之后到 2388 s 只有 `CoreAnalyticsHub` 心跳 |
| `0x266eff40` | 同上，`--reset` 复跑 | 5 / 1115 / 130 / 5 published / 0 | 153.1 → 1138.8 | 持久 Data resume 后仍稳定。早期 174–533 s 有一波容器缺失（corpse 9、group container 21），之后自愈 |
| `0x17a1da0f` | seed 替换前的 s7pmgr 轮 | 5 / 191 / 37 / 5 published / 0 | 104.5 → 212.5 | corpse 29；官方 seed 尚未换上 |
| `0x23928262` | `boot-s7pmgr-skip-smp.img` | 5 / 25 / 12 / 3 (msgbuf copied) / 2 | 29.6 → 55.5 | 8 核在线（`ml_processor_register` 16 行，cpu 0..7），`Early boot complete` 出现；切 EDL 过早，未发布 |
| `0x267949e2` | SMP 轮前一轮 | 未存窗口头 | 32.1 → 56.6 | 同上的短窗口，`SRT: setup done` 1 条、corpse 11 |
| `0x1a5d5b1a` | `boot-s7v5pon.img` | 5 / 27 / 12 / — / 1 | 21.6 → 50.5 | **唯一一组 shared cache 真正映射成功**：`SRT: setup pid=1 files=54 maps=62`、53 个分片 `rej=0`、`setup done err=0`、`srs err=0` |
| `0x12c714bd` | 同上（长跑） | 5 / 490 / 73 / 5 published / 1 | 46.6 → 496.5 | 用户态推到 SpringBoard，pid 到 397；corpse 66 全部因为 sandbox 拒绝读 `/private/preboot/.../dyld_shared_cache_arm64` |
| `0x1aadfabe` | 同上，重刷后 | 5 / 10 / 5 / — / 0 | 21.9 → **34.0** | 看门狗 armed：`gunyah_wdt status=0 since_ping=22229ms`，末行停在 page read，无 panic |
| `0x1b6783fe` | `boot-s7v5pon-gpuaddon.img` | 5 / 21 / 10 / — / 1 | 22.5 → 45.4 | 参数有 `sheng-gpu-metal=1` 但 payload 无 `NGPUADD2`，GPU 计数全 0 |
| `0x1807cbc2` | `boot-s7v5-ngpu.img` | 5 / 13 / 7 / — / 0 | 19.6 → 34.2 | 非 union：`cache mount target /private/preboot`，`probe cryptexes dir err=2`，launchd 两次 `vm_shared_region_start_address() failed` |
| `0x1f44cebb` | `boot-s7union-t.img` | 5 / 12 / 6 / — / 0 | 25.9 → 39.4 | union 生效（`cache mount target /`、`probe cache dir err=0`）但 **`SRT: setup` 0 条**，39.4 s 结束 ⇒ 相对 v5pon 是退步 |
| `0x235aaf19` | 重刷前的旧启动 | 5 / 13 / 7 / — / 0 | 29.4 → 44.2 | 不是本轮启动，仅用于证明 `boot_id` 判据有效 |

另一条工具链在 2026-11-17 另取过 `0x150eb656`（uptime 362 s、58 轮、0 写错，含 launchd 高唤醒与
SpringBoard/corpse 循环），见 `STATUS-STAGE7-EXIT-DIAGNOSTIC-2026-11-17.md`。它没有本仓库的解码产物，
所以不并入上表，但结论一致：短窗口 + 反复重启。

## 按官方 bring-up 阶段 7 判据横切

| 判据 | 最好的一轮 `0x267a1232` | 依据 |
|---|---|---|
| ① `launchd` 从根卷启动 | 通过 | 早期段被环形覆盖；`0x267949e2` / `0x23928262` 同内核同 seed 的短窗口可见 `load_init_program` 与 `launchd` |
| ② Data 卷挂载 | 通过 | `disk1s1 mount-complete volume Data, requested by: init (pid 1)`；换官方 seed 后容器类错误归零 |
| ③ 显示服务或控制台有输出 | **未通过** | 只有 `backboardd` 启动，没有 `WindowServer` / `CARenderServer` / scanout / Metal |
| ④ 启动进度层记录最后检查点 | **未通过** | 内核无 `storage-probe` 实现（二进制 0 命中），没有 PID 1 检查点数据源 |

## 已证伪、不要再试

1. 把 `op 0x28` 当写操作、据此认为 union 造成写风暴 —— `NativeUfs.cpp` 的 CDB 是 READ(10)，方向 2 是 toHost。
2. 靠屏幕颜色条判断进度 —— 绿条是 DarwinBoot `Entry.S` 在 `br x1` 之前画的，u055 系内核没有画条代码。
3. 用「日志全零」判断驱动没跑 —— 旧版 writer 在 root 校验失败时不写窗口，现在失败原因会写进最后一个槽。
4. 重签 `VROOT01` 信封、旁路 `log_open` 布尔门、NOP 掉身份门（u090/u092/u093）—— 回读全 0，已被证伪。
5. 整块搬 u053 的 panic cave —— `b` 是 PC 相对编码，跳过去落在 `udf` 上，内核直接打死。
6. 只加参数不加实体（`sheng-gpu-addon=1` 而没有 `NGPUADD2`）—— 参数空转，GPU 计数恒 0。
