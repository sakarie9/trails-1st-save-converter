# 空の軌跡 the 1st / 空之轨迹 1st —— Switch ⇄ Steam 存档转换脚本

[English README](README.md)

`sora_save_convert.py` 是一个独立的 Python 小脚本，用于在
**Nintendo Switch** 与 **Steam（PC）** 两种格式之间**双向**转换
《空之轨迹 the 1st / Trails in the Sky 1st Chapter》的存档，同时支持游戏写出的两类数据：
槽位存档（`saveNNN`）和系统数据（`sdmemNNN`）。

它的文件格式知识来自图形化存档修改器
[turtle-insect/TrailsintheSky1stChapter](https://github.com/turtle-insect/TrailsintheSky1stChapter)。
那个项目能**编辑**两种平台的存档，但没有"转换成另一种平台格式"的功能，
本脚本补上的正是这一步。

游戏状态数据（下称 payload）会被**原样逐字节复制**，脚本不会改写或重编码存档内容；
只重新封装容器、拆分/合并存档槽的元数据文件，并重算校验和。

---

## 环境要求

* Python 3.8 或更高版本
* `zstandard` 模块：

```bash
pip install zstandard
```

不需要其它第三方库（**不需要** Pillow）。

## 快速开始

```bash
# Switch -> Steam
python3 sora_save_convert.py convert save000_switch save000_steam_out

# Steam -> Switch
python3 sora_save_convert.py convert save000_steam  save000_switch_out

# 查看存档内容
python3 sora_save_convert.py info save000_switch

# 用一份已知正常的存档对做自检
python3 sora_save_convert.py selftest --switch save000_switch --steam save000_steam
```

> **只转换存档槽（`saveNNN`）就够了。** 转换存档就能在另一个平台继续玩。
> **不要转换系统数据（`sdmemNNN`）**：它只是分平台的选项设置和记录，目标平台会自己生成，
> 里面没有值得迁移的内容。

`SRC`（源）可以是存档**目录**，也可以是单个**文件**：

* `data.dat`（或包含它的目录）会被识别为 Switch 存档；
* `user.dat`（或包含它的目录）会被识别为 Steam 存档。如果直接指定裸的
  `user.dat`，脚本会在同一目录下寻找 `icon0.png` 和 `detail.json`。

常用参数：

* `--to steam|switch` —— 强制目标格式（默认自动取另一个平台）。
* `--level N` —— 生成 `user.dat` 时的 zstd 压缩等级（默认 `3`，与参考修改器一致）。
* `--set-u32 OFFSET=VALUE` —— 打包前修改 payload 中的一个小端 `uint32`
  （可重复，例如 `--set-u32 0x0=7`），脚本随后会重算校验和。
* `--force` —— 允许覆盖输出目录中已存在的文件。

完整参数见 `python3 sora_save_convert.py --help`。

## 输出内容

| 目标平台 | 在 `DST` 目录中生成 |
| --- | --- |
| Switch | `data.dat` |
| Steam | `user.dat`、`icon0.png`、`detail.json` |

`detail.json` 会以 UTF-8、CRLF 换行、4 空格缩进重新写出，与游戏自己写的文件逐字节一致。

## 两种存档格式

两个平台的 payload 布局相同，开头 12 字节为：

| 偏移 | 大小 | 含义 |
| --- | --- | --- |
| `0x00` | u32 | 小整数字段，原样保留（见"注意事项"） |
| `0x04` | u32 | 小整数字段，原样保留 |
| `0x08` | u32 | CRC32，覆盖 `payload[0x0C:]`，种子（seed）为 `payload 长度 - 0x0C` |
| `0x0C` | — | 游戏数据 |

区别只在容器外壳：

**Steam** —— `%USERPROFILE%\Saved Games\Falcom\Trails in the Sky 1st Chapter\savedata\`
下每个存档槽是一个文件夹，内含：

* `user.dat` —— payload 经 zstd 压缩；
* `icon0.png` —— 读档界面用的 228×128 截图；
* `detail.json` —— 槽位元数据（`Time`、`title`、`subtitle`、`detail`、`user_param`）。

**Switch** —— 每个存档槽是一个自包含的 `data.dat`：

```
[56 字节头][原始 payload][228×128 PNG][7×u32 日期结构][title\0subtitle\0detail\0][4 字节 0]
```

56 字节头由 14 个小端 `uint32` 组成，记录头部大小、footer 位置、payload 长度、
PNG 大小以及元数据块的偏移和长度。脚本会完全从零重建这些字段，
所以 Steam → Switch 不需要任何 Switch 模板存档。

### 两种数据，同一套容器

| 目录 | payload | 说明 |
| --- | --- | --- |
| `saveNNN` | 2,048,008 字节 | 真正的游戏进度，也是需要迁移的那一份 |
| `sdmemNNN` | 几百字节 | 系统数据：选项设置、成就等 |

两者使用完全相同的 Switch 容器和相同的 Steam 槽位结构，脚本都能重新封装；
系统数据的 `icon0.png` 在两个平台上甚至完全一致。但系统数据的 **payload 内容**
是分平台的（样本里 Switch 与 Steam 的长度和选项内容都不同），所以跨平台重封系统数据
只是把来源平台的选项块搬过去而已。建议让目标平台自己生成系统数据，脚本用于迁移 `saveNNN`。

## 已验证内容

开发时使用的参考存档（`save000_*` 槽位存档与 `sdmem000_*` 系统数据）**不随脚本分发**，
请用你自己的存档对运行 `selftest`。两类数据的所有检查均已通过：

* 两份 payload 的校验和都正确（上面的公式对 2,048,008 字节的槽位存档和几百字节的
  系统数据都能复现游戏写入的值）；
* Switch 解析→重建能得到逐字节一致的 `data.dat`；
* Steam `detail.json` 重新序列化逐字节一致；
* `Switch → Steam → Switch` 后 `data.dat` 逐字节完全还原；
* `Steam → Switch → Steam` 后 payload、`icon0.png`、`detail.json` 均逐字节一致。

## 注意事项

* **关于 `payload[0x00]`。** 在参考的**槽位存档**中该字段为 Switch=`3`、Steam=`7`
  （系统数据两边都是 `0`）。它不参与校验和，参考修改器也从不修改它；
  它可能是"存档格式版本"而非平台标记，因此脚本默认原样保留。
  如果游戏拒绝读取转换后的存档，可以强制改：
  转 Steam 用 `--set-u32 0x0=7`，转 Switch 用 `--set-u32 0x0=3`。
* **系统数据不是进度。** 如上所述，`sdmemNNN` 的 payload 是分平台的，
  跨平台转换只是重新封装而非迁移，建议保留目标平台自己的 `sdmemNNN`。
* **务必备份。** 替换前先复制一份原始槽位目录；换文件期间建议先关闭该游戏的
  Steam 云同步，避免旧的云端存档把转换结果覆盖掉。
* **导出 Switch 存档需要破解机**（如 JKSV、Checkpoint、nxdumptool），
  本脚本只处理导出的 `data.dat`。
* **格式版本。** 容器布局与校验和规则来自当前游戏版本；
  如果后续补丁改了存档格式，需要更新脚本。
* **非官方工具。** 属于玩家自制工具，使用风险自负。

## 致谢与参考

* [turtle-insect/TrailsintheSky1stChapter](https://github.com/turtle-insect/TrailsintheSky1stChapter)
  —— 本脚本所重新实现的格式来源（Switch & Steam 存档修改器）。
* [Steam 讨论：存档目录结构](https://steamcommunity.com/app/3375780/discussions/0/695374081774704068/)
  —— 确认了 `detail.json` / `icon0.png` / `user.dat` 的槽位结构。
* [3DM 论坛帖](https://bbs.3dmgame.com/thread-6618321-1-1.html) —— 同样支持
  Switch ⇄ Steam 互转的社区工具。

## 许可证

GPL-3.0。文件格式知识来自采用 GPL-3.0 的参考项目；本脚本为独立重写实现，
沿用同一许可证。
