# 开发与打包指南

本文面向想改代码、跑测试或自己打包的人。普通使用请看 [README](../README.md)。

## 1. 环境

- Windows 10 1709+ / Windows 11（x64）
- Python 3.11+
- 运行时依赖：PyQt6、pyqtgraph、numpy
- 构建依赖：PyInstaller（只在打包时需要）

```powershell
# 日常开发：范围依赖，允许小版本升级
python -m pip install -r requirements.txt

# 复现 CI / 发布构建：精确锁定版本（推荐）
python -m pip install -r requirements-lock.txt

python StutterTriage.py
```

PresentMon 已随包内置（`external/PresentMon/PresentMon-x64.exe`，Intel 官方 2.6.0，
SHA-256 见 [THIRD_PARTY.md](../THIRD_PARTY.md)）。
想用自己的版本：把 `PresentMon.exe` 放到 exe 所在目录，或在 ⚙ 设置里指定路径。

## 2. 目录结构

每个模块只干一件事。

```
StutterTriage.py                 启动器（薄壳）+ 旧顶层名字兼容导出
stuttertriage/
  paths.py                  「程序所在目录」的单一来源（配置/工具/提权脚本路径）
  theme.py                  主题系统：配色 + 运行时热切换注册表 + pg 菜单汉化
  consts.py                 诊断阈值常量（可被 stuttertriage_config.json 覆盖，热更新广播）
  utils.py                  结构化日志 / 有界队列 / 格式化 / pg 符号校验
  probes.py                 底层 ctypes 探针：PDH、DXGI、AMD ADL、缓存目录监视、CPU 功率
  models.py                 Event / HardwareSnapshot / FrameRecord / DiffRow
  etw.py                    ETW 会话管家（根治 PresentMon error 1450 静默 0 帧）
  engines/hardware.py       HardwareEngine 轮询线程（CPU/GPU/显存/IO/网络/缓存/功率）
  engines/presentmon.py     PresentMon 定位与能力探测 + PresentMonBridge 采集线程
  analysis.py               BaselineTracker / FrameStore / SnapshotDiffInspector（12 维归因）
  aiask.py                  「一键问 AI」：桌面 CSV 命名 + 体检摘要 + 专家提示词拼装（纯函数）
  swapdiag.py               内存-磁盘交换证据链：尖峰与 pagefile/磁盘/DWM 的可证伪判定（纯函数）
  perfmon.py                perfmon 采集模板 + perfmon CSV 解析 + 帧日志时间对齐（含 CLI）
  config.py                 stuttertriage_config.json 读写与回落
  hotkeys.py                全局热键（RegisterHotKey + 原生事件过滤器）
  winadmin.py               管理员权限 / PresentMon 指引 / 数据源判定 / 模拟水印
  csvio.py                  CSV 后台读取（不冻结界面）
  stats.py                  帧率统计口径：平均/最高/1% Low/顿挫/评级/瓶颈（纯函数）
  ui/base_window.py         可复用窗口逻辑（设置页 / CSV / ETW / 热键 …）
  ui/main_window.py         主窗口装配（左栏导航卡 + 右侧 QStackedWidget + 每帧刷新口径）
  ui/navtabs.py             左侧 5 张大号视图导航卡（图标 + 模块名 + 一行实时状态徽章）
  ui/pages.py               QStackedWidget 的 5 个全屏页面（总览/CPU/显卡/内存/网络）
  ui/gauge.py               图形元件（胶囊条/圆环/能量柱/频率柱/显存大横条/段位徽章）
  ui/chart.py               实时 FPS 曲线（渐变填充 + 均值虚线 + 顿挫红点 + 锁死坐标轴）
  ui/cpu_trend.py           CPU 折线：均频/功率总览 + 每线程一条（可滚动）折线
  ui/aiask_dialog.py        一键问 AI 结果窗口（专家提示词 / 一句话版 + 一键复制）
  ui/multiselect.py         多选游戏控件（勾选多个进程 / 手动添加 <unknown>）
  ui/float_window.py        置顶悬浮窗（状态 + 开始暂停/打点/问 AI + 透明度）
  ui/diagnosis.py           极简卡顿病历卡（idle/红/绿三态，高度恒定）+ 12 项专业对照表窗口
  ui/plain.py               小白话术映射表（专业归因 → 人话 + 解决建议）
  ui/widgets.py             通用可视化组件（CPU 热力块矩阵 / ElidedLabel / 探针读数条 / 进度条）
  app.py                    进程入口 main()
scripts/test.ps1            统一测试入口（本地与 CI 同一条命令）
docs/                       开发文档、设计资料、界面预览图、发行说明模板
assets/                     打包用图标（stuttertriage.ico 多尺寸 + 256px PNG 源图）
build_tools/                打包脚本与 PyInstaller 配置（build_exe.ps1 / StutterTriage.spec /
                            version_info.txt / check_version.py）
tests/                      18 个测试脚本 + 1 个预览图渲染脚本（见 §3）
```

