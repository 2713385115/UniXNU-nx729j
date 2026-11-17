# 维护机工具（Windows）

这些脚本是在维护机上用的，不是在设备上跑的。它们依赖两家外部东西，缺一样就跑不起来：

1. **高通 firehose 工具链**：`QSaharaServer.exe` 与 `fh_loader.exe`（本机取自中兴家族工具箱的
   `bin/tool/Win`）。`edl-flash-boot.py`、`edl-read-log.py`、`edl-verify-boot.py` 里写的是绝对路径，
   换机器要改 `TOOLBOX` 常量。
2. **GitHub token**：`tools/.gh-token`（一行个人访问令牌，除 `repo` 外不需要其它权限）。`ci-dispatch.py`、`ci-fetch*.py`、
   `gh-push.py`、`gh-push-batch.py` 读它。**不要把这个文件提交进仓库。**

内核不在这里编：这台机器没有 clang，也没有 hypervisor，`kernel.macho` 全部来自
`2713385115/UniXNU` 的 macOS runner。

## 设备侧

| 脚本 | 作用 |
|---|---|
| `edl-flash-boot.py` | 只写 `boot_a`（LUN4，sector 114054），写完 `--power=reset,1`。Sahara 阶段报错就去掉 `--no-loader` |
| `edl-read-log.py` | 读 32 MiB SHLOG 窗口，落 `log-window.bin` / `msgbuf.txt` / `slots.bin` / `panic.bin` / `failure.bin` / `phase.bin`，并解头部 |
| `edl-verify-boot.py` | 回读 `boot_a` 逐块比 sha256，用来证明设备上跑的到底是哪颗镜像 |

## 镜像侧

| 脚本 | 作用 |
|---|---|
| `build-boot-swap-kernel.py` | 把新的 `kernel.macho` 换进 payload，其余成员字节不变 |
| `build-boot-swap-seed.py` | 换 `Payloads/scratch-data.img`（Data seed） |
| `extend-fat-kernel.py` | 扩 FAT 簇链，给变大的 seed 腾空间 |
| `grow-toml-args.py` | 读/改/写 `EFI/UniXNU/unixnu.toml` 的 args（硬上限 1000 字节） |
| `build-stage7-trace.py` | 在 args 上限内加阶段 7 诊断参数（`sheng-build-tag`、`boot-ui-focus`） |
| `verify-p7-image.py` | 两个镜像逐 FAT 成员比哈希，输出「只有哪个文件变了」 |
| `inject-gpu-addon.py` | 写 GPU sidecar、ADT `GpuAddon` 属性与三合一 owner trust |

## CI 侧

| 脚本 | 作用 |
|---|---|
| `ci-dispatch.py` | 派发 `2713385115/UniXNU` 的内核构建（`--steps kernel` 只编内核） |
| `ci-fetch-parallel.py` | 并发 Range 下载产物。**单流被限速到约 32 KB/s，必须用这个** |

用法示例：

```bash
python tools/host/ci-dispatch.py --defconfig nx729j_stage7_defconfig --steps kernel \
    --device-repo 2713385115/UniXNU-nx729j --device-ref main
python tools/host/ci-fetch-parallel.py --run <run_id> --name unixnu-nx729j_stage7_defconfig \
    --into work/ci --block 4 --workers 12
python tools/host/build-boot-swap-kernel.py --base out/boot-s7pmgr-skip.img \
    --kernel work/ci/kernel.macho --out out/boot-next.img
python tools/host/verify-p7-image.py out/boot-s7pmgr-skip.img out/boot-next.img
```

下载完先 `python -c "import zipfile;zipfile.ZipFile(p).testzip()"` 验完整：断点续传会留空洞，
一个空洞的 zip 解出来是坏内核，而且报错位置离原因很远。
