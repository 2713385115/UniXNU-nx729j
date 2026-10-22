#!/usr/bin/env python3
"""RedMagic 8 Pro / 8 Pro+ (NX729J) helper for the UniXNU port.

Runs on Windows, macOS and Linux. Needs `adb` and `fastboot` on PATH
(Android platform-tools). No third-party Python packages.

    python nx729j.py doctor                     what state is my phone in?
    python nx729j.py measure                    read the userdata geometry
    python nx729j.py measure --write board.toml paste it into board.toml
    python nx729j.py probe                      fastboot getvar dump + verdict
    python nx729j.py probe --boot stock.img     does this ABL allow `fastboot boot`?
    python nx729j.py boot path/to/boot.img      sanity check, then fastboot boot
    python nx729j.py dump --list                list partitions (needs root)
    python nx729j.py dump                       back up the small partitions

`doctor`, `measure`, `probe` and `dump --list` only read. Nothing here writes to
the phone except the `boot` and `dump` subcommands, and those only read too:
`boot` starts an image from memory and `dump` copies partitions to this computer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Partitions that a stock Android install cannot be rebuilt without, and that
# are small enough to copy over USB. `super` (the system image) and `userdata`
# are excluded on purpose: they are tens to hundreds of GiB.
SMALL_PARTITIONS = [
    "boot_a", "boot_b",
    "init_boot_a", "vendor_boot_a",
    "dtbo_a", "dtbo_b",
    "vbmeta_a", "vbmeta_b", "vbmeta_system_a", "vbmeta_vendor_a",
    "abl_a", "abl_b", "xbl_a", "xbl_config_a",
    "modem_a", "modem_b", "dsp_a", "bluetooth_a",
    "persist", "devinfo", "frp", "misc", "apdp", "msadp",
]

# qcom UFS LUN 0 on SM8550 reports a 4096-byte logical block; the port's
# userdata manifest is written for that size.
EXPECTED_LBA_BYTES = 4096


# --------------------------------------------------------------------------
# console
# --------------------------------------------------------------------------
def setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def say(text: str = "") -> None:
    print(text, flush=True)


def rule(title: str) -> None:
    say("")
    say("-" * 66)
    say(title)
    say("-" * 66)


OK = "[OK]"
NG = "[NG]"
WARN = "[!!]"
INFO = "[--]"


# --------------------------------------------------------------------------
# process helpers
# --------------------------------------------------------------------------
def run(command, timeout=120, binary=False):
    """Run a command, return (returncode, stdout, stderr)."""
    resolved = which(command[0])
    if resolved:
        command = [resolved, *command[1:]]
    resolve = resolved or command[0]
    # Windows ships adb as a .cmd shim in some toolchains; CreateProcess cannot
    # start those directly, so go through the shell for .cmd/.bat only.
    through_shell = platform.system() == "Windows" and resolve.lower().endswith(
        (".cmd", ".bat"))
    try:
        result = subprocess.run(
            subprocess.list2cmdline(command) if through_shell else command,
            shell=through_shell,
            capture_output=True, timeout=timeout,
        )
    except FileNotFoundError:
        message = f"{command[0]} not found"
        return 127, (b"" if binary else ""), (message.encode() if binary else message)
    except subprocess.TimeoutExpired:
        return 124, (b"" if binary else ""), (b"timeout" if binary else "timeout")
    if binary:
        return result.returncode, result.stdout, result.stderr
    out = result.stdout.decode("utf-8", "replace")
    err = result.stderr.decode("utf-8", "replace")
    return result.returncode, out, err


def adb(*args, binary=False, timeout=120):
    return run(["adb", *args], timeout=timeout, binary=binary)


def fastboot(*args, timeout=120):
    return run(["fastboot", *args], timeout=timeout)


def adb_shell(script: str, timeout=120) -> str:
    """Run one shell fragment on the phone and return stdout without CR."""
    rc, out, err = adb("shell", script, timeout=timeout)
    if rc != 0 and not out:
        return ""
    return out.replace("\r\n", "\n").replace("\r", "")


def tool_version(name: str) -> str | None:
    if which(name) is None:
        return None
    rc, out, err = run([name, "--version"], timeout=30)
    text = (out or "") + (err or "")
    if rc != 0 and not text:
        return None
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return "present"


def which(name: str) -> str | None:
    """adb/fastboot from PATH, or the platform-tools bundle beside this repo."""
    found = shutil.which(name)
    if found:
        return found
    for base in (REPO.parent / "tools" / "platform-tools",
                 REPO / "tools" / "platform-tools"):
        for candidate in (base / name, base / f"{name}.exe"):
            if candidate.is_file():
                return str(candidate)
    return None


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------
def adb_devices() -> list[dict]:
    rc, out, err = adb("devices", "-l")
    devices = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        devices.append({
            "serial": parts[0],
            "state": parts[1] if len(parts) > 1 else "?",
            "extra": " ".join(parts[2:]),
        })
    return devices


def fastboot_devices() -> list[str]:
    rc, out, err = fastboot("devices")
    return [line.split()[0] for line in out.splitlines() if line.strip()]


def prop(name: str) -> str:
    return adb_shell(f"getprop {name}").strip()


def doctor(args) -> int:
    report = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "host": platform.platform()}

    rule("1. 这台电脑")
    say(f"{INFO} 操作系统 : {platform.system()} {platform.release()}")
    say(f"{INFO} Python   : {platform.python_version()}")
    if sys.version_info < (3, 9):
        say(f"{NG} Python 版本太旧，请安装 3.9 或更新版本")
    else:
        say(f"{OK} Python 可用")

    for tool in ("adb", "fastboot"):
        version = tool_version(tool)
        if version is None:
            say(f"{NG} 找不到 {tool}：请装 Android platform-tools 并加入 PATH")
        else:
            say(f"{OK} {tool} : {version}")
    report["tools"] = {t: tool_version(t) for t in ("adb", "fastboot")}

    free = shutil.disk_usage(Path.cwd())
    gib = free.free / 2 ** 30
    say(f"{INFO} 当前目录剩余空间 {gib:.1f} GiB")
    if gib < 60:
        say(f"{WARN} 编译内核和系统镜像建议留 100 GiB 以上")

    rule("2. 手机（adb 模式）")
    devices = adb_devices()
    report["adb_devices"] = devices
    if not devices:
        say(f"{WARN} adb 没看到手机。检查：数据线是不是数据线、USB 调试是否打开、")
        say("     手机上是否点了「允许 USB 调试」")
    for device in devices:
        say(f"{INFO} {device['serial']}  状态={device['state']}  {device['extra']}")
        if device["state"] != "device":
            say(f"{WARN} 状态不是 device：如果是 unauthorized，请看手机屏幕上的授权弹窗")
            continue
        info = {
            "model": prop("ro.product.model"),
            "device": prop("ro.product.device"),
            "android": prop("ro.build.version.release"),
            "slot": prop("ro.boot.slot_suffix"),
            "verified_boot": prop("ro.boot.verifiedbootstate"),
            "flash_locked": prop("ro.boot.flash.locked"),
            "oem_unlock_supported": prop("sys.oem_unlock_supported"),
            "kernel": adb_shell("uname -a").strip(),
        }
        report["adb_device_info"] = info
        for key, value in info.items():
            say(f"     {key:<20} {value}")
        if info["device"] and info["device"] != "NX729J":
            say(f"{WARN} 设备代号是 {info['device']}，这个仓库只针对 NX729J")
        block = adb_shell("cat /sys/block/sda/queue/logical_block_size").strip()
        say(f"     {'ufs_block_size':<20} {block or '?'}")
        say(f"     {'ufs_capacity':<20} "
            f"{int(adb_shell('cat /sys/block/sda/size').strip() or 0) * 512 / 2 ** 30:.1f} GiB")

    rule("3. 手机（fastboot 模式）")
    serials = fastboot_devices()
    report["fastboot_devices"] = serials
    if not serials:
        say(f"{INFO} 当前不在 fastboot 模式（正常；需要时执行 adb reboot bootloader）")
    else:
        variables = {}
        for name in ("product", "current-slot", "unlocked", "serialno",
                     "partition-size:userdata", "version-bootloader"):
            rc, out, err = fastboot("getvar", name, timeout=60)
            text = (out or "") + (err or "")
            value = "?"
            for line in text.splitlines():
                if line.startswith(name + ":"):
                    value = line.split(":", 1)[1].strip()
                    break
            variables[name] = value
            say(f"     {name:<24} {value}")
        report["fastboot_vars"] = variables
        unlocked = variables.get("unlocked", "").lower()
        if unlocked.startswith("yes") or unlocked == "true":
            say(f"{OK} bootloader 已解锁")
        elif unlocked:
            say(f"{NG} bootloader 未解锁，先做教程里的「阶段 1」")
        size = variables.get("partition-size:userdata", "")
        if size.startswith("0x"):
            gib = int(size, 16) / 2 ** 30
            say(f"{INFO} userdata 分区 {gib:.1f} GiB")

    rule("结论")
    adb_ok = any(d["state"] == "device" for d in devices)
    if adb_ok:
        say(f"{OK} 可以开始「阶段 0/1」：体检和备份")
        say(f"{INFO} 下一步：python nx729j.py measure   （量分区，免 root）")
    elif serials:
        say(f"{OK} 手机在 fastboot 模式，可以开始「阶段 1」解锁")
    else:
        say(f"{INFO} 先用 USB 连上手机并打开 USB 调试")

    out = Path(args.out or (Path.cwd() / "nx729j-report.json"))
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    say(f"{INFO} 报告已写入 {out}")
    return 0


# --------------------------------------------------------------------------
# measure
# --------------------------------------------------------------------------
PARTITION_LOOP = (
    'for f in /dev/block/by-name/*; do '
    'n=${f##*/}; d=$(readlink -f "$f"); b=${d##*/}; '
    's=$(cat /sys/class/block/$b/start 2>/dev/null); '
    'z=$(cat /sys/class/block/$b/size 2>/dev/null); '
    'echo "$n $b ${s:-0} ${z:-0}"; done'
)


def partition_table() -> list[dict]:
    """Read every GPT entry's start and size without root, through sysfs."""
    text = adb_shell(PARTITION_LOOP, timeout=180)
    rows = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 4:
            continue
        name, node, start, size = fields
        try:
            rows.append({
                "name": name,
                "node": node,
                "start_512": int(start),
                "size_512": int(size),
            })
        except ValueError:
            continue
    return rows