> `dist/`、`_build_env/`、`*.exe` 都在 `.gitignore` 里：产物走 Releases，不进仓库。

## 3. 测试

统一入口是 `scripts/test.ps1`，本地与 CI 跑的是同一条命令。脚本会逐个执行测试、
打印每个脚本的通过/失败/跳过，并在最后汇总；退出码取**首个失败脚本**的退出码，
单个脚本超过 `-TimeoutSec`（默认 300 秒）按失败处理。

```powershell
pwsh -File scripts\test.ps1                    # core：普通离线回归（默认）
pwsh -File scripts\test.ps1 -Suite integration # 真实 ETW / 管理员权限用例
pwsh -File scripts\test.ps1 -Suite preview     # 渲染 docs/preview_*.png（真实桌面）
pwsh -File scripts\test.ps1 -Suite all         # 全部
pwsh -File scripts\test.ps1 -Filter test_swap* # 只跑匹配的脚本
pwsh -File scripts\test.ps1 -List              # 只看会跑哪些，不执行
```

需要指定解释器时（例如项目外的构建虚拟环境）：

```powershell
pwsh -File scripts\test.ps1 -Python F:\Tools\_build_env\stuttertriage_venv\Scripts\python.exe
```

### 套件划分

| 套件 | 内容 | 运行环境 |
| --- | --- | --- |
| `core`（默认） | 纯逻辑、静态不变量、offscreen 运行时冒烟、暗色可读性、退出清理、版面零抖动、交换证据链、多选与悬浮窗 | 普通 Windows runner 即可，不需要管理员 |
| `integration` | `test_lifecycle.py`：真实 ETW 会话启停、线程回收、会话残留清理、stderr 洪水死锁回归 | 需要真实 ETW；`logman stop` 需要管理员，非管理员只报告「跳过」 |
| `preview` | `render_preview.py`：渲染界面预览图 | 需要真实 Windows 桌面（offscreen 没有中文字体） |

`core` 里的脚本在缺硬件计数器、缺 PyQt6 或没有管理员权限时会自行打印 `[SKIP]` 并继续，
不会把环境限制算成失败。集成类结果请**单独报告**，不要和离线回归混在一起看。

### 各脚本用途

| 脚本 | 套件 | 内容 |
| --- | --- | --- |
| `test_v1_layout.py` | core | 版面零抖动 / 折线去重 / 换肤持久化 / 归因表可读性 / 每线程窗口 / 换页归因 / 白底对比度 / 面板与设置 / 手动开始采集与清空重采 |
| `test_algofix.py` | core | 3 个算法修复：爆显存门控 / 降频门控 / 顿挫双重门控（正+负用例） |
| `test_chart_axis.py` | core | 坐标轴：空载 0~100 / FPS 0~144 / ms 0~35 + 参考虚线 |
| `smoke_v4_runtime.py` | core | 端到端：导航卡 / 五页 / 徽章 / 病历卡红绿两态 / 坐标轴 / 降频 / 显存门控 |
| `test_dark_settings_readability.py` | core | 暗色主题下菜单 / 设置页 / 弹窗对比度 ≥ WCAG AA（4 主题）；无真实桌面时跳过 |
| `test_close_cleanup.py` | core | 关闭后无残留窗口 / 无孤儿进程（含 Job Object 联动杀验证） |
| `test_static_integrity.py` | core | 拆分不变量：无漏 import / 无冻结常量 / 主题广播 / 路径来源 / 随包 PresentMon |
| `test_plain_and_stats.py` | core | 小白话术映射 + 帧率口径 + 评级 + 阈值广播 |
| `test_swap_evidence.py` | core | 交换证据链判定 + perfmon CSV 解析/时间对齐 + 导出列契约 |
| `test_aiask.py` | core | 一键问 AI：桌面路径 / 提示词内容 / 弹窗复制 / 点击端到端 |
| `test_target_select.py` | core | 多选游戏 / 手动添加 / 桌面帧过滤 / 浮窗与设置卡片接线 |
| `test_float_window.py` | core | 悬浮窗构造 / 状态 / 透明度 / 回调 / 折叠 / 隐藏信号 |
| `test_theme.py` | core | 主题 / 热键 / 图表样式契约 |
| `test_ux.py` | core | 59 项 UX 契约（配置 / 数据源 / 水印 / 热键 / 黑名单 / CSV / ETW / 打点） |
| `test_regression.py` | core | 阻断级缺陷回归（时间轴单位、导入失败复位、基线鲁棒性…） |
| `smoke_ux_runtime.py` | core | 旧 UX 冒烟（导入 / 水印 / 导出列 / 关闭流程） |
| `test_verdict_consistency.py` | core | 横幅 / 病历卡 / AI 摘要的结论一致性 |
| `test_lifecycle.py` | integration | 生命周期：线程可 join、无 ETW 残留、stderr 洪水不死锁、脏 CSV、硬件不可用不伪造 |
| `render_preview.py` | preview | 渲染界面预览图到 `docs/preview_*.png` |

