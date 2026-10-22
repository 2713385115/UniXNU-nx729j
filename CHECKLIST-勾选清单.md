# 红魔 8 Pro+ 移植 iOS：勾选清单

打印出来，或者开着放在旁边。做完一项打一个 `x`。
详细做法看 `GUIDE-从零开始.md` 里对应的章节号。

---

## 阶段前：先给自己三个答案

- [ ] 我有（或能借到）一台 Mac，或者愿意用 GitHub 的免费 macOS 机器
- [ ] 我的安卓数据可以清空（照片已备份）
- [ ] 我能接受结果是"能开机但不能上网、不能打电话"

三个里有一个是"不"，就先别开始，看完 `GUIDE-从零开始.md` 第〇节再决定。

---

## 阶段 0：体检和备份（第 3 节）

- [ ] 装好 Python，`python --version` 有输出
- [ ] 装好 platform-tools，`adb version` 和 `fastboot --version` 都有输出
- [ ] 手机开发者选项打开，USB 调试打开
- [ ] 双击 `tools\run-doctor.bat`，看到 `model = NX729J`
- [ ] 记下 `ufs_block_size`（必须是 4096）：`__________`
- [ ] 照片 / 视频 / 微信记录已拷到电脑
- [ ] 两步验证恢复码已存好
- [ ] 记下当前系统版本号：`__________`

---

## 阶段 1：解锁（第 4 节，会清数据）

- [ ] 官网申请解锁通过，收到官方解锁命令
- [ ] `adb reboot bootloader` 进到 FASTBOOT 界面
- [ ] `fastboot devices` 能看到设备
- [ ] 执行官方解锁命令，返回 OKAY
- [ ] `fastboot flashing unlock`，音量键选 Yes
- [ ] 重跑 `run-doctor.bat`，`flash_locked = 0`

> 卡在这里的话：官方不解锁 = 项目终止，没有绕过的办法。

---

## 阶段 2：编译环境（第 5 节）

- [ ] `wsl --install -d Ubuntu-24.04` 装完，能进 Ubuntu 终端
- [ ] `sudo apt install ...` 那一长条跑完
- [ ] `clang --version` / `mtools --version` / `dtc --version` 都有输出
- [ ] `~/UniXNU` 克隆完成（含子模块）
- [ ] `~/uefi-sheng` 克隆完成
- [ ] `~/UniXNU-nx729j` 克隆完成

---

## 阶段 3：接上你的板级文件（第 6 节）

- [ ] `apply-to-sheng-fork.py --dry-run` 列出的文件看着对
- [ ] 正式跑完覆盖脚本
- [ ] `grep "name = " board.toml` 输出 `name = "nx729j"`

---

## 阶段 4：里程碑 A——看到菜单（第 7 节）★ 生死关

- [ ] `./unixnu defconfig nx729j_defconfig -D ../UniXNU-nx729j`
- [ ] `./unixnu fetch` 跑完
- [ ] `./unixnu build package` 产出 `UniXNU.efi`
- [ ] `./unixnu build firmware --uefi ../uefi-sheng` 产出 `SILICIUM_UEFI.fd`
- [ ] `./unixnu bootimg --uefi ../uefi-sheng --dtb ... --ramdisk none` 产出 `boot.img`
- [ ] 把 `boot.img` 拷到 Windows 桌面
- [ ] **Windows** cmd 里 `fastboot boot boot.img`
- [ ] 手机上出现了 UniXNU 菜单

**结果记录（打勾）**

- [ ] 出现菜单 → 通关，继续阶段 5
- [ ] 黑屏有背光 → 看第 12 节"黑屏有背光"
- [ ] 黑屏无背光 → 看第 12 节"黑屏无背光"
- [ ] `Command not allowed` → 看 7.9 Plan B

> 到这里为止，手机没有任何数据被改过。长按电源 10 秒即可回到安卓。

---

## 阶段 5：内核和系统镜像（第 8 节，需要 Mac）

- [ ] 决定路线：借 Mac / GitHub Actions
- [ ] GitHub：设备仓库已推到你的账号
- [ ] GitHub：主仓库已 fork 并启用 Actions
- [ ] GitHub：加了 secret `UNIXNU_UNIQUE_CHIP_ID`
- [ ] 生成并记下 unique-chip-id：`________________`
- [ ] workflow 跑完，下载到 `kernel.macho`
- [ ] 下载到 `userdata-root.img`
- [ ] 在 Mac 上跑出 trust cache bundle（sha256 以 `7416ca4f` 开头）
- [ ] 回到 Ubuntu 跑 `build payload` + `bootimg`

---

## 阶段 6：量分区填表（第 9 节）

- [ ] `nx729j.py measure` 跑出 `first_lba` / `last_lba`
- [ ] `--write board.toml` 写入成功，回读校验通过
- [ ] 记下：`first_lba = __________  last_lba = __________`
- [ ] `check-nx729j.py --require-userdata-geometry` 全绿

---

## 阶段 7：真启动（第 10 节，会清空安卓）

- [ ] 再做一次心理确认：数据已备份
- [ ] `fastboot flash userdata userdata-root.img`（几十分钟，别拔线）
- [ ] `fastboot reboot bootloader`
- [ ] `fastboot boot boot.img`
- [ ] 记录走到第几步（1 菜单 / 2 DarwinBoot / 3 内核日志 / 4 等根设备 / 5 苹果 logo / 6 主屏幕）
- [ ] 卡住的话：拍照 + 拷贝 `out/nx729j/logs/`

---

## 阶段 8：触控（第 11 节）

- [ ] 系统能进、能显示之后再来做
- [ ] `adb shell "cat /proc/bus/input/devices"` 看到 Goodix
- [ ] 按 `drivers/NativeGoodixTouch/README.md` 写驱动

---

## 卡住了先自查这 5 条

1. 我在**哪个系统**里敲的命令？（编译在 Ubuntu，刷机在 Windows）
2. 我在**哪个目录**？（`./unixnu` 只能在 `~/UniXNU` 里跑）
3. `fastboot devices` **真的有输出**吗？
4. 我把完整报错**复制**下来看了吗，还是只看了最后一行？
5. 上一步的验证项，我真的看到预期输出了吗？

---

## 要记录下来的信息（出问题时要发给别人）

| 项 | 值 |
|---|---|
| 机型代号 | NX729J |
| 安卓版本 | |
| 系统版本号 | |
| UFS 逻辑块大小 | 4096 |
| flash_locked | |
| userdata first_lba | |
| userdata last_lba | |
| unique-chip-id | |
| 备份存放位置 | |
| 卡在第几阶段 | |