def measure(args) -> int:
    devices = adb_devices()
    if not any(d["state"] == "device" for d in devices):
        say(f"{NG} 手机不在 adb 模式。先在手机上打开 USB 调试，插线，允许授权。")
        return 2

    rule("读取 UFS 逻辑块大小")
    block = adb_shell("cat /sys/block/sda/queue/logical_block_size").strip()
    if not block.isdigit():
        say(f"{NG} 读不到 /sys/block/sda/queue/logical_block_size，无法继续")
        return 2
    lba_bytes = int(block)
    total = int(adb_shell("cat /sys/block/sda/size").strip() or 0) * 512
    say(f"{INFO} logical_block_size = {lba_bytes}")
    say(f"{INFO} LUN 0 容量         = {total / 2 ** 30:.1f} GiB")
    if lba_bytes != EXPECTED_LBA_BYTES:
        say(f"{WARN} 期望 {EXPECTED_LBA_BYTES}，实际 {lba_bytes}。")
        say("     本仓库的 userdata 清单按 4096 写，如果不是 4096 请先反馈，别刷。")

    rule("查找 userdata 分区")
    target = args.name
    rows = partition_table()
    if not rows:
        say(f"{NG} 读不到分区表。确认手机已授权 adb。")
        return 2
    row = next((r for r in rows if r["name"] == target), None)
    if row is None:
        say(f"{NG} 分区表里没有 {target}。现有分区：")
        say("     " + ", ".join(sorted(r["name"] for r in rows)))
        return 2
    say(f"{INFO} {target:<12} 节点={row['node']}")

    first_lba = (row["start_512"] * 512) // lba_bytes
    count = (row["size_512"] * 512) // lba_bytes
    last_lba = first_lba + count - 1
    bytes_total = count * lba_bytes

    if (row["start_512"] * 512) % lba_bytes or (row["size_512"] * 512) % lba_bytes:
        say(f"{WARN} 起点或长度不是 {lba_bytes} 的整数倍，请把原始数值一起反馈")

    rule("结果")
    say(f"  first_lba = 0x{first_lba:x}        ({first_lba})")
    say(f"  last_lba  = 0x{last_lba:x}        ({last_lba})")
    say(f"  lba_bytes = {lba_bytes}")
    say(f"  容量      = {bytes_total / 2 ** 30:.1f} GiB  ({bytes_total} bytes)")

    rc, out, err = fastboot("getvar", "partition-size:" + target, timeout=60)
    text = (out or "") + (err or "")
    for line in text.splitlines():
        if line.startswith("partition-size:"):
            value = line.split(":", 1)[1].strip()
            if value.startswith("0x"):
                cross = int(value, 16)
                say(f"{INFO} fastboot 报的分区大小 {cross} bytes "
                    f"（差 {cross - bytes_total} bytes）")
                if abs(cross - bytes_total) > lba_bytes:
                    say(f"{WARN} 两个来源不一致，先别填表，把结果发出来")
            break
    else:
        say(f"{INFO} 手机不在 fastboot 模式，跳过交叉验证（不影响填表）")

    payload = {
        "device": target,
        "first_lba": first_lba,
        "last_lba": last_lba,
        "lba_bytes": lba_bytes,
        "bytes": bytes_total,
        "lun_bytes": total,
        "partitions": sorted(rows, key=lambda r: r["start_512"]),
    }
    out_path = Path(args.out or (Path.cwd() / "device-info.json"))
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    say(f"{INFO} 明细已写入 {out_path}")

    if args.write:
        board = Path(args.write)
        if not board.exists():
            say(f"{NG} 找不到 {board}")
            return 2
        text = board.read_text(encoding="utf-8")
        start = text.find("[userdata]")
        if start < 0:
            say(f"{NG} {board} 里没有 [userdata] 段")
            return 2
        end = text.find("\n[", start + 1)
        if end < 0:
            end = len(text)
        block = text[start:end]
        new = re.sub(r"(?m)^first_lba\s*=.*$", f"first_lba = 0x{first_lba:x}", block)
        new = re.sub(r"(?m)^last_lba\s*=.*$", f"last_lba = 0x{last_lba:x}", new)
        new = re.sub(r"(?m)^lba_bytes\s*=.*$", f"lba_bytes = {lba_bytes}", new)
        if new == block:
            say(f"{WARN} 没有匹配到 first_lba/last_lba 行，未写入")
        else:
            board.write_text(text[:start] + new + text[end:], encoding="utf-8")
            say(f"{OK} 已写入 {board}")
            try:
                import tomllib
                with open(board, "rb") as handle:
                    data = tomllib.load(handle)
                got = data["userdata"]
                say(f"{OK} 回读校验：{got['first_lba']:#x}..{got['last_lba']:#x} "
                    f"lba_bytes={got['lba_bytes']}")
            except Exception as error:  # noqa: BLE001
                say(f"{NG} 写入后 TOML 解析失败：{error}，请用 git 还原")

    rule("下一步")
    say("  1. 把上面三行填进 board.toml（或加 --write board.toml 自动填）")
    say("  2. 跑 python scripts/check-nx729j.py --require-userdata-geometry 复验")
    return 0


