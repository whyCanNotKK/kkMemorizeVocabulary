# kkMemorizeVocabulary

[English](README.md) | **简体中文**

一个完全离线的 Windows 桌面单词本，用来手动录入英文单词、整理中文释义并遮掩答案复习。基于 Python、PySide6 / Qt Widgets 和 SQLite，所有词条与设置都保存在本机。

[GitHub 仓库](https://github.com/whyCanNotKK/kkMemorizeVocabulary) · [发布页面](https://github.com/whyCanNotKK/kkMemorizeVocabulary/releases) · [项目设计说明](docs/项目设计说明.md) · [验收记录](docs/验收记录.md)

## 主要功能

- **整理词条**：一个主词支持多条词性与释义、多行翻译，以及多个带独立释义的关联词。
- **遮掩复习**：单击遮罩揭晓整组答案，支持逐词隐藏、全局隐藏、重点星标和待记 / 已记下分类。
- **快速修改**：双击单元格就地编辑，或将整组载入底部表单；常用操作支持可配置快捷键。
- **搜索与排序**：搜索主词、关联词及其翻译；整组拖拽排序，待记列表支持随机打乱。
- **离线交换**：用 JSON 导入导出完整词条，遇到重名词可逐条对比或批量选择。
- **本地保存**：已保存的词条、记忆状态、排序和快捷键写入 SQLite，退出后保留。

适用于 Windows 10/11 x64，程序界面为中文。成品自带 Python / Qt 运行时，无需安装 Python，安装和使用均可离线；源码环境准备及首次构建需要联网。当前不提供联网词典、自动翻译、发音、账号、云同步或间隔重复算法。

## 获取与启动

### 使用安装包或免安装版

成品请查看 [Releases](https://github.com/whyCanNotKK/kkMemorizeVocabulary/releases)。如果发布页面没有附件，可按下文从源码运行或自行构建。GitHub 的 **Download ZIP** 下载的是源码，仓库不包含 `dist/` 下的成品。

| 文件 | 用途 |
| --- | --- |
| `kkMemorizeVocabulary-Setup.exe` | 双击安装，默认安装到 `%LOCALAPPDATA%\Programs\kkMemorizeVocabulary`，无需管理员权限；创建开始菜单快捷方式，可选择创建桌面快捷方式 |
| `kkMemorizeVocabulary-windows-x64.zip` | 解压整个文件夹后运行其中的 `kkMemorizeVocabulary.exe`；保留 EXE 旁的 `_internal` 目录 |
| `kkMemorizeVocabulary-Setup.exe.sha256` | 安装包的 SHA256 校验值，可用 `Get-FileHash` 核对 |

根目录的 **`BuildInstaller.exe` 是构建工具**，需要项目源码、Python 和构建环境；日常使用应启动安装后的程序或免安装版中的 `kkMemorizeVocabulary.exe`。

### 从源码运行

准备 Windows x64 和 Python 3.12 x64，在 PowerShell 中执行：

```powershell
git clone https://github.com/whyCanNotKK/kkMemorizeVocabulary.git
cd kkMemorizeVocabulary
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe main.py --data-dir artifacts\development
```

若已下载源码，从项目根目录的创建环境命令开始执行。若未安装 `py` 启动器，可在确认 `python` 指向 Python 3.12 x64 后，用 `python -m venv .venv` 创建环境。锁定依赖包含运行、测试与打包工具。

上述命令使用隔离的开发数据目录。环境准备好后，也可运行 `.\start.bat --data-dir artifacts\development`。日常使用默认数据目录时，执行：

```powershell
.\.venv\Scripts\python.exe main.py
```

或直接双击 `start.bat`。它使用已配置的 `.venv` 启动程序，不会自动安装依赖。

## 使用方法

### 录入与修改

1. 在底部依次输入单词、词性和翻译，按 **Ctrl+Enter** 保存。主词必须有名称和至少一条完整释义，关联词同样如此；翻译框内按 Enter 换行。
2. 用“增加释义”添加同一个词的其他含义，用“新增关联词”添加附属词。释义操作作用于当前焦点所在的词；每个词至少保留一条释义。
3. 新词默认设为重点，保存按钮左侧的星标可切换。保存后新词进入待记列表末尾，自动展开并滚动定位。
4. 双击列表中的名称、词性或翻译就地编辑；按保存快捷键或离开编辑框提交，Esc 取消。需要增删关联词或释义时，点击铅笔载入整组，完成后保存修改或取消。

主词去除首尾空格、忽略大小写后不能重名；关联词允许同名，也不会自动建立双向关系。重名或校验失败时保留输入，重复词提示提供“查看已有词”入口。

录入区随内容自动增高，最多占窗口客户区高度的一半，超出后滚动输入区域。顶部正中的折线按钮可收起 / 展开录入区，并保留当前草稿；草稿不会因收起而保存，退出前仍需处理未保存内容。

### 复习与分类

- 默认显示“待记”，还可切换“全部”和“已记下”。每次启动所有已存词条默认隐藏。
- 单击灰色遮罩，显示主词全部释义与关联词；非编辑状态下 **Ctrl+单击翻译**重新隐藏整组。
- 右上角全局隐藏开关控制所有词条，包含当前分类之外的词；展开状态仅在本次运行中保留，新保存的词仍默认展开。
- 点击圆圈标记已记下，在已记下列表中点击红色横杠恢复待记。列表星标立即保存重点状态；垃圾桶删除整个主词及其附属内容，删除前会确认。
- 待记、全部、已记下各自记住本次运行的滚动位置，重启后从顶部开始。

### 搜索与排序

搜索框匹配主词、主词翻译、关联词及其翻译中的字面子字符串，忽略大小写和关键词首尾空格；切换分类保留关键词。匹配后展示完整主词组，并临时揭晓全部答案；清空搜索后恢复原有遮掩状态。搜索时逐词隐藏无效，全局隐藏开关仍可更新清空搜索后的状态。

待记和已记下列表支持通过操作栏左侧的拖拽柄移动整组，松开鼠标保存；Esc 或在列表视口外释放取消。全部页不支持拖拽。待记页左上角的随机按钮可打乱顺序，成功后回到列表顶部。**搜索关键词非空时，拖拽和随机均禁用**；少于两个待记词时也不能随机。

顺序保存在数据库中。新增词及切换记忆状态的词追加到目标分类末尾，普通编辑保留位置；全部页先显示待记，再显示已记下。

## 默认快捷键

顶部“快捷键设置”可修改前六项操作，配置即时生效并保存在数据库中。快捷键仅在应用自身窗口内接收，支持中英文输入状态。

| 快捷键 | 操作 |
| --- | --- |
| Ctrl+N | 新增关联词并聚焦其单词框 |
| Ctrl+M | 移除最后一个关联词 |
| Ctrl+J | 新增当前焦点所在词的释义 |
| Ctrl+K | 移除当前词最后一条释义，至少保留一条 |
| Ctrl+Enter | 保存整组；列表编辑时优先保存当前单元格 |
| Ctrl+I | 切换底部当前录入主词的重点状态 |
| Tab / Shift+Tab | 正向 / 反向切换焦点 |
| Enter | 翻译输入框内换行 |
| 空格 | 词性框聚焦时打开下拉框；星标聚焦时切换重点 |
| 1–9 | 词性框聚焦时直接选择词性 |
| n / v / a | 词性框聚焦时选择名词 / 动词 / 形容词；重复按 a 在 adj. 与 adv. 间切换 |

词性数字依次对应：1 名词、2 动词、3 形容词、4 副词、5 介词、6 代词、7 连词、8 感叹词、9 其他。字母支持大小写，词性框收起或展开时均可使用。

## JSON 导入导出

顶部全局隐藏开关右侧依次是导入、导出按钮。导出范围是**当前分类与搜索关键词共同匹配的全部词条**，包括需要滚动才能看到的结果；遮掩状态不影响内容，未保存的表单内容需先保存。

导出为 UTF-8 JSON，包含重点、记忆状态、排序号、全部释义与关联词，保留数组顺序和译文内部换行；不包含快捷键、数据库内部 ID 或临时展开状态。当前导出版本为 2，导入兼容版本 1、2：

```json
{
  "format": "kkMemorizeVocabulary",
  "version": 2,
  "words": [
    {
      "text": "record",
      "important": true,
      "learned": false,
      "sort_order": 1,
      "senses": [{"pos": "n.", "translation": "记录；唱片"}],
      "related": [
        {"text": "recorder", "senses": [{"pos": "n.", "translation": "录音机"}]}
      ]
    }
  ]
}
```

除 `sort_order` 可缺省外，示例中的字段均需提供；`related` 可为空数组，`senses` 至少包含一条完整释义。词性取值为 `n.`、`v.`、`adj.`、`adv.`、`prep.`、`pron.`、`conj.`、`interj.` 或 `其他`；`important`、`learned` 必须是 JSON 布尔值。排序号存在时须为正整数，不能是布尔值。整个文件先校验，格式或内容无效时不会写入任何词条。

导入顺序与重复处理：

- 全部词都有排序号时，按号稳定升序处理；否则按文件顺序。入库时重新分配排序号，不直接沿用导入值。
- 先在一个事务中导入非重复词，再弹出全部重复项供对比。仅按主词去首尾空格、忽略大小写判断重复，也检查同一文件内的重复词。
- 点击左侧保留现有词；点击右侧采用导入词的完整内容和状态，事务内删除旧词并新建，生成新 ID，追加到目标分类末尾。成功后该对卡片立即移除。
- “全部使用左侧 / 右侧”处理剩余项目。逐条选择按点击顺序生效，批量按剩余候选顺序生效；同名词最后一次采用的内容生效。批量采用右侧失败时整批回滚。
- 关闭对比窗口保留未处理的原词，已导入的新词和已确认的选择继续保留。

## 数据、备份与更新

默认数据目录为 `%LOCALAPPDATA%\kkMemorizeVocabulary`，与程序安装目录分开：

| 文件 | 内容 |
| --- | --- |
| `vocabulary.sqlite3` | 全部词条、重点、记忆状态、排序和快捷键配置 |
| `application.log` 及轮转日志 | 程序错误与诊断信息 |

**完整备份与恢复**：先退出程序，再复制或恢复数据库文件。运行期间可能出现 `vocabulary.sqlite3-wal` 和 `vocabulary.sqlite3-shm`，不要在运行时单独移动或删除它们。JSON 适合交换词条，完整备份应使用数据库，以保留快捷键设置。

**更新**：先退出程序。安装版运行新的安装包并使用原安装目录；免安装版替换完整程序文件夹。用户数据目录会保留，在 Windows“设置 → 应用”卸载安装版也会保留词条与设置。

**版本兼容**：当前 SQLite 数据库版本为 2，旧版 v1 数据库会自动迁移并保留原分类内顺序。升级前建议备份；升级后继续使用新版程序，旧程序不能打开 v2 数据库或 v2 JSON 文件。数据库版本与应用版本分别管理。

可通过启动参数指定其他可写目录，例如：

```powershell
.\kkMemorizeVocabulary.exe --data-dir "D:\MyVocabulary"
```

免安装版的数据库默认仍在上述用户数据目录中；如果需要随文件夹携带数据，应显式指定 `--data-dir`。

## 开发与验证

开发运行使用前文的 `--data-dir artifacts\development`。自动化测试使用临时数据库，不写入默认用户数据目录：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

源码冒烟检查会写入演示词、输出 `smoke-result.json` 和界面截图，然后自动退出，**必须显式指定隔离目录**：

```powershell
.\.venv\Scripts\python.exe main.py --smoke-test --data-dir artifacts\source-smoke
```

同一命令再次运行可检查重启后的数据和快捷键恢复。可在报告中查看 `ok` 与各项检查结果。验收细节及历史结果见 [验收记录](docs/验收记录.md)。

| 路径 | 职责 |
| --- | --- |
| `main.py`、`vocabulary/app.py` | 启动参数、数据目录、日志和程序生命周期 |
| `vocabulary/domain.py`、`vocabulary/storage.py` | 领域对象、校验、SQLite 与事务 |
| `vocabulary/window.py`、`vocabulary/table.py`、`vocabulary/widgets.py` | 窗口协调、列表编辑与输入控件 |
| `vocabulary/shortcuts.py`、`vocabulary/native_shortcuts.py`、`vocabulary/theme.py` | 快捷键、Windows 原生按键接收与主题 |
| `vocabulary/transfer.py`、`vocabulary/import_dialog.py` | JSON 格式与重复词对比导入 |
| `tests/` | 存储与界面测试 |
| `build.ps1`、`installer/`、`scripts/` | 构建、安装配置与发布校验 |

完整需求、模块边界和数据版本策略以 [项目设计说明](docs/项目设计说明.md) 为准。新增功能前先更新设计与受影响的数据版本策略。

## 构建 Windows 安装包

构建机需要 Windows x64 和 Python 3.12 x64。双击根目录 **`BuildInstaller.exe`**，或在项目根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

双击入口调用同目录的 `build.ps1`，窗口显示进度，结束后按 Enter 关闭，成功时打开输出目录并选中安装包。入口必须与源码一起使用，每次打包当前源码。

脚本会创建缺失的 `.venv`、补齐锁定依赖、准备 Inno Setup、执行测试，再使用 PyInstaller 构建并校验源码与 DLL，生成免安装 ZIP 和安装包。首次联网下载固定版本的 Inno Setup 6.7.3，校验 SHA256 后以便携模式缓存到 `build\tools`，无需全局安装编译器；环境齐全后可复用缓存。

构建成功后输出：

| 路径 | 内容 |
| --- | --- |
| `dist\kkMemorizeVocabulary-Setup.exe` | 可单独分发的完整安装包 |
| `dist\kkMemorizeVocabulary-Setup.exe.sha256` | 安装包 SHA256 校验值 |
| `dist\kkMemorizeVocabulary\` | 完整免安装程序目录，包含 `_internal` |
| `dist\kkMemorizeVocabulary-windows-x64.zip` | 免安装程序目录压缩包 |
| `build\installer-build.log` | 构建日志 |

打包后的程序同时包含中英文 README。安装包版本读取 `pyproject.toml`。失败时显示 `BUILD FAILED` 并返回非零退出码；`dist/` 中可能残留旧产物，不能据此判断本次构建成功。重复启动只允许一个构建运行。

修改构建入口源码 `scripts\BuildInstaller.cs` 后，可用 Windows 自带的 .NET Framework 编译器重建入口：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-launcher.ps1
```

若成品启动报 Qt DLL 加载错误，先确认使用的是完整程序目录，并用构建脚本重新打包及校验。构建脚本会隔离依赖搜索路径，检查源码一致性和 Windows / ICU 依赖，避免其他开发工具的 DLL 混入产物。
