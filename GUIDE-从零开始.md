# 从零开始：把 iOS 跑在红魔 8 Pro+ 上

> 写给完全没接触过这个项目的人。假定你只会用 Windows，会复制粘贴命令，能看懂英文报错大概意思。
> 每一步都写清楚三件事：**你要做什么、敲什么、屏幕上应该看到什么**。看不到的时候怎么办，写在最后一张对照表里。

---

## 目录

- [〇、先花五分钟想清楚](#〇先花五分钟想清楚)
- [一、这件事的完整地图](#一这件事的完整地图)
- [二、准备清单](#二准备清单)
- [三、阶段 0：体检和备份（约 1 小时）](#三阶段-0体检和备份约-1-小时)
- [四、阶段 1：解锁 bootloader（约 1 小时，会清数据）](#四阶段-1解锁-bootloader约-1-小时会清数据)
- [五、阶段 2：装编译环境（1-2 小时，主要在下东西）](#五阶段-2装编译环境1-2-小时主要在下东西)
- [六、阶段 3：把为你机型准备好的文件接上去（10 分钟）](#六阶段-3把为你机型准备好的文件接上去10-分钟)
- [七、阶段 4：里程碑 A——让手机显示 UniXNU 菜单](#七阶段-4里程碑-a让手机显示-unixnu-菜单)
- [八、阶段 5：里程碑 B——出内核和系统镜像（需要 Mac）](#八阶段-5里程碑-b出内核和系统镜像需要-mac)
- [九、阶段 6：量分区、填表（5 分钟）](#九阶段-6量分区填表5-分钟)
- [十、阶段 7：真启动（会清空安卓数据）](#十阶段-7真启动会清空安卓数据)
- [十一、阶段 8：显示与触控](#十一阶段-8显示与触控)
- [十二、报错对照表](#十二报错对照表)
- [十三、名词表](#十三名词表)
- [十四、怎么回到安卓 / 不想玩了怎么办](#十四怎么回到安卓--不想玩了怎么办)
- [十五、几句实话](#十五几句实话)

---

## 〇、先花五分钟想清楚

### 1. 这不是"刷个 ROM"

UniXNU 是一个**研究项目**：它用安卓的 bootloader 去启动一段自制 UEFI 固件，再由这段固件把苹果的 XNU 内核（iOS 的内核）拉起来，最后用苹果官方 iPadOS 安装包里的**原版系统文件**当桌面。

它不是你熟悉的"解锁—刷入—重启—用上"的流程。它是**操作系统移植**，跟"把 Linux 装到一台没人装过的手机上"是同一类事。

### 2. 你的机型没有人做成过

- 红魔 8 Pro+（内部代号 **NX729J**）的 UEFI 固件部分，社区已经提交进官方仓库了——这是好消息，说明最难啃的固件层已经有人趟过。
- 但**设备仓库**（板级参数、内核配置、触控驱动）没有任何人公开做过。我给你把这份仓库写好了，参数是从你真机的设备树里读出来的。
- 结论：**你是这个机型上第一个人**。没有人能保证它会启动。我为你做的准备能让你少走几周弯路，但不能保证结果。

### 3. 三个硬门槛，先看你能不能过

| 门槛 | 说明 | 过不去会怎样 |
|---|---|---|
| **必须有一台 Mac** | 编译内核要用苹果的 Xcode，做系统镜像要用苹果的 `hdiutil`。Windows 做不到。没有 Mac 可以借 GitHub 的免费 macOS 机器（教程里有步骤） | 只能做到"看到菜单"，上不了桌面 |
| **手机必须允许 `fastboot boot`** | 就是从内存临时启动一个没签名的镜像。**努比亚的 bootloader 让不让，没人替你验证过** | 直接到此为止（有退路，见阶段 4 的 Plan B） |
| **第一次刷系统会清空安卓数据** | `fastboot flash userdata` 会覆盖整个用户数据区 | 照片、聊天记录、微信全部消失。**先备份** |

### 4. 做完之后，它是什么样的

参照同芯片（骁龙 8 Gen 2）已经成功的小米平板 6S Pro：

| 功能 | 状态 |
|---|---|
| 显示 | 可用 |
| 触控 | 可用（但你的触控芯片和它不同，驱动要另外写） |
| 图形加速 | 可用 |
| 存储 | 可用 |
| Wi-Fi / 蓝牙 | **不可用** |
| 声音 | **不可用** |
| 摄像头 | **不可用** |
| 打电话 / 短信 | **不可用** |
| 电池显示 / 休眠 | **不可用** |
| App Store、Apple ID、iCloud | **不可用**（没有网络，也没法激活） |

翻译一下：**做成了，你得到一块能滑能点、能开设置、但不能上网的"iPad 形状的砖"。**

如果你要的是"日常用 iOS"，这个项目现在做不到，安卓+iOS 双系统也做不到。如果你的兴趣是"看它能不能跑起来"，那这就是一个很过瘾的项目。

### 5. 每一步的可信度我都标出来

文档里的每个结论，我标了来源：

| 标记 | 含义 |
|---|---|
| `[已核对]` | 有官方文档、源码或我实际跑过的验证 |
| `[待你实测]` | 只能在你手机上确认，我给的是方法和判断标准 |
| `[未验证]` | 我无法确认，可能是错的，做好失败准备 |

---

## 一、这件事的完整地图

整个流程分三个阶段，**每个阶段都有独立的价值**，做不完也能停在一个有意义的地方。

```
阶段 A  只用 Windows（不清任何数据）
  装 WSL 环境 → 编 UEFI 固件 → 打包成 boot.img → fastboot boot
  通关标志：手机屏幕上出现 UniXNU 的文字菜单
  ↑ 这一步验证了"你的手机到底允不允许我们启动自制镜像"，是整件事的生死关

阶段 B  需要 Mac（借的也行）
  编 XNU 内核（4-8 小时机器时间）→ 做系统镜像（userdata，几十 GB）

阶段 C  会清空安卓数据
  量 userdata 分区 → 刷入系统镜像 → 真启动 → 看它卡在哪一步
  通关标志：苹果 logo / 主屏幕
```

**重要：阶段 A 完全没有风险。** 它不写手机任何分区，失败最多黑屏，长按电源 10 秒就回到安卓。
所以你完全可以先做完阶段 A，看看有没有希望，再决定要不要投入后面。

### 你会用到的四个"仓库"

| 名字 | 是什么 | 谁提供 |
|---|---|---|
| `UniXNU` | 编译驱动 + 通用代码 | 上游项目 |
| `uefi-sheng` | UEFI 固件源码，里面**已经有你的机型** `Platforms/ZTE/nx729jPkg` | 上游项目 |
| `UniXNU-sheng` | 小米平板 6S Pro 的设备仓库（同芯片，我们借它的机器码） | 上游项目 |
| `UniXNU-nx729j` | **我给你写的**：你机型的板级参数、脚本、工具 | 就在你手上这个文件夹 |

---

## 二、准备清单

### 硬件

- [ ] 红魔 8 Pro+（NX729J），能开机
- [ ] **一条能传数据的 USB 线**（很多"充电线"只有两根电源线，接上电脑没反应，这是新手第一个坑）
- [ ] 一台 Windows 电脑，**至少 100 GiB 空闲空间**（内核编译很吃空间）
- [ ] 网速能接受下载 20-30 GB
- [ ] 一台 Mac（自己的/借的/或用 GitHub 免费机器，阶段 B 才需要）

### 软件（阶段 0 会带你装）

- [ ] Python 3.9+（Windows）
- [ ] Android platform-tools（含 adb 和 fastboot）
- [ ] WSL2 + Ubuntu（在 Windows 里跑一个真正的 Linux）
- [ ] Git

### 账号

- [ ] 红魔账号（解锁 bootloader 要在官网申请解锁权限）
- [ ] GitHub 账号（阶段 B 用免费 macOS 机器时要）

### 时间

| 阶段 | 纯操作时间 | 主要是在等 |
|---|---|---|
| 阶段 0 体检备份 | 1 小时 | 拷照片 |
| 阶段 1 解锁 | 1 小时 | 申请解锁权限可能要等 |
| 阶段 2 环境 | 1-2 小时 | 下载 |
| 阶段 4 里程碑 A | 1 小时 | 编译 10-20 分钟 |
| 阶段 5 里程碑 B | 半天 | 内核编译 4-8 小时 |
| 阶段 7 真启动 | 1-2 小时 | 刷几十 GB 镜像 |

**建议节奏：一天只干一个阶段。** 熬夜连着干，出错你会分不清是哪一步的问题。

### 必须先备份的东西

1. **照片、视频、微信聊天记录**——阶段 7 会全没。
2. **微信/支付宝/银行 App 的登录状态**——清数据后要重新验证，有些要跑网点。
3. **两步验证器（Authenticator）的恢复码**——这个不清醒的人最容易被锁在账号外面。
4. **记下当前系统版本号**（设置 → 关于手机 → 版本号），万一要回滚。

做法：手机连电脑 → 选"文件传输" → 把 `DCIM`、`Pictures`、`Download`、`Documents` 整个拷到电脑。微信记录用微信自带的"聊天记录迁移与备份"到电脑版。

---

## 三、阶段 0：体检和备份（约 1 小时）

### 3.1 装 Python

打开 <https://www.python.org/downloads/>，下载 Windows 版。

**安装时务必勾选最下面的 `Add python.exe to PATH`**，这一步漏了后面所有命令都会报"找不到 python"。

验证：按 `Win + R`，输入 `cmd`，回车，然后敲：

```
python --version
```

看到 `Python 3.x.x` 就对了。`[已核对]`

### 3.2 装 adb 和 fastboot

这两个是跟手机"说话"的工具，合称 platform-tools。

1. 打开 <https://developer.android.com/studio/releases/platform-tools>
2. 下载 **SDK Platform-Tools for Windows**
3. 解压到一个**路径里没有中文和空格**的地方，比如 `C:\platform-tools`
4. 把这个目录加进 PATH：
   - `Win` 键 → 输入"环境变量" → 打开"编辑系统环境变量"
   - 点"环境变量" → 在下面"系统变量"里找到 `Path` → 双击 → "新建" → 粘贴 `C:\platform-tools` → 一路确定
   - **关掉所有 cmd 窗口再重开**（不重开读不到新 PATH，这是第二个常见坑）

验证：

```
adb version
fastboot --version
```

两条都要有版本号输出。`[已核对]`

> 提示：如果你电脑上已经有 adb（比如装过某些手机助手），版本太老会出问题。上面前两条命令如果报错，说明没装好。

### 3.3 打开手机的开发者选项和 USB 调试

1. 设置 → 关于手机 → 版本号，**连续点 7 次**，提示"已进入开发者模式"
2. 设置 → 系统 → 开发者选项 → 打开 **USB 调试**
3. 顺手看一眼有没有 **"OEM 解锁"** 开关，有的话先打开（没有就跳过，阶段 1 再说）

### 3.4 跑体检

把手机用 USB 线连电脑，手机上会弹"允许 USB 调试吗"，勾选"一律允许"并确定。

然后在你手上这个文件夹里，双击：

```
tools\run-doctor.bat
```

它会做四件事：检查电脑上的工具、读手机型号和系统版本、看有没有解锁、打印下一步该做什么。

**正常应该看到：**

```
[OK] adb : Android Debug Bridge version 1.0.41
[OK] fastboot : ...
     model                NX729J
     android              14
     verified_boot        green
     flash_locked         1
     ufs_block_size       4096
```

要关注的几个值：

| 字段 | 正常值 | 含义 |
|---|---|---|
| `model` / `device` | `NX729J` | 机型对上了 |
| `flash_locked` | `1` = 还锁着，`0` = 已解锁 | 阶段 1 要把它变成 0 |
| `ufs_block_size` | `4096` | 必须是 4096，不是的话先别往下走，把报告发出来 |
| `verified_boot` | `green` | 未解锁时的正常状态 |

报告会存成 `nx729j-report.json`，出问题时把这个文件发出来，别人一看就知道你的机器什么状态。

**`[待你实测]`** 这一步的每一项都需要你手机真实配合，我无法替代。

### 3.5 备份

按上面"必须先备份的东西"做完。

---

## 四、阶段 1：解锁 bootloader（约 1 小时，会清数据）

> **这一节做完，手机等于恢复出厂设置。** 照片没拷出来的现在去拷。

### 4.1 为什么必须解锁

手机的 bootloader（红魔用的是高通方案，叫 ABL）默认只肯启动**厂商签过名**的系统。我们要启动的是自己编的固件，签名对不上，所以必须解锁。

解锁后手机会在开机时显示"设备已解锁，无法保证安全"之类的警告，**这是正常的，不是坏了**。

### 4.2 努比亚的解锁流程

努比亚/红魔的解锁要在官网申请，不是所有型号都开放。流程大致是：

1. 打开红魔官网的"解锁申请"页面（搜"红魔 bootloader 解锁 申请"，或从"红魔社区"进）
2. 登录红魔账号，填机型（**NX729J / 红魔 8 Pro+**）、IMEI、申请理由
3. 等审核（有的秒过，有的要几天）
4. 通过后会给你一条解锁命令，形如：

```
fastboot oem nubia_unlock NUBIA_NX729J
```

`[未验证]` 这条命令的具体格式以你收到的官方通知为准，**不要照抄网上的**，写错可能变砖。

> **如果官方不给你解锁**：这件事到你这里就结束了，后面所有步骤都做不了。没有"绕过"的办法，也不要去找付费解锁服务（绝大多数是骗钱或者偷数据的）。

### 4.3 执行解锁

确认手机里"OEM 解锁"开关已打开（开发者选项里），然后：

```
adb reboot bootloader
```

手机会黑屏一下，进入一个写着 FASTBOOT 的界面。然后：

```
fastboot devices
```

应该输出一串序列号和 `fastboot` 字样。没有的话见[报错对照表](#十二报错对照表)。

接着敲你的解锁命令（用官方给你的那条），等它返回 `OKAY`。然后：

```
fastboot flashing unlock
```

手机会问你"是否解锁"，**用音量键选 Yes，电源键确认**。手机会自己清数据并重启，第一次开机要等 5-10 分钟。

### 4.4 验证

开机后重新打开 USB 调试，再跑一次 `tools\run-doctor.bat`，看 `flash_locked`：

```
     flash_locked         0
```

变成 `0` 就解锁成功了。`[待你实测]`

---

## 五、阶段 2：装编译环境（1-2 小时，主要在下东西）

### 5.1 为什么不在 Windows 里编

编译这个项目要用 Linux 的一套工具（大小写敏感的文件系统、clang、lld、mtools）。Windows 的文件系统不分大小写，会出各种莫名其妙的错。

**WSL2 是 Windows 自带的 Linux**，用起来就像开一个终端窗口，不需要重装系统、不需要虚拟机软件。

### 5.2 装 WSL2 + Ubuntu

以**管理员身份**打开 PowerShell（右键开始菜单 → "终端(管理员)"），敲：

```
wsl --install -d Ubuntu-24.04
```

等它下完（约 2-3 GB），会让你设一个 Linux 用户名和密码（**密码输入时不显示字符，这是正常的**，盲打完回车）。

看到 `username@机器名:~$` 这种提示符，就进 Linux 了。以后想再进来：开始菜单搜 "Ubuntu"。

`[已核对]` 这是微软官方支持的方式。

> 如果 `wsl --install` 报错说"不支持虚拟化"，需要进 BIOS 打开 CPU 虚拟化（Intel VT-x / AMD SVM）。

### 5.3 装编译工具

在 Ubuntu 窗口里，逐条敲（`sudo` 会要你输刚才设的密码）：

```
sudo apt update
sudo apt install -y build-essential git python3 python3-venv clang lld llvm cmake ninja-build mtools device-tree-compiler nasm acpica-tools curl unzip
```

**这一步要下 1-2 GB，慢慢等。** 装完验证：

```
clang --version
mtools --version
dtc --version
```

三条都要有输出。`[已核对]`

### 5.4 把你的文件夹放进 Linux

Windows 的 `C:` 盘在 Linux 里是 `/mnt/c/`。所以你这个文件夹在 Linux 里是：

```
/mnt/c/Users/Administrator/WorkBuddy/2026-10-22-09-08-08/UniXNU-nx729j
```

先验证一下路径对不对（把路径换成你实际的）：

```
ls "/mnt/c/Users/Administrator/WorkBuddy/2026-10-22-09-08-08/UniXNU-nx729j"
```

应该列出 `board.toml`、`scripts`、`tools` 这些。看不到的话，说明路径不对，找到你实际的文件夹位置。

### 5.5 下载三个上游仓库

在 Ubuntu 里：

```
cd ~
git clone --recurse-submodules https://github.com/UniXNU/UniXNU
git clone https://github.com/UniXNU/uefi-sheng
git clone --recurse-submodules https://github.com/UniXNU/UniXNU-sheng UniXNU-nx729j
```

三条分别下：编译驱动、你的机型固件、同芯片的参照设备仓库。

`[已核对]` 命令来自官方文档。

**这三条要下几 GB，尤其第一条会拉一堆子模块。** 网络不好会断，断了就重跑同一条命令（git 会续传）。

---

## 六、阶段 3：把为你机型准备好的文件接上去（10 分钟）

现在 `~/UniXNU-nx729j` 里是小米平板的内容，需要用我写的文件替换掉板级部分（机器码、kit、GPU 那些**保留**，因为同芯片可以复用）。

先看它打算改什么（不实际改）：

```
cd ~/UniXNU-nx729j
python3 "/mnt/c/Users/Administrator/WorkBuddy/2026-10-22-09-08-08/UniXNU-nx729j/scripts/apply-to-sheng-fork.py" --target . --dry-run
```

会列出要写入的文件。确认列出的都是 `board.toml`、`configs/nx729j_defconfig`、`scripts/make-nx729j-adt.py` 这类文件，然后真跑（去掉 `--dry-run`）：

```
python3 "/mnt/c/Users/Administrator/WorkBuddy/2026-10-22-09-08-08/UniXNU-nx729j/scripts/apply-to-sheng-fork.py" --target .
```

**验证：**

```
git status
ls board.toml configs/nx729j_defconfig scripts/make-nx729j-adt.py
grep "name = " board.toml | head -3
```

`grep` 那行应该输出 `name = "nx729j"`。`[已核对]` 这个脚本我实际跑过，会拒绝覆盖不是 sheng 的仓库。

---

## 七、阶段 4：里程碑 A——让手机显示 UniXNU 菜单

**这是整件事的生死关。** 它验证两件事：固件能不能编出来、你的手机到底让不让启动它。

**这一步不写你手机的任何分区，绝对不会变砖。**

### 7.1 选设备仓库

```
cd ~/UniXNU
./unixnu defconfig nx729j_defconfig -D ../UniXNU-nx729j
```

**应该看到**：把 `configs/nx729j_defconfig` 复制到了 `out/nx729j/.config`。`[已核对]`

### 7.2 拉源码

```
./unixnu fetch
```

这一步会去拉 XNU 源码、edk2（固件框架）等一堆东西，**要下几个 GB，可能 10-30 分钟**。中间出现进度条是正常的。

如果断了，重跑同一条命令。`[已核对]`

### 7.3 编板级管理程序

```
./unixnu build package
```

几分钟。产物在 `out/nx729j/package/Build/Out/`，里面有 `UniXNU.efi`。`[已核对]`

### 7.4 编 UEFI 固件

```
./unixnu build firmware --uefi ../uefi-sheng
```

**10-20 分钟。** 这一步会先跑我写的预检脚本，检查固件基址、内存映射和你的设备树有没有冲突。

**注意**：预检里会有一条黄色提醒说 `userdata` 的分区 LBA 还没量。**这条现在可以忽略**——编固件用不到它，等阶段 6 再填。

成功的标志：出现 `SILICIUM_UEFI.fd` 的路径，并且文件存在：

```
ls -l ../uefi-sheng/Build/nx729jPkg/*/FV/SILICIUM_UEFI.fd
```

`[已核对]` 目标文件名来自 `nx729j.fdf`。

### 7.5 打包成 boot.img

```
./unixnu bootimg --uefi ../uefi-sheng \
  --dtb ../uefi-sheng/Resources/DTBs/nx729j.dtb \
  --ramdisk none
```

三个参数的意思：
- `--uefi`：固件从哪来
- `--dtb`：用**你机型的真机设备树**（这个文件仓库里已经有，是从真机 dump 出来的）
- `--ramdisk none`：**空 ramdisk**，也就是这次只启动固件和菜单，不加载内核

**应该看到**：`out/nx729j/boot/boot.img`，大约 10-20 MB。

### 7.6 把镜像拿到 Windows 上

在 Ubuntu 里：

```
mkdir -p /mnt/c/Users/Administrator/Desktop/nx729j
cp out/nx729j/boot/boot.img /mnt/c/Users/Administrator/Desktop/nx729j/
ls -l /mnt/c/Users/Administrator/Desktop/nx729j/boot.img
```

然后**打开一个新的 Windows cmd 窗口**（不要在 WSL 里刷机，WSL 默认看不到 USB 设备，这是新手第三个坑）。

### 7.7 启动它

先让手机进 fastboot：

```
adb reboot bootloader
fastboot devices
```

然后（`cd` 到你放 boot.img 的地方）：

```
cd %USERPROFILE%\Desktop\nx729j
fastboot boot boot.img
```

或者用我写的工具（它会先检查镜像格式，再让你确认）：

```
python "C:\Users\Administrator\WorkBuddy\2026-10-22-09-08-08\UniXNU-nx729j\tools\nx729j.py" boot "%USERPROFILE%\Desktop\nx729j\boot.img"
```

### 7.8 看结果

**这是本阶段唯一重要的事。** 三分钟后，你手机屏幕上会出现下面之一：

| 现象 | 说明 | 下一步 |
|---|---|---|
| **出现黑底白字的菜单**（`UniXNU` 字样、几行选项） | **通关了。** 固件、显示、设备树全部正常 | 直接进阶段 5 |
| 屏幕亮着但全黑 / 只有背光 | 固件跑起来了但没画东西 | 看 [报错对照表](#十二报错对照表) 的"黑屏有背光" |
| 完全黑，没背光 | 固件没起来（基址或内存映射不对） | 同上，"黑屏无背光" |
| 卡在红魔 logo | 镜像没被接受，或者卡在 shim | 同上 |
| 自己重启回到安卓 | 固件崩了，watchdog 复位 | 同上 |
| 报错 `Command not allowed` / `unknown command` | **你的 bootloader 不让 `fastboot boot`** | 见下面的 Plan B |

**不管哪种结果，退出方法都一样：长按电源键 10 秒强制重启，安卓原样回来。**

### 7.9 如果 `fastboot boot` 被拒绝（Plan B）

努比亚有些版本的 ABL 会禁用 `fastboot boot`。如果报：

```
FAILED (remote: 'Command not allowed')
```

你有三个选择，按推荐顺序：

**Plan B1：先确认是不是解锁没生效。** 重跑 `fastboot getvar unlocked`，必须是 `yes`。不是的话回阶段 1。

**Plan B2：刷到 boot 分区（有风险，务必先备份）。** 原理是 `fastboot flash boot_a boot.img` 后重启，让它走正常启动流程，再刷回原厂 boot。

```
# 1. 先把原厂 boot 读出来（有些机型支持，你的不一定）
fastboot fetch boot_a stock-boot.img

# 2. 如果没有 fetch，用 root 后 adb 备份，或者直接下官方固件包提取
```

> `[未验证]` 努比亚 ABL 是否支持 `fastboot fetch` 我无法确认。**在没备份原厂 boot 之前，不要刷 boot 分区**——刷错了手机不进系统也不进 fastboot，只能送修。

**Plan B3：到上游问。** 把你 `nx729j-report.json` 和 `fastboot getvar all` 的输出发到 UniXNU 的 GitHub Issues，问 nx729j 有没有人试过 `fastboot boot`。这是最有价值的一步——你可能正好补上项目缺的一块信息。

---

## 八、阶段 5：里程碑 B——出内核和系统镜像（需要 Mac）

从这里开始，Windows 和 Linux 都做不了了，因为：

- 内核编译调 `xcodebuild`，用苹果的 `iphoneos` SDK `[已核对]`
- 系统镜像（userdata）用 `hdiutil` 挂载苹果的磁盘镜像 `[已核对]`

### 8.1 两条路，选一条

**路线 1：借/租一台 Mac。** 最省心。macOS + Xcode（App Store 免费装）+ Homebrew：

```
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install cmake ninja llvm mtools dtc unifdef
```

**路线 2：用 GitHub 免费的 macOS 机器。** 不需要自己有 Mac，但要把代码传到 GitHub。

### 8.2 路线 2 的具体做法

1. **把设备仓库传到 GitHub**（私有仓库就行）：
   - 注册 GitHub 账号
   - 网页上新建一个空仓库，名字 `UniXNU-nx729j`
   - 在你的 Ubuntu 里：
     ```
     cd ~/UniXNU-nx729j
     git add -A
     git commit -m "nx729j port"
     git remote set-url origin https://github.com/你的用户名/UniXNU-nx729j.git
     git push -u origin main
     ```
     （会要你输 GitHub 的用户名和 token，token 在 GitHub 设置里生成，选 `repo` 权限）

2. **Fork 主仓库**：打开 <https://github.com/UniXNU/UniXNU>，点右上 Fork。然后在你 fork 的仓库里点 **Actions** 标签，启用 workflow。

3. **加一个 secret**：你 fork 的仓库 → Settings → Secrets and variables → Actions → New repository secret：
   - 名字：`UNIXNU_UNIQUE_CHIP_ID`
   - 值：一串 16 位十六进制数，比如 `3f2a91c4d7005be8`
     > 这个值的作用是给系统一个"这台设备的唯一身份"，用来生成 `IOPlatformUUID`。官方仓库里不放这个值，所以你自己生成一个，**生成一次就固定下来，以后都用它**。
     > 生成方法（任意电脑）：`python -c "import secrets;print(secrets.token_hex(8))"`

4. **跑 workflow**：Actions → 选 `macOS build` → Run workflow，填：
   - `device_repository`：`你的用户名/UniXNU-nx729j`
   - `device_ref`：`main`
   - `defconfig`：`nx729j_defconfig`
   - `steps`：`kernel userdata`
   - 其它留空

5. 等 4-8 小时。跑完在运行页面的 Artifacts 里下载 `kernel.macho` 和 `userdata-root.img`。

`[未验证]` 这条 workflow 我无法替你跑。如果它报"device repository not found"，检查第 3 步的 secret 是不是漏了私有仓库 token（公开仓库不需要）。

### 8.3 你还需要两个文件

开机加载内核时，需要苹果签名的"白名单"（trust cache）和一块可写的用户数据（Data 种子）。这两个文件**不在任何仓库里**，要自己从 iPadOS 安装包里生成，而且脚本只能在 Mac 上跑：

```
# 在 Mac 上，在 UniXNU 目录里
./unixnu ipsw fetch                       # 下载 iPadOS 17.7.11 安装包（约 6 GB）
python3 scripts/build-original-system-trust.py
```

产物应该是一个 sha256 以 `7416ca4f...` 开头的 bundle（这个值已经写在你的 `board.toml` 允许列表里，对不上就说明生成错了）。

Data 种子（可选，用来跳过"你好"设置向导、直接到主屏幕）：

```
python3 scripts/prepare-owner-data-fixture.py
```

`[未验证]` 这个脚本要求系统镜像已经挂载在 `.cache/firmware/target-volumes/os-mount`，也就是要先跑过 `build userdata`。第一次做不出来很正常，**第一轮可以先不要 Data 种子，先让它进到恢复模式界面**，那已经证明内核和用户态跑起来了。

### 8.4 在 Linux 上打包最终镜像

内核和系统镜像拿到以后，回到 Ubuntu：

```
cd ~/UniXNU

# 内核放到指定位置（用你下载的文件名）
ls out/nx729j/kernel/kernel.macho

# 打包负载：把内核、ADT、trust cache 装进一个 FAT 卷
./unixnu build payload \
  --unique-chip-id 你的16位十六进制值 \
  --trust-cache build/original-static-system-trust/original-static-system.bundle

# 打包最终启动镜像
./unixnu bootimg --uefi ../uefi-sheng \
  --dtb ../uefi-sheng/Resources/DTBs/nx729j.dtb
```

（如果 workflow 是用 macOS 机器跑的，把 `kernel.macho` 和 `userdata-root.img` 拷回 Ubuntu 的 `out/nx729j/` 对应目录。）

---

## 九、阶段 6：量分区、填表（5 分钟）

**这一步是安全阀。** 系统镜像要写进 `userdata` 分区，写之前必须知道它从哪个扇区开始、到哪个扇区结束。填错了会写坏别的分区，那是真变砖。

好消息：**不用 root 就能量。** 手机正常开机、adb 连上，跑：

```
python "C:\Users\Administrator\WorkBuddy\2026-10-22-09-08-08\UniXNU-nx729j\tools\nx729j.py" measure --write "C:\Users\Administrator\WorkBuddy\2026-10-22-09-08-08\UniXNU-nx729j\board.toml"
```

**应该看到：**

```
[--] logical_block_size = 4096
[--] LUN 0 容量         = 476.7 GiB
  first_lba = 0x2f3840        (3094592)
  last_lba  = 0x3e8ffff        (65568767)
  lba_bytes = 4096
  容量      = 238.1 GiB
[OK] 已写入 ...\board.toml
[OK] 回读校验：0x2f3840..0x3e8ffff lba_bytes=4096
```

（数字是示意，你机器上是多少就是多少。）

**注意**：如果你要同步到 Linux 里的仓库，把 `board.toml` 拷过去，或者两个都改一遍。

然后复验：

```
cd ~/UniXNU-nx729j
python3 scripts/check-nx729j.py --require-userdata-geometry
```

这条加了 `--require-userdata-geometry`，意思是"没量分区就直接判失败"，专门用来在刷机前把关。 `[已核对]`

---

## 十、阶段 7：真启动（会清空安卓数据）

> **再问一次：照片拷出来了吗？微信记录备份了吗？两步验证恢复码记下了吗？**
> 这一步做完，你的安卓就是一台刚出厂的手机。

### 10.1 做系统镜像

如果上一步是在 Mac / GitHub 上做的，`userdata-root.img` 应该在 `out/nx729j/userdata/`。

这个文件**几十 GB**，做和拷都很慢。做完会有一个 `userdata-root.json` 报告，里面记录了刷写命令，照它执行就行。

### 10.2 刷入

```
fastboot devices
fastboot flash userdata out/nx729j/userdata/userdata-root.img
```

**要等很久**（几十 GB 通过 USB 写进手机，20-60 分钟），进度条走到 100% 前**千万别拔线**。

刷完：

```
fastboot reboot bootloader
fastboot boot boot.img
```

### 10.3 看它走到哪一步

这是整个项目最有意思的部分。按顺序看，卡在哪里，就说明那一层的问题：

| 序号 | 你应该看到 | 说明什么 | 卡住怎么办 |
|---|---|---|---|
| 1 | 黑底白字菜单 | UEFI 固件正常（阶段 4 已经验证过） | — |
| 2 | 选菜单项后开始刷屏白字，最后一行是 `DarwinBoot:` 开头 | 引导器把内核装进内存了 | 没有 `DarwinBoot:` → trust cache 哈希不对 |
| 3 | 出现 `AppleARMPE` / `IOPlatformExpert` 之类的内核日志 | XNU 内核跑起来了 | 报 `Unable to find driver for this platform` → ADT 里的机型身份不对 |
| 4 | 日志停在 `Still waiting for root device` | 内核找不到系统盘 | userdata 没刷成功，或者 LBA 填错 |
| 5 | 变成苹果 logo + 进度条 | 用户态开始加载 | 卡在进度条 → Data 卷问题 |
| 6 | 出现锁屏 / "你好"设置向导 | **成功。** iOS 用户态在骁龙上跑起来了 | — |
| 7 | 点得动、能进设置 | 触控也通了 | 点不动 → 阶段 8 |

**每一层的日志都能看到**，这是这个项目做得好的地方。`serial=3` 这个内核参数就是让它把日志打到屏幕上的串口。`[已核对]`

### 10.4 出问题怎么记录

1. 拍下屏幕最后 20 行（手机拍照就行）
2. 把 `out/nx729j/logs/` 里的日志拷出来
3. 用这两样东西去 GitHub Issues 提问，比"它不动了"有效一百倍

---

## 十一、阶段 8：显示与触控

这一节是你的机型**真正跟小米平板不一样**的两块硬件，也就是必须自己啃的部分。

### 11.1 显示：参数已经提出来了

面板参数不用你再去翻固件，我已经从你的真机设备树里全部提取出来了，放在
`docs/reference/panel/nx729j-panel.json`。里面有三样东西：

| 内容 | 值 |
|---|---|
| 面板 | BOE `BF068` 屏体 + Raydium `RM692H0` 驱动 IC，1116 × 2480 |
| 模式 | DSI **command mode** + burst，DSC 10 bit，slice 558 × 20，2 slice/包 |
| 三档时序 | 60 Hz（261.5 MHz）、90 Hz（392.3 MHz）、120 Hz（375.5 MHz，消隐换了一套） |
| 初始化序列 | **1349 字节**完整 DCS 命令流，含 RM692H0 的 page 切换 |

两个容易踩的坑，我替你排了：

1. **设备树里有两个长得几乎一样的面板节点。** 一个是 `..._1116p_...`（60/120 Hz），
   一个是 `..._magic_...`（60/90/120 Hz）。你的机器用的是**后者**，判据是设备树里
   `panel = <0x6b2>` 指向它的 phandle。别人按名字 grep 到第一个就会拿错参数。
2. **120 Hz 的消隐参数和 60 Hz 不一样**（HBP 由 336 变 76，HFP 由 248 变 26），
   直接套 60 Hz 的值会算出错误的像素时钟。

想自己复核一遍（在你自己的仓库里跑）：

```bash
python3 scripts/extract-panel-timings.py --dts nx729j.dts --active
```

它会打印三档时序表，并检查初始化流的首尾（应该以 `FE 98` 页切换开头、以
`sleep out` + `display on` 结尾）。`[已核对]` 这个脚本我用真机 DTS 跑过，输出见上表。

**剩下要做的**：把这组数据接到 `build-native-legacy-display.py` 的输入格式上。
那一步要在完整构建树里做（脚本依赖预链接内核的符号分析），不属于"补参数"，属于写绑定代码。

在安卓里也能交叉验证面板型号（手机正常开机、adb 连上）：

```
adb shell dmesg | findstr -i "panel"
```

应该能看到 `BF068_RM692H0_Full_Screen_MAGIC...`。`[待你实测]`

### 11.2 触控：唯一要真写代码的地方

你的触控芯片是 **Goodix GT9916**（设备树里写的是 `goodix,brl-d`），挂在 `spi@a8c000` 上，中断是 GPIO25，复位是 GPIO24，坐标是 **16 倍定点**（17856 = 1116 × 16，不除 16 触点会跑到屏幕外）。

参考设备小米平板用的是 Novatek 的芯片，**驱动完全不通用**，必须重写。详细规格我写在 `drivers/NativeGoodixTouch/README.md` 里了。

这一步的前提是：**先能显示、能进系统，再搞触控**。没显示之前写触控驱动是浪费时间。

在写驱动之前，先在安卓里确认一下你的触控硬件：

```
adb shell "cat /proc/bus/input/devices" | findstr -i goodix
adb shell "ls /sys/bus/spi/devices"
```

如果能看到 Goodix 相关字样，说明我给的参数方向是对的。`[待你实测]`

---

## 十二、报错对照表

### 连接类

| 报错 | 原因 | 怎么办 |
|---|---|---|
| `adb` 无输出 / `device not found` | 线是充电线、没开 USB 调试、没授权 | 换线；重新打开 USB 调试；看手机屏幕有没有授权弹窗 |
| `unauthorized` | 手机上没点"允许" | 拔插一次，手机上勾"一律允许" |
| `fastboot devices` 空的 | 没进 fastboot，或 Windows 缺驱动 | `adb reboot bootloader`；设备管理器里看有没有黄色感叹号，装 Google USB Driver |
| `'adb' 不是内部或外部命令` | PATH 没配好，或没重开 cmd | 关掉所有 cmd 重开；重做 3.2 |
| WSL 里 `fastboot devices` 空 | WSL2 默认看不到 USB | **刷机在 Windows 里做，编译在 WSL 里做** |

### 编译类

| 报错 | 原因 | 怎么办 |
|---|---|---|
| `unixnu: command not found` | 不在仓库根目录 | `cd ~/UniXNU` 再跑 |
| `defconfig: unknown device repository` | `-D` 路径不对 | 用绝对路径试：`-D ~/UniXNU-nx729j` |
| `fetch` 中断 | 网络 | 重跑同一条命令，git 会续传；国内网络可以考虑配代理 |
| 固件编译报 `Platforms/ZTE` 找不到 | `--uefi` 指错了 | 指向 `uefi-sheng` 的**根目录**，不是子目录 |
| 预检报 `uefi.dtb missing` | uefi-sheng 没下全 | `cd ~/uefi-sheng && git status`，重新 clone |

### 启动类

| 现象 | 最可能的原因 | 怎么办 |
|---|---|---|
| `FAILED (remote: 'Command not allowed')` | ABL 禁止 `fastboot boot` | 阶段 4 的 Plan B |
| `FAILED (status read failed (Too many links))` | 传输完设备重启了，**可能其实已经启动** | 看手机屏幕，别急着重试 |
| `cannot load 'boot.img'` | 路径不对 | 用绝对路径 |
| 黑屏无背光 | 固件没起来：FD 基址/大小不对 | 跑 `scripts/check-nx729j.py`，看 `uefi.memory_map` 那几项 |
| 黑屏有背光 | 固件起来了但没出图 | 显示初始化序列和设备树里的面板参数不匹配 |
| 卡在红魔 logo | shim 跳转失败 | 确认 `boot.img` 是 `bootimg` 步骤刚产出的，别用旧的 |
| 启动后自己重启 | 崩溃后被看门狗复位 | 内核参数里已经有 `sheng-disable-watchdogd=1`，还重启就是更早的阶段就崩了 |
| 菜单里按键没反应 | 输入驱动 | 你的机型用 GPIO 按键，需要确认按键节点 |

---

## 十三、名词表

| 词 | 说人话 |
|---|---|
| **bootloader / ABL** | 手机开机第一个跑的程序，负责决定"启动谁"。红魔用的是高通 ABL |
| **fastboot** | 电脑跟 bootloader 说话的工具。`flash` = 写进去，`boot` = 只启动不写 |
| **解锁 bootloader** | 让 bootloader 肯启动厂商没签名的系统。代价是清数据+开机警告 |
| **分区 / userdata** | 手机存储被切成很多块，每块叫一个分区。`userdata` 是放你照片、聊天记录、App 数据的那块 |
| **LBA** | 磁盘上的"第几个扇区"。`first_lba`/`last_lba` 就是分区的头和尾 |
| **UEFI** | 电脑上那种"开机进 BIOS 设置界面"的固件标准。这里我们给手机编了一个 |
| **DTB / 设备树** | 一份描述"这个设备有哪些硬件、分别在哪"的清单。内核靠它认识硬件 |
| **ADT** | 苹果版的设备树，格式和安卓的不一样 |
| **XNU** | 苹果的内核（macOS/iOS 共用的那个） |
| **kernelcache** | 苹果内核的压缩包，在安装包里，本项目要和它"预链接" |
| **IPSW** | 苹果的固件安装包（.ipsw）。本项目用 iPad Pro 10.5 的 iPadOS 17.7.11 |
| **trust cache** | 苹果的"允许运行的代码"白名单 |
| **payload** | 本项目的一个 FAT 镜像，里面装内核、设备树、配置 |
| **ramdisk** | 启动时临时加载的小文件系统 |
| **prelink** | 把"我们编的内核"和"苹果原版内核里的驱动"缝在一起 |

---

## 十四、怎么回到安卓 / 不想玩了怎么办

| 情况 | 做法 |
|---|---|
| `fastboot boot` 之后黑屏/卡住 | 长按电源 10 秒强制重启，安卓原样回来（没写任何东西） |
| 刷完 userdata，想回安卓 | 手机能进 fastboot：刷官方固件包（官网下载，`fastboot flashall` 之类）。数据回不来，系统能回来 |
| 刷坏了 boot 分区 | 用官方固件包里的 boot.img 刷回去。**所以 Plan B 之前必须备份 boot** |
| 完全进不了 fastboot | 高通 9008 模式（EDL）救砖。需要专门的工具和授权，普通人做不到，只能送修 |

**安全底线：**
- `fastboot boot` 是安全的，随便试
- `fastboot flash boot` 有风险，必须先备份
- `fastboot flash userdata` 会清数据，但系统可恢复
- 别的分区（`abl`、`xbl`、`persist`）**绝对不要碰**，碰了可能永久变砖

---

## 十五、几句实话

### 关于难度

我按你能做下来的标准写了这份教程，但必须说清楚：**这个项目对新手不友好**，不是因为你笨，是因为它本来就不是给个人用户准备的东西。它的目标读者是能读 C 代码、会用 Linux、出问题会自己看日志的人。

你大概会在下面几个地方卡住（按概率排）：

1. 努比亚不给你解锁（这里就结束了）
2. `fastboot boot` 被拒（还有救，但要折腾）
3. 内核编译在 macOS 那一步卡住（最难自己解决）
4. 触控驱动要自己写（这是真正的开发工作）

**如果你只是想体验一下，做完成阶段 4 就已经很有价值了**——你会看到自己的手机显示出一个别人从没在这个机型上见过的东西。

### 关于合法性和风险

- iOS 的系统文件来自苹果官方的 iPadOS 安装包，未做修改。苹果的许可协议只授权在苹果硬件上运行它，所以这种做法处在法律灰区。**自用研究、不传播不商用**，风险你自己判断。
- 刷机可能失去保修。
- 清空数据不可逆。
- 我给你的所有参数，能核对的都标了来源，**但 `[待你实测]` 和 `[未验证]` 的部分需要你自己承担结果**。我没有你的真机，编不出"保证能启动"的承诺。

### 做出来之后建议做的事

如果阶段 4 或阶段 7 通了，**把结果发到 UniXNU 的 GitHub Discussions**，带上 `nx729j-report.json`、屏幕照片和日志。

理由很实际：这个项目缺的正是"第二个机型"，你的数据能让别人少走几个月的路；而且一旦维护者愿意把 nx729j 合并进官方设备列表，以后别人用你的机型就是一条命令的事，不用再走一遍你今天走的路。

---

*本教程对应仓库 `UniXNU-nx729j`。技术细节见 `PORTING-NX729J.md`，硬件数据来源见 `docs/reference/nx729j-dtb-facts.md`。*