# --------------------------------------------------------------------------
# dump
# --------------------------------------------------------------------------
def have_root() -> bool:
    for probe in ("su -c id", "su 0 id"):
        out = adb_shell(probe)
        if "uid=0" in out:
            return True
    return False


def dump(args) -> int:
    devices = adb_devices()
    if not any(d["state"] == "device" for d in devices):
        say(f"{NG} 手机不在 adb 模式")
        return 2
    rows = partition_table()
    if not rows:
        say(f"{NG} 读不到分区表")
        return 2
    by_name = {r["name"]: r for r in rows}

    if args.list:
        rule("分区表（按位置）")
        for row in sorted(rows, key=lambda r: r["start_512"]):
            size = row["size_512"] * 512
            say(f"  {row['name']:<24} {row['node']:<8} "
                f"{size / 2 ** 20:>12.1f} MiB")
        return 0

    if not have_root():
        rule("没有 root")
        say("备份分区需要读 /dev/block，普通 adb shell 没有权限。三条路：")
        say("  1. 用 Magisk 给安卓 root 后再跑本命令（推荐，教程第 11 节）")
        say("  2. 只备份个人数据（照片/微信），安卓本身用官方固件包恢复")
        say("  3. 直接跳过备份：`fastboot boot` 不写手机，只有")
        say("     `fastboot flash userdata` 会清数据，那之前把照片拷出来就够")
        return 3

    wanted = args.parts.split(",") if args.parts else SMALL_PARTITIONS
    wanted = [w.strip() for w in wanted if w.strip()]
    unknown = [w for w in wanted if w not in by_name]
    if unknown:
        say(f"{WARN} 分区表里没有：{', '.join(unknown)}（跳过）")
    wanted = [w for w in wanted if w in by_name]

    out_dir = Path(args.out or (Path.cwd() / "nx729j-backup"))
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "files": {}}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    rule(f"备份 {len(wanted)} 个分区到 {out_dir}")
    for index, name in enumerate(wanted, 1):
        size = by_name[name]["size_512"] * 512
        if size > args.max_mib * 2 ** 20:
            say(f"{WARN} [{index}/{len(wanted)}] {name} 是 {size / 2 ** 30:.1f} GiB，"
                f"超过 {args.max_mib} MiB 上限，跳过（确要备份用 --max-mib）")
            continue
        target = out_dir / f"{name}.img"
        say(f"{INFO} [{index}/{len(wanted)}] {name} ({size / 2 ** 20:.1f} MiB) ...")
        started = time.time()
        rc, out, err = run(
            ["adb", "exec-out", f"su -c 'dd if=/dev/block/by-name/{name} bs=1048576'"],
            timeout=max(300, int(size / 2 ** 20)), binary=True,
        )
        if rc != 0 or len(out) != size:
            say(f"{NG} {name} 读出来 {len(out)} 字节，期望 {size}，已丢弃")
            continue
        target.write_bytes(out)
        digest = hashlib.sha256(out).hexdigest()
        manifest.setdefault("files", {})[name] = {
            "bytes": size,
            "sha256": digest,
            "taken": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        say(f"{OK} {name} {size / 2 ** 20:.1f} MiB sha256 {digest[:16]}... "
            f"({time.time() - started:.1f}s)")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    say(f"{OK} 清单：{manifest_path}")
    return 0


# --------------------------------------------------------------------------
# boot
# --------------------------------------------------------------------------
def boot(args) -> int:
    image = Path(args.image)
    if not image.exists():
        say(f"{NG} 找不到 {image}")
        return 2
    head = image.read_bytes()[:8]
    if head != b"ANDROID!":
        say(f"{NG} {image} 不是 Android boot image（头 8 字节是 {head!r}）")
        say("     确认用的是 out/nx729j/boot/boot.img")
        return 2
    size = image.stat().st_size
    say(f"{OK} boot 镜像 {image.name} {size / 2 ** 20:.1f} MiB，头标识正确")
    serials = fastboot_devices()
    if not serials:
        say(f"{NG} 手机不在 fastboot 模式：adb reboot bootloader")
        return 2
    say(f"{INFO} fastboot 设备 {serials}")
    if not args.yes:
        say("")
        say("  这会从内存临时启动这个镜像，不写任何分区。")
        say("  失败了黑屏/卡住：长按电源 10 秒强制重启，安卓原样回来。")
        say("  输入 BOOT 继续，其它任意键退出：")
        try:
            answer = input("  > ").strip()
        except EOFError:
            answer = ""
        if answer != "BOOT":
            say(f"{INFO} 已取消")
            return 1
    rule("fastboot boot")
    rc, out, err = run(["fastboot", "boot", str(image)], timeout=600)
    say(out.strip())
    if err.strip():
        say(err.strip())
    if rc != 0:
        say("")
        say(f"{NG} 启动被拒绝或失败。常见原因见教程「报错对照表」：")
        say("     remote: 'Command not allowed'  -> ABL 不允许 fastboot boot")
        say("     waiting for device            -> 手机没进 fastboot 或驱动没装")
        say("     FAILED (remote: 'unknown command') -> 同上，走 Plan B")
        return 3
    say(f"{OK} 镜像已发出。看手机屏幕。")
    return 0


# --------------------------------------------------------------------------
# probe
# --------------------------------------------------------------------------
# The abortl bootloader answers these without touching storage. Values arrive on
# stderr on most fastboot builds ("(bootloader) product: ..."), so both streams
# are parsed.
PROBE_VARS = [
    "product", "variant", "serialno", "hw-revision",
    "unlocked", "secure", "current-slot", "slot-count",
    "max-download-size", "is-userspace", "battery-voltage",
    "partition-type:userdata", "partition-size:userdata",
    "partition-size:boot_a", "partition-size:super",
]


def fastboot_getvar(name: str) -> str | None:
    rc, out, err = fastboot("getvar", name, timeout=60)
    text = (out or "") + "\n" + (err or "")
    for line in text.splitlines():
        line = line.strip()
        line = re.sub(r"^\(bootloader\)\s*", "", line)
        if line.startswith(name + ":"):
            value = line.split(":", 1)[1].strip()
            if value:
                return value
    return None


def probe(args) -> int:
    report = {"when": time.strftime("%Y-%m-%d %H:%M:%S")}
    serials = fastboot_devices()
    if not serials:
        say(f"{NG} 手机不在 fastboot 模式")
        say("     先执行：adb reboot bootloader")
        say("     或关机后按住「音量下 + 电源」进入")
        return 2
    say(f"{OK} fastboot 设备：{serials}")

    rule("1. bootloader 身份和状态")
    values = {}
    for name in PROBE_VARS:
        value = fastboot_getvar(name)
        values[name] = value
        if value is not None:
            say(f"{INFO} {name:28} {value}")
    report["getvar"] = values

    unlocked = (values.get("unlocked") or "").lower()
    secure = (values.get("secure") or "").lower()
    rule("2. 结论")
    if unlocked in ("yes", "true", "1"):
        say(f"{OK} bootloader 已解锁（unlocked={unlocked}）")
    elif unlocked:
        say(f"{NG} bootloader 未解锁（unlocked={unlocked}）——先解锁再来")
        return 3
    else:
        say(f"{WARN} 没读到 unlocked，努比亚可能改用 oem device-info，下面再试")

    # Nubia's own command, harmless read
    rc, out, err = fastboot("oem", "device-info", timeout=60)
    text = ((out or "") + (err or "")).strip()
    if text:
        for line in text.splitlines():
            say(f"{INFO} {line.strip()}")
        report["oem_device_info"] = text

    size = values.get("partition-size:userdata")
    if size:
        # fastboot 的实际回包会重复分区名：userdata:  0x718BCD3000
        size = re.sub(r"^\s*[A-Za-z_][\w-]*\s*:\s*", "", size).strip()
        try:
            total_bytes = int(size, 16) if size.lower().startswith("0x") else int(size)
            sectors4k = total_bytes // 4096
            say("")
            say(f"{OK} userdata 分区 {total_bytes} 字节 = {total_bytes / 2 ** 30:.1f} GiB")
            say(f"{INFO} 换成 4 KiB 扇区：{sectors4k}")
            say(f"     board.toml 里填 first_lba = 0x296328, last_lba = 0x{sectors4k - 6:x}")
            report["userdata_sectors_4k"] = sectors4k
            report["userdata_last_lba"] = sectors4k - 6
        except ValueError:
            say(f"{WARN} 解析不了 partition-size:userdata = {size}")
    else:
        say(f"{WARN} fastboot 没给 partition-size:userdata，改用 adb 读 /sys/block/sda/size")

    if args.boot_image:
        rule("3. fastboot boot 能力测试")
        image = Path(args.boot_image)
        if not image.exists():
            say(f"{NG} 找不到 {image}")
            return 2
        head = image.read_bytes()[:8]
        if head != b"ANDROID!":
            say(f"{NG} {image} 不是 Android boot image（头 {head!r}）")
            return 2
        say(f"{INFO} 用 {image.name} 测试；只从内存启动，不写任何分区")
        say("     用原厂镜像测试可以分清两种失败：")
        say("       未知命令        -> ABL 去掉了 fastboot boot，这条路走不通")
        say("       签名/校验失败    -> 命令在，但会检查签名")
        say("       正常重启进安卓    -> 命令可用，之后可以试自己编译的镜像")
        if not args.yes:
            try:
                answer = input("  输入 PROBE 继续，其它键退出：").strip()
            except EOFError:
                answer = ""
            if answer != "PROBE":
                say(f"{INFO} 已取消")
                return 1
        rc, out, err = run(["fastboot", "boot", str(image)], timeout=600)
        text = ((out or "") + "\n" + (err or "")).strip()
        say(text or "(没有输出)")
        report["fastboot_boot"] = {"rc": rc, "output": text}
        lowered = text.lower()
        if "unknown command" in lowered or "not allowed" in lowered or "unsupported" in lowered:
            say("")
            say(f"{NG} ABL 不接受 fastboot boot。需要先写 boot 分区（有风险，先备份）")
            verdict = "rejected"
        elif rc == 0 or "finished" in lowered or "booting" in lowered:
            say("")
            say(f"{OK} ABL 接受了 fastboot boot ——这个机型可以做阶段 A")
            verdict = "accepted"
        else:
            say("")
            say(f"{WARN} 结果不明确，把上面的输出发我")
            verdict = "unclear"
        report["fastboot_boot_verdict"] = verdict

    out_path = args.out or Path.cwd() / "probe-report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    say("")
    say(f"{INFO} 报告已写入 {out_path}")
    return 0


# --------------------------------------------------------------------------
def main() -> int:
    setup_console()
    parser = argparse.ArgumentParser(
        description="RedMagic 8 Pro / 8 Pro+ (NX729J) UniXNU helper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command")

    p_doctor = sub.add_parser("doctor", help="检查电脑、adb、fastboot 和手机状态")
    p_doctor.add_argument("--out", type=Path, default=None)

    p_measure = sub.add_parser("measure", help="读取 userdata 分区的 LBA 几何（免 root）")
    p_measure.add_argument("--name", default="userdata")
    p_measure.add_argument("--out", type=Path, default=None)
    p_measure.add_argument("--write", type=Path, default=None,
                           help="直接写回 board.toml")

    p_dump = sub.add_parser("dump", help="备份分区（需要 root；--list 不需要）")
    p_dump.add_argument("--list", action="store_true")
    p_dump.add_argument("--parts", default=None)
    p_dump.add_argument("--out", type=Path, default=None)
    p_dump.add_argument("--max-mib", type=int, default=4096)

    p_boot = sub.add_parser("boot", help="校验并 fastboot boot 一个 boot.img")
    p_boot.add_argument("image", type=Path)
    p_boot.add_argument("--yes", action="store_true")

    p_probe = sub.add_parser("probe", help="在 fastboot 模式读 getvar，并测试 fastboot boot 是否被允许")
    p_probe.add_argument("--boot", "--boot-image", dest="boot_image", type=Path, default=None,
                         help="用这个镜像测试 fastboot boot（建议用原厂 boot.img）")
    p_probe.add_argument("--yes", action="store_true")
    p_probe.add_argument("--out", type=Path, default=None)

    args = parser.parse_args()
    if args.command == "doctor":
        return doctor(args)
    if args.command == "measure":
        return measure(args)
    if args.command == "dump":
        return dump(args)
    if args.command == "boot":
        return boot(args)
    if args.command == "probe":
        return probe(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
