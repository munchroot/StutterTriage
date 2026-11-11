# 更新日志（CHANGELOG）

> 本文件记录每一次公开迭代；对应构建见 [Releases](https://github.com/munchroot/StutterTriage/releases)。
> 版本号规则：`1.x.y`，功能/重构进 `x`，修复与打磨进 `y`。

---

## v1.1.3 · 六项体验修复（全屏置顶 / 灰字 / 抽动 / exe 选择 / 采集指引 / 按钮文案）

> 对应六条用户实测反馈，全部为修复与打磨，诊断口径不变。

### 修复
- **悬浮窗全屏被盖**：`WindowStaysOnTopHint` 只在建窗时生效，全屏独占游戏会
  事后压住浮窗。现在显示后与每 4s 用原生 `SetWindowPos(HWND_TOPMOST,
  NOMOVE|NOSIZE|NOACTIVATE)` 重置顶，绝不抢游戏焦点。
- **设置页灰字过多**：行标签改用主题主前景色；说明/脚注用 `contrast_on`
  与卡片底色做对比度抬升，深浅主题下都读得清。
- **选进程卡片抽动**：进程列表改为「只追加、不整表重排」，内容未变时
  不重建菜单，2.5s 刷新不再引起闪动。
- **手动添加进程**：改为直接打开文件浏览器选游戏主程序 exe；取消后仍可
  手动输入（保留 `<unknown>` 口子）。
- **选了进程却采集不到**：无帧提示现在点名帧流里**实际存在**的进程
  （勾了不产帧的名字 = 永远 0 帧的主因），并指引勾 `<unknown>` / 管理员运行。
- **「FPS / ms」看不懂**：按钮改为自解释文案「切到帧耗时视角 / 切回帧率视角」，
  tooltip 用大白话解释两种视角。

---

## 未发布 · 文档与构建整备

> 本段不改变功能与诊断口径，只整理仓库的入口、测试与发布链路。版本号留给下一版功能迭代。

### 文档
- 首页精简为「下载 / 系统要求 / 首次采集三步 / 功能亮点 / 已知限制 / 文档」，
  目录细节、测试分类、构建说明全部迁到 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。
- 踩坑、实测结论与口径说明迁到 [docs/NOTES.md](docs/NOTES.md)；
  新增 [CONTRIBUTING.md](CONTRIBUTING.md)、[docs/RELEASE_TEMPLATE.md](docs/RELEASE_TEMPLATE.md)
  与 `.github/ISSUE_TEMPLATE/bug_report.md`。
- 修正文档漂移：测试脚本数量只由 `scripts/test.ps1 -List` 给出（不再一处写 18、一处写 14）；
  构建体积/耗时标注为 **v1.0.0 历史实测**；`render_preview.py` 统一由 `preview` 套件运行。

### 测试
- 新增统一入口 `scripts/test.ps1`：本地与 CI 同一条命令，逐脚本打印通过/失败/跳过，
  汇总检查项数量，以首个失败脚本的退出码结束，并支持单脚本超时。
- 套件拆分为 `core`（离线回归，CI 默认）/ `integration`（真实 ETW、需管理员）/
  `preview`（渲染预览图，需真实桌面）；集成结果单独报告，不阻塞普通提交。
- CI 改用该入口，去掉重复的 `test_static_integrity.py` 步骤；新增可手动触发的
  integration 任务。
- `tests/test_lifecycle.py` 移除写死的本机 CapFrameX PresentMon 路径；
  「无残留 ETW 会话」在非管理员环境改为 `[SKIP]` 报告，不再误判为失败。

### 构建与发布
- 构建环境改为**干净 venv**（不再 `--system-site-packages`），依赖按
  `requirements-lock.txt` 精确安装；新增 `build_tools/check_version.py` 校验
  tag / `__version__` / PE 版本资源 / CHANGELOG / README 徽章一致。
- `build_exe.ps1 -Release`：缺内置 PresentMon 直接失败，打包后核对 EXE 的 PE 版本，
  输出 `dist/SHA256SUMS.txt` 与 `dist/THIRD_PARTY.md`。
- 新增 `.github/workflows/release.yml`：tag 触发 → 跑 core 测试 → 发布构建 →
  上传 EXE、校验值、第三方声明并创建 Release。

### 第三方声明
- `THIRD_PARTY.md` 换成 PresentMon v2.6.0 官方发布页/资产链接与 SHA-256（已与仓库内文件核对），
  并按实际构建产物清点随包 Qt 模块与插件清单。

---

## v1.1.2 · 2026-11-11

**主题：把「看得见」和「抓得到」补齐**（对应玩家实机反馈）

### 新增
- **悬浮窗**：置顶小窗显示「运行状态 / 当前游戏 / 平均帧 · 顿挫次数 / 交换证据一行」，
  带 `▶⏸`（开始暂停）、`★`（打点）、`🤖`（一键问 AI）、`👁`（透明度循环）、`✕`（隐藏）；
  可拖动、双击折叠；透明度 30~100%，配置项 `float_enabled` / `float_opacity`。
- **游戏多选 + 手动添加**：左侧「游戏」从单选下拉改为可勾选菜单，可同时盯「游戏 + 启动器」，
  也能手动输入进程名或 `<unknown>`；列表自动并入「实际在产帧的进程」。
- **全屏独占提示**：检测到 `<unknown>` 产帧时，横幅直接提示「手动添加 `<unknown>`」（GTA5 类场景）。
- **一键问 AI 入口前移**：从左侧栏底部移到顶部横幅最右侧。

### 修复
- **桌面「假游戏帧」**：桌面上 `dwm.exe` 按刷新率持续 Present，旧实现把所有帧当游戏帧，
  于是在桌面也会显示 ~95 FPS 与周期性尖峰。现在自动模式剔除桌面合成类进程
  （保留 `<unknown>`），无游戏时明确提示「未检测到游戏进程 · 桌面合成帧(dwm)已忽略」。
- **设置页阈值行文案重复**：标题里的「单位 · 默认」与说明文字重复，改为只留标题、说明进 tooltip。

### 变更
- **设置页按 FlClash 风格重做**：圆角卡片 + 「左标签（含灰字说明）/ 右控件」；
  外观 / 悬浮窗 / 每个归因维度 / 保存 / PresentMon / 交换证据链 / 热键各自成卡；
  开关为胶囊 indicator、透明度为滑杆，全部跟随主题换色。
- 测试从 16 个脚本增至 **18 个**（新增 `test_target_select.py`、`test_float_window.py`）。

---

## v1.1.1 · 2026-11-11

### 新增
- **一键问 AI**：一键把本次体检导出到桌面 CSV（32 列，含绝对时间戳），
  并弹出**可直接复制**的提示词 —— 让 ChatGPT / DeepSeek / Kimi 以「Windows 性能专家」身份，
  按证据强度给出根因排序、可执行修复步骤、不可判定项与矛盾点。

### 修复
- 标题栏写死 `1.0.0`（实际已升级）→ 改为跟随 `__version__`。
- 平均 95 帧 · C 级 却写「帧率偏低」→ 按成因分开：帧率低 / 抖动大。
- 「全程丝滑」与「顿挫 72 次」并存 → 新增 `stats.motion_verdict()`：
  顿挫 ≥10% 或 1% Low < 平均帧 25% 判「阵发性顿挫」，≥3% 判「偶发顿挫」，零顿挫才说「全程丝滑」。
- 第 12 维「内存-磁盘交换」没登记人话规则，WARN 时被静默丢弃（表格有橙行、横幅说运行很稳）→ 已补。
- 暂停时引擎徽章冻结在旧文案 → 加「已暂停 · 」前缀。

---

## v1.1.0 · 2026-11-10

**主题：把「到底是不是内存交换造成的」从猜测变成实测**（对应 `ST的改进.txt`）

### 新增
- **内存-磁盘交换证据链**（`stuttertriage/swapdiag.py`）：交换链路（pagefile% + 硬缺页）
  × 磁盘链路（物理磁盘延迟/队列）× 合成链路（`dwm.exe`）× CPU 链路**四路互证**；
  输出 `swap / disk / dwm / cpu / none / unknown` 与置信度；链路全平稳时明确写「已排除内存交换」。
- **采集层**新增 12 个 PDH 系统计数器：`Paging File(_Total)\% Usage`、`Memory\Pages Input/sec`、
  `Pages Output/sec`、`Pages/sec`、`Page Faults/sec`、`% Committed Bytes In Use`、
  `PhysicalDisk(_Total)\Avg. Disk sec/Read|Write`、`Current Disk Queue Length`、
  `Disk Read/Write Bytes/sec`、`Process(dwm)\% Processor Time`、`Process(*)\Working Set`。
- **perfmon 同步录制**（`stuttertriage/perfmon.py`）：导出采集模板（计数器清单 + `logman` 一键 bat +
  数据收集器集 XML）、解析 perfmon CSV（自动判定月/日/年顺序）、帧日志 × perfmon 逐尖峰对齐，含 CLI：
  `python -m stuttertriage.perfmon align --record … --perfmon … -o merged.csv`。
- **导出 CSV 13 → 32 列**（前 13 列完全不变），新增 `WallClock` 绝对时间戳（对齐前提）、
  页面文件 / 磁盘延迟 / 队列 / DWM / 目标进程工作集 / `SwapVerdict` 等。
- 归因表 11 → **12 维**；9 个新阈值进 `stuttertriage_config.json` 并支持热更新。

### 说明
- 实测 `logman` / `typeperf` 的 `-si` 只接受整秒（100–200ms 采样在系统自带工具上不可实现），
  故 perfmon 模板固定 1 秒、对齐容差 ±1.2s；软件内置 PDH 心跳为 0.4s，粒度更细。

---

## v1.0.0 · 2026-10-31

首个公开版本。

- 帧时间体检：FPS / 1% Low / 0.1% Low / 帧耗时 P99，实时曲线可缩放
- 归因：显存溢出 / CPU 单核拥堵 / 撞墙降频 / 着色器编译 / 内存换页 等维度的一句話结论 + 建议
- 硬件心跳：CPU 每核频率与占用、GPU 频率温度显存、内存换页、磁盘与网络
- 事件流（自动告警 + 全局热键打点）、CSV 导入回放、模拟数据降级、多套皮肤
- 随包内置 Intel PresentMon 2.6.0