`render_preview.py` 会生成：`preview_deepspace.png`（总览·深空黑）、`preview_light.png`（总览·浅色）、
`preview_ms_view.png`（ms 视角）、`preview_page_cpu/gpu/mem/net.png`（四张导航页）、
`preview_settings_dark/light.png`（设置页）。

## 4. 构建与发布

产物：`dist\StutterTriage.exe`（单文件、免安装、便携）。

### 4.1 本地构建

```powershell
# 干净环境安装锁定依赖 → 打包 → 输出 dist\StutterTriage.exe
pwsh -File build_tools\build_exe.ps1

# 重建构建环境（依赖升级 / 环境损坏时）
pwsh -File build_tools\build_exe.ps1 -RebuildVenv

# 发布构建：校验版本一致性、要求 PresentMon 必须在位、输出 SHA-256
pwsh -File build_tools\build_exe.ps1 -Release -Tag v1.1.2
```

| 项 | 说明 |
| --- | --- |
| 构建环境 | 默认在项目外：`<项目>\..\_build_env\stuttertriage_venv`。**不再使用 `--system-site-packages`**：虚拟环境是干净的，依赖一律按 `requirements-lock.txt` 安装，本机全局装了什么都影响不到产物。可用 `-Venv <路径>` 或 `STUTTERTRIAGE_BUILD_VENV` 覆盖 |
| 依赖锁定 | `requirements-lock.txt` 精确锁定 PyQt6 / Qt6 / sip / pyqtgraph / numpy / PyInstaller 的版本；CI 与发布构建都用它 |
| 中间产物 | PyInstaller 的 work 目录也在项目外（`_build_env\work`），打完自动清掉，项目里只留 `dist\` |
| 体积 / 耗时 | **历史数据（v1.0.0 实测）**：约 50.3 MB、46 秒（Python 3.11 + PyQt6 6.11 + numpy 2.4 + pyqtgraph 0.14）。当前版本请以 `-Release` 输出的实测值为准 |
| 启动耗时 | 单文件模式约 4 秒（每次启动要先把约 50 MB 解包到 `%TEMP%\_MEI*`）。想更快，把 spec 里的 `EXE(...)` 改成 `COLLECT(...)` 出 onedir 版，启动 < 1 秒 |
| 图标 / 版本 | `assets\stuttertriage.ico`（16~256 多尺寸）+ `build_tools\version_info.txt` |
| 隐藏导入 | spec 里用 `collect_submodules("stuttertriage")` 显式列出全部子模块，防止函数内延迟导入被静态分析漏掉 |

`-Release` 会做三件事：

1. 调用 `build_tools/check_version.py` 校验 tag、`stuttertriage/__init__.py` 的 `__version__`、
   `build_tools/version_info.txt` 的文件版本与 `CHANGELOG.md` 最新条目是否一致；
2. 缺少 `external/PresentMon/PresentMon-x64.exe` 时**直接失败**（正式包不能缺件）；
3. 打包后核对 EXE 的 PE 文件版本，并输出 `dist/SHA256SUMS.txt`。

**便携式行为**：`stuttertriage/paths.py` 按 `sys.frozen` 分支处理，打包后「程序目录」就是 exe 所在目录。

- `stuttertriage_config.json` 会生成/读取 exe 旁边那一份。想沿用源码里的设置，把它复制到 exe 同目录即可。
- PresentMon 查找顺序：exe 目录 → `PresentMon\` 子目录 → PATH → 设置里指定的路径。
- 运行日志写在 `%USERPROFILE%\.stuttertriage\stuttertriage_diag.log`，不污染程序目录。

### 4.2 CI 与发布

| 工作流 | 触发 | 内容 |
| --- | --- | --- |
| `.github/workflows/ci.yml` | push / PR 到 `main` | 安装 `requirements-lock.txt`，跑 `scripts/test.ps1 -Suite core`；集成用例单独一个可手动触发的任务 |
| `.github/workflows/release.yml` | push tag `v*` | 跑 core 测试 → `build_exe.ps1 -Release -Tag <tag>` → 校验 PE 版本 → 上传 EXE、`SHA256SUMS.txt`、`THIRD_PARTY.md` 并创建 Release |

发布说明只写本版本变化，不放开发细节；模板见 [RELEASE_TEMPLATE.md](RELEASE_TEMPLATE.md)。

### 4.3 发布验收

在干净的 Windows x64 环境里，用发布产物走一遍：

1. 把 EXE 复制到另一个目录启动，确认窗口标题版本与 tag 一致；
2. 配置可持久化（改主题/阈值 → 重启仍生效）；
3. 内置 PresentMon 可定位（设置页显示路径），无管理员权限时提示可解释、不静默 0 帧；
4. 离线导入一份 CSV，确认帧数、时间范围、数据来源与界面一致；
5. 关闭后无残留 StutterTriage / PresentMon 进程与本实例 ETW 会话。

## 5. 配置与日志

| 文件 | 位置 | 说明 |
| --- | --- | --- |
| `stuttertriage_config.json` | 与 `StutterTriage.py` 同目录 | 主题、图表样式、热键、PresentMon 路径 + 12 个诊断阈值；脏值一律回落默认，不会让程序起不来 |
| `stuttertriage_diag.log` | `%USERPROFILE%\.stuttertriage\` | 结构化异常日志（关键路径异常必留痕、同类限频） |

路径来源统一由 `stuttertriage/paths.py` 提供。

## 6. 内存-磁盘交换与 perfmon 对齐

PresentMon 的 CSV 只有帧时间，没有页面文件交换、硬缺页、磁盘延迟与队列，所以「窗口化时 >100ms 的尖峰是不是内存交换造成的」要靠系统计数器证明。

1. **采集**：硬件心跳新增 pagefile 占用、Pages Input/Output、物理磁盘读/写延迟与队列、`dwm.exe` 占用、目标游戏工作集/缺页速率（全部是 PDH 英文计数器，中英文 Windows 通用）。
2. **诊断**：归因表第 12 行用三条链路互证：交换链路（pagefile% / 硬缺页）+ 磁盘链路（延迟 / 队列）+ 合成链路（DWM）。交换与磁盘同时升高 → DANGER；全部平稳 → 明确写「已排除内存交换」，并指出磁盘还是 DWM/CPU 更可疑。
3. **导出**：`stuttertriage_record.csv` 从 13 列扩到 32 列，前 13 列不变；新增 `WallClock`（每帧绝对时间戳），这是与 perfmon 对齐的前提。
4. **对齐**：设置页 → 内存交换证据链：
   - 「导出 perfmon 采集模板」得到 `perfmon_counters.txt`、`start/stop_perfmon.bat` 和数据收集器集 XML；
   - 录完游戏后「对齐 perfmon CSV…」，逐尖峰输出「交换 / 磁盘 / DWM / 已排除」，并另存 `*_aligned.csv`。

命令行方式：

```powershell
python -m stuttertriage.perfmon template -o .\perfmon_template
python -m stuttertriage.perfmon align --record stuttertriage_record.csv --perfmon perfmon_xxx.csv -o merged.csv
```

设计取舍与阈值口径见 [UPGRADE_1.1.0.md](UPGRADE_1.1.0.md)。

## 7. 第三方组件

- 随包分发的 PresentMon 版本、来源链接、SHA-256 与许可：见 [THIRD_PARTY.md](../THIRD_PARTY.md)。
- Python 依赖的版本与许可：见 `requirements-lock.txt` 与 [THIRD_PARTY.md](../THIRD_PARTY.md)。
- 换 GUI 框架（PyQt6 → PySide6）前，先验证 PyQtGraph、信号、Qt 枚举、悬浮窗与 offscreen
  测试是否全部可用，再改依赖与构建配置；许可差异见 THIRD_PARTY.md。
